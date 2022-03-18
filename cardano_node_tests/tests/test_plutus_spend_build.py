"""Tests for spending with Plutus using `transaction build`."""
import logging
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

import allure
import distutils.spawn
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_spend
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture
def cluster_lock_always_suceeds(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one txin plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=[str(plutus_spend.ALWAYS_SUCCEEDS_PLUTUS.stem)])


@pytest.fixture
def payment_addrs_lock_always_suceeds(
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
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one guessing game plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=[str(plutus_spend.GUESSING_GAME_PLUTUS.stem)])


@pytest.fixture
def payment_addrs_lock_guessing_game(
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
def cluster_lock_context_eq(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one guessing game plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=[str(plutus_spend.CONTEXT_EQUIVALENCE_PLUTUS.stem)])


@pytest.fixture
def pool_users_lock_context_eq(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_context_eq: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create new pool users while using the `cluster_lock_context_eq` fixture."""
    cluster = cluster_lock_context_eq
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        created_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template="plutus_payment_lock_context_eq_ci"
            f"{cluster_manager.cluster_instance_num}",
            no_of_addr=2,
        )
        fixture_cache.value = created_users

    # fund source address
    clusterlib_utils.fund_from_faucet(
        created_users[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=20_000_000_000,
    )

    return created_users


@pytest.fixture
def cluster_lock_always_fails(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Make sure just one txin plutus test run at a time.

    Plutus script always has the same address. When one script is used in multiple
    tests that are running in parallel, the blanaces etc. don't add up.
    """
    return cluster_manager.get(lock_resources=[str(plutus_spend.ALWAYS_FAILS_PLUTUS.stem)])


@pytest.fixture
def payment_addrs_lock_always_fails(
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
                f"plutus_payment_lock_allfails_ci{cluster_manager.cluster_instance_num}_{i}"
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


def _build_fund_script(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_spend.PlutusOp,
    tokens: Optional[List[plutus_spend.Token]] = None,  # tokens must already be in `payment_addr`
    tokens_collateral: Optional[
        List[plutus_spend.Token]
    ] = None,  # tokens must already be in `payment_addr`
) -> clusterlib.TxRawOutput:
    """Fund a plutus script and create the locked UTxO and collateral UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    script_fund = 1000_000_000
    collateral_fund = 1500_000_000

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.gen_script_addr(
        addr_name=temp_template, script_file=plutus_op.script_file
    )
    script_init_balance = cluster_obj.get_address_balance(script_address)

    # create a Tx ouput with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    datum_hash = cluster_obj.get_hash_script_data(
        script_data_file=plutus_op.datum_file if plutus_op.datum_file else None,
        script_data_cbor_file=plutus_op.datum_cbor_file if plutus_op.datum_cbor_file else None,
    )
    txouts = [
        clusterlib.TxOut(address=script_address, amount=script_fund, datum_hash=datum_hash),
        # for collateral
        clusterlib.TxOut(address=dst_addr.address, amount=collateral_fund),
    ]

    for token in stokens:
        txouts.append(
            clusterlib.TxOut(
                address=script_address,
                amount=token.amount,
                coin=token.coin,
                datum_hash=datum_hash,
            )
        )

    for token in ctokens:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=token.amount,
                coin=token.coin,
            )
        )

    tx_output = cluster_obj.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
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

    for token in stokens:
        assert (
            cluster_obj.get_address_balance(script_address, coin=token.coin) == token.amount
        ), f"Incorrect token balance for script address `{script_address}`"

    for token in ctokens:
        assert (
            cluster_obj.get_address_balance(dst_addr.address, coin=token.coin) == token.amount
        ), f"Incorrect token balance for address `{dst_addr.address}`"

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    return tx_output


def _build_spend_locked_txin(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    script_utxos: List[clusterlib.UTXOData],
    collateral_utxos: List[clusterlib.UTXOData],
    plutus_op: plutus_spend.PlutusOp,
    amount: int,
    deposit_amount: int = 0,
    tx_files: Optional[clusterlib.TxFiles] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    tokens: Optional[List[plutus_spend.Token]] = None,
    expect_failure: bool = False,
    script_valid: bool = True,
    submit_tx: bool = True,
) -> Tuple[str, Optional[clusterlib.TxRawOutput]]:
    """Spend the locked UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    # pylint: disable=too-many-arguments
    script_address = script_utxos[0].address
    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

    script_init_balance = cluster_obj.get_address_balance(script_address)
    dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

    # spend the "locked" UTxO

    plutus_txins = [
        clusterlib.ScriptTxIn(
            txins=script_utxos,
            script_file=plutus_op.script_file,
            collaterals=collateral_utxos,
            datum_file=plutus_op.datum_file if plutus_op.datum_file else "",
            datum_cbor_file=plutus_op.datum_cbor_file if plutus_op.datum_cbor_file else "",
            redeemer_file=plutus_op.redeemer_file if plutus_op.redeemer_file else "",
            redeemer_cbor_file=plutus_op.redeemer_cbor_file if plutus_op.redeemer_cbor_file else "",
        )
    ]
    tx_files = tx_files._replace(
        signing_key_files=list({*tx_files.signing_key_files, dst_addr.skey_file}),
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
                tx_files=tx_files,
                txouts=txouts,
                script_txins=plutus_txins,
                change_address=script_address,
            )
        return str(excinfo.value), None

    tx_output = cluster_obj.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txouts=txouts,
        script_txins=plutus_txins,
        change_address=script_address,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        deposit=deposit_amount,
        script_valid=script_valid,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step2",
    )

    if not submit_tx:
        return "", tx_output

    if not script_valid:
        cluster_obj.submit_tx(tx_file=tx_signed, txins=collateral_utxos)

        assert (
            cluster_obj.get_address_balance(dst_addr.address)
            == dst_init_balance - collateral_utxos[0].amount
        ), f"Collateral was NOT spent from `{dst_addr.address}`"

        assert (
            cluster_obj.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        return "", tx_output

    # calculate cost of Plutus script
    plutus_cost = cluster_obj.calculate_plutus_script_cost(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txouts=txouts,
        script_txins=plutus_txins,
        change_address=script_address,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        deposit=deposit_amount,
        script_valid=script_valid,
    )

    cluster_obj.submit_tx(
        tx_file=tx_signed, txins=[t.txins[0] for t in tx_output.script_txins if t.txins]
    )

    # check tx view
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    assert (
        cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
    ), f"Incorrect balance for destination address `{dst_addr.address}`"

    assert (
        cluster_obj.get_address_balance(script_address)
        == script_init_balance - amount - tx_output.fee - deposit_amount
    ), f"Incorrect balance for script address `{script_address}`"

    for token in spent_tokens:
        assert (
            cluster_obj.get_address_balance(script_address, coin=token.coin) == 0
        ), f"Incorrect token balance for script address `{script_address}`"

    tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)
    # compare cost of Plutus script with data from db-sync
    if tx_db_record:
        dbsync_utils.check_plutus_cost(
            redeemers_record=tx_db_record.redeemers[0], cost_record=plutus_cost[0]
        )

    return "", tx_output


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
@pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
class TestBuildLocking:
    """Tests for txin locking using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_locking(
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
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_spend.PLUTUS_DIR / "typed-42.datum",
            redeemer_cbor_file=plutus_spend.PLUTUS_DIR / "42.redeemer.cbor",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[2],
            dst_addr=payment_addrs_lock_always_suceeds[3],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        __, tx_output = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[2],
            dst_addr=payment_addrs_lock_always_suceeds[3],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
        )

        # check expected fees
        expected_fee_fund = 168845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 170782
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not distutils.spawn.find_executable("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_context_equivalance(
        self,
        cluster_lock_context_eq: clusterlib.ClusterLib,
        pool_users_lock_context_eq: List[clusterlib.PoolUser],
    ):
        """Test context equivalence while spending a locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * spend the locked UTxO using the derived redeemer
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_context_eq
        temp_template = common.get_test_id(cluster)
        amount = 10_000_000
        deposit_amount = cluster.get_address_deposit()

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr2",
            stake_vkey_file=pool_users_lock_context_eq[0].stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(certificate_files=[stake_addr_reg_cert_file])

        # generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")
        clusterlib_utils.create_script_context(
            cluster_obj=cluster, redeemer_file=redeemer_file_dummy
        )

        plutus_op_dummy = plutus_spend.PlutusOp(
            script_file=plutus_spend.CONTEXT_EQUIVALENCE_PLUTUS,
            datum_file=plutus_spend.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=redeemer_file_dummy,
        )

        # fund the script address
        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users_lock_context_eq[0].payment,
            dst_addr=pool_users_lock_context_eq[1].payment,
            plutus_op=plutus_op_dummy,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        invalid_hereafter = cluster.get_slot_no() + 1000

        __, tx_output_dummy = _build_spend_locked_txin(
            temp_template=f"{temp_template}_dummy",
            cluster_obj=cluster,
            payment_addr=pool_users_lock_context_eq[0].payment,
            dst_addr=pool_users_lock_context_eq[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op_dummy,
            amount=amount,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
            submit_tx=False,
        )
        assert tx_output_dummy

        # generate the "real" redeemer
        redeemer_file = Path(f"{temp_template}_script_context.redeemer")
        tx_file_dummy = Path(f"{tx_output_dummy.out_file.with_suffix('')}.signed")

        try:
            clusterlib_utils.create_script_context(
                cluster_obj=cluster, redeemer_file=redeemer_file, tx_file=tx_file_dummy
            )
        except AssertionError as err:
            err_msg = str(err)
            if "DeserialiseFailure" in err_msg:
                pytest.xfail("DeserialiseFailure: see issue #944")
            if "TextEnvelopeTypeError" in err_msg and cluster.use_cddl:  # noqa: SIM106
                pytest.xfail(
                    "TextEnvelopeTypeError: `create-script-context` doesn't work with CDDL format"
                )
            else:
                raise

        plutus_op = plutus_op_dummy._replace(redeemer_file=redeemer_file)

        __, tx_output = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users_lock_context_eq[0].payment,
            dst_addr=pool_users_lock_context_eq[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            deposit_amount=deposit_amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

        # check expected fees
        if tx_output:
            expected_fee = 372438
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

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
        temp_template = f"{common.get_test_id(cluster)}_{script}"

        if script.endswith("game_42_43"):
            datum_file = plutus_spend.PLUTUS_DIR / "typed-42.datum"
            redeemer_file = plutus_spend.PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        elif script.endswith("game_43_42"):
            datum_file = plutus_spend.PLUTUS_DIR / "typed-43.datum"
            redeemer_file = plutus_spend.PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = True
        elif script.endswith("game_43_43"):
            datum_file = plutus_spend.PLUTUS_DIR / "typed-43.datum"
            redeemer_file = plutus_spend.PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True
        else:
            datum_file = plutus_spend.PLUTUS_DIR / "typed-42.datum"
            redeemer_file = plutus_spend.PLUTUS_DIR / "typed-42.redeemer"
            expect_failure = False

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[0],
            dst_addr=payment_addrs_lock_guessing_game[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        err, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_guessing_game[0],
            dst_addr=payment_addrs_lock_guessing_game[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=expect_failure,
        )
        if expect_failure:
            assert "The Plutus script evaluation failed" in err

        # check expected fees
        expected_fee_fund = 168845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_always_fails(
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
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_FAILS_PLUTUS,
            datum_file=plutus_spend.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=plutus_spend.PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        err, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            expect_failure=True,
        )
        assert "The Plutus script evaluation failed" in err

        # check expected fees
        expected_fee_fund = 168845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_script_invalid(
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
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_FAILS_PLUTUS,
            datum_file=plutus_spend.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=plutus_spend.PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        __, tx_output = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_fails[0],
            dst_addr=payment_addrs_lock_always_fails[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            script_valid=False,
        )

        # check expected fees
        expected_fee_fund = 168845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 171309
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_token_locking(
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
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_spend.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=plutus_spend.PLUTUS_DIR / "typed-42.redeemer",
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [plutus_spend.Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            tokens=tokens_rec,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        __, tx_output = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )

        # check expected fees
        expected_fee_fund = 173597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 175710
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_w_tokens(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while collateral contains native tokens.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs_lock_always_suceeds[0]

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_SUCCEEDS_PLUTUS,
            datum_cbor_file=plutus_spend.PLUTUS_DIR / "typed-42.datum.cbor",
            redeemer_cbor_file=plutus_spend.PLUTUS_DIR / "42.redeemer.cbor",
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [plutus_spend.Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
            tokens_collateral=tokens_rec,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs_lock_always_suceeds[0],
                dst_addr=payment_addrs_lock_always_suceeds[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )

        assert "CollateralContainsNonADA" in str(excinfo.value)

        # check expected fees
        expected_fee_fund = 173597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_same_collateral_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while using the same UTxO as collateral.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_spend.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=plutus_spend.PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs_lock_always_suceeds[0],
            dst_addr=payment_addrs_lock_always_suceeds[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs_lock_always_suceeds[0],
                dst_addr=payment_addrs_lock_always_suceeds[1],
                script_utxos=script_utxos,
                collateral_utxos=script_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "Expected key witnessed collateral" in str(excinfo.value)

        # check expected fees
        expected_fee_fund = 168845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_no_datum_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx ouput without a datum hash
        * try to spend the UTxO like it was locked Plutus UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = common.get_test_id(cluster)

        payment_addr = payment_addrs_lock_always_suceeds[0]
        dst_addr = payment_addrs_lock_always_suceeds[1]

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_spend.PLUTUS_DIR / "typed-42.datum",
            redeemer_file=plutus_spend.PLUTUS_DIR / "typed-42.redeemer",
        )

        plutusrequiredtime, plutusrequiredspace = 700_000_000, 10_000_000
        fee_redeem = int(plutusrequiredtime + plutusrequiredspace) + 10_000_000
        collateral_fraction = cluster.get_protocol_params()["collateralPercentage"] / 100
        collateral_amount = int(fee_redeem * collateral_fraction)

        txouts = [
            clusterlib.TxOut(address=payment_addr.address, amount=50_000_000 + fee_redeem),
            clusterlib.TxOut(address=payment_addr.address, amount=collateral_amount),
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output_fund = cluster.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )
        assert "not a Plutus script witnessed tx input" in str(excinfo.value)

        # check expected fees
        expected_fee_fund = 199087
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_is_txin(
        self,
        cluster_lock_always_suceeds: clusterlib.ClusterLib,
        payment_addrs_lock_always_suceeds: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while using single UTxO for both collateral and Tx input.

        Uses `cardano-cli transaction build` command for building the transactions.

        Tests bug https://github.com/input-output-hk/cardano-db-sync/issues/750

        * create a Tx ouput with a datum hash at the script address and a collateral UTxO
        * check that the expected amount was locked at the script address
        * spend the locked UTxO while using the collateral UTxO both as collateral and as
          normal Tx input
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        cluster = cluster_lock_always_suceeds
        temp_template = common.get_test_id(cluster)

        payment_addr = payment_addrs_lock_always_suceeds[2]
        dst_addr = payment_addrs_lock_always_suceeds[3]

        amount = 50_000_000

        # Step 1: fund the script address

        plutus_op = plutus_spend.PlutusOp(
            script_file=plutus_spend.ALWAYS_SUCCEEDS_PLUTUS,
            datum_cbor_file=plutus_spend.PLUTUS_DIR / "typed-42.datum.cbor",
            redeemer_file=plutus_spend.PLUTUS_DIR / "typed-42.redeemer",
        )

        tx_output_step1 = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
        )

        # Step 2: spend the "locked" UTxO

        txid_step1 = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid_step1}#2")
        script_address = script_utxos[0].address

        script_step1_balance = cluster.get_address_balance(script_address)
        dst_step1_balance = cluster.get_address_balance(dst_addr.address)

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                datum_file=plutus_op.datum_file if plutus_op.datum_file else "",
                datum_cbor_file=plutus_op.datum_cbor_file if plutus_op.datum_cbor_file else "",
                redeemer_file=plutus_op.redeemer_file if plutus_op.redeemer_file else "",
                redeemer_cbor_file=plutus_op.redeemer_cbor_file
                if plutus_op.redeemer_cbor_file
                else "",
            )
        ]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files,
            # `collateral_utxos` is used both as collateral and as normal Tx input
            txins=collateral_utxos,
            txouts=txouts,
            script_txins=plutus_txins,
            change_address=script_address,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_step2.script_txins if t.txins]
        )

        assert (
            cluster.get_address_balance(dst_addr.address)
            == dst_step1_balance + amount - collateral_utxos[0].amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        assert (
            cluster.get_address_balance(script_address)
            == script_step1_balance - amount - tx_output_step2.fee + collateral_utxos[0].amount
        ), f"Incorrect balance for script address `{script_address}`"

        # check expected fees
        expected_fee_step1 = 168845
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 176986
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)
