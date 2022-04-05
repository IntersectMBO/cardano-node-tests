"""Tests for spending with Plutus using `transaction build`."""
import json
import logging
import shutil
from pathlib import Path
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# skip all tests if Tx era < alonzo
pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    rand_str = clusterlib.get_rand_str(4)
    addrs = clusterlib_utils.create_payment_addr_records(
        *[
            f"payment_addrs_ci{cluster_manager.cluster_instance_num}_{rand_str}_{i}"
            for i in range(2)
        ],
        cluster_obj=cluster,
    )

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=3_000_000_000,
    )

    return addrs


@pytest.fixture
def pool_users(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create new pool users."""
    rand_str = clusterlib.get_rand_str(4)
    created_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"pool_users_ci{cluster_manager.cluster_instance_num}_{rand_str}",
        no_of_addr=2,
    )

    # fund source address
    clusterlib_utils.fund_from_faucet(
        created_users[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=3_000_000_000,
    )

    return created_users


@pytest.fixture
def fund_script_guessing_game(
    cluster: clusterlib.ClusterLib,
    payment_addrs: List[clusterlib.AddressRecord],
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]]:
    """Fund a plutus script and create the locked UTxO and collateral UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    temp_template = common.get_test_id(cluster)

    plutus_op = plutus_common.PlutusOp(
        script_file=plutus_common.GUESSING_GAME_PLUTUS, datum_file=plutus_common.DATUM_42_TYPED
    )

    tx_output_fund = _build_fund_script(
        temp_template=temp_template,
        cluster_obj=cluster,
        payment_addr=payment_addrs[0],
        dst_addr=payment_addrs[1],
        plutus_op=plutus_op,
    )

    txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
    script_utxos = cluster.get_utxo(txin=f"{txid}#1")
    collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")

    return script_utxos, collateral_utxos


def _build_fund_script(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    tokens: Optional[List[plutus_common.Token]] = None,  # tokens must already be in `payment_addr`
    tokens_collateral: Optional[
        List[plutus_common.Token]
    ] = None,  # tokens must already be in `payment_addr`
) -> clusterlib.TxRawOutput:
    """Fund a plutus script and create the locked UTxO and collateral UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    script_fund = 1_000_000_000
    collateral_fund = 1_500_000_000

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    # create a Tx output with a datum hash at the script address

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

    txid = cluster_obj.get_txid(tx_body_file=tx_output.out_file)

    script_utxos = cluster_obj.get_utxo(txin=f"{txid}#1", coins=[clusterlib.DEFAULT_COIN])
    assert script_utxos, "No script UTxO"

    script_balance = script_utxos[0].amount
    assert script_balance == script_fund, f"Incorrect balance for script address `{script_address}`"

    for token in stokens:
        token_balance = cluster_obj.get_utxo(txin=f"{txid}#1", coins=[token.coin])[0].amount
        assert (
            token_balance == token.amount
        ), f"Incorrect token balance for script address `{script_address}`"

    for token in ctokens:
        token_balance = cluster_obj.get_utxo(txin=f"{txid}#2", coins=[token.coin])[0].amount
        assert (
            token_balance == token.amount
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
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    deposit_amount: int = 0,
    tx_files: Optional[clusterlib.TxFiles] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    tokens: Optional[List[plutus_common.Token]] = None,
    expect_failure: bool = False,
    script_valid: bool = True,
    submit_tx: bool = True,
) -> Tuple[str, Optional[clusterlib.TxRawOutput], list]:
    """Spend the locked UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    # pylint: disable=too-many-arguments,too-many-locals
    script_address = script_utxos[0].address
    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

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
            redeemer_value=plutus_op.redeemer_value if plutus_op.redeemer_value else "",
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
            cluster_obj.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files,
                txouts=txouts,
                script_txins=plutus_txins,
                change_address=script_address,
            )
        return str(excinfo.value), None, []

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
        return "", tx_output, []

    dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

    script_utxos_lovelace = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]

    if not script_valid:
        cluster_obj.submit_tx(tx_file=tx_signed, txins=collateral_utxos)

        assert (
            cluster_obj.get_address_balance(dst_addr.address)
            == dst_init_balance - collateral_utxos[0].amount
        ), f"Collateral was NOT spent from `{dst_addr.address}`"

        for u in script_utxos_lovelace:
            assert cluster_obj.get_utxo(
                txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were unexpectedly spent for `{script_address}`"

        return "", tx_output, []

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

    assert (
        cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance + amount
    ), f"Incorrect balance for destination address `{dst_addr.address}`"

    for u in script_utxos_lovelace:
        assert not cluster_obj.get_utxo(
            txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[clusterlib.DEFAULT_COIN]
        ), f"Inputs were NOT spent for `{script_address}`"

    for token in spent_tokens:
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        for u in script_utxos_token:
            assert not cluster_obj.get_utxo(
                txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[token.coin]
            ), f"Token inputs were NOT spent for `{script_address}`"

    # check tx view
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)
    # compare cost of Plutus script with data from db-sync
    if tx_db_record:
        dbsync_utils.check_plutus_cost(
            redeemers_record=tx_db_record.redeemers[0], cost_record=plutus_cost[0]
        )

    return "", tx_output, plutus_cost


@pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
class TestBuildLocking:
    """Tests for txin locking using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        __, tx_output, plutus_cost = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
        )

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 170_782
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

        if plutus_cost:
            plutus_common.check_plutus_cost(
                plutus_cost=plutus_cost,
                expected_cost=[
                    plutus_common.ExpectedCost(
                        expected_time=476_468, expected_space=1_700, expected_lovelace=133
                    )
                ],
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not shutil.which("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_context_equivalance(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Test context equivalence while spending a locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * spend the locked UTxO using the derived redeemer
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 10_000_000
        deposit_amount = cluster.get_address_deposit()

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr2",
            stake_vkey_file=pool_users[0].stake.vkey_file,
        )

        tx_files = clusterlib.TxFiles(certificate_files=[stake_addr_reg_cert_file])

        # generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")
        clusterlib_utils.create_script_context(
            cluster_obj=cluster, redeemer_file=redeemer_file_dummy
        )

        plutus_op_dummy = plutus_common.PlutusOp(
            script_file=plutus_common.CONTEXT_EQUIVALENCE_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=redeemer_file_dummy,
        )

        # fund the script address
        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            plutus_op=plutus_op_dummy,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        invalid_hereafter = cluster.get_slot_no() + 1000

        __, tx_output_dummy, __ = _build_spend_locked_txin(
            temp_template=f"{temp_template}_dummy",
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
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

        __, tx_output, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
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
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        script: str,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "guessing game" script that expects specific datum and redeemer value.
        Test also negative scenarios where datum or redeemer value is different than expected.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * (optional) check transactions in db-sync
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{script}"

        if script.endswith("game_42_43"):
            datum_file = plutus_common.DATUM_42_TYPED
            redeemer_file = plutus_common.REDEEMER_43_TYPED
            expect_failure = True
        elif script.endswith("game_43_42"):
            datum_file = plutus_common.DATUM_43_TYPED
            redeemer_file = plutus_common.REDEEMER_42_TYPED
            expect_failure = True
        elif script.endswith("game_43_43"):
            datum_file = plutus_common.DATUM_43_TYPED
            redeemer_file = plutus_common.REDEEMER_43_TYPED
            expect_failure = True
        else:
            datum_file = plutus_common.DATUM_42_TYPED
            redeemer_file = plutus_common.REDEEMER_42_TYPED
            expect_failure = False

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        err, __, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
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
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the expected error was raised
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        err, __, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
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
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test failing script together with the `--script-invalid` argument - collateral is taken.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        __, tx_output, plutus_cost = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            script_valid=False,
        )

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 171_309
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

        if plutus_cost:
            plutus_common.check_plutus_cost(
                plutus_cost=plutus_cost,
                expected_cost=[
                    plutus_common.ExpectedCost(
                        expected_time=476_468, expected_space=1_700, expected_lovelace=133
                    )
                ],
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_token_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs[0]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            tokens=tokens_rec,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid}#2")
        __, tx_output, plutus_cost = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=50_000_000,
            tokens=tokens_rec,
        )

        # check expected fees
        expected_fee_fund = 173_597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 175_710
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

        if plutus_cost:
            plutus_common.check_plutus_cost(
                plutus_cost=plutus_cost,
                expected_cost=[
                    plutus_common.ExpectedCost(
                        expected_time=476_468, expected_space=1_700, expected_lovelace=133
                    )
                ],
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_is_txin(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while using single UTxO for both collateral and Tx input.

        Uses `cardano-cli transaction build` command for building the transactions.

        Tests bug https://github.com/input-output-hk/cardano-db-sync/issues/750

        * create a Tx output with a datum hash at the script address and a collateral UTxO
        * check that the expected amount was locked at the script address
        * spend the locked UTxO while using the collateral UTxO both as collateral and as
          normal Tx input
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        amount = 50_000_000

        # Step 1: fund the script address

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
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

        for u in script_utxos:
            assert not cluster.get_utxo(
                txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were NOT spent for `{script_address}`"

        # check expected fees
        expected_fee_step1 = 168845
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 176986
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)


@pytest.mark.testnets
class TestNegative:
    """Tests for txin locking using Plutus smart contracts that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_number=st.integers(min_value=-2 ^ 64, max_value=2 ^ 64))
    @common.hypothesis_settings()
    def test_guessing_game_pbt(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_number: int,
    ):
        """Try to spend a locked UtxO with and an invalid redeemer.

        Expect failure.
        """
        hypothesis.assume(redeemer_number != 42)

        temp_template = f"test_guessing_game_pbt{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = "redeemer_file.datum"

        redeemer_content = {}
        if redeemer_number % 3 == 0:
            redeemer_content = {"constructor": 0, "fields": [{"int": redeemer_number}]}
        elif redeemer_number % 2 == 0:
            redeemer_content = {"int": redeemer_number}

        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=Path(redeemer_file) if redeemer_content else None,
            redeemer_value=None if redeemer_content else str(redeemer_number),
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=50_000_000,
            )

        assert "The Plutus script evaluation failed" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_w_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while collateral contains native tokens.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs[0]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=100,
        )
        tokens_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
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
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
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
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test spending the locked UTxO while using the same UTxO as collateral.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
        )

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
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
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output without a datum hash
        * try to spend the UTxO like it was locked Plutus UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
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
