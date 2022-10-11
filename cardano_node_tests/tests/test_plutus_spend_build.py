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
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# skip all tests if Tx era < alonzo
pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
    pytest.mark.smoke,
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    test_id = common.get_test_id(cluster)
    addrs = clusterlib_utils.create_payment_addr_records(
        *[f"{test_id}_payment_addr_{i}" for i in range(3)],
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
    embed_datum: bool = False,
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
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

    script_txout = plutus_common.txout_factory(
        address=script_address,
        amount=script_fund,
        plutus_op=plutus_op,
        embed_datum=embed_datum,
    )

    txouts = [
        script_txout,
        # for collateral
        clusterlib.TxOut(address=dst_addr.address, amount=redeem_cost.collateral),
    ]

    for token in stokens:
        txouts.append(script_txout._replace(amount=token.amount, coin=token.coin))

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

    out_utxos = cluster_obj.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    script_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    assert script_utxos, "No script UTxO"

    assert (
        clusterlib.calculate_utxos_balance(utxos=script_utxos) == script_fund
    ), f"Incorrect balance for script address `{script_address}`"

    collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)
    assert collateral_utxos, "No collateral UTxO"

    assert (
        clusterlib.calculate_utxos_balance(utxos=collateral_utxos) == redeem_cost.collateral
    ), f"Incorrect balance for collateral address `{dst_addr.address}`"

    for token in stokens:
        assert (
            clusterlib.calculate_utxos_balance(utxos=script_utxos, coin=token.coin) == token.amount
        ), f"Incorrect token balance for script address `{script_address}`"

    for token in ctokens:
        assert (
            clusterlib.calculate_utxos_balance(utxos=collateral_utxos, coin=token.coin)
            == token.amount
        ), f"Incorrect token balance for address `{dst_addr.address}`"

    if VERSIONS.transaction_era >= VERSIONS.ALONZO:
        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    return script_utxos, collateral_utxos, tx_output


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
    txins: clusterlib.OptionalUTXOData = (),
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
        script_token_balance = clusterlib.calculate_utxos_balance(
            utxos=script_utxos, coin=token.coin
        )
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
                txins=txins,
                txouts=txouts,
                script_txins=plutus_txins,
                change_address=payment_addr.address,
            )
        return str(excinfo.value), None, []

    tx_output = cluster_obj.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step2",
        tx_files=tx_files,
        txins=txins,
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
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were unexpectedly spent for `{u.address}`"

        return "", tx_output, []

    # calculate cost of Plutus script
    plutus_costs = cluster_obj.calculate_plutus_script_cost(
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
            utxo=u, coins=[clusterlib.DEFAULT_COIN]
        ), f"Inputs were NOT spent for `{u.address}`"

    for token in spent_tokens:
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        for u in script_utxos_token:
            assert not cluster_obj.get_utxo(
                utxo=u, coins=[token.coin]
            ), f"Token inputs were NOT spent for `{u.address}`"

    # check tx view
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_output)

    tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_output)
    # compare cost of Plutus script with data from db-sync
    if tx_db_record:
        dbsync_utils.check_plutus_costs(
            redeemer_records=tx_db_record.redeemers, cost_records=plutus_costs
        )

    return "", tx_output, plutus_costs


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestBuildLocking:
    """Tests for Tx output locking using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_txout_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        __, tx_output, plutus_costs = _build_spend_locked_txin(
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

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not shutil.which("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
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
            cluster_obj=cluster, plutus_version=1, redeemer_file=redeemer_file_dummy
        )

        plutus_op_dummy = plutus_common.PlutusOp(
            script_file=plutus_common.CONTEXT_EQUIVALENCE_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=redeemer_file_dummy,
            execution_cost=plutus_common.CONTEXT_EQUIVALENCE_COST,
        )

        # fund the script address
        script_utxos, collateral_utxos, __ = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            plutus_op=plutus_op_dummy,
        )

        invalid_hereafter = cluster.get_slot_no() + 200

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
                cluster_obj=cluster,
                plutus_version=1,
                redeemer_file=redeemer_file,
                tx_file=tx_file_dummy,
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
    @pytest.mark.parametrize("embed_datum", (True, False), ids=("embedded_datum", "datum"))
    @pytest.mark.parametrize(
        "variant",
        ("typed_json", "typed_cbor", "untyped_value", "untyped_json", "untyped_cbor"),
    )
    @common.PARAM_PLUTUS_VERSION
    def test_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        variant: str,
        plutus_version: str,
        embed_datum: bool,
        request: FixtureRequest,
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
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        datum_file: Optional[Path] = None
        datum_cbor_file: Optional[Path] = None
        datum_value: Optional[str] = None
        redeemer_file: Optional[Path] = None
        redeemer_cbor_file: Optional[Path] = None
        redeemer_value: Optional[str] = None

        if variant == "typed_json":
            script_file = plutus_common.GUESSING_GAME[plutus_version].script_file
            datum_file = plutus_common.DATUM_42_TYPED
            redeemer_file = plutus_common.REDEEMER_42_TYPED
        elif variant == "typed_cbor":
            script_file = plutus_common.GUESSING_GAME[plutus_version].script_file
            datum_cbor_file = plutus_common.DATUM_42_TYPED_CBOR
            redeemer_cbor_file = plutus_common.REDEEMER_42_TYPED_CBOR
        elif variant == "untyped_value":
            script_file = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file
            datum_value = "42"
            redeemer_value = "42"
        elif variant == "untyped_json":
            script_file = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file
            datum_file = plutus_common.DATUM_42
            redeemer_file = plutus_common.REDEEMER_42
        elif variant == "untyped_cbor":  # noqa: SIM106
            script_file = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file
            datum_cbor_file = plutus_common.DATUM_42_CBOR
            redeemer_cbor_file = plutus_common.REDEEMER_42_CBOR
        else:
            raise AssertionError("Unknown test variant.")

        execution_cost = plutus_common.GUESSING_GAME[plutus_version].execution_cost
        if script_file == plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file:
            execution_cost = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost

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

        script_utxos, collateral_utxos, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            embed_datum=embed_datum,
        )

        __, __, plutus_costs = _build_spend_locked_txin(
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

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize(
        "plutus_version",
        (
            "plutus_v1",
            pytest.param("mix_v1_v2", marks=common.SKIPIF_PLUTUSV2_UNUSABLE),
            pytest.param("mix_v2_v1", marks=common.SKIPIF_PLUTUSV2_UNUSABLE),
            pytest.param("plutus_v2", marks=common.SKIPIF_PLUTUSV2_UNUSABLE),
        ),
    )
    def test_two_scripts_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000
        script_fund = 200_000_000

        protocol_params = cluster.get_protocol_params()

        script_file1_v1 = plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V1
        execution_cost1_v1 = plutus_common.ALWAYS_SUCCEEDS_COST
        script_file2_v1 = plutus_common.GUESSING_GAME_PLUTUS_V1
        # this is higher than `plutus_common.GUESSING_GAME_COST`, because the script
        # context has changed to include more stuff
        if configuration.ALONZO_COST_MODEL or VERSIONS.cluster_era == VERSIONS.ALONZO:
            execution_cost2_v1 = plutus_common.ExecutionCost(
                per_time=388_458_303, per_space=1_031_312, fixed_cost=87_515
            )
        else:
            execution_cost2_v1 = plutus_common.ExecutionCost(
                per_time=280_668_068, per_space=1_031_312, fixed_cost=79_743
            )

        script_file1_v2 = plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2
        execution_cost1_v2 = plutus_common.ALWAYS_SUCCEEDS_V2_COST
        script_file2_v2 = plutus_common.GUESSING_GAME_PLUTUS_V2
        execution_cost2_v2 = plutus_common.ExecutionCost(
            per_time=208_314_784,
            per_space=662_274,
            fixed_cost=53_233,
        )

        expected_fee_fund = 174_389
        if plutus_version == "plutus_v1":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
            expected_fee_redeem = 378_768
        elif plutus_version == "mix_v1_v2":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
            expected_fee_redeem = 321_739
        elif plutus_version == "mix_v2_v1":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
            expected_fee_redeem = 378_584
        elif plutus_version == "plutus_v2":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
            expected_fee_redeem = 321_378
        else:
            raise AssertionError("Unknown test variant.")

        plutus_op1 = plutus_common.PlutusOp(
            script_file=script_file1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=execution_cost1,
        )
        plutus_op2 = plutus_common.PlutusOp(
            script_file=script_file2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_TYPED_CBOR,
            execution_cost=execution_cost2,
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

        fund_utxos = cluster.get_utxo(tx_raw_output=tx_output_fund)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output_fund.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)

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
        plutus_costs = cluster.calculate_plutus_script_cost(
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
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were NOT spent for `{u.address}`"

        # check expected fees
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)
        assert helpers.is_in_interval(tx_output_redeem.fee, expected_fee_redeem, frac=0.15)

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[execution_cost1, execution_cost2],
        )

        # check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        # check transactions in db-sync
        tx_redeem_record = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_output_redeem
        )
        if tx_redeem_record:
            dbsync_utils.check_plutus_costs(
                redeemer_records=tx_redeem_record.redeemers, cost_records=plutus_costs
            )

    @allure.link(helpers.get_vcs_link())
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
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
        )

        script_utxos, collateral_utxos, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

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
        assert "The Plutus script evaluation failed" in err, err

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
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
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
        )

        script_utxos, collateral_utxos, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        # include any payment txin
        txins = [
            r
            for r in cluster.get_utxo(
                address=payment_addrs[0].address, coins=[clusterlib.DEFAULT_COIN]
            )
            if not (r.datum_hash or r.inline_datum_hash)
        ][:1]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        try:
            __, tx_output, __ = _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=2_000_000,
                txins=txins,
                tx_files=tx_files,
                script_valid=False,
            )
        except clusterlib.CLIError as err:
            # TODO: broken on node 1.35.0 and 1.35.1
            if "ScriptWitnessIndexTxIn 0 is missing from the execution units" in str(err):
                pytest.xfail("See cardano-node issue #4013")
            else:
                raise

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

        if tx_output:
            expected_fee = 171_309
            assert helpers.is_in_interval(tx_output.fee, expected_fee, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_txout_token_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        token_rand = clusterlib.get_rand_str(5)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
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

        script_utxos, collateral_utxos, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            tokens=tokens_rec,
        )

        __, tx_output_spend, plutus_costs = _build_spend_locked_txin(
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

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_partial_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        token_rand = clusterlib.get_rand_str(5)

        amount_spend = 10_000_000
        token_amount_fund = 100
        token_amount_spend = 20

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
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

        script_utxos, collateral_utxos, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            tokens=tokens_fund_rec,
        )

        tokens_spend_rec = [
            plutus_common.Token(coin=t.token, amount=token_amount_spend) for t in tokens
        ]

        __, tx_output_spend, plutus_costs = _build_spend_locked_txin(
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

        out_utxos = cluster.get_utxo(tx_raw_output=tx_output_spend)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos, txouts=tx_output_spend.txouts
        )

        # UTxO we created for tokens and minimum required Lovelace
        change_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)
        # UTxO that was created by `build` command for rest of the Lovelace change (this will not
        # have the script's datum)
        # TODO: change UTxO used to be first, now it's last
        build_change_utxo = out_utxos[0] if utxo_ix_offset else out_utxos[-1]

        # Lovelace balance on original script UTxOs
        script_lovelace_balance = clusterlib.calculate_utxos_balance(utxos=script_utxos)
        # Lovelace balance on change UTxOs
        change_lovelace_balance = clusterlib.calculate_utxos_balance(
            utxos=[*change_utxos, build_change_utxo]
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

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_collateral_is_txin(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        # Step 1: fund the script address

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_step1 = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
        )

        # Step 2: spend the "locked" UTxO

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
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were NOT spent for `{script_address}`"

        # check expected fees
        expected_fee_step1 = 168_845
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 176_986
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestDatum:
    """Tests for datum."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_datum_on_key_credential_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test creating UTxO with datum on address with key credentials (non-script address).

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        txouts = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
                datum_hash_file=plutus_common.DATUM_42_TYPED,
            )
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        datum_utxo = clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0]
        assert datum_utxo.datum_hash, f"UTxO should have datum hash: {datum_utxo}"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestNegative:
    """Tests for Tx output locking using Plutus smart contracts that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_collateral_w_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test spending the locked UTxO while collateral contains native tokens.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        token_rand = clusterlib.get_rand_str(5)
        payment_addr = payment_addrs[0]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
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

        script_utxos, collateral_utxos, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            tokens_collateral=tokens_rec,
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
                amount=2_000_000,
            )

        err_str = str(excinfo.value)
        assert "CollateralContainsNonADA" in err_str, err_str

        # check expected fees
        expected_fee_fund = 173597
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_same_collateral_txin(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, __, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

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

        err_str = str(excinfo.value)
        assert (
            "expected to be key witnessed but are actually script witnessed: "
            f'["{script_utxos[0].utxo_hash}#{script_utxos[0].utxo_ix}"]' in err_str
            # in 1.35.3 and older
            or "Expected key witnessed collateral" in err_str
        ), err_str

        # check expected fees
        expected_fee_fund = 168_845
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize(
        "variant",
        (
            "42_43",  # correct datum, wrong redeemer
            "43_42",  # wrong datum, correct redeemer
            "43_43",  # wrong datum and redeemer
        ),
    )
    @common.PARAM_PLUTUS_VERSION
    def test_invalid_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        variant: str,
        plutus_version: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{variant}"

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
            script_file=plutus_common.GUESSING_GAME[plutus_version].script_file,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
            execution_cost=plutus_common.GUESSING_GAME[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
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
                amount=2_000_000,
            )

        err_str = str(excinfo.value)
        assert "The Plutus script evaluation failed" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_two_scripts_spending_one_fail(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx, one fails.

        Uses `cardano-cli transaction build` command for building the transactions.

        * create a Tx output with a datum hash at the script addresses
        * try to spend the locked UTxOs
        * check that the expected error was raised
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 50_000_000

        script_fund = 200_000_000

        protocol_params = cluster.get_protocol_params()

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        plutus_op2 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
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

        out_utxos = cluster.get_utxo(tx_raw_output=tx_output_fund)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos, txouts=tx_output_fund.txouts
        )

        script_utxos1 = clusterlib.filter_utxos(
            utxos=out_utxos, utxo_ix=utxo_ix_offset, coin=clusterlib.DEFAULT_COIN
        )
        script_utxos2 = clusterlib.filter_utxos(
            utxos=out_utxos, utxo_ix=utxo_ix_offset + 1, coin=clusterlib.DEFAULT_COIN
        )
        collateral_utxos1 = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 2)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 3)

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

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
                change_address=payment_addrs[0].address,
            )

        err_str = str(excinfo.value)
        assert "The Plutus script evaluation failed" in err_str, err_str


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestNegativeRedeemer:
    """Tests for Tx output locking using Plutus smart contracts with wrong redeemer."""

    MAX_INT_VAL = (2**64) - 1
    MIN_INT_VAL = -MAX_INT_VAL
    AMOUNT = 2_000_000

    @pytest.fixture
    def fund_script_guessing_game_v1(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]]:
        """Fund a PlutusV1 script and create the locked UTxO and collateral UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42,
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
        )

        script_utxos, collateral_utxos, __ = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

        return script_utxos, collateral_utxos

    @pytest.fixture
    def fund_script_guessing_game_v2(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]]:
        """Fund a PlutusV2 script and create the locked UTxO and collateral UTxO.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42,
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED_V2_COST,
        )

        script_utxos, collateral_utxos, __ = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
        )

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
        plutus_version: str,
    ):
        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file) if redeemer_content else None,
            redeemer_value=None if redeemer_content else str(redeemer_value),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert "Value out of range within the script data" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(min_value=MIN_INT_VAL, max_value=MAX_INT_VAL))
    @hypothesis.example(redeemer_value=MIN_INT_VAL)
    @hypothesis.example(redeemer_value=MAX_INT_VAL)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_inside_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value that is in the valid range.

        Expect failure.
        """
        hypothesis.assume(redeemer_value != 42)

        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"

        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(redeemer_content, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file) if redeemer_content else None,
            redeemer_value=None if redeemer_content else str(redeemer_value),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert "The Plutus script evaluation failed" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(min_value=MAX_INT_VAL + 1))
    @hypothesis.example(redeemer_value=MAX_INT_VAL + 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_above_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value, above max value allowed.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game
        self._int_out_of_range(
            cluster=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_version=plutus_version,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(max_value=MIN_INT_VAL - 1))
    @hypothesis.example(redeemer_value=MIN_INT_VAL - 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_bellow_range(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a wrong redeemer value, bellow min value allowed.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game
        self._int_out_of_range(
            cluster=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_version=plutus_version,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO with a wrong redeemer type, try to use bytes.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value.hex()}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert "Script debugging logs: Incorrect datum. Expected 42." in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO using redeemer that is too big.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": redeemer_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert "must consist of at most 64 bytes" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in typed format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"int": redeemer_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert (
            'The value in the field "int" does not have the type required by the schema.' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in untyped format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"int": redeemer_value.hex()}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert (
            'The value in the field "int" does not have the type required by the schema.' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in typed format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": redeemer_value}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert (
            'The value in the field "bytes" does not have the type required by the schema.'
            in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in untyped format and the value doesn't comply to JSON schema.
        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert (
            'The value in the field "bytes" does not have the type required by the schema.'
            in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_invalid_json(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_value: str,
    ):
        """Try to build a Tx using a redeemer value that is invalid JSON.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{redeemer_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert "JSON object expected. Unexpected value" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON typed schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{redeemer_type: 42}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert (
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        fund_script_guessing_game_v1: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        fund_script_guessing_game_v2: Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData]],
        plutus_version: str,
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON untyped schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({redeemer_type: 42}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_file=Path(redeemer_file),
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
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

        err_str = str(excinfo.value)
        assert (
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err_str
        ), err_str


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestNegativeDatum:
    """Tests for Tx output locking using Plutus smart contracts with wrong datum."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("address_type", ("script_address", "key_address"))
    @common.PARAM_PLUTUS_VERSION
    def test_no_datum_txout(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        address_type: str,
        plutus_version: str,
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        Expect failure.

        * create a Tx output without a datum hash
        * try to spend the UTxO like it was locked Plutus UTxO
        * check that the expected error was raised
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{address_type}"
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        if address_type == "script_address":
            redeem_address = cluster.gen_payment_addr(
                addr_name=temp_template, payment_script_file=plutus_op.script_file
            )
        else:
            redeem_address = payment_addrs[2].address

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        txouts = [
            clusterlib.TxOut(address=redeem_address, amount=amount + redeem_cost.fee),
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

        out_utxos = cluster.get_utxo(tx_raw_output=tx_output_fund)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos, txouts=tx_output_fund.txouts
        )

        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
        collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)

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
        err_str = str(excinfo.value)

        if address_type == "script_address":
            assert "txin does not have a script datum" in err_str, err_str
        else:
            assert (
                "not a Plutus script witnessed tx input" in err_str
                or "points to a script hash that is not known" in err_str
            ), err_str

        # check expected fees
        expected_fee_fund = 199_087
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_lock_tx_invalid_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        datum_value: str,
        plutus_version: str,
    ):
        """Test locking a Tx output with an invalid datum.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_fund_script(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
            )

        err_str = str(excinfo.value)
        assert "JSON object expected. Unexpected value" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_unlock_tx_wrong_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking a Tx output and try to spend it with a wrong datum.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        plutus_op_1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op_1,
        )

        # use a wrong datum to try to unlock the funds
        plutus_op_2 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op_2,
                amount=2_000_000,
            )

        err_str = str(excinfo.value)
        assert (
            "The Plutus script witness has the wrong datum (according to the UTxO)." in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_unlock_non_script_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Try to spend a non-script UTxO with datum as if it was script locked UTxO.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        amount_fund = 4_000_000
        amount_redeem = 2_000_000
        amount_collateral = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        datum_file = plutus_common.DATUM_42_TYPED

        datum_hash = cluster.get_hash_script_data(script_data_file=datum_file)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=datum_file,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        # create datum and collateral UTxOs

        txouts = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount_fund,
                datum_hash=datum_hash,
            ),
            clusterlib.TxOut(
                address=payment_addr.address,
                amount=amount_collateral,
            ),
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        datum_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=dst_addr.address, datum_hash=datum_hash
        )[0]
        collateral_utxos = clusterlib.filter_utxos(
            utxos=out_utxos, address=payment_addr.address, utxo_ix=datum_utxo.utxo_ix + 1
        )
        assert (
            datum_utxo.datum_hash == datum_hash
        ), f"UTxO should have datum hash '{datum_hash}': {datum_utxo}"

        # try to spend the "locked" UTxO

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=[datum_utxo],
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount_redeem,
            )

        err_str = str(excinfo.value)
        assert "points to a script hash that is not known" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        datum_value: bytes,
        plutus_version: str,
    ):
        """Try to lock a UTxO with datum that is too big.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": datum_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_fund_script(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
            )

        err_str = str(excinfo.value)
        assert "must consist of at most 64 bytes" in err_str, err_str


@pytest.mark.testnets
class TestCompatibility:
    """Tests for checking compatibility with previous Tx eras."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era > VERSIONS.ALONZO,
        reason="runs only with Tx era <= Alonzo",
    )
    @pytest.mark.dbsync
    def test_plutusv2_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test spending a UTxO locked with PlutusV2 script using old Tx era.

        Expect failure.

        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
        )

        script_utxos, collateral_utxos, __ = _build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
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
                amount=2_000_000,
            )

        err_str = str(excinfo.value)
        assert "PlutusScriptV2 is not supported" in err_str, err_str
