"""Tests for spending with Plutus V2 using `transaction build`."""
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
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# skip all tests if Tx era < babbage
pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
    pytest.mark.smoke,
]

PLUTUS_OP_ALWAYS_SUCCEEDS = plutus_common.PlutusOp(
    script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
    datum_file=plutus_common.DATUM_42,
    redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
    execution_cost=plutus_common.ALWAYS_SUCCEEDS["v2"].execution_cost,
)

PLUTUS_OP_GUESSING_GAME = plutus_common.PlutusOp(
    script_file=plutus_common.GUESSING_GAME["v2"].script_file,
    datum_file=plutus_common.DATUM_42_TYPED,
    redeemer_cbor_file=plutus_common.REDEEMER_42_TYPED_CBOR,
    execution_cost=plutus_common.GUESSING_GAME["v2"].execution_cost,
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
        amount=1_000_000_000,
    )

    return addrs


def _build_fund_script(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    plutus_op: plutus_common.PlutusOp,
    use_reference_script: bool = False,
    use_inline_datum: bool = True,
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
    """Fund a Plutus script and create the locked UTxO and collateral UTxO and reference script.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    # pylint: disable=too-many-arguments

    # for mypy
    assert plutus_op.execution_cost

    script_fund = 200_000_000

    script_address = cluster.gen_payment_addr(
        addr_name=temp_template, payment_script_file=plutus_op.script_file
    )

    redeem_cost = plutus_common.compute_cost(
        execution_cost=plutus_op.execution_cost, protocol_params=cluster.get_protocol_params()
    )

    # create a Tx output with a datum hash at the script address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    txouts = [
        clusterlib.TxOut(
            address=script_address,
            amount=script_fund,
            inline_datum_file=(
                plutus_op.datum_file if plutus_op.datum_file and use_inline_datum else ""
            ),
            inline_datum_value=(
                plutus_op.datum_value if plutus_op.datum_value and use_inline_datum else ""
            ),
            datum_hash_file=(
                plutus_op.datum_file if plutus_op.datum_file and not use_inline_datum else ""
            ),
            datum_hash_value=(
                plutus_op.datum_value if plutus_op.datum_value and not use_inline_datum else ""
            ),
        ),
        # for collateral
        clusterlib.TxOut(
            address=dst_addr.address, amount=collateral_amount or redeem_cost.collateral
        ),
    ]

    if use_reference_script:
        txouts.append(
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=10_000_000,
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

    tx_output = cluster.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=2_000_000,
        join_txouts=bool(tokens_collateral),
    )
    tx_signed = cluster.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )
    cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    script_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    assert script_utxos, "No script UTxO"

    collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)
    assert collateral_utxos, "No collateral UTxO"

    reference_utxo = None
    if use_reference_script:
        reference_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 2)
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    if VERSIONS.transaction_era >= VERSIONS.BABBAGE:
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

        # check if inline datum is returned by 'query utxo'
        if use_inline_datum:
            if plutus_op.datum_file:
                with open(plutus_op.datum_file, encoding="utf-8") as json_datum:
                    expected_datum = json.load(json_datum)
            else:
                expected_datum = plutus_op.datum_value

            assert (
                script_utxos[0].inline_datum == expected_datum
            ), "The inline datum returned by 'query utxo' is different than the expected"

    # check "transaction view"
    tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)

    return script_utxos, collateral_utxos, reference_utxo, tx_output


def _build_reference_txin(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    amount: int,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: Optional[clusterlib.AddressRecord] = None,
) -> List[clusterlib.UTXOData]:
    """Create a basic txin to use as readonly reference input.

    Uses `cardano-cli transaction build` command for building the transaction.
    """
    dst_addr = dst_addr or cluster.gen_payment_addr_and_keys(name=f"{temp_template}_readonly_input")

    txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    tx_output = cluster.build_tx(
        src_address=payment_addr.address,
        tx_name=temp_template,
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=1_000_000,
    )
    tx_signed = cluster.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=temp_template,
    )
    cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    reference_txin = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    assert reference_txin, "UTxO not created"

    return reference_txin


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestBuildLocking:
    """Tests for Tx output locking using Plutus V2 functionalities and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("use_inline_datum", (True, False), ids=("inline_datum", "datum_file"))
    @pytest.mark.parametrize(
        "use_reference_script", (True, False), ids=("reference_script", "script_file")
    )
    @pytest.mark.dbsync
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
        * spend the locked UTxO
        * check that the expected UTxOs were correctly spent
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        plutus_op = PLUTUS_OP_GUESSING_GAME

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_utxos, collateral_utxos, reference_utxo, tx_output_fund = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=use_inline_datum,
            use_reference_script=use_reference_script,
        )
        assert reference_utxo or not use_reference_script, "No reference script UTxO"

        #  spend the "locked" UTxO

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        plutus_costs = cluster.calculate_plutus_script_cost(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that script address UTxO was spent
        assert not cluster.get_utxo(
            utxo=script_utxos[0]
        ), f"Script address UTxO was NOT spent `{script_utxos}`"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.get_utxo(
            utxo=reference_utxo
        ), "Reference input was spent"

        # check expected fees
        if use_reference_script:
            expected_fee_fund = 258_913
            expected_fee_redeem = 213_889
        else:
            expected_fee_fund = 167_965
            expected_fee_redeem = 293_393

        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)
        assert helpers.is_in_interval(tx_output_redeem.fee, expected_fee_redeem, frac=0.15)

        assert PLUTUS_OP_GUESSING_GAME.execution_cost  # for mypy
        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[PLUTUS_OP_GUESSING_GAME.execution_cost],
            frac=0.2,
        )


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestInlineDatum:
    """Tests for Tx output with inline datum."""

    @allure.link(helpers.get_vcs_link())
    def test_check_inline_datum_cost(self, cluster: clusterlib.ClusterLib):
        """Check that the min UTxO value with an inline datum depends on the size of the datum.

        * calculate the min UTxO value with a small datum, using both inline and hash
        * calculate the min UTxO value with a big datum, using both inline and hash
        * check that the min UTxO value with an inline datum depends on datum size
        """
        temp_template = common.get_test_id(cluster)

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS
        assert plutus_op.datum_file

        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=plutus_op.script_file
        )

        # small datum

        txouts_with_small_inline_datum = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                inline_datum_file=plutus_op.datum_file,
            )
        ]

        min_utxo_small_inline_datum = cluster.calculate_min_req_utxo(
            txouts=txouts_with_small_inline_datum
        )

        small_datum_hash = cluster.get_hash_script_data(script_data_file=plutus_op.datum_file)

        txouts_with_small_datum_hash = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                datum_hash=small_datum_hash,
            )
        ]

        min_utxo_small_datum_hash = cluster.calculate_min_req_utxo(
            txouts=txouts_with_small_datum_hash
        )

        # big datum

        txouts_with_big_inline_datum = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                inline_datum_file=plutus_common.DATUM_BIG,
            )
        ]

        min_utxo_big_inline_datum = cluster.calculate_min_req_utxo(
            txouts=txouts_with_big_inline_datum
        )

        big_datum_hash = cluster.get_hash_script_data(script_data_file=plutus_common.DATUM_BIG)

        txouts_with_big_datum_hash = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                datum_hash=big_datum_hash,
            )
        ]

        min_utxo_big_datum_hash = cluster.calculate_min_req_utxo(txouts=txouts_with_big_datum_hash)

        # check that the min UTxO value with an inline datum depends on the size of the datum

        assert (
            min_utxo_small_inline_datum < min_utxo_small_datum_hash
            and min_utxo_big_inline_datum > min_utxo_big_datum_hash
            and min_utxo_big_inline_datum > min_utxo_small_inline_datum
        ), "The min UTxO value doesn't correspond to the inline datum size"


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

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # create a Tx output with an invalid inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
            )
        err_str = str(excinfo.value)
        assert "JSON object expected. Unexpected value" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
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

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V1,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_COST,
        )

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address
        script_utxos, collateral_utxos, __, __ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
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
                inline_datum_present=True,
            )
        ]

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[1].skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert (
            "Error translating the transaction context: InlineDatumsNotSupported" in err_str
        ), err_str

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

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_value=f'"{datum_content}"',
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
            )
        err_str = str(excinfo.value)
        assert "Byte strings in script data must consist of at most 64 bytes" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_lock_tx_datum_as_witness(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test unlock a Tx output with a datum as witness.

        Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address
        script_utxos, collateral_utxos, __, __ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(
                tx_file=tx_signed,
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

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 100_000_000

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
                amount=script_fund,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
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

        tx_output = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        reference_utxo2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 5)

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
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

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 200_000_000

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
                address=script_address_1, amount=script_fund, inline_datum_file=plutus_op.datum_file
            ),
            clusterlib.TxOut(
                address=script_address_2, amount=script_fund, inline_datum_file=plutus_op.datum_file
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost.collateral),
        ]

        tx_output = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
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

        plutus_op1 = PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = PLUTUS_OP_GUESSING_GAME_UNTYPED

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create the necessary UTxOs

        script_fund = 100_000_000

        script_address_1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=script_fund,
                datum_hash_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)

        #  spend the "locked" UTxOs

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        plutus_costs = cluster.calculate_plutus_script_cost(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that script address UTxOs were spent
        assert not (
            cluster.get_utxo(utxo=script_utxos1[0]) or cluster.get_utxo(utxo=script_utxos2[0])
        ), f"Script address UTxOs were NOT spent - `{script_utxos1}` and `{script_utxos2}`"

        # check that the script hash is included for all scripts
        for script in plutus_costs:
            assert script.get(
                "scriptHash"
            ), "Missing script hash on calculate-plutus-script-cost result"

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

        tx_output = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txins=[reference_utxo],
            tx_files=tx_files,
            txouts=txouts,
            witness_override=2,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        # check that reference script utxo was spent
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

        tx_output_step1 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts_step1,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        out_utxos_step1 = cluster.get_utxo(tx_raw_output=tx_output_step1)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos_step1, txouts=tx_output_step1.txouts
        )
        reference_script = clusterlib.filter_utxos(utxos=out_utxos_step1, utxo_ix=utxo_ix_offset)
        assert reference_script[0].reference_script, "No reference script UTxO"

        #  Step 2: spend an UTxO and reference the script

        txouts_step2 = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
            )
        ]

        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files,
            txouts=txouts_step2,
            readonly_reference_txins=reference_script,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=tx_output_step2.txins)

        out_utxos_step2 = cluster.get_utxo(tx_raw_output=tx_output_step2)
        new_utxo = clusterlib.filter_utxos(utxos=out_utxos_step2, utxo_ix=utxo_ix_offset)
        utxo_balance = clusterlib.calculate_utxos_balance(utxos=new_utxo)
        assert utxo_balance == amount, f"Incorrect balance for destination UTxO `{new_utxo}`"


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

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.DATUM_42_TYPED,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # create a Tx output with an inline datum at the script address

        with pytest.raises(clusterlib.CLIError) as excinfo:
            _build_fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
                use_reference_script=True,
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

        plutus_op1 = PLUTUS_OP_ALWAYS_SUCCEEDS
        plutus_op2 = PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 100_000_000

        script_address_1 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr1", payment_script_file=plutus_op1.script_file
        )

        script_address_2 = cluster.gen_payment_addr(
            addr_name=f"{temp_template}_addr2", payment_script_file=plutus_op2.script_file
        )

        redeem_cost1 = plutus_common.compute_cost(
            execution_cost=plutus_op1.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        redeem_cost2 = plutus_common.compute_cost(
            execution_cost=plutus_op2.execution_cost, protocol_params=cluster.get_protocol_params()
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address_1,
                amount=script_fund,
                inline_datum_file=plutus_op1.datum_file,
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op1.script_file,
            ),
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=10_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost2.collateral),
        ]

        tx_output = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        reference_utxo2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 5)

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert "The Plutus script evaluation failed" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_lock_tx_v1_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a Plutus V1 reference script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

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

        # create a Tx output with an inline datum at the script address

        script_utxos, collateral_utxos, reference_utxo, __ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_reference_script=True,
        )
        assert reference_utxo, "No reference script UTxO"

        #  spend the "locked" UTxO

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
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

        * create the Tx output with an attached V1 script
        * create the Tx output with the reference V2 script
        * spend the locked UTxOs
        * check that the UTxOs were correctly spent
        """
        temp_template = common.get_test_id(cluster)

        plutus_op1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS["v1"].execution_cost,
        )

        plutus_op2 = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op1.execution_cost and plutus_op2.execution_cost
        assert plutus_op1.datum_file and plutus_op2.datum_file
        assert plutus_op1.redeemer_cbor_file and plutus_op2.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_fund = 200_000_000

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
                address=script_address_1, amount=script_fund, datum_hash_file=plutus_op1.datum_file
            ),
            clusterlib.TxOut(
                address=script_address_2,
                amount=script_fund,
                inline_datum_file=plutus_op2.datum_file,
            ),
            # for reference script
            clusterlib.TxOut(
                address=payment_addrs[1].address,
                amount=2_000_000,
                reference_script_file=plutus_op2.script_file,
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_1.collateral),
            clusterlib.TxOut(address=payment_addrs[1].address, amount=redeem_cost_2.collateral),
        ]

        tx_output = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
            join_txouts=False,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        fund_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=fund_utxos, txouts=tx_output.txouts
        )
        script_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset)
        script_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 1)
        reference_utxo = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 2)[0]
        collateral_utxos1 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 3)
        collateral_utxos2 = clusterlib.filter_utxos(utxos=fund_utxos, utxo_ix=utxo_ix_offset + 4)

        #  spend the "locked" UTxO

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert "ReferenceInputsNotSupported" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
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

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        script_utxos, collateral_utxos, *__ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_reference_script=False,
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

        #  spend the "locked" UTxO

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
            clusterlib.TxOut(address=payment_addrs[1].address, amount=-1),
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        assert "ByronTxOutInContext" in err_str, err_str


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("reference_input_scenario", ("single", "duplicated"))
    @pytest.mark.dbsync
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
        * (optional) check transactions in db-sync
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        reference_input_amount = 2_000_000

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=reference_input_amount,
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

        if reference_input_scenario == "single":
            readonly_reference_txins = reference_input
        else:
            readonly_reference_txins = reference_input * 2

        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            readonly_reference_txins=readonly_reference_txins,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that the reference input was not spent
        assert cluster.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

        expected_redeem_fee = 172_578
        assert helpers.is_in_interval(
            tx_output_redeem.fee, expected_redeem_fee, frac=0.15
        ), "Expected fee doesn't match the actual fee"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_same_input_as_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test use a reference input that is also a regular input of the same transaction.

        * create the necessary Tx outputs
        * use a reference input that is also a regular input and spend the locked UTxO
        * check that input was spent
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        reference_input_amount = 2_000_000

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=reference_input_amount,
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

        tx_output_redeem = cluster.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            txins=reference_input,
            tx_files=tx_files_redeem,
            readonly_reference_txins=reference_input,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that the input used also as reference was spent
        assert not cluster.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was NOT spent `{reference_input[0]}`"

        # TODO check command 'transaction view' bug on cardano-node 4045

    @allure.link(helpers.get_vcs_link())
    def test_use_same_reference_input_multiple_times(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test 2 transactions using the same reference input in the same block.

        * create the UTxO that will be used as readonly reference input
        * create the transactions using the same readonly reference input
        * submit both transactions
        * check that the readonly reference input was not spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        # fund payment address
        clusterlib_utils.fund_from_faucet(
            payment_addrs[1],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        # create the reference input

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=amount,
        )

        #  build 2 tx using the same readonly reference input

        tx_address_combinations = [
            {"payment_addr": payment_addrs[0], "dst_addr": payment_addrs[1]},
            {"payment_addr": payment_addrs[1], "dst_addr": payment_addrs[0]},
        ]

        tx_to_submit = []
        txins = []

        for addr in tx_address_combinations:
            txouts = [clusterlib.TxOut(address=addr["dst_addr"].address, amount=amount)]
            tx_files = clusterlib.TxFiles(signing_key_files=[addr["payment_addr"].skey_file])

            tx_output = cluster.build_tx(
                src_address=addr["payment_addr"].address,
                tx_name=f"{temp_template}_{addr['payment_addr'].address}_tx",
                tx_files=tx_files,
                txouts=txouts,
                readonly_reference_txins=reference_input,
            )

            tx_signed = cluster.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_{addr['payment_addr'].address}_signed",
            )

            tx_to_submit.append(tx_signed)

            txins += tx_output.txins

        for tx_signed in tx_to_submit:
            cluster.submit_tx_bare(tx_file=tx_signed)

        clusterlib_utils.check_txins_spent(cluster_obj=cluster, txins=txins)

        # check that the reference input was not spent
        assert cluster.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_reference_input_non_plutus(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test using a read-only reference input in non-Plutus transaction.

        * use a reference input in normal non-Plutus transaction
        * check that the reference input was not spent
        * (optional) check transactions in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=src_addr,
            amount=amount,
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]

        tx_output = cluster.build_tx(
            src_address=src_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            readonly_reference_txins=reference_input,
            tx_files=tx_files,
        )
        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        # check that the reference input was not spent
        assert cluster.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was spent `{reference_input[0]}`"

        # check expected balances
        out_utxos = cluster.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestNegativeReadonlyReferenceInputs:
    """Tests for Tx with readonly reference inputs that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
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

        plutus_op = PLUTUS_OP_ALWAYS_SUCCEEDS

        reference_input_amount = 2_000_000

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            amount=reference_input_amount,
        )

        #  spend the output that will be used as reference input

        tx_output_spend_reference_input = cluster.build_tx(
            src_address=payment_addrs[1].address,
            tx_name=f"{temp_template}_step2",
            txins=reference_input,
            txouts=[clusterlib.TxOut(address=payment_addrs[0].address, amount=-1)],
        )

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_spend_reference_input.out_file,
            signing_key_files=[payment_addrs[1].skey_file],
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_output_spend_reference_input.txins)

        # check that the input used also as reference was spent
        assert not cluster.get_utxo(
            utxo=reference_input[0]
        ), f"The reference input was NOT spent `{reference_input[0]}`"

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
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                readonly_reference_txins=reference_input,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
            )
        err_str = str(excinfo.value)
        # TODO improve error message cardano-node 4012
        assert (
            "TranslationLogicMissingInput (TxIn (TxId "
            f'{{_unTxId = SafeHash "{reference_input[0].utxo_hash}"}})' in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_v1_script_with_reference_input(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test use a reference input with a v1 Plutus script.

        Expect failure
        """
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

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

        # create the necessary Tx outputs

        script_utxos, collateral_utxos, __, __ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_inline_datum=False,
        )

        # create the reference input

        reference_input = _build_reference_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            amount=2_000_000,
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
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_redeem,
                readonly_reference_txins=reference_input,
                txouts=txouts_redeem,
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
                    "build",
                    "--read-only-tx-in-reference",
                    f"{reference_input[0].utxo_hash}#{reference_input[0].utxo_ix}",
                    "--change-address",
                    payment_addrs[0].address,
                    "--tx-out",
                    f"{payment_addrs[1].address}+{2_000_000}",
                    "--out-file",
                    f"{temp_template}_tx.body",
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *cluster.tx_era_arg,
                ]
            )
        err_str = str(excinfo.value)
        assert "Missing: (--tx-in TX-IN)" in err_str, err_str


@common.SKIPIF_PLUTUSV2_UNUSABLE
@pytest.mark.testnets
class TestCollateralOutput:
    """Tests for Tx output locking using Plutus with collateral output."""

    def _build_spend_locked_txin(
        self,
        temp_template: str,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        dst_addr: clusterlib.AddressRecord,
        script_utxos: List[clusterlib.UTXOData],
        collateral_utxos: List[clusterlib.UTXOData],
        plutus_op: plutus_common.PlutusOp,
        total_collateral_amount: Optional[int] = None,
        return_collateral_txouts: clusterlib.OptionalTxOuts = (),
    ) -> clusterlib.TxRawOutput:
        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.redeemer_cbor_file

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
            signing_key_files=[payment_addr.skey_file, dst_addr.skey_file],
        )
        txouts_redeem = [
            clusterlib.TxOut(address=dst_addr.address, amount=2_000_000),
        ]
        # include any payment txin
        txins = [
            r
            for r in cluster.get_utxo(address=payment_addr.address, coins=[clusterlib.DEFAULT_COIN])
            if not (r.datum_hash or r.inline_datum_hash)
        ][:1]

        err_str = ""
        try:
            tx_output_redeem = cluster.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                txins=txins,
                tx_files=tx_files_redeem,
                txouts=txouts_redeem,
                script_txins=plutus_txins,
                return_collateral_txouts=return_collateral_txouts,
                total_collateral_amount=total_collateral_amount,
                change_address=payment_addr.address,
                script_valid=False,
            )
        except clusterlib.CLIError as err:
            err_str = str(err)

        # TODO: broken on node 1.35.0 and 1.35.1
        if "ScriptWitnessIndexTxIn 0 is missing from the execution units" in err_str:
            pytest.xfail("See cardano-node issue #4013")

        tx_signed = cluster.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed, txins=collateral_utxos)

        return tx_output_redeem

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
    @pytest.mark.dbsync
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

        protocol_params = cluster.get_protocol_params()

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost, protocol_params=protocol_params
        )

        # fund the script address and create a UTxO for collateral

        amount_for_collateral = (
            redeem_cost.collateral * 4 if use_return_collateral else redeem_cost.collateral
        )
        return_collateral_amount = amount_for_collateral - redeem_cost.collateral

        script_utxos, collateral_utxos, *__ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            collateral_amount=amount_for_collateral,
        )

        #  spend the "locked" UTxO

        return_collateral_txouts = [
            clusterlib.TxOut(dst_addr.address, amount=return_collateral_amount)
        ]

        tx_output_redeem = self._build_spend_locked_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            total_collateral_amount=redeem_cost.collateral if use_total_collateral else None,
            return_collateral_txouts=return_collateral_txouts if use_return_collateral else (),
        )

        # check that collateral was taken
        assert not cluster.get_utxo(utxo=collateral_utxos), "Collateral was NOT spent"

        # check that input UTxOs were not spent
        assert cluster.get_utxo(utxo=tx_output_redeem.txins), "Payment UTxO was spent"
        assert cluster.get_utxo(utxo=script_utxos), "Script UTxO was spent"

        return_collateral_utxos = cluster.get_utxo(tx_raw_output=tx_output_redeem)

        # when total collateral amount is specified, it is necessary to specify also return
        # collateral `TxOut`
        if use_total_collateral and not use_return_collateral:
            assert not return_collateral_utxos, "Return collateral UTxO was unexpectedly created"
            return

        # check "transaction view"
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

        # TODO: automatic return collateral is not supported on 1.35.3 and older
        if not (
            use_return_collateral or use_total_collateral or "return collateral" in tx_view_out
        ):
            return

        # check that correct return collateral UTxO was created
        assert return_collateral_utxos, "Return collateral UTxO was NOT created"

        # check that return collateral is the only output and that the index matches
        out_utxos_ix = {r.utxo_ix for r in return_collateral_utxos}
        assert len(out_utxos_ix) == 1, "There are other outputs other than return collateral"
        # TODO: the index of change can be either 0 (in old node versions) or `txouts_count`,
        # that affects index of return collateral UTxO
        assert return_collateral_utxos[0].utxo_ix in (
            tx_output_redeem.txouts_count,
            tx_output_redeem.txouts_count + 1,
        )

        returned_collateral_amount = clusterlib.calculate_utxos_balance(
            utxos=return_collateral_utxos
        )

        if use_return_collateral:
            assert (
                returned_collateral_amount == return_collateral_amount
            ), f"Incorrect balance for collateral return address `{dst_addr.address}`"
            assert (
                return_collateral_txouts[0].address == return_collateral_utxos[0].address
            ), "Return collateral address doesn't match the specified address"
        else:
            # check that the collateral amount charged corresponds to 'collateralPercentage'
            collateral_charged = amount_for_collateral - returned_collateral_amount
            assert collateral_charged == round(
                tx_output_redeem.fee * protocol_params["collateralPercentage"] / 100
            ), "The collateral amount charged is not the expected amount"

            assert (
                payment_addr.address == return_collateral_utxos[0].address
            ), "Return collateral address doesn't match change address"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_collateral_with_tokens(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test failing script using collaterals with tokens.

        * create the token
        * fund the script address and create a UTxO for collateral
        * spend the locked UTxO
        * check that the expected amount of collateral was spent
        """
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

        token_amount = 100
        amount_for_collateral = redeem_cost.collateral * 4
        return_collateral_amount = amount_for_collateral - redeem_cost.collateral

        # create the token
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

        script_utxos, collateral_utxos, *__ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            collateral_amount=amount_for_collateral,
            tokens_collateral=tokens_rec,
        )

        #  spend the "locked" UTxO

        txouts_return_collateral = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=return_collateral_amount,
            ),
            clusterlib.TxOut(
                address=dst_addr.address, amount=token_amount, coin=tokens_rec[0].coin
            ),
        ]

        tx_output_redeem = self._build_spend_locked_txin(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            script_utxos=script_utxos,
            collateral_utxos=collateral_utxos,
            plutus_op=plutus_op,
            total_collateral_amount=redeem_cost.collateral,
            return_collateral_txouts=txouts_return_collateral,
        )

        # check that collateral was taken
        assert not cluster.get_utxo(utxo=collateral_utxos), "Collateral was NOT spent"

        # check that input UTxOs were not spent
        assert cluster.get_utxo(utxo=tx_output_redeem.txins), "Payment UTxO was spent"
        assert cluster.get_utxo(utxo=script_utxos), "Script UTxO was spent"

        # check that the right amount of collateral was spent and that the tokens were returned

        return_collateral_utxos = cluster.get_utxo(tx_raw_output=tx_output_redeem)

        assert return_collateral_utxos, "Return collateral UTxO was NOT created"

        # check that return collateral is the only output and that the index matches
        out_utxos_ix = {r.utxo_ix for r in return_collateral_utxos}
        assert len(out_utxos_ix) == 1, "There are other outputs other than return collateral"
        # TODO: the index of change can be either 0 (in old node versions) or `txouts_count`,
        # that affects index of return collateral UTxO
        assert return_collateral_utxos[0].utxo_ix in (
            tx_output_redeem.txouts_count,
            tx_output_redeem.txouts_count + 1,
        )

        assert (
            clusterlib.calculate_utxos_balance(utxos=return_collateral_utxos)
            == return_collateral_amount
        ), f"Incorrect balance for collateral return address `{dst_addr.address}`"

        assert (
            clusterlib.calculate_utxos_balance(
                utxos=return_collateral_utxos, coin=tokens_rec[0].coin
            )
            == tokens_rec[0].amount
        ), f"Incorrect token balance for collateral return address `{dst_addr.address}`"

        # check "transaction view"
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)
        # TODO: "return collateral" is not present in the transaction view in 1.35.3 and older
        if "return collateral" in tx_view_out:
            policyid, asset_name = token[0].token.split(".")
            tx_view_policy_key = f"policy {policyid}"
            tx_view_token_rec = tx_view_out["return collateral"]["amount"][tx_view_policy_key]
            tx_view_asset_key = next(iter(tx_view_token_rec))
            assert (
                asset_name in tx_view_asset_key
            ), "Token is missing from tx view return collateral"
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

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # create a Tx output with an inline datum at the script address
        try:
            script_utxos, *__ = _build_fund_script(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
            )
        except clusterlib.CLIError as exc:
            if "Inline datums cannot be used" not in str(exc):
                raise
            return

        assert script_utxos and not script_utxos[0].inline_datum, "Inline datum was NOT ignored"

        pytest.xfail("Inconsistent handling of Babbage-only features, see node issue #4424")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era >= VERSIONS.BABBAGE,
        reason="runs only with Tx era < Babbage",
    )
    @pytest.mark.dbsync
    def test_reference_script_old_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test locking a Tx output with a reference script using old Tx era."""
        __: Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS_V2_COST,
        )

        # create a Tx output with an inline datum at the script address
        __, __, reference_utxo, *__ = _build_fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op,
            use_reference_script=True,
            use_inline_datum=False,
        )
        assert (
            reference_utxo and not reference_utxo.reference_script
        ), "Reference script was NOT ignored"

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
        txouts = [clusterlib.TxOut(address=payment_addrs[1].address, amount=amount)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=txouts,
                readonly_reference_txins=reference_input,
            )
        err_str = str(excinfo.value)
        assert "Reference inputs cannot be used" in err_str, err_str
