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
    GUESSING_GAME_PLUTUS = PLUTUS_DIR / "custom-guess-42-datum-42.plutus"
    MINTING_PLUTUS = PLUTUS_DIR / "anyone-can-mint.plutus"

    def _txin_locking(
        self,
        temp_template: str,
        cluster_obj: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        plutus_op: PlutusOp,
        amount: int,
        expect_failure: bool = False,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO."""
        # pylint: disable=too-many-locals
        assert plutus_op.execution_units, "Execution units not provided"
        plutusrequiredspace, plutusrequiredtime = plutus_op.execution_units

        script_address = cluster_obj.gen_script_addr(
            addr_name=temp_template, script_file=plutus_op.script_file
        )

        fee_redeem = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_fraction = cluster_obj.get_protocol_params()["collateralPercentage"] / 100
        collateral_amount = int(fee_redeem * collateral_fraction)

        script_init_balance = cluster_obj.get_address_balance(script_address)

        # Step 1: create a Tx ouput with a datum hash at the script address

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        datum_hash = cluster_obj.get_hash_script_data(script_data_file=plutus_op.datum_file)
        txouts_step1 = [
            clusterlib.TxOut(
                address=script_address, amount=amount + fee_redeem, datum_hash=datum_hash
            ),
            # for collateral
            clusterlib.TxOut(address=dst_addr.address, amount=collateral_amount),
        ]
        fee_step1 = cluster_obj.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts_step1,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output_step1 = cluster_obj.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee=fee_step1,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster_obj.sign_tx(
            tx_body_file=tx_raw_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster_obj.submit_tx(tx_file=tx_signed_step1, txins=tx_raw_output_step1.txins)

        script_step1_balance = cluster_obj.get_address_balance(script_address)
        assert (
            script_step1_balance == script_init_balance + amount + fee_redeem
        ), f"Incorrect balance for script address `{script_address}`"

        dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

        # Step 2: spend the "locked" UTxO

        txid_body = cluster_obj.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        script_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body,
            utxo_ix=0,
            amount=amount + fee_redeem,
            address=script_address,
        )
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body, utxo_ix=1, amount=collateral_amount, address=dst_addr.address
        )
        plutus_txins = [
            clusterlib.PlutusTxIn(
                txin=script_utxo,
                collateral=collateral_utxo,
                script_file=plutus_op.script_file,
                execution_units=(plutusrequiredspace, plutusrequiredtime),
                datum_file=plutus_op.datum_file,
                redeemer_file=plutus_op.redeemer_file,
            )
        ]
        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]
        tx_raw_output_step2 = cluster_obj.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            fee=fee_redeem,
            plutus_txins=plutus_txins,
        )
        tx_signed_step2 = cluster_obj.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        if expect_failure:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster_obj.submit_tx(tx_file=tx_signed_step2, txins=[collateral_utxo])
            assert "ValidationTagMismatch (IsValid True)" in str(excinfo.value)

            assert (
                cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance
            ), f"Collateral was taken from `{dst_addr.address}`"

            assert (
                cluster_obj.get_address_balance(script_address) == script_step1_balance
            ), f"Incorrect balance for script address `{script_address}`"

            return

        cluster_obj.submit_tx(
            tx_file=tx_signed_step2, txins=[t.txin for t in tx_raw_output_step2.plutus_txins]
        )

        assert (
            cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        assert (
            cluster_obj.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output_step2)

    def _build_txin_locking(
        self,
        temp_template: str,
        cluster_obj: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        plutus_op: PlutusOp,
        amount: int,
        expect_failure: bool = False,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        script_fund = 1000_000_000
        collateral_fund = 1500_000_000

        script_address = cluster_obj.gen_script_addr(
            addr_name=temp_template, script_file=plutus_op.script_file
        )
        script_init_balance = cluster_obj.get_address_balance(script_address)

        # Step 1: create a Tx ouput with a datum hash at the script address

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        datum_hash = cluster_obj.get_hash_script_data(script_data_file=plutus_op.datum_file)
        txouts_step1 = [
            clusterlib.TxOut(address=script_address, amount=script_fund, datum_hash=datum_hash),
            # for collateral
            clusterlib.TxOut(address=dst_addr.address, amount=collateral_fund),
        ]
        tx_output_step1 = cluster_obj.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee_buffer=2_000_000,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster_obj.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster_obj.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        script_step1_balance = cluster_obj.get_address_balance(script_address)
        assert (
            script_step1_balance == script_init_balance + script_fund
        ), f"Incorrect balance for script address `{script_address}`"

        dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

        # Step 2: spend the "locked" UTxO

        txid_body = cluster_obj.get_txid(tx_body_file=tx_output_step1.out_file)
        script_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body,
            utxo_ix=1,
            amount=script_fund,
            address=script_address,
        )
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body, utxo_ix=2, amount=collateral_fund, address=dst_addr.address
        )
        plutus_txins = [
            clusterlib.PlutusTxIn(
                txin=script_utxo,
                collateral=collateral_utxo,
                script_file=plutus_op.script_file,
                datum_file=plutus_op.datum_file,
                redeemer_file=plutus_op.redeemer_file,
            )
        ]
        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        if expect_failure:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                tx_output_step2 = cluster_obj.build_tx(
                    src_address=payment_addr.address,
                    tx_name=f"{temp_template}_step2",
                    txouts=txouts_step2,
                    tx_files=tx_files_step2,
                    plutus_txins=plutus_txins,
                )
            assert "The Plutus script evaluation failed" in str(excinfo.value)
            return

        tx_output_step2 = cluster_obj.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            plutus_txins=plutus_txins,
        )
        tx_signed_step2 = cluster_obj.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster_obj.submit_tx(
            tx_file=tx_signed_step2, txins=[t.txin for t in tx_output_step2.plutus_txins]
        )

        assert (
            cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        assert (
            cluster_obj.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output_step2)

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
            execution_units=(700_000_000, 700_000_000),
        )

        self._txin_locking(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
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
        * OR check that the amount was not transferred and collateral UTxO was spent
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
            execution_units=(700_000_000, 700_000_000),
        )

        self._txin_locking(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[0],
            dst_addr=payment_addrs_lock_guessing_game[1],
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=expect_failure,
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
        plutusrequiredspace = 700_000_000
        plutusrequiredtime = 700_000_000
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

        txid_body = cluster.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        mint_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body,
            utxo_ix=0,
            amount=lovelace_amount + fee_step2,
            address=issuer_addr.address,
        )
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body, utxo_ix=1, amount=collateral_amount, address=issuer_addr.address
        )
        plutus_mint_data = [
            clusterlib.PlutusMint(
                txin=mint_utxo,
                collateral=collateral_utxo,
                script_file=self.MINTING_PLUTUS,
                execution_units=(plutusrequiredspace, plutusrequiredtime),
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
        cluster.submit_tx(tx_file=tx_signed_step2, txins=[mint_utxo])

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_amount + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

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

        self._build_txin_locking(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[2],
            dst_addr=payment_addrs_lock_always_suceeds[3],
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
        * OR check that the amount was not transferred and collateral UTxO was spent
          when failure is expected
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

        self._build_txin_locking(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[2],
            dst_addr=payment_addrs_lock_guessing_game[3],
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=expect_failure,
        )

    @allure.link(helpers.get_vcs_link())
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

        txid_body = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        mint_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body,
            utxo_ix=1,
            amount=lovelace_amount,
            address=issuer_addr.address,
        )
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body, utxo_ix=2, amount=collateral_fund, address=issuer_addr.address
        )
        plutus_mint_data = [
            clusterlib.PlutusMint(
                txin=mint_utxo,
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
        cluster.submit_tx(tx_file=tx_signed_step2, txins=[mint_utxo])

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_fund + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"
