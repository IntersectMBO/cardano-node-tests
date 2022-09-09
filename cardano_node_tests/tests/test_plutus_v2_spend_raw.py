"""Tests for spending with Plutus V2 using `transaction build-raw`."""
import binascii
import json
import logging
import string
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
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.smoke,
]

# approx. fee for Tx size
FEE_REDEEM_TXSIZE = 400_000

PLUTUS_OP_ALWAYS_SUCCEEDS = plutus_common.PlutusOp(
    script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
)

PLUTUS_OP_GUESSING_GAME_UNTYPED = plutus_common.PlutusOp(
    script_file=plutus_common.GUESSING_GAME_UNTYPED["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.GUESSING_GAME_UNTYPED["v2"].execution_cost,
)

PLUTUS_OP_ALWAYS_FAILS = plutus_common.PlutusOp(
    script_file=plutus_common.ALWAYS_FAILS["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.ALWAYS_FAILS["v2"].execution_cost,
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
        amount=3_000_000_000,
    )

    return addrs


def _fund_script(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    amount: int,
    redeem_cost: plutus_common.ScriptCost,
    use_reference_script: bool = False,
    use_inline_datum: bool = False,
    collateral_amount: Optional[int] = None,
    tokens_collateral: Optional[
        List[plutus_common.Token]
    ] = None,  # tokens must already be in `payment_addr`
) -> Tuple[
    List[clusterlib.UTXOData],
    List[clusterlib.UTXOData],
    Optional[clusterlib.UTXOData],
    clusterlib.TxRawOutput,
]:
    """Fund a Plutus script and create the locked UTxO, collateral UTxO and reference script."""
    # pylint: disable=too-many-arguments

    script_address = cluster.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    # create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    txouts = [
        clusterlib.TxOut(
            address=script_address,
            amount=amount + redeem_cost.fee + FEE_REDEEM_TXSIZE,
            inline_datum_file=(
                plutus_op.datum_file if plutus_op.datum_file and use_inline_datum else ""
            ),
            inline_datum_value=(
                plutus_op.datum_value if plutus_op.datum_value and use_inline_datum else ""
            ),
            inline_datum_cbor_file=(
                plutus_op.datum_cbor_file if plutus_op.datum_cbor_file and use_inline_datum else ""
            ),
            datum_hash_file=(
                plutus_op.datum_file if plutus_op.datum_file and not use_inline_datum else ""
            ),
            datum_hash_value=(
                plutus_op.datum_value if plutus_op.datum_value and not use_inline_datum else ""
            ),
            datum_hash_cbor_file=(
                plutus_op.datum_cbor_file
                if plutus_op.datum_cbor_file and not use_inline_datum
                else ""
            ),
        ),
        # for collateral
        clusterlib.TxOut(
            address=dst_addr.address, amount=collateral_amount or redeem_cost.collateral
        ),
    ]

    # for reference script
    if use_reference_script:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
                reference_script_file=plutus_op.script_file,
            )
        )

    for token in tokens_collateral or []:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=token.amount,
                coin=token.coin,
            )
        )

    tx_raw_output = cluster.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=2,
        join_txouts=bool(tokens_collateral),
    )

    txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)

    script_utxos = cluster.get_utxo(txin=f"{txid}#0")
    assert script_utxos, "No script UTxO"

    collateral_utxos = cluster.get_utxo(txin=f"{txid}#1")
    assert collateral_utxos, "No collateral UTxO"

    reference_utxo = None
    if use_reference_script:
        reference_utxos = cluster.get_utxo(txin=f"{txid}#2")
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    if VERSIONS.transaction_era >= VERSIONS.BABBAGE:
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        # check if inline datum is returned by 'query utxo'
        if use_inline_datum:
            expected_datum = None
            if plutus_op.datum_file:
                with open(plutus_op.datum_file, encoding="utf-8") as json_datum:
                    expected_datum = json.load(json_datum)
            elif plutus_op.datum_value:
                expected_datum = plutus_op.datum_value

            assert (
                expected_datum is None or script_utxos[0].inline_datum == expected_datum
            ), "The inline datum returned by 'query utxo' is different than the expected"

    # check "transaction view"
    tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    return script_utxos, collateral_utxos, reference_utxo, tx_raw_output


def _build_reference_txin(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    amount: int,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: Optional[clusterlib.AddressRecord] = None,
) -> List[clusterlib.UTXOData]:
    """Create a basic txin to use as readonly reference input.

    Uses `cardano-cli transaction build-raw` command for building the transaction.
    """
    dst_addr = dst_addr or cluster.gen_payment_addr_and_keys(name=f"{temp_template}_readonly_input")

    txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    tx_raw_output = cluster.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
    )

    txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)

    reference_txin = cluster.get_utxo(txin=f"{txid}#0")
    assert reference_txin, "UTxO not created"

    return reference_txin


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestLockingV2:
    """Tests for Tx output locking using Plutus V2 smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("use_inline_datum", (True, False), ids=("inline_datum", "datum_file"))
    @pytest.mark.parametrize(
        "use_reference_script", (True, False), ids=("reference_script", "script_file")
    )
    def test_txout_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_inline_datum: bool,
        use_reference_script: bool,
        request: FixtureRequest,
    ):
        """Test combinations of inline datum and datum file + reference script and script file.

        * create the necessary Tx outputs
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected UTxOs were correctly spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        amount = 2_000_000

        plutus_op = PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # Step 1: fund the Plutus script

        script_utxos, collateral_utxos, reference_utxo, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_inline_datum=use_inline_datum,
            use_reference_script=use_reference_script,
        )
        assert reference_utxo or not use_reference_script, "No reference script UTxO"

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file if not use_reference_script else "",
                reference_txin=reference_utxo if use_reference_script else None,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2 if use_reference_script else "",
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=use_inline_datum,
                datum_file=plutus_op.datum_file if not use_inline_datum else "",
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
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
            cluster.get_address_balance(payment_addrs[1].address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{payment_addrs[1].address}`"

        # check that script address UTxO was spent
        assert not cluster.get_utxo(
            utxo=script_utxos[0]
        ), f"Script address UTxO was not spent `{script_utxos[0]}`"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.get_utxo(
            utxo=reference_utxo
        ), "Reference input was spent"

        # check expected fees
        expected_fee_redeem = 176_024 if use_reference_script else 179_764

        fee = (
            # for tx size
            cluster.estimate_fee(
                txbody_file=tx_output_redeem.out_file,
                txin_count=len(tx_output_redeem.txins),
                txout_count=len(tx_output_redeem.txouts),
                witness_count=len(tx_files_redeem.signing_key_files),
            )
            # for script execution
            + redeem_cost.fee
        )

        assert helpers.is_in_interval(fee, expected_fee_redeem, frac=0.15)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    def test_datum_bytes_in_dbsync(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test that datum bytes in db-sync corresponds to original datum.

        * create a Tx output with an inline datum at the script address
        * double-check that the UTxO datum hash corresponds to the datum CBOR file
        * check that datum from db-sync produces the original datum hash
        * check that datum bytes in db-sync corresponds to the original datum
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_cbor_file=plutus_common.DATUM_FINITE_TYPED_CBOR,
            redeemer_cbor_file=plutus_common.DATUM_FINITE_TYPED_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )
        assert plutus_op.execution_cost  # for mypy

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create a Tx output with an inline datum at the script address
        script_utxos, *__ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_inline_datum=True,
        )
        script_utxo = script_utxos[0]

        # double-check that the UTxO datum hash corresponds to the datum CBOR file
        datum_hash = cluster.get_hash_script_data(
            script_data_cbor_file=plutus_common.DATUM_FINITE_TYPED_CBOR
        )
        assert datum_hash == script_utxo.inline_datum_hash, "Unexpected datum hash"

        datum_db_response = list(
            dbsync_queries.query_datum(datum_hash=script_utxo.inline_datum_hash)
        )

        # check that datum from db-sync produces the original datum hash
        db_cbor_hex = datum_db_response[0].bytes.hex()
        db_cbor_bin = binascii.unhexlify(db_cbor_hex)
        db_cbor_file = f"{temp_template}_db_datum.cbor"
        with open(db_cbor_file, "wb") as out_fp:
            out_fp.write(db_cbor_bin)
        db_datum_hash = cluster.get_hash_script_data(script_data_cbor_file=db_cbor_file)
        assert (
            db_datum_hash == datum_hash
        ), "Datum hash of bytes in db-sync doesn't correspond to the original datum hash"

        # check that datum bytes in db-sync corresponds to the original datum
        with open(plutus_common.DATUM_FINITE_TYPED_CBOR, "rb") as in_fp:
            orig_cbor_bin = in_fp.read()
            orig_cbor_hex = orig_cbor_bin.hex()

        # see https://github.com/input-output-hk/cardano-db-sync/issues/1214
        assert (
            db_cbor_hex == orig_cbor_hex
        ), "Datum bytes in db-sync doesn't correspond to the original datum"


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestNegativeInlineDatum:
    """Tests for Tx output with inline datum that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.text())
    @common.hypothesis_settings()
    def test_lock_tx_invalid_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        datum_value: str,
    ):
        """Test locking a Tx output with an invalid datum.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # for mypy
        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create a Tx output with an invalid inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                amount=amount,
                redeem_cost=redeem_cost,
                use_inline_datum=True,
            )
        err_str = str(excinfo.value)
        assert "JSON object expected. Unexpected value" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_lock_tx_v1_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an inline datum and a v1 script.

        Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        script_utxos, collateral_utxos, __, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_inline_datum=True,
        )

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )

        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "InlineDatumsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_content=st.text(alphabet=string.ascii_letters, min_size=65))
    @common.hypothesis_settings()
    def test_lock_tx_big_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        datum_content: str,
    ):
        """Test locking a Tx output with a datum bigger than the allowed size.

        Expect failure.
        """
        hypothesis.assume(datum_content)
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_value=f'"{datum_content}"',
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # for mypy
        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                amount=amount,
                redeem_cost=redeem_cost,
                use_inline_datum=True,
            )
        err_str = str(excinfo.value)
        assert "Byte strings in script data must consist of at most 64 bytes" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_lock_tx_datum_as_witness(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test unlock a Tx output with a datum as witness.

        Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        script_utxos, collateral_utxos, __, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_inline_datum=True,
        )

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )

        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "NonOutputSupplimentaryDatums" in err_str, err_str


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestReferenceScripts:
    """Tests for Tx output locking using Plutus smart contracts with reference scripts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_same_script", (True, False), ids=("same_script", "multiple_script")
    )
    def test_reference_multiple_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_same_script: bool,
        request: FixtureRequest,
    ):
        """Test locking two Tx output with a V2 reference script and spending it.

        * create the Tx outputs with an inline datum at the script address
        * create the Tx outputs with the reference scripts
        * spend the locked UTxOs using the reference UTxOs
        * check that the UTxOs were correctly spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        plutus_op1 = PLUTUS_OP_ALWAYS_SUCCEEDS

        if use_same_script:
            plutus_op2 = PLUTUS_OP_GUESSING_GAME_UNTYPED
        else:
            plutus_op2 = PLUTUS_OP_ALWAYS_SUCCEEDS

        amount = 2_000_000

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op1.script_file,
            ),
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.get_utxo(txin=f"{txid}#1")
        reference_utxo1 = cluster.get_utxo(txin=f"{txid}#2")[0]
        reference_utxo2 = cluster.get_utxo(txin=f"{txid}#3")[0]
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid}#4")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid}#5")

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                reference_txin=reference_utxo1,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op1.execution_cost.per_time,
                    plutus_op1.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
                inline_datum_present=True,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                reference_txin=reference_utxo2,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.get_utxo(utxo=script_utxos1[0]) or cluster.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

    @allure.link(helpers.get_vcs_link())
    def test_reference_same_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking two Tx output with the same V2 reference script and spending it.

        * create the Tx outputs with an inline datum at the script address
        * create the Tx output with the reference script
        * spend the locked UTxOs using the reference UTxO
        * check that the UTxOs were correctly spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op.script_file
        )

        script_address_2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op.script_file
        )

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost.fee,
                inline_datum_file=plutus_op.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost.fee + FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
        ]

        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.get_utxo(txin=f"{txid}#1")
        reference_utxo = cluster.get_utxo(txin=f"{txid}#2")[0]
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid}#3")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid}#4")

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost.fee * 2 + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.get_utxo(utxo=script_utxos1[0]) or cluster.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

    @allure.link(helpers.get_vcs_link())
    def test_mix_reference_attached_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an attached V2 script and one using reference V2 script.

        * create the Tx output with an attached script
        * create the Tx output with the reference script
        * spend the locked UTxOs
        * check that the UTxOs were correctly spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op1 = PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                datum_hash_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.get_utxo(txin=f"{txid}#1")
        reference_utxo = cluster.get_utxo(txin=f"{txid}#2")[0]
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid}#3")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid}#4")

        #  spend the "locked" UTxO

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
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.submit_tx(
            tx_file=tx_signed_redeem,
            txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.get_utxo(utxo=script_utxos1[0]) or cluster.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("plutus_version", ("v1", "v2"), ids=("plutus_v1", "plutus_v2"))
    @pytest.mark.parametrize("address_type", ("shelley", "byron"))
    def test_spend_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
        address_type: str,
    ):
        """Test spending a UTxO that holds a reference script.

        * create a Tx output with reference script (reference script UTxO)
        * check that the expected amount was transferred
        * spend the UTxO
        * check that the UTxO was spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{address_type}"
        amount = 2_000_000

        script_file = plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file
        payment_addr = payment_addrs[0]

        reference_addr = payment_addrs[1]
        if address_type == "byron":
            # create reference UTxO on Byron address
            reference_addr = clusterlib_utils.gen_byron_addr(
                cluster_obj=cluster, name_template=temp_template
            )

        # create a Tx output with the reference script
        reference_utxo, __ = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=reference_addr,
            script_file=script_file,
            amount=amount,
        )
        assert reference_utxo.reference_script, "Reference script is missing"
        assert reference_utxo.amount == amount, "Incorrect amount transferred"

        # spend the Tx output with the reference script
        txouts = [clusterlib.TxOut(address=payment_addr.address, amount=-1)]
        tx_files = clusterlib.TxFiles(signing_key_files=[reference_addr.skey_file])

        cluster.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_spend",
            txins=[reference_utxo],
            txouts=txouts,
            tx_files=tx_files,
        )

        # check that reference script UTxO was spent
        assert not cluster.get_utxo(
            utxo=reference_utxo
        ), f"Reference script UTxO was NOT spent: '{reference_utxo}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("plutus_version", ("v1", "v2"), ids=("plutus_v1", "plutus_v2"))
    def test_spend_regular_utxo_and_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
        request: FixtureRequest,
    ):
        """Test spend an UTxO and use a reference a script on the same transaction.

        * create the reference script UTxO with the 'ALWAYS_FAILS' script to have confidence that
          the script was not being executed
        * spend a regular UTxO and reference the script at the same transaction
        * check that the destination UTxO have the right balance
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_FAILS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_FAILS[plutus_version].execution_cost,
        )

        # Step 1: create the reference script UTxO

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )

        txouts_step1 = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
                reference_script_file=plutus_op.script_file,
            )
        ]

        tx_output_step1 = cluster.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        reference_script = cluster.get_utxo(txin=f"{txid}#0")
        assert reference_script[0].reference_script, "No reference script UTxO"

        #  Step 2: spend an UTxO and reference the script

        txouts_step2 = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
            )
        ]

        tx_output_step2 = cluster.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step2,
            tx_files=tx_files,
        )

        txid = cluster.get_txid(tx_body_file=tx_output_step2.out_file)
        new_utxo = cluster.get_utxo(txin=f"{txid}#0")
        utxo_balance = clusterlib.calculate_utxos_balance(utxos=new_utxo)
        assert utxo_balance == amount, f"Incorrect balance for destination UTxO `{new_utxo}`"

    @allure.link(helpers.get_vcs_link())
    def test_reference_script_byron_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test creating reference script UTxO on Byron address.

        * create a Byron address
        * create a reference script UTxO on Byron address with the 'ALWAYS_FAILS' script to have
          confidence that the script was not being executed
        """
        temp_template = common.get_test_id(cluster)
        script_file = plutus_common.ALWAYS_FAILS_PLUTUS_V2

        byron_addr = clusterlib_utils.gen_byron_addr(
            cluster_obj=cluster, name_template=temp_template
        )

        # create reference UTxO
        reference_utxo, __ = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=byron_addr,
            script_file=script_file,
            amount=2_000_000,
        )

        assert reference_utxo.address == byron_addr.address, "Incorrect address for reference UTxO"
        assert reference_utxo.reference_script, "Reference script is missing"


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestNegativeReferenceScripts:
    """Tests for Tx output with reference scripts that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    def test_not_a_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an invalid reference script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.DATUM_42_TYPED,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                amount=amount,
                redeem_cost=redeem_cost,
            )
        err_str = str(excinfo.value)
        assert "Syntax error in script" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_two_scripts_one_fail(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking two Tx with different Plutus reference scripts in single Tx, one fails.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op1 = PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op1.script_file,
            ),
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.get_utxo(txin=f"{txid}#1")
        reference_utxo1 = cluster.get_utxo(txin=f"{txid}#2")[0]
        reference_utxo2 = cluster.get_utxo(txin=f"{txid}#3")[0]
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid}#4")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid}#5")

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos1,
                reference_txin=reference_utxo1,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos1,
                execution_units=(
                    plutus_op1.execution_cost.per_time,
                    plutus_op1.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op1.redeemer_cbor_file,
                inline_datum_present=True,
            ),
            clusterlib.ScriptTxIn(
                txins=script_utxos2,
                reference_txin=reference_utxo2,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        script2_hash = helpers.decode_bech32(bech32=script_address_2)[2:]
        assert rf"ScriptHash \"{script2_hash}\") fails" in str(excinfo.value) in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_lock_tx_v1_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus V1 reference script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # Step 1: fund the Plutus script

        script_utxos, collateral_utxos, reference_utxo, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_reference_script=True,
            use_inline_datum=True,
        )

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_v1_attached_v2_reference(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an attached V1 script and one using reference V2 script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

        plutus_op2 = PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_address_1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost_1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        redeem_cost_2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=amount + redeem_cost_1.fee,
                datum_hash_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=amount + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            join_txouts=False,
        )

        txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)
        script_utxos1 = cluster.get_utxo(txin=f"{txid}#0")
        script_utxos2 = cluster.get_utxo(txin=f"{txid}#1")
        reference_utxo = cluster.get_utxo(txin=f"{txid}#2")[0]
        collateral_utxos1 = cluster.get_utxo(txin=f"{txid}#3")
        collateral_utxos2 = cluster.get_utxo(txin=f"{txid}#4")

        #  spend the "locked" UTxO

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
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos2,
                execution_units=(
                    plutus_op2.execution_cost.per_time,
                    plutus_op2.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op2.redeemer_cbor_file,
                inline_datum_present=True,
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
            fee=redeem_cost_1.fee + redeem_cost_2.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_lock_byron_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus V2 reference script on Byron address.

        Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # Step 1: fund the Plutus script

        script_utxos, collateral_utxos, *__ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_reference_script=False,
            use_inline_datum=True,
        )

        # create reference UTxO on Byron address
        byron_addr = clusterlib_utils.gen_byron_addr(
            cluster_obj=cluster, name_template=temp_template
        )
        reference_utxo, __ = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=byron_addr,
            script_file=plutus_op.script_file,
            amount=2_000_000,
        )
        assert reference_utxo.address == byron_addr.address, "Incorrect address for reference UTxO"
        assert reference_utxo.reference_script, "Reference script is missing"

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                reference_txin=reference_utxo,
                reference_type=clusterlib.ScriptTypes.PLUTUS_V2,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                inline_datum_present=True,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(
                tx_file=tx_signed_redeem,
                txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins],
            )
        err_str = str(excinfo.value)
        assert "ByronTxOutInContext" in err_str, err_str


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("reference_input_scenario", ("single", "duplicated"))
    def test_use_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        reference_input_scenario: str,
    ):
        """Test use a reference input when unlock some funds.

        * create the necessary Tx outputs
        * use a reference input and spend the locked UTxO
        * check that the reference input was not spent
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{reference_input_scenario}"

        amount = 2_000_000

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=2_000_000,
            redeem_cost=redeem_cost,
        )

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        if reference_input_scenario == "single":
            readonly_reference_txins = reference_input
        else:
            readonly_reference_txins = reference_input * 2

        tx_raw_output = cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            readonly_reference_txins=readonly_reference_txins,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            join_txouts=False,
            script_txins=plutus_txins,
        )

        # check that the reference input was not spent
        reference_input_utxo = cluster.get_utxo(utxo=reference_input[0])
        assert (
            clusterlib.calculate_utxos_balance(utxos=reference_input_utxo) == amount
        ), f"The reference input was spent `{reference_input_utxo}`"

        # check that the reference input is present on 'transaction view'
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    def test_same_input_as_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test use a reference input that is also a regular input of the same transaction.

        * create the necessary Tx outputs
        * use a reference input that is also a regular input and spend the locked UTxO
        * check that the input was spent
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=2_000_000,
            redeem_cost=redeem_cost,
        )

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=amount,
        )

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2_tx.body",
            txins=reference_input,
            txouts=txouts_redeem,
            readonly_reference_txins=reference_input,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            join_txouts=False,
            script_txins=plutus_txins,
        )

        # check that the reference input was spent
        reference_input_utxo = cluster.get_utxo(utxo=reference_input[0])
        assert (
            not reference_input_utxo
        ), f"The reference input was not spent `{reference_input_utxo}`"

        # TODO check command 'transaction view' bug on cardano-node 4045


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestNegativeReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    def test_reference_spent_output(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test use a reference input that was already spent.

        Expect failure
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
        )

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=amount,
        )

        reference_utxo = f"{reference_input[0].utxo_hash}#{reference_input[0].utxo_ix}"

        #  spend the output that will be used as reference input

        cluster.send_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_spend_reference_input_tx.body",
            txins=reference_input,
            txouts=[clusterlib.TxOut(address=payment_addrs[0].address, amount=-1)],
            tx_files=clusterlib.TxFiles(signing_key_files=[payment_addrs[1].skey_file]),
        )

        # check that the input used also as reference was spent
        reference_input_utxo = cluster.get_utxo(txin=reference_utxo)

        assert not reference_input_utxo, f"The reference input was not spent `{reference_utxo}`"

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=amount),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2_tx.body",
                txouts=txouts_redeem,
                readonly_reference_txins=reference_input,
                tx_files=tx_files_redeem,
                fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
                join_txouts=False,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        # TODO improve error message cardano-node 4012
        assert (
            "TranslationLogicMissingInput (TxIn (TxId "
            f'{{_unTxId = SafeHash "{reference_input[0].utxo_hash}"}})' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    def test_v1_script_with_reference_input(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test use a reference input with a v1 Plutus script.

        Expect failure
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
        )

        # create the reference input
        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        #  spend the "locked" UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2_tx.body",
                txouts=txouts_redeem,
                tx_files=tx_files_redeem,
                fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
                readonly_reference_txins=reference_input,
                join_txouts=False,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    def test_reference_input_without_spend_anything(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test using a read-only reference input without spending any UTxO.

        Expect failure
        """
        temp_template = common.get_test_id(cluster)
        reference_input_amount = 2_000_000

        # create the necessary Tx outputs

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=reference_input_amount,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--babbage-era",
                    "--fee",
                    f"{200_000}",
                    "--read-only-tx-in-reference",
                    f"{reference_input[0].utxo_hash}#{reference_input[0].utxo_ix}",
                    "--tx-out",
                    f"{payment_addrs[1].address}+{2_000_000}",
                    "--out-file",
                    f"{temp_template}_tx.body",
                ]
            )
        err_str = str(excinfo.value)
        assert "Missing: (--tx-in TX-IN)" in err_str, err_str


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestCollateralOutput:
    """Tests for Tx output locking using Plutus with collateral output."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_return_collateral",
        (True, False),
        ids=("using_return_collateral", "without_return_collateral"),
    )
    @pytest.mark.parametrize(
        "use_total_collateral",
        (True, False),
        ids=("using_total_collateral", "without_total_collateral"),
    )
    def test_with_total_return_collateral(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_return_collateral: bool,
        use_total_collateral: bool,
        request: FixtureRequest,
    ):
        """Test failing script with combination of total and return collateral set.

        * fund the script address and create a UTxO for collateral
        * spend the locked UTxO
        * check that the expected amount of collateral was spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        amount = 2_000_000
        amount_for_collateral = (
            redeem_cost.collateral * 4 if use_return_collateral else redeem_cost.collateral
        )
        return_collateral_amount = amount_for_collateral - redeem_cost.collateral

        # fund the script address and create a UTxO for collateral

        script_utxos, collateral_utxos, *__ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            collateral_amount=amount_for_collateral,
        )

        dst_init_balance = cluster.get_address_balance(dst_addr.address)

        # try to spend the locked UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        txouts_return_collateral = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=return_collateral_amount,
            ),
        ]

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
            script_valid=False,
            return_collateral_txouts=txouts_return_collateral if use_return_collateral else (),
            total_collateral_amount=redeem_cost.collateral if use_total_collateral else None,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.submit_tx(
            tx_file=tx_signed_redeem,
            txins=collateral_utxos,
        )

        # check that the right amount of collateral was spent
        dst_balance = cluster.get_address_balance(dst_addr.address)
        assert (
            dst_balance == dst_init_balance - redeem_cost.collateral
        ), "The collateral amount charged was wrong `{collateral_utxos[0].address}`"

        if use_return_collateral:
            txid_redeem = cluster.get_txid(tx_body_file=tx_output_redeem.out_file)
            return_col_utxos = cluster.get_utxo(
                txin=f"{txid_redeem}#{tx_output_redeem.txouts_count}"
            )
            assert return_col_utxos, "Return collateral UTxO was not created"

            assert (
                clusterlib.calculate_utxos_balance(utxos=return_col_utxos)
                == return_collateral_amount
            ), f"Incorrect balance for collateral return address `{dst_addr.address}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    def test_collateral_with_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test failing script using collaterals with tokens.

        * create the token
        * fund the script address and create a UTxO for collateral
        * spend the locked UTxO
        * check that the expected amount of collateral was spent
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        amount = 2_000_000
        token_amount = 100

        amount_for_collateral = redeem_cost.collateral * 4
        return_collateral_amount = amount_for_collateral - redeem_cost.collateral

        # creat the token
        token_rand = clusterlib.get_rand_str(5)
        token = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}".encode("utf-8").hex()],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=token_amount,
        )
        tokens_rec = [plutus_common.Token(coin=token[0].token, amount=token[0].amount)]

        # fund the script address and create a UTxO for collateral
        script_utxos, collateral_utxos, *__ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            collateral_amount=amount_for_collateral,
            tokens_collateral=tokens_rec,
        )

        # spend the locked UTxO

        plutus_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                script_file=plutus_op.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_op.execution_cost.per_time,
                    plutus_op.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_op.redeemer_cbor_file,
                datum_file=plutus_op.datum_file,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]

        txouts_return_collateral = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount_for_collateral - redeem_cost.collateral,
            ),
            clusterlib.TxOut(
                address=dst_addr.address, amount=token_amount, coin=tokens_rec[0].coin
            ),
        ]

        tx_output_redeem = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
            script_valid=False,
            return_collateral_txouts=txouts_return_collateral,
            total_collateral_amount=redeem_cost.collateral,
            join_txouts=False,
        )
        tx_signed_redeem = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.submit_tx(
            tx_file=tx_signed_redeem,
            txins=collateral_utxos,
        )

        # check that the right amount of collateral was spent and that the tokens were returned

        txid_redeem = cluster.get_txid(tx_body_file=tx_output_redeem.out_file)
        return_col_utxos = cluster.get_utxo(txin=f"{txid_redeem}#{tx_output_redeem.txouts_count}")
        assert return_col_utxos, "Return collateral UTxO was not created"

        assert (
            clusterlib.calculate_utxos_balance(utxos=return_col_utxos) == return_collateral_amount
        ), f"Incorrect balance for collateral return address `{dst_addr.address}`"

        assert (
            clusterlib.calculate_utxos_balance(utxos=return_col_utxos, coin=tokens_rec[0].coin)
            == tokens_rec[0].amount
        ), f"Incorrect token balance for collateral return address `{dst_addr.address}`"

        # check "transaction view"
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)
        policyid, asset_name = token[0].token.split(".")
        tx_view_policy_key = f"policy {policyid}"
        tx_view_token_rec = tx_view_out["return collateral"]["amount"][tx_view_policy_key]
        tx_view_asset_key = next(iter(tx_view_token_rec))
        assert asset_name in tx_view_asset_key, "Token is missing from tx view return collateral"
        assert tx_view_token_rec[tx_view_asset_key] == token_amount, "Incorrect token amount"


@pytest.mark.testnets
class TestCompatibility:
    """Tests for checking compatibility with previous Tx eras."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.BABBAGE,
        reason="runs only with Tx era < Babbage",
    )
    def test_inline_datum_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with an inline datum using old Tx era.

        Expect failure with Alonzo-era Tx.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # for mypy
        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create a Tx output with an inline datum at the script address
        try:
            script_utxos, *__ = _fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                amount=amount,
                redeem_cost=redeem_cost,
                use_inline_datum=True,
            )
        except clusterlib.CLIError as exc:
            if "Inline datums cannot be used" not in str(exc):
                raise
            return

        assert script_utxos and not script_utxos[0].inline_datum, "Inline datum was not ignored"

        pytest.xfail("Inconsistent handling of Babbage-only features, see node issue #4424")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.BABBAGE,
        reason="runs only with Tx era < Babbage",
    )
    def test_reference_script_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a reference script using old Tx era."""
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        assert plutus_op.execution_cost

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        # create a Tx output with an inline datum at the script address
        __, __, reference_utxo, *__ = _fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            use_reference_script=True,
            use_inline_datum=False,
        )
        assert (
            reference_utxo and not reference_utxo.reference_script
        ), "Reference script was not ignored"

        pytest.xfail("Inconsistent handling of Babbage-only features, see node issue #4424")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.BABBAGE,
        reason="runs only with Tx era < Babbage",
    )
    def test_ro_reference_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test building Tx with read-only reference input using old Tx era.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        destinations = [clusterlib.TxOut(address=payment_addrs[1].address, amount=amount)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_tx(
                src_address=payment_addrs[0].address,
                tx_name=temp_template,
                txouts=destinations,
                readonly_reference_txins=reference_input,
                tx_files=tx_files,
            )
        err_str = str(excinfo.value)
        assert "Reference inputs cannot be used" in err_str, err_str
