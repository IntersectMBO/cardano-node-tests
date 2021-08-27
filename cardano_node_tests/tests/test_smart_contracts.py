"""Tests for smart contracts."""
import logging
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"


class PlutusOp(NamedTuple):
    script_file: Path
    datum_file: Path
    redeemer_file: Path
    execution_units: Optional[Tuple[int, int]] = None


class Token(NamedTuple):
    coin: str
    amount: int


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
class TestPlutus:
    """Tests for Plutus smart contracts."""

    PLUTUS_DIR = DATA_DIR / "plutus"
    ALWAYS_SUCCEEDS_PLUTUS = PLUTUS_DIR / "always-succeeds-spending.plutus"
    ALWAYS_FAILS_PLUTUS = PLUTUS_DIR / "always-fails.plutus"
    GUESSING_GAME_PLUTUS = PLUTUS_DIR / "custom-guess-42-datum-42.plutus"
    MINTING_PLUTUS = PLUTUS_DIR / "anyone-can-mint.plutus"

    def _fund_script(
        self,
        temp_template: str,
        cluster_obj: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        plutus_op: PlutusOp,
        amount: int,
        tokens: Optional[List[Token]] = None,  # tokens must already be in `payment_addr`
    ) -> clusterlib.TxRawOutput:
        """Fund a plutus script and create the locked UTxO and collateral UTxO."""
        assert plutus_op.execution_units, "Execution units not provided"
        plutusrequiredtime, plutusrequiredspace = plutus_op.execution_units

        fund_tokens = tokens or ()

        script_address = cluster_obj.gen_script_addr(
            addr_name=temp_template, script_file=plutus_op.script_file
        )

        fee_redeem = int(plutusrequiredtime + plutusrequiredspace) + 10_000_000
        collateral_fraction = cluster_obj.get_protocol_params()["collateralPercentage"] / 100
        collateral_amount = int(fee_redeem * collateral_fraction)

        script_init_balance = cluster_obj.get_address_balance(script_address)

        # create a Tx ouput with a datum hash at the script address

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        datum_hash = cluster_obj.get_hash_script_data(script_data_file=plutus_op.datum_file)
        txouts = [
            clusterlib.TxOut(
                address=script_address, amount=amount + fee_redeem, datum_hash=datum_hash
            ),
            # for collateral
            clusterlib.TxOut(address=dst_addr.address, amount=collateral_amount),
        ]

        for token in fund_tokens:
            txouts.append(
                clusterlib.TxOut(
                    address=script_address,
                    amount=token.amount,
                    coin=token.coin,
                    datum_hash=datum_hash,
                )
            )

        fee = cluster_obj.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output = cluster_obj.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        tx_signed = cluster_obj.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )

        cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)

        script_balance = cluster_obj.get_address_balance(script_address)
        assert (
            script_balance == script_init_balance + amount + fee_redeem
        ), f"Incorrect balance for script address `{script_address}`"

        for token in fund_tokens:
            assert (
                cluster_obj.get_address_balance(script_address, coin=token.coin) == token.amount
            ), f"Incorrect token balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

        return tx_raw_output

    def _spend_locked_txin(
        self,
        temp_template: str,
        cluster_obj: clusterlib.ClusterLib,
        dst_addr: clusterlib.AddressRecord,
        tx_output_fund: clusterlib.TxRawOutput,
        plutus_op: PlutusOp,
        amount: int,
        tokens: Optional[List[Token]] = None,
        expect_failure: bool = False,
        script_valid: bool = True,
    ) -> str:
        """Spend the locked UTxO."""
        assert plutus_op.execution_units, "Execution units not provided"
        plutusrequiredtime, plutusrequiredspace = plutus_op.execution_units

        spent_tokens = tokens or ()

        script_address = tx_output_fund.txouts[0].address  # the first txout is script
        fee_redeem = int(plutusrequiredtime + plutusrequiredspace) + 10_000_000
        collateral_amount = tx_output_fund.txouts[1].amount  # the second txout is collateral

        script_init_balance = cluster_obj.get_address_balance(script_address)
        dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

        # spend the "locked" UTxO

        txid = cluster_obj.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster_obj.get_utxo(txin=f"{txid}#0")
        collateral_utxo = cluster_obj.get_utxo(txin=f"{txid}#1")[0]
        plutus_txins = [
            clusterlib.PlutusTxIn(
                txins=script_utxos,
                collateral=collateral_utxo,
                script_file=plutus_op.script_file,
                execution_units=(plutusrequiredtime, plutusrequiredspace),
                datum_file=plutus_op.datum_file,
                redeemer_file=plutus_op.redeemer_file,
            )
        ]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        for token in spent_tokens:
            txouts.append(
                clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
            )

        tx_raw_output = cluster_obj.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee_redeem,
            plutus_txins=plutus_txins,
            script_valid=script_valid,
        )
        tx_signed = cluster_obj.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        if not script_valid:
            cluster_obj.submit_tx(tx_file=tx_signed, txins=[collateral_utxo])

            assert (
                cluster_obj.get_address_balance(dst_addr.address)
                == dst_init_balance - collateral_amount
            ), f"Collateral was NOT spent from `{dst_addr.address}`"

            assert (
                cluster_obj.get_address_balance(script_address) == script_init_balance
            ), f"Incorrect balance for script address `{script_address}`"

            return ""

        if expect_failure:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster_obj.submit_tx_bare(tx_file=tx_signed)
            err = str(excinfo.value)
            assert (
                cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance
            ), f"Collateral was spent from `{dst_addr.address}`"

            assert (
                cluster_obj.get_address_balance(script_address) == script_init_balance
            ), f"Incorrect balance for script address `{script_address}`"

            return err

        cluster_obj.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_raw_output.plutus_txins]
        )

        assert (
            cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        assert (
            cluster_obj.get_address_balance(script_address)
            == script_init_balance - amount - fee_redeem
        ), f"Incorrect balance for script address `{script_address}`"

        for token in spent_tokens:
            assert (
                cluster_obj.get_address_balance(script_address, coin=token.coin) == 0
            ), f"Incorrect token balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

        return ""

    def _build_fund_script(
        self,
        temp_template: str,
        cluster_obj: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        plutus_op: PlutusOp,
        tokens: Optional[List[Token]] = None,  # tokens must already be in `payment_addr`
    ) -> clusterlib.TxRawOutput:
        """Fund a plutus script and create the locked UTxO and collateral UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        script_fund = 1000_000_000
        collateral_fund = 1500_000_000

        fund_tokens = tokens or ()

        script_address = cluster_obj.gen_script_addr(
            addr_name=temp_template, script_file=plutus_op.script_file
        )
        script_init_balance = cluster_obj.get_address_balance(script_address)

        # create a Tx ouput with a datum hash at the script address

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        datum_hash = cluster_obj.get_hash_script_data(script_data_file=plutus_op.datum_file)
        txouts = [
            clusterlib.TxOut(address=script_address, amount=script_fund, datum_hash=datum_hash),
            # for collateral
            clusterlib.TxOut(address=dst_addr.address, amount=collateral_fund),
        ]

        for token in fund_tokens:
            txouts.append(
                clusterlib.TxOut(
                    address=script_address,
                    amount=token.amount,
                    coin=token.coin,
                    datum_hash=datum_hash,
                )
            )

        tx_output = cluster_obj.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            fee_buffer=2_000_000,
        )
        tx_signed = cluster_obj.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        script_balance = cluster_obj.get_address_balance(script_address)
        assert (
            script_balance == script_init_balance + script_fund
        ), f"Incorrect balance for script address `{script_address}`"

        for token in fund_tokens:
            assert (
                cluster_obj.get_address_balance(script_address, coin=token.coin) == token.amount
            ), f"Incorrect token balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)

        return tx_output

    def _build_spend_locked_txin(
        self,
        temp_template: str,
        cluster_obj: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        tx_output_fund: clusterlib.TxRawOutput,
        plutus_op: PlutusOp,
        amount: int,
        tokens: Optional[List[Token]] = None,
        expect_failure: bool = False,
        script_valid: bool = True,
    ) -> str:
        """Spend the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        # pylint: disable=too-many-arguments
        script_address = tx_output_fund.txouts[0].address  # the first txout is script
        collateral_amount = tx_output_fund.txouts[1].amount  # the second txout is collateral

        spent_tokens = tokens or ()

        script_init_balance = cluster_obj.get_address_balance(script_address)
        dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

        # spend the "locked" UTxO

        txid = cluster_obj.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster_obj.get_utxo(txin=f"{txid}#1")
        collateral_utxo = cluster_obj.get_utxo(txin=f"{txid}#2")[0]
        plutus_txins = [
            clusterlib.PlutusTxIn(
                txins=script_utxos,
                collateral=collateral_utxo,
                script_file=plutus_op.script_file,
                datum_file=plutus_op.datum_file,
                redeemer_file=plutus_op.redeemer_file,
            )
        ]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        for token in spent_tokens:
            txouts.append(
                clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
            )

        if expect_failure:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                tx_output = cluster_obj.build_tx(
                    src_address=payment_addr.address,
                    tx_name=f"{temp_template}_step2",
                    txouts=txouts,
                    tx_files=tx_files,
                    plutus_txins=plutus_txins,
                )
            return str(excinfo.value)

        tx_output = cluster_obj.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            txouts=txouts,
            tx_files=tx_files,
            plutus_txins=plutus_txins,
            script_valid=script_valid,
        )
        tx_signed = cluster_obj.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        if not script_valid:
            cluster_obj.submit_tx(tx_file=tx_signed, txins=[collateral_utxo])

            assert (
                cluster_obj.get_address_balance(dst_addr.address)
                == dst_init_balance - collateral_amount
            ), f"Collateral was NOT spent from `{dst_addr.address}`"

            assert (
                cluster_obj.get_address_balance(script_address) == script_init_balance
            ), f"Incorrect balance for script address `{script_address}`"

            return ""

        cluster_obj.submit_tx(tx_file=tx_signed, txins=[t.txins[0] for t in tx_output.plutus_txins])

        assert (
            cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        # TODO: fee is not known when using `transaction build` command
        assert (
            cluster_obj.get_address_balance(script_address) < script_init_balance - amount
        ), f"Incorrect balance for script address `{script_address}`"

        for token in spent_tokens:
            assert (
                cluster_obj.get_address_balance(script_address, coin=token.coin) == 0
            ), f"Incorrect token balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)

        return ""

    @pytest.fixture
    def cluster_lock_always_suceeds(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> clusterlib.ClusterLib:
        """Make sure just one txin plutus test run at a time.

        Plutus script always has the same address. When one script is used in multiple
        tests that are running in parallel, the blanaces etc. don't add up.
        """
        return cluster_manager.get(lock_resources=["always_suceeds_script"])

    @pytest.fixture
    def payment_addrs_lock_always_suceeds(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment address while using the `cluster_lock_always_suceeds` fixture."""
        cluster = cluster_lock_always_suceeds
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"plutus_payment_lock_allsucceeds_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(4)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            addrs[2],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=20_000_000_000,
        )

        return addrs

    @pytest.fixture
    def cluster_lock_guessing_game(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> clusterlib.ClusterLib:
        """Make sure just one guessing game plutus test run at a time.

        Plutus script always has the same address. When one script is used in multiple
        tests that are running in parallel, the blanaces etc. don't add up.
        """
        return cluster_manager.get(lock_resources=["guessing_game_script"])

    @pytest.fixture
    def payment_addrs_lock_guessing_game(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_guessing_game: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment address while using the `cluster_lock_guessing_game` fixture."""
        cluster = cluster_lock_guessing_game
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"plutus_payment_lock_ggame_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(4)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            addrs[2],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=20_000_000_000,
        )

        return addrs

    @pytest.fixture
    def cluster_lock_always_fails(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> clusterlib.ClusterLib:
        """Make sure just one txin plutus test run at a time.

        Plutus script always has the same address. When one script is used in multiple
        tests that are running in parallel, the blanaces etc. don't add up.
        """
        return cluster_manager.get(lock_resources=["always_fails_script"])

    @pytest.fixture
    def payment_addrs_lock_always_fails(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_always_fails: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment address while using the `cluster_lock_always_fails` fixture."""
        cluster = cluster_lock_always_fails
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"plutus_payment_lock_allfailss_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(2)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=20_000_000_000,
        )

        return addrs

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment address."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[f"plutus_payment_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(4)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            addrs[2],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=10_000_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = helpers.get_func_name()

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = self._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        self._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_suceeds[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "script",
        (
            "guessing_game_42",  # correct datum and redeemer
            "guessing_game_42_43",  # correct datum, wrong redeemer
            "guessing_game_43_42",  # wrong datum, correct redeemer
            "guessing_game_43_43",  # wrong datum and redeemer
        ),
    )
    def test_guessing_game(
        self,
        cluster_lock_guessing_game: clusterlib.ClusterLib,
        payment_addrs_lock_guessing_game: List[clusterlib.AddressRecord],
        script: str,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Test with with "guessing game" script that expects specific datum and redeemer value.
        Test also negative scenarios where datum or redeemer value is different than expected.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was not spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_guessing_game
        temp_template = f"{helpers.get_func_name()}_{script}"

        if script.endswith("game_42_43"):
            datum_file = self.PLUTUS_DIR / "typed-42.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        elif script.endswith("game_43_42"):
            datum_file = self.PLUTUS_DIR / "typed-43.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = True
        elif script.endswith("game_43_43"):
            datum_file = self.PLUTUS_DIR / "typed-43.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        else:
            datum_file = self.PLUTUS_DIR / "typed-42.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = False

        plutus_op = PlutusOp(
            script_file=self.GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = self._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[0],
            dst_addr=payment_addrs_lock_guessing_game[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        err = self._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_guessing_game[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=expect_failure,
        )
        if expect_failure:
            assert "ValidationTagMismatch (IsValid True)" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_always_fails(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred, collateral UTxO was not spent
          and the expected error was raised
        """
        cluster = cluster_lock_always_fails
        temp_template = helpers.get_func_name()

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_FAILS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = self._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        err = self._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_fails[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=True,
        )
        assert "PlutusFailure" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_script_invalid(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test failing script together with the `--script-invalid` argument - collateral is taken.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        cluster = cluster_lock_always_fails
        temp_template = helpers.get_func_name()

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_FAILS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tx_output_fund = self._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
            amount=50_000_000,
        )
        self._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_fails[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            script_valid=False,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a plutus script.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = helpers.get_func_name()
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 5000_000
        token_amount = 5
        plutusrequiredtime = 700_000_000
        plutusrequiredspace = 10_000_000
        fee_step2 = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_amount = int(fee_step2 * 1.5)

        redeemer_file = self.PLUTUS_DIR / "42.redeemer"

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount + fee_step2),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_amount),
        ]
        fee_step1 = cluster.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts_step1,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output_step1 = cluster.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee=fee_step1,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_raw_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance
            == issuer_init_balance + lovelace_amount + fee_step2 + collateral_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        txid_step1 = cluster.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#0")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=1, amount=collateral_amount, address=issuer_addr.address
        )
        plutus_mint_data = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collateral=collateral_utxo,
                script_file=self.MINTING_PLUTUS,
                execution_units=(plutusrequiredtime, plutusrequiredspace),
                redeemer_file=redeemer_file,
            )
        ]

        policyid = cluster.get_policyid(self.MINTING_PLUTUS)
        token = f"{policyid}.qacoin"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]
        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            fee=fee_step2,
            plutus_mint=plutus_mint_data,
            mint=mint,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_amount + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_token_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        * create a Tx ouput that contains native tokens with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = helpers.get_func_name()
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
            execution_units=(700_000_000, 10_000_000),
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"couttscoin{token_rand}{i}" for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = self._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )
        self._spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs_lock_always_suceeds[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_build_txin_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = helpers.get_func_name()

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = self._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[2],
            dst_addr=payment_addrs_lock_always_suceeds[3],
            plutus_op=plutus_op,
        )
        self._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[2],
            dst_addr=payment_addrs_lock_always_suceeds[3],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "script",
        (
            "guessing_game_42",  # correct datum and redeemer
            "guessing_game_42_43",  # correct datum, wrong redeemer
            "guessing_game_43_42",  # wrong datum, correct redeemer
            "guessing_game_43_43",  # wrong datum and redeemer
        ),
    )
    def test_build_guessing_game(
        self,
        cluster_lock_guessing_game: clusterlib.ClusterLib,
        payment_addrs_lock_guessing_game: List[clusterlib.AddressRecord],
        script: str,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with with "guessing game" script that expects specific datum and redeemer value.
        Test also negative scenarios where datum or redeemer value is different than expected.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_guessing_game
        temp_template = f"{helpers.get_func_name()}_{script}"

        if script.endswith("game_42_43"):
            datum_file = self.PLUTUS_DIR / "typed-42.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        elif script.endswith("game_43_42"):
            datum_file = self.PLUTUS_DIR / "typed-43.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = True
        elif script.endswith("game_43_43"):
            datum_file = self.PLUTUS_DIR / "typed-43.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        else:
            datum_file = self.PLUTUS_DIR / "typed-42.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = False

        plutus_op = PlutusOp(
            script_file=self.GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
        )

        tx_output_fund = self._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[2],
            dst_addr=payment_addrs_lock_guessing_game[3],
            plutus_op=plutus_op,
        )
        err = self._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[2],
            dst_addr=payment_addrs_lock_guessing_game[3],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=expect_failure,
        )
        if expect_failure:
            assert "The Plutus script evaluation failed" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_build_always_fails(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the expected error was raised
        """
        cluster = cluster_lock_always_fails
        temp_template = helpers.get_func_name()

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_FAILS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = self._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
        )
        err = self._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=True,
        )
        assert "The Plutus script evaluation failed" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_build_script_invalid(
        self,
        cluster_lock_always_fails: clusterlib.ClusterLib,
        payment_addrs_lock_always_fails: List[clusterlib.AddressRecord],
    ):
        """Test failing script together with the `--script-invalid` argument - collateral is taken.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with with "always fails" script that fails for all datum / redeemer values.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        cluster = cluster_lock_always_fails
        temp_template = helpers.get_func_name()

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_FAILS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = self._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
        )
        self._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            script_valid=False,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_build_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a plutus script.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = helpers.get_func_name()
        payment_addr = payment_addrs[2]
        issuer_addr = payment_addrs[3]

        lovelace_amount = 5000_000
        script_fund = 1000_000_000
        collateral_fund = 1500_000_000
        token_amount = 5

        redeemer_file = self.PLUTUS_DIR / "42.redeemer"

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=script_fund),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_fund),
        ]

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        tx_output_step1 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee_buffer=2_000_000,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance == issuer_init_balance + script_fund + collateral_fund
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        txid_step1 = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_step1, utxo_ix=2, amount=collateral_fund, address=issuer_addr.address
        )
        plutus_mint_data = [
            clusterlib.PlutusMint(
                txins=mint_utxos,
                collateral=collateral_utxo,
                script_file=self.MINTING_PLUTUS,
                redeemer_file=redeemer_file,
            )
        ]

        policyid = cluster.get_policyid(self.MINTING_PLUTUS)
        token = f"{policyid}.qacoin"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            plutus_mint=plutus_mint_data,
            mint=mint,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_fund + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_build_txin_token_locking(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput that contains native tokens with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = helpers.get_func_name()
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = PlutusOp(
            script_file=self.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=self.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=self.PLUTUS_DIR / "typed-42.redeemer",
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"couttscoin{token_rand}{i}" for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = self._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            tokens=tokens_rec,
        )
        self._build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            tx_output_fund=tx_output_fund,
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )
