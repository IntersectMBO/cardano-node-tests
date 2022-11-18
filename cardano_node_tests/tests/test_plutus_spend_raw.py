"""Tests for spending with Plutus using `transaction build-raw`."""
import itertools
import json
import logging
import shutil
import time
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

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# skip all tests if Tx era < alonzo
pytestmark = [
    pytest.mark.smoke,
]


FundTupleT = Tuple[
    List[clusterlib.UTXOData], List[clusterlib.UTXOData], List[clusterlib.AddressRecord]
]

# approx. fee for Tx size
FEE_REDEEM_TXSIZE = 400_000


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
        amount=3_000_000_000,
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


def _fund_script(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    fee_txsize: int = FEE_REDEEM_TXSIZE,
    deposit_amount: int = 0,
    tokens: Optional[List[plutus_common.Token]] = None,  # tokens must already be in `payment_addr`
    tokens_collateral: Optional[
        List[plutus_common.Token]
    ] = None,  # tokens must already be in `payment_addr`
    collateral_fraction_offset: float = 1.0,
    embed_datum: bool = False,
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Fund a Plutus script and create the locked UTxO and collateral UTxO."""
    # pylint: disable=too-many-locals,too-many-arguments
    assert plutus_op.execution_cost  # for mypy

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.g_address.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost,
        protocol_params=cluster_obj.g_query.get_protocol_params(),
        collateral_fraction_offset=collateral_fraction_offset,
    )

    # create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    script_txout = plutus_common.txout_factory(
        address=script_address,
        amount=amount + redeem_cost.fee + fee_txsize + deposit_amount,
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

    tx_raw_output = cluster_obj.g_transaction.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=2,
    )

    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    script_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#0")
    assert script_utxos, "No script UTxO"

    assert (
        clusterlib.calculate_utxos_balance(utxos=script_utxos) == txouts[0].amount
    ), f"Incorrect balance for script address `{script_address}`"

    collateral_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#1")
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
        dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return script_utxos, collateral_utxos, tx_raw_output


def _spend_locked_txin(  # noqa: C901
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    dst_addr: clusterlib.AddressRecord,
    script_utxos: List[clusterlib.UTXOData],
    collateral_utxos: List[clusterlib.UTXOData],
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    fee_txsize: int = FEE_REDEEM_TXSIZE,
    txins: clusterlib.OptionalUTXOData = (),
    tx_files: Optional[clusterlib.TxFiles] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    tokens: Optional[List[plutus_common.Token]] = None,
    expect_failure: bool = False,
    script_valid: bool = True,
    submit_tx: bool = True,
) -> Tuple[str, clusterlib.TxRawOutput]:
    """Spend the locked UTxO."""
    # pylint: disable=too-many-arguments,too-many-locals
    assert plutus_op.execution_cost

    tx_files = tx_files or clusterlib.TxFiles()
    spent_tokens = tokens or ()

    # change will be returned to address of the first script
    change_rec = script_utxos[0]

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost,
        protocol_params=cluster_obj.g_query.get_protocol_params(),
    )

    script_utxos_lovelace = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]
    script_lovelace_balance = clusterlib.calculate_utxos_balance(
        utxos=[*script_utxos_lovelace, *txins]
    )

    # spend the "locked" UTxO

    plutus_txins = [
        clusterlib.ScriptTxIn(
            txins=script_utxos,
            script_file=plutus_op.script_file,
            collaterals=collateral_utxos,
            execution_units=(plutus_op.execution_cost.per_time, plutus_op.execution_cost.per_space),
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
    # append change
    if script_lovelace_balance > amount + redeem_cost.fee + fee_txsize:
        txouts.append(
            clusterlib.TxOut(
                address=change_rec.address,
                amount=script_lovelace_balance - amount - redeem_cost.fee - fee_txsize,
                datum_hash=change_rec.datum_hash,
            )
        )

    for token in spent_tokens:
        txouts.append(
            clusterlib.TxOut(address=dst_addr.address, amount=token.amount, coin=token.coin)
        )
        # append change
        script_token_balance = clusterlib.calculate_utxos_balance(
            utxos=script_utxos, coin=token.coin
        )
        if script_token_balance > token.amount:
            txouts.append(
                clusterlib.TxOut(
                    address=change_rec.address,
                    amount=script_token_balance - token.amount,
                    coin=token.coin,
                    datum_hash=change_rec.datum_hash,
                )
            )

    tx_raw_output = cluster_obj.g_transaction.build_raw_tx_bare(
        out_file=f"{temp_template}_step2_tx.body",
        txins=txins,
        txouts=txouts,
        tx_files=tx_files,
        fee=redeem_cost.fee + fee_txsize,
        script_txins=plutus_txins,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        script_valid=script_valid,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step2",
    )

    if not submit_tx:
        return "", tx_raw_output

    dst_init_balance = cluster_obj.g_query.get_address_balance(dst_addr.address)

    if not script_valid:
        cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=collateral_utxos)

        assert (
            cluster_obj.g_query.get_address_balance(dst_addr.address)
            == dst_init_balance - collateral_utxos[0].amount
        ), f"Collateral was NOT spent from `{dst_addr.address}`"

        for u in script_utxos_lovelace:
            assert cluster_obj.g_query.get_utxo(
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were unexpectedly spent for `{u.address}`"

        return "", tx_raw_output

    if expect_failure:
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.submit_tx_bare(tx_file=tx_signed)
        err = str(excinfo.value)
        assert (
            cluster_obj.g_query.get_address_balance(dst_addr.address) == dst_init_balance
        ), f"Collateral was spent from `{dst_addr.address}`"

        for u in script_utxos_lovelace:
            assert cluster_obj.g_query.get_utxo(
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were unexpectedly spent for `{u.address}`"

        return err, tx_raw_output

    cluster_obj.g_transaction.submit_tx(
        tx_file=tx_signed, txins=[t.txins[0] for t in tx_raw_output.script_txins if t.txins]
    )

    assert (
        cluster_obj.g_query.get_address_balance(dst_addr.address) == dst_init_balance + amount
    ), f"Incorrect balance for destination address `{dst_addr.address}`"

    for u in script_utxos_lovelace:
        assert not cluster_obj.g_query.get_utxo(
            utxo=u, coins=[clusterlib.DEFAULT_COIN]
        ), f"Inputs were NOT spent for `{u.address}`"

    for token in spent_tokens:
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        for u in script_utxos_token:
            assert not cluster_obj.g_query.get_utxo(
                utxo=u, coins=[token.coin]
            ), f"Token inputs were NOT spent for `{u.address}`"

    # check tx view
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return "", tx_raw_output


def _check_pretty_utxo(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> str:
    """Check that pretty printed `query utxo` output looks as expected."""
    err = ""
    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    utxo_out = (
        cluster_obj.cli(
            [
                "query",
                "utxo",
                "--tx-in",
                f"{txid}#0",
                *cluster_obj.magic_args,
            ]
        )
        .stdout.decode("utf-8")
        .split()
    )

    cluster_era = VERSIONS.cluster_era_name.title()
    datum_hash = clusterlib_utils.datum_hash_from_txout(
        cluster_obj=cluster_obj, txout=tx_raw_output.txouts[0]
    )
    expected_out = [
        "TxHash",
        "TxIx",
        "Amount",
        "--------------------------------------------------------------------------------------",
        txid,
        "0",
        str(tx_raw_output.txouts[0].amount),
        tx_raw_output.txouts[0].coin,
        "+",
        "TxOutDatumHash",
        f"ScriptDataIn{cluster_era}Era",
        f'"{datum_hash}"',
    ]

    if utxo_out != expected_out:
        err = f"Pretty UTxO output doesn't match expected output:\n{utxo_out}\nvs\n{expected_out}"

    return err


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestLocking:
    """Tests for Tx output locking using Plutus smart contracts."""

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

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, tx_output_fund = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        utxo_err = _check_pretty_utxo(cluster_obj=cluster, tx_raw_output=tx_output_fund)

        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
        )

        if utxo_err:
            pytest.fail(utxo_err)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not shutil.which("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    def test_context_equivalence(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Test context equivalence while spending a locked UTxO.

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
        deposit_amount = cluster.g_query.get_address_deposit()

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
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
        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            plutus_op=plutus_op_dummy,
            amount=amount,
            deposit_amount=deposit_amount,
        )

        invalid_hereafter = cluster.g_query.get_slot_no() + 200

        __, tx_output_dummy = _spend_locked_txin(
            temp_template=f"{temp_template}_dummy",
            cluster_obj=cluster,
            dst_addr=pool_users[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op_dummy,
            amount=amount,
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

        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=pool_users[1].payment,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            tx_files=tx_files,
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("embed_datum", (True, False), ids=("embedded_datum", "datum"))
    @pytest.mark.parametrize(
        "variant",
        (
            "typed_json",
            "typed_cbor",
            "untyped_value",
            "untyped_json",
            "untyped_cbor",
        ),
    )
    @common.PARAM_PLUTUS_VERSION
    def test_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        request: FixtureRequest,
        embed_datum: bool,
        variant: str,
        plutus_version: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Test with "guessing game" scripts that expect specific datum and redeemer value.
        Test both typed and untyped redeemer and datum.
        Test passing datum and redeemer to `cardano-cli` as value, json file and cbor file.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        amount = 2_000_000

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

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            embed_datum=embed_datum,
        )
        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
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

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        protocol_params = cluster.g_query.get_protocol_params()

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

        if plutus_version == "plutus_v1":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
        elif plutus_version == "mix_v1_v2":
            script_file1 = script_file1_v1
            execution_cost1 = execution_cost1_v1
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
        elif plutus_version == "mix_v2_v1":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v1
            execution_cost2 = execution_cost2_v1
        elif plutus_version == "plutus_v2":
            script_file1 = script_file1_v2
            execution_cost1 = execution_cost1_v2
            script_file2 = script_file2_v2
            execution_cost2 = execution_cost2_v2
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

        script_address1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )
        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=protocol_params
        )
        datum_hash1 = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op1.datum_file
        )

        script_address2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )
        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=protocol_params
        )
        datum_hash2 = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op2.datum_file
        )

        # create a Tx output with a datum hash at the script address

        tx_files_fund = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )
        txouts_fund = [
            clusterlib.TxOut(
                address=script_address1,
                amount=amount + redeem_cost1.fee,
                datum_hash=datum_hash1,
            ),
            clusterlib.TxOut(
                address=script_address2,
                amount=amount + redeem_cost2.fee + FEE_REDEEM_TXSIZE,
                datum_hash=datum_hash2,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output_fund = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_fund,
            tx_files=tx_files_fund,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid_fund = cluster.g_transaction.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos1 = cluster.g_query.get_utxo(
            txin=f"{txid_fund}#0", coins=[clusterlib.DEFAULT_COIN]
        )
        script_utxos2 = cluster.g_query.get_utxo(
            txin=f"{txid_fund}#1", coins=[clusterlib.DEFAULT_COIN]
        )
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid_fund}#2")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid_fund}#3")

        assert script_utxos1 and script_utxos2, "No script UTxOs"
        assert collateral_utxos1 and collateral_utxos2, "No collateral UTxOs"

        assert (
            script_utxos1[0].amount == amount + redeem_cost1.fee
        ), f"Incorrect balance for script address `{script_utxos1[0].address}`"
        assert (
            script_utxos2[0].amount == amount + redeem_cost2.fee + FEE_REDEEM_TXSIZE
        ), f"Incorrect balance for script address `{script_utxos2[0].address}`"

        # Step 2: spend the "locked" UTxOs

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                script_file=plutus_op1.script_file,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op1.execution_cost.per_time,
                    plutus_op1.execution_cost.per_space,
                ),
                datum_file=plutus_op1.datum_file,
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                script_file=plutus_op2.script_file,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
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
        tx_output_redeem = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost1.fee + redeem_cost2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        dst_init_balance = cluster.g_query.get_address_balance(payment_addrs[1].address)

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        assert (
            cluster.g_query.get_address_balance(payment_addrs[1].address)
            == dst_init_balance + amount * 2
        ), f"Incorrect balance for destination address `{payment_addrs[1].address}`"

        script_utxos_lovelace = [
            u for u in [*script_utxos1, *script_utxos2] if u.coin == clusterlib.DEFAULT_COIN
        ]
        for u in script_utxos_lovelace:
            assert not cluster.g_query.get_utxo(
                utxo=u, coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were NOT spent for `{u.address}`"

        # check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    def test_always_fails(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        worker_id: str,
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred, collateral UTxO was not spent
          and the expected error was raised
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
        )

        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="ValidationTagMismatch",
            ignore_file_id=worker_id,
        )

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )
        err, __ = _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            expect_failure=True,
        )
        assert "PlutusFailure" in err, err

        # wait a bit so there's some time for error messages to appear in log file
        time.sleep(1 if cluster.network_magic == configuration.NETWORK_MAGIC_LOCAL else 5)

    @allure.link(helpers.get_vcs_link())
    def test_script_invalid(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test failing script together with the `--script-invalid` argument - collateral is taken.

        Test with "always fails" script that fails for all datum / redeemer values.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_FAILS_COST,
        )

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        # include any payment txin
        txins = [
            r
            for r in cluster.g_query.get_utxo(
                address=payment_addrs[0].address, coins=[clusterlib.DEFAULT_COIN]
            )
            if not (r.datum_hash or r.inline_datum_hash)
        ][:1]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            txins=txins,
            tx_files=tx_files,
            script_valid=False,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_txout_token_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking a Tx output with native tokens and spending the locked UTxO.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO
        * check that the expected amounts of Lovelace and native tokens were spent
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        token_rand = clusterlib.get_rand_str(5)

        amount = 2_000_000
        token_amount = 100

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode().hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[0],
            amount=token_amount,
        )
        tokens_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            tokens=tokens_rec,
        )
        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            tokens=tokens_rec,
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

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO and create new locked UTxO with change
        * check that the expected amounts of Lovelace and native tokens were spent
        * (optional) check transactions in db-sync
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        token_rand = clusterlib.get_rand_str(5)

        amount_fund = 6_000_000
        amount_spend = 2_000_000
        token_amount_fund = 100
        token_amount_spend = 20

        # add extra fee for tokens
        fee_redeem_txsize = FEE_REDEEM_TXSIZE + 5_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode().hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[0],
            amount=token_amount_fund,
        )
        tokens_fund_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount_fund,
            fee_txsize=fee_redeem_txsize,
            tokens=tokens_fund_rec,
        )

        tokens_spend_rec = [
            plutus_common.Token(coin=t.token, amount=token_amount_spend) for t in tokens
        ]

        __, tx_output_spend = _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount_spend,
            fee_txsize=fee_redeem_txsize,
            tokens=tokens_spend_rec,
        )

        txid_spend = cluster.g_transaction.get_txid(tx_body_file=tx_output_spend.out_file)
        change_utxos = cluster.g_query.get_utxo(txin=f"{txid_spend}#1")

        # check that the expected amounts of Lovelace and native tokens were spent and change UTxOs
        # with appropriate datum hash were created
        token_amount_exp = token_amount_fund - token_amount_spend
        assert len(change_utxos) == len(tokens_spend_rec) + 1
        for u in change_utxos:
            if u.coin == clusterlib.DEFAULT_COIN:
                assert u.amount == amount_fund - amount_spend
            else:
                assert u.amount == token_amount_exp
            assert u.datum_hash == script_utxos[0].datum_hash

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("scenario", ("max", "max+1", "none"))
    @common.PARAM_PLUTUS_VERSION
    def test_collaterals(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        scenario: str,
        plutus_version: str,
    ):
        """Test dividing required collateral amount into multiple collateral UTxOs.

        Test 3 scenarios:
        1. maximum allowed number of collateral inputs
        2. more collateral inputs than what is allowed
        3. no collateral input

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * create multiple UTxOs for collateral
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was not spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{scenario}"
        amount = 2_000_000

        max_collateral_ins = cluster.g_query.get_protocol_params()["maxCollateralInputs"]
        collateral_utxos = []

        if scenario == "max":
            collateral_num = max_collateral_ins
            exp_err = ""
            collateral_fraction_offset = 250_000.0
        elif scenario == "max+1":
            collateral_num = max_collateral_ins + 1
            exp_err = "TooManyCollateralInputs"
            collateral_fraction_offset = 250_000.0
        else:
            collateral_num = 0
            exp_err = "Transaction body has no collateral inputs"
            collateral_fraction_offset = 1.0

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, fund_collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            amount=amount,
            collateral_fraction_offset=collateral_fraction_offset,
        )

        if collateral_num:
            # instead of using the collateral UTxO created by `_fund_script`, create multiple new
            # collateral UTxOs with the combined amount matching the original UTxO
            collateral_amount_part = int(fund_collateral_utxos[0].amount // collateral_num) + 1
            txouts_collaterals = [
                clusterlib.TxOut(address=dst_addr.address, amount=collateral_amount_part)
                for __ in range(collateral_num)
            ]
            tx_files_collaterals = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])
            tx_output_collaterals = cluster.g_transaction.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_collaterals",
                txouts=txouts_collaterals,
                tx_files=tx_files_collaterals,
                join_txouts=False,
            )
            txid_collaterals = cluster.g_transaction.get_txid(
                tx_body_file=tx_output_collaterals.out_file
            )
            _utxos_nested = [
                cluster.g_query.get_utxo(txin=f"{txid_collaterals}#{i}")
                for i in range(collateral_num)
            ]
            collateral_utxos = list(itertools.chain.from_iterable(_utxos_nested))

        if exp_err:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                _spend_locked_txin(
                    temp_template=temp_template,
                    cluster_obj=cluster,
                    dst_addr=dst_addr,
                    script_utxos=script_utxos,
                    collateral_utxos=collateral_utxos,
                    plutus_op=plutus_op,
                    amount=amount,
                )
            err_str = str(excinfo.value)
            assert exp_err in err_str, err_str
        else:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )


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
        """Test creating UTxO with datum on address with key credentials (non-script address)."""
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

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        datum_utxo = clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0]
        assert datum_utxo.datum_hash, f"UTxO should have datum hash: {datum_utxo}"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestNegative:
    """Tests for Tx output locking using Plutus smart contracts that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
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

        Test with "guessing game" script that expects specific datum and redeemer value.
        Test negative scenarios where datum or redeemer value is different than expected.
        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO
        * check that the amount was not transferred and collateral UTxO was not spent
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{variant}"
        amount = 2_000_000

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

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )
        err, __ = _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            expect_failure=True,
        )

        assert "ValidationTagMismatch (IsValid True)" in err, err

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

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        token_rand = clusterlib.get_rand_str(5)

        amount = 2_000_000
        token_amount = 100

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        tokens = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}{i}".encode().hex() for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[0],
            amount=token_amount,
        )
        tokens_rec = [plutus_common.Token(coin=t.token, amount=t.amount) for t in tokens]

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            tokens_collateral=tokens_rec,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "CollateralContainsNonADA" in err_str, err_str

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

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_cbor_file=plutus_common.DATUM_42_CBOR,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, *__ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=script_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )
        err_str = str(excinfo.value)
        assert "cardano-cli transaction submit" in err_str, err_str
        assert "ScriptsNotPaidUTxO" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @common.PARAM_PLUTUS_VERSION
    def test_collateral_percent(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Try to spend locked UTxO while collateral is less than required.

        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * create a collateral UTxO with amount of ADA less than required by `collateralPercentage`
        * try to spend the UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        # increase fixed cost so the required collateral is higher than minimum collateral of 2 ADA
        execution_cost = plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost
        execution_cost_increased = execution_cost._replace(fixed_cost=2_000_000)
        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=execution_cost_increased,
        )

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            collateral_fraction_offset=0.9,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "InsufficientCollateral" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_two_scripts_spending_one_fail(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx, one fails.

        Expect failure.

        * create a Tx output with a datum hash at the script addresses
        * try to spend the locked UTxOs
        * check that the expected error was raised
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

        protocol_params = cluster.g_query.get_protocol_params()

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

        script_address1 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )
        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=protocol_params
        )
        datum_hash1 = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op1.datum_file
        )

        script_address2 = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )
        script2_hash = helpers.decode_bech32(bech32=script_address2)[2:]
        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=protocol_params
        )
        datum_hash2 = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_op2.datum_file
        )

        # create a Tx output with a datum hash at the script address

        tx_files_fund = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )
        txouts_fund = [
            clusterlib.TxOut(
                address=script_address1,
                amount=amount + redeem_cost1.fee,
                datum_hash=datum_hash1,
            ),
            clusterlib.TxOut(
                address=script_address2,
                amount=amount + redeem_cost2.fee + FEE_REDEEM_TXSIZE,
                datum_hash=datum_hash2,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output_fund = cluster.g_transaction.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_fund,
            tx_files=tx_files_fund,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid_fund = cluster.g_transaction.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos1 = cluster.g_query.get_utxo(
            txin=f"{txid_fund}#0", coins=[clusterlib.DEFAULT_COIN]
        )
        script_utxos2 = cluster.g_query.get_utxo(
            txin=f"{txid_fund}#1", coins=[clusterlib.DEFAULT_COIN]
        )
        collateral_utxos1 = cluster.g_query.get_utxo(txin=f"{txid_fund}#2")
        collateral_utxos2 = cluster.g_query.get_utxo(txin=f"{txid_fund}#3")

        # Step 2: spend the "locked" UTxOs

        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                script_file=plutus_op1.script_file,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op1.execution_cost.per_time,
                    plutus_op1.execution_cost.per_space,
                ),
                datum_file=plutus_op1.datum_file,
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                script_file=plutus_op2.script_file,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
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

        tx_output_redeem = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost1.fee + redeem_cost2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed_redeem)

        err_str = str(excinfo.value)
        assert rf"ScriptHash \"{script2_hash}\") fails" in err_str, err_str


@common.SKIPIF_PLUTUS_UNUSABLE
@pytest.mark.testnets
class TestNegativeRedeemer:
    """Tests for Tx output locking using Plutus smart contracts with wrong redeemer."""

    MAX_INT_VAL = (2**64) - 1
    MIN_INT_VAL = -MAX_INT_VAL
    AMOUNT = 2_000_000

    def _fund_script_guessing_game(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        plutus_version: str,
    ) -> FundTupleT:
        """Fund a Plutus script and create the locked UTxO and collateral UTxO."""
        payment_addrs = clusterlib_utils.create_payment_addr_records(
            *[f"{temp_template}_payment_addr_{i}" for i in range(2)],
            cluster_obj=cluster_obj,
        )

        # fund source address
        clusterlib_utils.fund_from_faucet(
            payment_addrs[0],
            cluster_obj=cluster_obj,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=3_000_000_000,
        )

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            execution_cost=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster_obj,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=self.AMOUNT,
        )

        return script_utxos, collateral_utxos, payment_addrs

    @pytest.fixture
    def fund_script_guessing_game_v1(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> FundTupleT:
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = common.get_test_id(cluster)

            script_utxos, collateral_utxos, payment_addrs = self._fund_script_guessing_game(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=temp_template,
                plutus_version="v1",
            )
            fixture_cache.value = script_utxos, collateral_utxos, payment_addrs

        return script_utxos, collateral_utxos, payment_addrs

    @pytest.fixture
    def fund_script_guessing_game_v2(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> FundTupleT:
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = common.get_test_id(cluster)

            script_utxos, collateral_utxos, payment_addrs = self._fund_script_guessing_game(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=temp_template,
                plutus_version="v2",
            )
            fixture_cache.value = script_utxos, collateral_utxos, payment_addrs

        return script_utxos, collateral_utxos, payment_addrs

    @pytest.fixture
    def cost_per_unit(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> plutus_common.ExecutionCost:
        return plutus_common.get_cost_per_unit(
            protocol_params=cluster.g_query.get_protocol_params()
        )

    def _failed_tx_build(
        self,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        redeemer_content: str,
        dst_addr: clusterlib.AddressRecord,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
    ) -> str:
        """Try to build a Tx and expect failure."""
        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            outfile.write(redeemer_content)

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=Path(redeemer_file),
            )
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_step2_tx.body",
                txouts=txouts,
                tx_files=tx_files,
                fee=fee_redeem + FEE_REDEEM_TXSIZE,
                script_txins=plutus_txins,
            )
        return str(excinfo.value)

    def _int_out_of_range(
        self,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        redeemer_value: int,
        dst_addr: clusterlib.AddressRecord,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
    ):
        """Try to spend a locked UTxO with redeemer int value that is not in allowed range."""
        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=redeemer_file if redeemer_content else "",
                redeemer_value=str(redeemer_value) if not redeemer_content else "",
            )
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_step2_tx.body",
                txouts=txouts,
                tx_files=tx_files,
                fee=fee_redeem + FEE_REDEEM_TXSIZE,
                script_txins=plutus_txins,
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
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with an unexpected redeemer value.

        Expect failure.
        """
        hypothesis.assume(redeemer_value != 42)

        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        # try to spend the "locked" UTxO

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=redeemer_file if redeemer_content else "",
                redeemer_value=str(redeemer_value) if not redeemer_content else "",
            )
        ]
        tx_raw_output = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee_redeem + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed)

        err_str = str(excinfo.value)
        assert "ValidationTagMismatch (IsValid True)" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(max_value=MIN_INT_VAL - 1))
    @hypothesis.example(redeemer_value=MIN_INT_VAL - 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_bellow_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a redeemer int value < minimum allowed value.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        self._int_out_of_range(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers(min_value=MAX_INT_VAL + 1))
    @hypothesis.example(redeemer_value=MAX_INT_VAL + 1)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_value_above_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a redeemer int value > maximum allowed value.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        self._int_out_of_range(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_wrong_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO with an invalid redeemer type.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value.hex()}, outfile)

        # try to spend the "locked" UTxO

        per_time = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_time
        per_space = plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.per_space

        fee_redeem = (
            round(per_time * cost_per_unit.per_time + per_space * cost_per_unit.per_space)
            + plutus_common.GUESSING_GAME_UNTYPED[plutus_version].execution_cost.fixed_cost
        )

        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED[plutus_version].script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    per_time,
                    per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=Path(redeemer_file),
            )
        ]

        tx_raw_output = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee_redeem + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(tx_file=tx_signed)

        err_str = str(excinfo.value)
        assert "ValidationTagMismatch (IsValid True)" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
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

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_content = json.dumps(
            {"constructor": 0, "fields": [{"bytes": redeemer_value.hex()}]}
        )

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert "must consist of at most 64 bytes" in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in typed format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_content = json.dumps({"constructor": 0, "fields": [{"int": redeemer_value.hex()}]})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "int" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.binary(max_size=64))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in untyped format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({"int": redeemer_value.hex()})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "int" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in typed format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({"constructor": 0, "fields": [{"bytes": redeemer_value}]})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "bytes" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        plutus_version: str,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in untyped format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        fund_script_guessing_game = (
            fund_script_guessing_game_v1 if plutus_version == "v1" else fund_script_guessing_game_v2
        )

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({"bytes": redeemer_value})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert 'field "bytes" does not have the type required by the schema' in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_value=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_invalid_json(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
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

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = f'{{"{redeemer_value}"}}'

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )
        assert "Invalid JSON format" in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_typed_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
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

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({redeemer_type: 42})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

        assert (
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err
        ), err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_json_schema_untyped_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game_v1: FundTupleT,
        fund_script_guessing_game_v2: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
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

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        redeemer_content = json.dumps({redeemer_type: 42})

        # try to build a Tx for spending the "locked" UTxO
        err = self._failed_tx_build(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_content=redeemer_content,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
            plutus_version=plutus_version,
        )

        assert (
            'Expected a single field named "int", "bytes", "string", "list" or "map".' in err
        ), err


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
            redeem_address = cluster.g_address.gen_payment_addr(
                addr_name=temp_template, payment_script_file=plutus_op.script_file
            )
        else:
            redeem_address = payment_addrs[2].address

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        txouts = [
            clusterlib.TxOut(
                address=redeem_address, amount=amount + redeem_cost.fee + FEE_REDEEM_TXSIZE
            ),
            clusterlib.TxOut(address=payment_addr.address, amount=redeem_cost.collateral),
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output_fund = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )
        txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.g_query.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.g_query.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "NonOutputSupplimentaryDatums" in err_str, err_str

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
        amount = 2_000_000
        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _fund_script(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                plutus_op=plutus_op,
                amount=amount,
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
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op_1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op_1.execution_cost  # for mypy

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op_1,
            amount=amount,
        )

        # use a wrong datum to try to unlock the funds
        plutus_op_2 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op_2,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "NonOutputSupplimentaryDatums" in err_str, err_str

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

        datum_hash = cluster.g_transaction.get_hash_script_data(script_data_file=datum_file)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=datum_file,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

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
        tx_files_fund = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files_fund,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        datum_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=dst_addr.address, datum_hash=datum_hash
        )[0]
        collateral_utxos = clusterlib.filter_utxos(
            utxos=out_utxos, address=payment_addr.address, utxo_ix=datum_utxo.utxo_ix + 1
        )
        assert (
            datum_utxo.datum_hash == datum_hash
        ), f"UTxO should have datum hash '{datum_hash}': {datum_utxo}"

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file, dst_addr.skey_file]
        )

        # try to spend the "locked" UTxO

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=[datum_utxo],
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount_redeem,
                tx_files=tx_files_redeem,
            )

        err_str = str(excinfo.value)
        assert "ExtraneousScriptWitnessesUTXOW" in err_str, err_str

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
        amount = 2_000_000
        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": datum_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _fund_script(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                plutus_op=plutus_op,
                amount=amount,
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
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
        )

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "PlutusScriptV2 is not supported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.ALONZO,
        reason="runs only with Tx era < Alonzo",
    )
    @pytest.mark.dbsync
    def test_plutusv1_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test spending a UTxO locked with PlutusV1 script using old Tx era.

        Expect failure.

        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_cbor_file=plutus_common.DATUM_42_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "PlutusScriptV1 is not supported" in err_str, err_str
