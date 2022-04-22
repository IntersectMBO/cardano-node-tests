"""Tests for spending with Plutus using `transaction build`."""
import functools
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
    test_id = common.get_test_id(cluster)
    addrs = clusterlib_utils.create_payment_addr_records(
        *[f"{test_id}_payment_addr_{i}" for i in range(2)],
        cluster_obj=cluster,
    )

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=1_000_000_000,
    )

    return addrs


@pytest.fixture
def pool_users(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.PoolUser]:
    """Create new pool users."""
    test_id = common.get_test_id(cluster)
    created_users = clusterlib_utils.create_pool_users(
        cluster_obj=cluster,
        name_template=f"{test_id}_pool_users",
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
    """Fund a Plutus script and create the locked UTxO and collateral UTxO.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    assert plutus_op.execution_cost  # for mypy

    script_fund = 200_000_000

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost, protocol_params=cluster_obj.get_protocol_params()
    )

    # create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    datum_hash = cluster_obj.get_hash_script_data(
        script_data_file=plutus_op.datum_file if plutus_op.datum_file else None,
        script_data_cbor_file=plutus_op.datum_cbor_file if plutus_op.datum_cbor_file else None,
        script_data_value=plutus_op.datum_value if plutus_op.datum_value else "",
    )
    txouts = [
        clusterlib.TxOut(address=script_address, amount=script_fund, datum_hash=datum_hash),
        # for collateral
        clusterlib.TxOut(address=dst_addr.address, amount=redeem_cost.collateral),
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


def _build_spend_locked_txin(  # noqa: C901
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
    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

    # Change that was calculated manually will be returned to address of the first script.
    # The remaining change that is automatically handled by the `build` command will be returned
    # to `payment_addr`, because it would be inaccessible on script address without proper
    # datum hash (datum hash is not provided for change that is handled by `build` command).
    script_change_rec = script_utxos[0]

    # spend the "locked" UTxO

    plutus_txins = [
        clusterlib.ScriptTxIn(
            txins=script_utxos,
            script_file=plutus_op.script_file,
            collaterals=collateral_utxos,
            datum_file=plutus_op.datum_file if plutus_op.datum_file else "",
            datum_cbor_file=plutus_op.datum_cbor_file if plutus_op.datum_cbor_file else "",
            datum_value=plutus_op.datum_value if plutus_op.datum_value else "",
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

    lovelace_change_needed = False
    for token in spent_tokens:
        txouts.append(
            clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
        )
        # append change
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        script_token_balance = functools.reduce(lambda x, y: x + y.amount, script_utxos_token, 0)
        if script_token_balance > token.amount:
            lovelace_change_needed = True
            txouts.append(
                clusterlib.TxOut(
                    address=script_change_rec.address,
                    amount=script_token_balance - token.amount,
                    coin=token.coin,
                    datum_hash=script_change_rec.datum_hash,
                )
            )
    # add minimum (+ some) required Lovelace to change Tx output
    if lovelace_change_needed:
        txouts.append(
            clusterlib.TxOut(
                address=script_change_rec.address,
                amount=4_000_000,
                coin=clusterlib.DEFAULT_COIN,
                datum_hash=script_change_rec.datum_hash,
            )
        )

    if expect_failure:
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files,
                txouts=txouts,
                script_txins=plutus_txins,
                change_address=payment_addr.address,
            )
        return str(excinfo.value), None, []

    tx_output = cluster_obj.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txouts=txouts,
        script_txins=plutus_txins,
        change_address=payment_addr.address,
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
            ), f"Inputs were unexpectedly spent for `{u.address}`"

        return "", tx_output, []

    # calculate cost of Plutus script
    plutus_cost = cluster_obj.calculate_plutus_script_cost(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txouts=txouts,
        script_txins=plutus_txins,
        change_address=payment_addr.address,
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
        ), f"Inputs were NOT spent for `{u.address}`"

    for token in spent_tokens:
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        for u in script_utxos_token:
            assert not cluster_obj.get_utxo(
                txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[token.coin]
            ), f"Token inputs were NOT spent for `{u.address}`"

    # check tx view
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)
    # compare cost of Plutus script with data from db-sync
    if tx_db_record:
        dbsync_utils.check_plutus_cost(
            redeemer_record=tx_db_record.redeemers[0], cost_record=plutus_cost[0]
        )

    return "", tx_output, plutus_cost


@pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG)
class TestBuildLocking:
    """Tests for Tx output locking using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txout_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
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
            amount=2_000_000,
        )

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee = 170_782
        assert tx_output and helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[plutus_common.ALWAYS_SUCCEEDS_COST],
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
            execution_cost=plutus_common.CONTEXT_EQUIVALENCE_COST,
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
        invalid_hereafter = cluster.get_slot_no() + 1_000

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
            expected_fee = 372_438
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "variant",
        ("typed_json", "typed_cbor", "untyped_value", "untyped_json", "untyped_cbor"),
    )
    def test_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        variant: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "guessing game" scripts that expect specific datum and redeemer value.
        Test both typed and untyped redeemer and datum.
        Test passing datum and redeemer to `cardano-cli` as value, json file and cbor file.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{variant}"

        datum_file: Optional[Path] = None
        datum_cbor_file: Optional[Path] = None
        datum_value: Optional[str] = None
        redeemer_file: Optional[Path] = None
        redeemer_cbor_file: Optional[Path] = None
        redeemer_value: Optional[str] = None

        if variant == "typed_json":
            script_file = plutus_common.GUESSING_GAME_PLUTUS
            datum_file = plutus_common.DATUM_42_TYPED
            redeemer_file = plutus_common.REDEEMER_42_TYPED
        elif variant == "typed_cbor":
            script_file = plutus_common.GUESSING_GAME_PLUTUS
            datum_cbor_file = plutus_common.DATUM_42_TYPED_CBOR
            redeemer_cbor_file = plutus_common.REDEEMER_42_TYPED_CBOR
        elif variant == "untyped_value":
            script_file = plutus_common.GUESSING_GAME_UNTYPED_PLUTUS
            datum_value = "42"
            redeemer_value = "42"
        elif variant == "untyped_json":
            script_file = plutus_common.GUESSING_GAME_UNTYPED_PLUTUS
            datum_file = plutus_common.DATUM_42
            redeemer_file = plutus_common.REDEEMER_42
        elif variant == "untyped_cbor":  # noqa: SIM106
            script_file = plutus_common.GUESSING_GAME_UNTYPED_PLUTUS
            datum_cbor_file = plutus_common.DATUM_42_CBOR
            redeemer_cbor_file = plutus_common.REDEEMER_42_CBOR
        else:
            raise AssertionError("Unknown test variant.")

        execution_cost = plutus_common.GUESSING_GAME_COST
        if script_file == plutus_common.GUESSING_GAME_UNTYPED_PLUTUS:
            execution_cost = plutus_common.GUESSING_GAME_UNTYPED_COST

        plutus_op = plutus_common.PlutusOp(
            script_file=script_file,
            datum_file=datum_file,
            datum_cbor_file=datum_cbor_file,
            datum_value=datum_value,
            redeemer_file=redeemer_file,
            redeemer_cbor_file=redeemer_cbor_file,
            redeemer_value=redeemer_value,
            execution_cost=execution_cost,
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
        _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=2_000_000,
        )

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_two_scripts_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000
        script_fund = 200_000_000

        protocol_params = cluster.get_protocol_params()

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )
        plutus_op2 = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_TYPED_CBOR,
            # this is higher than `plutus_common.GUESSING_GAME_COST`, because the script
            # context has changed to include more stuff
            execution_cost=plutus_common.ExecutionCost(
                per_time=388_458_303, per_space=1_031_312, fixed_cost=87_515
            ),
        )

        # Step 1: fund the Plutus scripts

        assert plutus_op1.execution_cost and plutus_op2.execution_cost  # for mypy

        script_address1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )
        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=protocol_params
        )
        datum_hash1 = cluster.get_hash_script_data(script_data_file=plutus_op1.datum_file)

        script_address2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )
        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=protocol_params
        )
        datum_hash2 = cluster.get_hash_script_data(script_data_file=plutus_op2.datum_file)

        # create a Tx output with a datum hash at the script address

        tx_files_fund = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )
        txouts_fund = [
            clusterlib.TxOut(
                address=script_address1,
                amount=script_fund,
                datum_hash=datum_hash1,
            ),
            clusterlib.TxOut(
                address=script_address2,
                amount=script_fund,
                datum_hash=datum_hash2,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]
        tx_output_fund = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_fund,
            txouts=txouts_fund,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed_fund = cluster.sign_tx(
            tx_body_file=tx_output_fund.out_file,
            signing_key_files=tx_files_fund.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )

        cluster.submit_tx(tx_file=tx_signed_fund, txins=tx_output_fund.txins)

        txid_fund = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid_fund}#1", coins=[clusterlib.DEFAULT_COIN])
        script_utxos2 = cluster.get_utxo(txin=f"{txid_fund}#2", coins=[clusterlib.DEFAULT_COIN])
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid_fund}#3")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid_fund}#4")

        assert script_utxos1 and script_utxos2, "No script UTxOs"
        assert collateral_utxos1 and collateral_utxos2, "No collateral UTxOs"

        assert (
            script_utxos1[0].amount == script_fund
        ), f"Incorrect balance for script address `{script_utxos1[0].address}`"
        assert (
            script_utxos2[0].amount == script_fund
        ), f"Incorrect balance for script address `{script_utxos2[0].address}`"

        # Step 2: spend the "locked" UTxOs

        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                script_file=plutus_op1.script_file,
                collaterals=collateral_utxos1,
                datum_file=plutus_op1.datum_file,
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                script_file=plutus_op2.script_file,
                collaterals=collateral_utxos2,
                datum_file=plutus_op2.datum_file,
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
            ),
        ]
        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount * 2),
        ]
        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
            change_address=payment_addrs[0].address,
        )

        # calculate cost of Plutus script
        plutus_cost = cluster.calculate_plutus_script_cost(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
            change_address=payment_addrs[0].address,
        )

        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        dst_init_balance = cluster.get_address_balance(payment_addrs[1].address)

        cluster.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        assert (
            cluster.get_address_balance(payment_addrs[1].address) == dst_init_balance + amount * 2
        ), f"Incorrect balance for destination address `{payment_addrs[1].address}`"

        script_utxos_lovelace = [
            u for u in [*script_utxos1, *script_utxos2] if u.coin == clusterlib.DEFAULT_COIN
        ]
        for u in script_utxos_lovelace:
            assert not cluster.get_utxo(
                txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were NOT spent for `{u.address}`"

        # check expected fees
        expected_fee_fund = 173_597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee_redeem = 378_504
        assert helpers.is_in_interval(tx_output_redeem.fee, expected_fee_redeem, frac=0.15)

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[
                plutus_common.ALWAYS_SUCCEEDS_COST,
                plutus_op2.execution_cost,
            ],
        )

        # check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        # check transactions in db-sync
        tx_redeem_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_output_redeem
        )
        if tx_redeem_record:
            dbsync_utils.check_plutus_costs(
                redeemer_records=tx_redeem_record.redeemers, cost_records=plutus_cost
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_always_fails(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

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
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
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
            amount=2_000_000,
            expect_failure=True,
        )
        assert "The Plutus script evaluation failed" in err

        # check expected fees
        expected_fee_fund = 168_845
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
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
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
        __, tx_output, __ = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=2_000_000,
            script_valid=False,
        )

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 171_309
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txout_token_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO
        * check that the expected amounts of Lovelace and native tokens were spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[0],
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
        __, tx_output_spend, plutus_cost = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=2_000_000,
            tokens=tokens_rec,
        )

        # check expected fees
        expected_fee_fund = 173_597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee = 175_710
        assert tx_output_spend and helpers.is_in_interval(
            tx_output_spend.fee, expected_fee, frac=0.15
        )

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[plutus_common.ALWAYS_SUCCEEDS_COST],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_partial_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test spending part of funds (Lovelace and native tokens) on a locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO and create new locked UTxO with change
        * check that the expected amounts of Lovelace and native tokens were spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        token_rand = clusterlib.get_rand_str(5)

        amount_spend = 10_000_000
        token_amount_fund = 100
        token_amount_spend = 20

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode("utf-8").hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[0],
            amount=token_amount_fund,
        )
        tokens_fund_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            tokens=tokens_fund_rec,
        )

        txid_fund = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.get_utxo(txin=f"{txid_fund}#1")
        collateral_utxos = cluster.get_utxo(txin=f"{txid_fund}#2")
        tokens_spend_rec = [
            plutus_common.Token(coin=t.token, amount=token_amount_spend) for t in tokens
        ]

        __, tx_output_spend, plutus_cost = _build_spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount_spend,
            tokens=tokens_spend_rec,
        )

        # check that the expected amounts of Lovelace and native tokens were spent and change UTxOs
        # with appropriate datum hash were created

        assert tx_output_spend
        txid_spend = cluster.get_txid(tx_body_file=tx_output_spend.out_file)
        # UTxO we created for tokens and minimum required Lovelace
        change_utxos = cluster.get_utxo(txin=f"{txid_spend}#2")
        # UTxO that was created by `build` command for rest of the Lovelace change (this will not
        # have the script's datum
        build_change_utxos = cluster.get_utxo(txin=f"{txid_spend}#0")

        # Lovelace balance on original script UTxOs
        script_lovelace_utxos = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]
        script_lovelace_balance = functools.reduce(
            lambda x, y: x + y.amount, script_lovelace_utxos, 0
        )
        # Lovelace balance on change UTxOs
        change_lovelace_utxos = [
            u for u in [*change_utxos, *build_change_utxos] if u.coin == clusterlib.DEFAULT_COIN
        ]
        change_lovelace_balance = functools.reduce(
            lambda x, y: x + y.amount, change_lovelace_utxos, 0
        )

        assert (
            change_lovelace_balance == script_lovelace_balance - tx_output_spend.fee - amount_spend
        )

        token_amount_exp = token_amount_fund - token_amount_spend
        assert len(change_utxos) == len(tokens_spend_rec) + 1
        for u in change_utxos:
            if u.coin != clusterlib.DEFAULT_COIN:
                assert u.amount == token_amount_exp
            assert u.datum_hash == script_utxos[0].datum_hash

        # check expected fees
        expected_fee_fund = 173_597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        expected_fee = 183_366
        assert tx_output_spend and helpers.is_in_interval(
            tx_output_spend.fee, expected_fee, frac=0.15
        )

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[plutus_common.ALWAYS_SUCCEEDS_COST],
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
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        # Step 1: fund the script address

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
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
        expected_fee_step1 = 168_845
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 176_986
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)


@pytest.mark.testnets
class TestNegative:
    """Tests for Tx output locking using Plutus smart contracts that are expected to fail."""

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
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
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
                amount=2_000_000,
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
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
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
                amount=2_000_000,
            )
        assert "Expected key witnessed collateral" in str(excinfo.value)

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_no_datum_txout(
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
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )
        assert plutus_op.execution_cost  # for mypy

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        txouts = [
            clusterlib.TxOut(address=payment_addr.address, amount=amount + redeem_cost.fee),
            clusterlib.TxOut(address=payment_addr.address, amount=redeem_cost.collateral),
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
                amount=amount,
            )
        assert "not a Plutus script witnessed tx input" in str(excinfo.value)

        # check expected fees
        expected_fee_fund = 199_087
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "variant",
        (
            "42_43",  # correct datum, wrong redeemer
            "43_42",  # wrong datum, correct redeemer
            "43_43",  # wrong datum and redeemer
        ),
    )
    def test_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        variant: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.

        Test with "guessing game" script that expects specific datum and redeemer value.
        Test negative scenarios where datum or redeemer value is different than expected.
        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was not spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{variant}"

        if variant == "42_43":
            datum_file = plutus_common.DATUM_42_TYPED
            redeemer_file = plutus_common.REDEEMER_43_TYPED
        elif variant == "43_42":
            datum_file = plutus_common.DATUM_43_TYPED
            redeemer_file = plutus_common.REDEEMER_42_TYPED
        elif variant == "43_43":  # noqa: SIM106
            datum_file = plutus_common.DATUM_43_TYPED
            redeemer_file = plutus_common.REDEEMER_43_TYPED
        else:
            raise AssertionError("Unknown test variant.")

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
            execution_cost=plutus_common.GUESSING_GAME_COST,
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
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=2_000_000,
            )

        assert "The Plutus script evaluation failed" in str(excinfo.value)


@pytest.mark.testnets
class TestNegativeRedeemer:
    """Tests for Tx output locking using Plutus smart contracts with wrong redeemer."""

    MAX_INT_VAL = (2**64) - 1
    MIN_INT_VAL = -MAX_INT_VAL
    AMOUNT = 2_000_000

    @pytest.fixture
    def fund_script_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]]:
        """Fund a Plutus script and create the locked UTxO and collateral UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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

    def _int_out_of_range(
        self,
        cluster: clusterlib.ClusterLib,
        temp_template: str,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        redeemer_value: int,
    ):
        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file) if redeemer_content else None,
            redeemer_value=None if redeemer_content else str(redeemer_value),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=self.AMOUNT,
            )

        assert "Value out of range within the script data" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers(min_value=MIN_INT_VAL, max_value=MAX_INT_VAL))
    @common.hypothesis_settings()
    def test_wrong_value_inside_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value that is in the valid range.

        Expect failure.
        """
        hypothesis.assume(redeemer_value != 42)

        temp_template = f"test_wrong_value_inside_range_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"

        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(redeemer_content, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file) if redeemer_content else None,
            redeemer_value=None if redeemer_content else str(redeemer_value),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert "The Plutus script evaluation failed" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers(min_value=MAX_INT_VAL + 1))
    @common.hypothesis_settings()
    def test_wrong_value_above_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value, above max value allowed.

        Expect failure.
        """
        temp_template = f"test_wrong_value_above_range_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game
        self._int_out_of_range(
            cluster=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers(max_value=MIN_INT_VAL - 1))
    @common.hypothesis_settings()
    def test_wrong_value_bellow_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value, bellow min value allowed.

        Expect failure.
        """
        temp_template = f"test_wrong_value_bellow_range_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game
        self._int_out_of_range(
            cluster=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.binary())
    @common.hypothesis_settings()
    def test_wrong_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO with a wrong redeemer type, try to use bytes.

        Expect failure.
        """
        temp_template = f"test_wrong_type_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value.hex()}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert "Script debugging logs: Incorrect datum. Expected 42." in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.binary())
    @common.hypothesis_settings()
    def test_json_schema_typed_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in typed format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"test_json_schema_typed_int_bytes_declared_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"int": redeemer_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert 'The value in the field "int" does not have the type required by the schema.' in str(
            excinfo.value
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.binary())
    @common.hypothesis_settings()
    def test_json_schema_untyped_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in untyped format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"test_json_schema_untyped_int_bytes_declared_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"int": redeemer_value.hex()}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert 'The value in the field "int" does not have the type required by the schema.' in str(
            excinfo.value
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings()
    def test_json_schema_typed_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in typed format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"test_typed_bytes_schema_int_declared_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": redeemer_value}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert (
            'The value in the field "bytes" does not have the type required by the schema.'
            in str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings()
    def test_json_schema_untyped_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in untyped format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"test_untyped_bytes_schema_int_declared_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert (
            'The value in the field "bytes" does not have the type required by the schema.'
            in str(excinfo.value)
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.text())
    @common.hypothesis_settings()
    def test_invalid_json(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_value: str,
    ):
        """Try to build a Tx using a redeemer value that is invalid JSON.

        Expect failure.
        """
        temp_template = f"test_invalid_json_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{redeemer_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert "JSON object expected. Unexpected value" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings()
    def test_json_schema_typed_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON typed schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"test_json_schema_typed_invalid_type_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{redeemer_type: 42}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert 'Expected a single field named "int", "bytes", "string", "list" or "map".' in str(
            excinfo.value
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings()
    def test_json_schema_untyped_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON untyped schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"test_json_schema_untyped_invalid_type_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({redeemer_type: 42}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
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
                amount=self.AMOUNT,
            )

        assert 'Expected a single field named "int", "bytes", "string", "list" or "map".' in str(
            excinfo.value
        )
