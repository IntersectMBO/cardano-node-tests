"""Tests for spending with Plutus using `transaction build-raw`."""
import functools
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
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_management
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
    pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALONZO,
        reason="runs only with Alonzo+ TX",
    ),
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
        *[f"{test_id}_payment_addr_{i}" for i in range(2)],
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
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Fund a Plutus script and create the locked UTxO and collateral UTxO."""
    # pylint: disable=too-many-locals,too-many-arguments
    assert plutus_op.execution_cost  # for mypy

    stokens = tokens or ()
    ctokens = tokens_collateral or ()

    script_address = cluster_obj.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost,
        protocol_params=cluster_obj.get_protocol_params(),
        collateral_fraction_offset=collateral_fraction_offset,
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
        clusterlib.TxOut(
            address=script_address,
            amount=amount + redeem_cost.fee + fee_txsize + deposit_amount,
            datum_hash=datum_hash,
        ),
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

    txid = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)

    script_utxos = cluster_obj.get_utxo(txin=f"{txid}#0")
    assert script_utxos, "No script UTxO"

    script_utxos_lovelace = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]
    script_lovelace_balance = functools.reduce(lambda x, y: x + y.amount, script_utxos_lovelace, 0)
    assert (
        script_lovelace_balance == txouts[0].amount
    ), f"Incorrect balance for script address `{script_address}`"

    collateral_utxos = cluster_obj.get_utxo(txin=f"{txid}#1")
    assert collateral_utxos, "No collateral UTxO"

    collateral_utxos_lovelace = [u for u in collateral_utxos if u.coin == clusterlib.DEFAULT_COIN]
    collateral_lovelace_balance = functools.reduce(
        lambda x, y: x + y.amount, collateral_utxos_lovelace, 0
    )
    assert (
        collateral_lovelace_balance == redeem_cost.collateral
    ), f"Incorrect balance for collateral address `{dst_addr.address}`"

    for token in stokens:
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        script_token_balance = functools.reduce(lambda x, y: x + y.amount, script_utxos_token, 0)
        assert (
            script_token_balance == token.amount
        ), f"Incorrect token balance for script address `{script_address}`"

    for token in ctokens:
        collateral_utxos_token = [u for u in collateral_utxos if u.coin == token.coin]
        collateral_token_balance = functools.reduce(
            lambda x, y: x + y.amount, collateral_utxos_token, 0
        )
        assert (
            collateral_token_balance == token.amount
        ), f"Incorrect token balance for address `{dst_addr.address}`"

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
        execution_cost=plutus_op.execution_cost, protocol_params=cluster_obj.get_protocol_params()
    )

    script_utxos_lovelace = [u for u in script_utxos if u.coin == clusterlib.DEFAULT_COIN]
    script_lovelace_balance = functools.reduce(lambda x, y: x + y.amount, script_utxos_lovelace, 0)

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
        script_utxos_token = [u for u in script_utxos if u.coin == token.coin]
        script_token_balance = functools.reduce(lambda x, y: x + y.amount, script_utxos_token, 0)
        if script_token_balance > token.amount:
            txouts.append(
                clusterlib.TxOut(
                    address=change_rec.address,
                    amount=script_token_balance - token.amount,
                    coin=token.coin,
                    datum_hash=change_rec.datum_hash,
                )
            )

    tx_raw_output = cluster_obj.build_raw_tx_bare(
        out_file=f"{temp_template}_step2_tx.body",
        txouts=txouts,
        tx_files=tx_files,
        fee=redeem_cost.fee + fee_txsize,
        script_txins=plutus_txins,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        script_valid=script_valid,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step2",
    )

    if not submit_tx:
        return "", tx_raw_output

    dst_init_balance = cluster_obj.get_address_balance(dst_addr.address)

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

        return "", tx_raw_output

    if expect_failure:
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.submit_tx_bare(tx_file=tx_signed)
        err = str(excinfo.value)
        assert (
            cluster_obj.get_address_balance(dst_addr.address) == dst_init_balance
        ), f"Collateral was spent from `{dst_addr.address}`"

        for u in script_utxos_lovelace:
            assert cluster_obj.get_utxo(
                txin=f"{u.utxo_hash}#{u.utxo_ix}", coins=[clusterlib.DEFAULT_COIN]
            ), f"Inputs were unexpectedly spent for `{u.address}`"

        return err, tx_raw_output

    cluster_obj.submit_tx(
        tx_file=tx_signed, txins=[t.txins[0] for t in tx_raw_output.script_txins if t.txins]
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
    tx_view.check_tx_view(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return "", tx_raw_output


def _check_pretty_utxo(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> str:
    """Check that pretty printed `query utxo` output looks as expected."""
    err = ""
    txid = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)

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
        "ScriptDataInAlonzoEra",
        f'"{tx_raw_output.txouts[0].datum_hash}"',
    ]

    if utxo_out != expected_out:
        err = f"Pretty UTxO output doesn't match expected output:\n{utxo_out}\nvs\n{expected_out}"

    return err


class TestLocking:
    """Tests for Tx output locking using Plutus smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txout_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus script and spending the locked UTxO.

        Corresponds to Exercise 3 for Alonzo Blue.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
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
    @pytest.mark.testnets
    def test_context_equivalance(
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
        deposit_amount = cluster.get_address_deposit()

        # create stake address registration cert
        stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
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
        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=pool_users[0].payment,
            dst_addr=pool_users[1].payment,
            plutus_op=plutus_op_dummy,
            amount=amount,
            deposit_amount=deposit_amount,
        )

        invalid_hereafter = cluster.get_slot_no() + 1_000

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
    @pytest.mark.testnets
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
    def test_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        variant: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{variant}"
        amount = 2_000_000

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

        script_utxos, collateral_utxos, __ = _fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
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
    @pytest.mark.testnets
    def test_two_scripts_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

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
        fee_fund = cluster.calculate_tx_fee(
            src_address=payment_addrs[0].address,
            txouts=txouts_fund,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_fund,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_output_fund = cluster.build_raw_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_fund,
            tx_files=tx_files_fund,
            fee=fee_fund,
            join_txouts=False,
        )
        tx_signed_fund = cluster.sign_tx(
            tx_body_file=tx_output_fund.out_file,
            signing_key_files=tx_files_fund.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )

        cluster.submit_tx(tx_file=tx_signed_fund, txins=tx_output_fund.txins)

        txid_fund = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid_fund}#0", coins=[clusterlib.DEFAULT_COIN])
        script_utxos2 = cluster.get_utxo(txin=f"{txid_fund}#1", coins=[clusterlib.DEFAULT_COIN])
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid_fund}#2")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid_fund}#3")

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
        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost1.fee + redeem_cost2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
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

        # check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
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
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS,
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
        assert "PlutusFailure" in err

        # wait a bit so there's some time for error messages to appear in log file
        time.sleep(1 if cluster.network_magic == configuration.NETWORK_MAGIC_LOCAL else 5)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
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
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS,
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
        _spend_locked_txin(
            temp_template=temp_template,
            cluster_obj=cluster,
            dst_addr=payment_addrs[1],
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            amount=amount,
            script_valid=False,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txout_token_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with native tokens and spending the locked UTxO.

        * create a Tx output that contains native tokens with a datum hash at the script address
        * check that expected amounts of Lovelace and native tokens were locked at the script
          address
        * spend the locked UTxO
        * check that the expected amounts of Lovelace and native tokens were spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)

        amount = 2_000_000
        token_amount = 100

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
    @pytest.mark.testnets
    def test_partial_spending(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
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
        temp_template = common.get_test_id(cluster)

        token_rand = clusterlib.get_rand_str(5)

        amount_fund = 6_000_000
        amount_spend = 2_000_000
        token_amount_fund = 100
        token_amount_spend = 20

        # add extra fee for tokens
        fee_redeem_txsize = FEE_REDEEM_TXSIZE + 5_000

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

        txid_spend = cluster.get_txid(tx_body_file=tx_output_spend.out_file)
        change_utxos = cluster.get_utxo(txin=f"{txid_spend}#1")

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
    @pytest.mark.parametrize("scenario", ("max", "max+1", "none"))
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collaterals(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        scenario: str,
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
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        max_collateral_ins = cluster.get_protocol_params()["maxCollateralInputs"]
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
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
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
            tx_output_collaterals = cluster.send_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_collaterals",
                txouts=txouts_collaterals,
                tx_files=tx_files_collaterals,
                join_txouts=False,
            )
            txid_collaterals = cluster.get_txid(tx_body_file=tx_output_collaterals.out_file)
            _utxos_nested = [
                cluster.get_utxo(txin=f"{txid_collaterals}#{i}") for i in range(collateral_num)
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
            assert exp_err in str(excinfo.value)
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
    def test_invalid_guessing_game(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        variant: str,
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
        temp_template = f"{common.get_test_id(cluster)}_{variant}"
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
            script_file=plutus_common.GUESSING_GAME_PLUTUS,
            datum_file=datum_file,
            redeemer_file=redeemer_file,
            execution_cost=plutus_common.GUESSING_GAME_COST,
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

        assert "ValidationTagMismatch (IsValid True)" in err

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

        * create a collateral UTxO with native tokens
        * try to spend the locked UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        token_rand = clusterlib.get_rand_str(5)

        amount = 2_000_000
        token_amount = 100

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
        assert "CollateralContainsNonADA" in str(excinfo.value)

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

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * try to spend the locked UTxO while using the same UTxO as collateral
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_cbor_file=plutus_common.DATUM_42_CBOR,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
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
        assert "cardano-cli transaction submit" in err_str
        assert "ScriptsNotPaidUTxO" in err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_no_datum_txout(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        Expect failure.

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
            clusterlib.TxOut(
                address=payment_addr.address, amount=amount + redeem_cost.fee + FEE_REDEEM_TXSIZE
            ),
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
            _spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )
        assert "NonOutputSupplimentaryDatums" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_collateral_percent(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Try to spend locked UTxO while collateral is less than required by `collateralPercentage`.

        Expect failure.

        * create a Tx output with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * create a collateral UTxO with amount of ADA less than required by `collateralPercentage`
        * try to spend the UTxO
        * check that the expected error was raised
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        # increase fixed cost so the required collateral is higher than minimum collateral of 2 ADA
        execution_cost = plutus_common.ALWAYS_SUCCEEDS_COST._replace(fixed_cost=2_000_000)
        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=execution_cost,
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
        assert "InsufficientCollateral" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_two_scripts_spending_one_fail(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking two Tx outputs with two different Plutus scripts in single Tx, one fails.

        Expect failure.

        * create a Tx output with a datum hash at the script addresses
        * try to spend the locked UTxOs
        * check that the expected error was raised
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        protocol_params = cluster.get_protocol_params()

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )
        plutus_op2 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS_PLUTUS,
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
        script2_hash = helpers.decode_bech32(bech32=script_address2)[2:]
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
        fee_fund = cluster.calculate_tx_fee(
            src_address=payment_addrs[0].address,
            txouts=txouts_fund,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_fund,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_output_fund = cluster.build_raw_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_fund,
            tx_files=tx_files_fund,
            fee=fee_fund,
            join_txouts=False,
        )
        tx_signed_fund = cluster.sign_tx(
            tx_body_file=tx_output_fund.out_file,
            signing_key_files=tx_files_fund.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )

        cluster.submit_tx(tx_file=tx_signed_fund, txins=tx_output_fund.txins)

        txid_fund = cluster.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid_fund}#0", coins=[clusterlib.DEFAULT_COIN])
        script_utxos2 = cluster.get_utxo(txin=f"{txid_fund}#1", coins=[clusterlib.DEFAULT_COIN])
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid_fund}#2")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid_fund}#3")

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

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost1.fee + redeem_cost2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx_bare(tx_file=tx_signed_redeem)
        assert rf"ScriptHash \"{script2_hash}\") fails" in str(excinfo.value)


@pytest.mark.testnets
class TestNegativeRedeemer:
    """Tests for Tx output locking using Plutus smart contracts with wrong redeemer."""

    MAX_INT_VAL = (2**64) - 1
    MIN_INT_VAL = -MAX_INT_VAL
    AMOUNT = 2_000_000

    @pytest.fixture
    def fund_script_guessing_game(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> FundTupleT:
        """Fund a Plutus script and create the locked UTxO and collateral UTxO."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = common.get_test_id(cluster)

            payment_addrs = clusterlib_utils.create_payment_addr_records(
                *[f"{temp_template}_payment_addr_{i}" for i in range(2)],
                cluster_obj=cluster,
            )

            # fund source address
            clusterlib_utils.fund_from_faucet(
                payment_addrs[0],
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=3_000_000_000,
            )

            plutus_op = plutus_common.PlutusOp(
                script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
                datum_file=plutus_common.DATUM_42,
                execution_cost=plutus_common.GUESSING_GAME_UNTYPED_COST,
            )

            script_utxos, collateral_utxos, __ = _fund_script(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                amount=self.AMOUNT,
            )

            fixture_cache.value = script_utxos, collateral_utxos, payment_addrs

        return script_utxos, collateral_utxos, payment_addrs

    @pytest.fixture
    def cost_per_unit(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> plutus_common.ExecutionCost:
        return plutus_common.get_cost_per_unit(protocol_params=cluster.get_protocol_params())

    def _failed_tx_build(
        self,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        redeemer_content: str,
        dst_addr: clusterlib.AddressRecord,
        cost_per_unit: plutus_common.ExecutionCost,
    ) -> str:
        """Try to build a Tx and expect failure."""
        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            outfile.write(redeemer_content)

        fee_redeem = (
            round(
                plutus_common.GUESSING_GAME_UNTYPED_COST.per_time * cost_per_unit.per_time
                + plutus_common.GUESSING_GAME_UNTYPED_COST.per_space * cost_per_unit.per_space
            )
            + plutus_common.GUESSING_GAME_UNTYPED_COST.fixed_cost
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_time,
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=Path(redeemer_file),
            )
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.build_raw_tx_bare(
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
    ):
        """Try to spend a locked UTxO with redeemer int value that is not in allowed range."""
        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        fee_redeem = (
            round(
                plutus_common.GUESSING_GAME_UNTYPED_COST.per_time * cost_per_unit.per_time
                + plutus_common.GUESSING_GAME_UNTYPED_COST.per_space * cost_per_unit.per_space
            )
            + plutus_common.GUESSING_GAME_UNTYPED_COST.fixed_cost
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_time,
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=redeemer_file if redeemer_content else "",
                redeemer_value=str(redeemer_value) if not redeemer_content else "",
            )
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.build_raw_tx_bare(
                out_file=f"{temp_template}_step2_tx.body",
                txouts=txouts,
                tx_files=tx_files,
                fee=fee_redeem + FEE_REDEEM_TXSIZE,
                script_txins=plutus_txins,
            )
        assert "Value out of range within the script data" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers(min_value=MIN_INT_VAL, max_value=MAX_INT_VAL))
    @common.hypothesis_settings()
    def test_wrong_value_inside_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        redeemer_value: int,
        cost_per_unit: plutus_common.ExecutionCost,
    ):
        """Try to spend a locked UTxO with an unexpected redeemer value.

        Expect failure.
        """
        hypothesis.assume(redeemer_value != 42)

        temp_template = f"test_wrong_value_inside_range_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_content = {}
        if redeemer_value % 2 == 0:
            redeemer_content = {"int": redeemer_value}

        redeemer_file = f"{temp_template}.redeemer"
        if redeemer_content:
            with open(redeemer_file, "w", encoding="utf-8") as outfile:
                json.dump(redeemer_content, outfile)

        # try to spend the "locked" UTxO

        fee_redeem = (
            round(
                plutus_common.GUESSING_GAME_UNTYPED_COST.per_time * cost_per_unit.per_time
                + plutus_common.GUESSING_GAME_UNTYPED_COST.per_space * cost_per_unit.per_space
            )
            + plutus_common.GUESSING_GAME_UNTYPED_COST.fixed_cost
        )

        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_time,
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=redeemer_file if redeemer_content else "",
                redeemer_value=str(redeemer_value) if not redeemer_content else "",
            )
        ]
        tx_raw_output = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee_redeem + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx_bare(tx_file=tx_signed)

        assert "ValidationTagMismatch (IsValid True)" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers(max_value=MIN_INT_VAL - 1))
    @common.hypothesis_settings()
    def test_wrong_value_bellow_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a redeemer int value < minimum allowed value.

        Expect failure.
        """
        temp_template = f"test_wrong_value_bellow_range_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        self._int_out_of_range(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers(min_value=MAX_INT_VAL + 1))
    @common.hypothesis_settings()
    def test_wrong_value_above_range(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: int,
    ):
        """Try to spend a locked UTxO with a redeemer int value > maximum allowed value.

        Expect failure.
        """
        temp_template = f"test_wrong_value_above_range_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game
        self._int_out_of_range(
            cluster_obj=cluster,
            temp_template=temp_template,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            redeemer_value=redeemer_value,
            dst_addr=payment_addrs[1],
            cost_per_unit=cost_per_unit,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.binary())
    @common.hypothesis_settings()
    def test_wrong_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: bytes,
    ):
        """Try to spend a locked UTxO with an invalid redeemer type.

        Expect failure.
        """
        temp_template = f"test_wrong_type_ci{cluster.cluster_id}"

        script_utxos, collateral_utxos, payment_addrs = fund_script_guessing_game

        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": redeemer_value.hex()}, outfile)

        # try to spend the "locked" UTxO

        fee_redeem = (
            round(
                plutus_common.GUESSING_GAME_UNTYPED_COST.per_time * cost_per_unit.per_time
                + plutus_common.GUESSING_GAME_UNTYPED_COST.per_space * cost_per_unit.per_space
            )
            + plutus_common.GUESSING_GAME_UNTYPED_COST.fixed_cost
        )

        dst_addr = payment_addrs[1]

        tx_files = clusterlib.TxFiles(signing_key_files=[dst_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=self.AMOUNT)]

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_common.GUESSING_GAME_UNTYPED_PLUTUS,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_time,
                    plutus_common.GUESSING_GAME_UNTYPED_COST.per_space,
                ),
                datum_file=plutus_common.DATUM_42,
                redeemer_file=Path(redeemer_file),
            )
        ]

        tx_raw_output = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts,
            tx_files=tx_files,
            fee=fee_redeem + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx_bare(tx_file=tx_signed)

        assert "ValidationTagMismatch (IsValid True)" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.binary())
    @common.hypothesis_settings()
    def test_json_schema_typed_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in typed format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"test_json_schema_typed_int_ci{cluster.cluster_id}"

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
        )
        assert 'field "int" does not have the type required by the schema' in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.binary())
    @common.hypothesis_settings()
    def test_json_schema_untyped_int_bytes_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: bytes,
    ):
        """Try to build a Tx using byte string for redeemer when JSON schema specifies int.

        Redeemer is in untyped format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"test_json_schema_untyped_int_ci{cluster.cluster_id}"

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
        )
        assert 'field "int" does not have the type required by the schema' in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings()
    def test_json_schema_typed_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in typed format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"test_json_schema_typed_bytes_ci{cluster.cluster_id}"

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
        )
        assert 'field "bytes" does not have the type required by the schema' in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.integers())
    @common.hypothesis_settings()
    def test_json_schema_untyped_bytes_int_declared(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: int,
    ):
        """Try to build a Tx using int value for redeemer when JSON schema specifies byte string.

        Redeemer is in untyped format and the value doesn't comply to JSON schema. Expect failure.
        """
        temp_template = f"test_json_schema_untyped_bytes_ci{cluster.cluster_id}"

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
        )
        assert 'field "bytes" does not have the type required by the schema' in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_value=st.text())
    @common.hypothesis_settings()
    def test_invalid_json(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_value: str,
    ):
        """Try to build a Tx using a redeemer value that is invalid JSON.

        Expect failure.
        """
        temp_template = f"test_invalid_json_ci{cluster.cluster_id}"

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
        )
        assert "Invalid JSON format" in err

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings()
    def test_json_schema_typed_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON typed schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"test_json_schema_typed_invalid_type_ci{cluster.cluster_id}"

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
        )

        assert 'Expected a single field named "int", "bytes", "string", "list" or "map".' in str(
            err
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @hypothesis.given(redeemer_type=st.text())
    @common.hypothesis_settings()
    def test_json_schema_untyped_invalid_type(
        self,
        cluster: clusterlib.ClusterLib,
        fund_script_guessing_game: FundTupleT,
        cost_per_unit: plutus_common.ExecutionCost,
        redeemer_type: str,
    ):
        """Try to build a Tx using a JSON untyped schema that specifies an invalid type.

        Expect failure.
        """
        temp_template = f"test_json_schema_typed_invalid_type_ci{cluster.cluster_id}"

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
        )

        assert 'Expected a single field named "int", "bytes", "string", "list" or "map".' in str(
            err
        )
