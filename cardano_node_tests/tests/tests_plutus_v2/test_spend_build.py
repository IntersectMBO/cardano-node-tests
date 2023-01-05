"""Tests for spending with Plutus V2 using `transaction build`."""
import logging
from typing import List

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib
from cardano_clusterlib import txtools

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import spend_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
    common.SKIPIF_PLUTUSV2_UNUSABLE,
    pytest.mark.smoke,
    pytest.mark.plutus,
]


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
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        if use_reference_script and use_inline_datum:
            per_time = 171_623_997
            per_space = 548_658
            fixed_cost = 44_032
        elif use_reference_script and not use_inline_datum:
            per_time = 174_674_459
            per_space = 558_180
            fixed_cost = 44_802
        elif not use_reference_script and use_inline_datum:
            per_time = 140_633_161
            per_space = 452_332
            fixed_cost = 36_240
        else:
            per_time = 143_683_623
            per_space = 461_854
            fixed_cost = 37_009

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.GUESSING_GAME["v2"].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_TYPED_CBOR,
            execution_cost=plutus_common.ExecutionCost(
                per_time=per_time, per_space=per_space, fixed_cost=fixed_cost
            ),
        )

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        # create a Tx output with an inline datum at the script address

        (
            script_utxos,
            collateral_utxos,
            reference_utxo,
            tx_output_fund,
        ) = spend_build._build_fund_script(
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

        tx_output_redeem = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        plutus_costs = cluster.g_transaction.calculate_plutus_script_cost(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_redeem,
            txouts=txouts_redeem,
            script_txins=plutus_txins,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(
            tx_file=tx_signed, txins=[t.txins[0] for t in tx_output_redeem.script_txins if t.txins]
        )

        # check that script address UTxO was spent
        assert not cluster.g_query.get_utxo(
            utxo=script_utxos[0]
        ), f"Script address UTxO was NOT spent `{script_utxos}`"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.g_query.get_utxo(
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

        assert spend_build.PLUTUS_OP_GUESSING_GAME.execution_cost  # for mypy
        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[spend_build.PLUTUS_OP_GUESSING_GAME.execution_cost],
            frac=0.2,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("use_inline_datum", (True, False), ids=("inline_datum", "datum_file"))
    @pytest.mark.parametrize("use_token", (True, False), ids=("with_token", "without_token"))
    @pytest.mark.parametrize(
        "use_reference_script",
        (True, False),
        ids=("with_reference_script", "without_reference_script"),
    )
    def test_min_required_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_inline_datum: bool,
        use_token: bool,
        use_reference_script: bool,
        request: FixtureRequest,
    ):
        """Test minimum required UTxO in different scenarios with v2 functionalities.

        * create the necessary Tx outputs
        * check the min required UTxO
        """
        test_scenario = request.node.callspec.id
        temp_template = f"{common.get_test_id(cluster)}_{test_scenario}"

        expected_min_required_utxo = {
            "with_reference_script-with_token-inline_datum": 9_848_350,
            "with_reference_script-with_token-datum_file": 9_956_100,
            "with_reference_script-without_token-inline_datum": 9_650_090,
            "with_reference_script-without_token-datum_file": 9_757_840,
            "without_reference_script-with_token-inline_datum": 1_107_670,
            "without_reference_script-with_token-datum_file": 1_215_420,
            "without_reference_script-without_token-inline_datum": 909_410,
            "without_reference_script-without_token-datum_file": 1_017_160,
        }

        plutus_op = spend_build.PLUTUS_OP_GUESSING_GAME

        # for mypy
        assert plutus_op.datum_file

        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=plutus_op.script_file
        )

        # create a Tx outputs

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address,
                amount=expected_min_required_utxo[test_scenario],
                inline_datum_file=plutus_op.datum_file if use_inline_datum else "",
                datum_hash_file=plutus_op.datum_file if not use_inline_datum else "",
                reference_script_file=plutus_op.script_file if use_reference_script else "",
            )
        ]

        if use_token:
            # create the token
            token_rand = clusterlib.get_rand_str(5)
            token = clusterlib_utils.new_tokens(
                *[f"qacoin{token_rand}".encode().hex()],
                cluster_obj=cluster,
                temp_template=f"{temp_template}_{token_rand}",
                token_mint_addr=payment_addrs[0],
                issuer_addr=payment_addrs[0],
                amount=100,
            )

            txouts = [
                *txouts,
                clusterlib.TxOut(
                    address=script_address,
                    amount=10,
                    coin=token[0].token,
                    inline_datum_file=plutus_op.datum_file if use_inline_datum else "",
                    datum_hash_file=plutus_op.datum_file if not use_inline_datum else "",
                    reference_script_file=plutus_op.script_file if use_reference_script else "",
                ),
                # TODO: add ADA txout for change address - see node issue #3057
                clusterlib.TxOut(address=payment_addrs[0].address, amount=2_000_000),
            ]

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addrs[0].address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files,
            txouts=txouts,
            fee_buffer=2_000_000,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        joined_txouts = txtools.get_joined_txouts(txouts=txouts)
        min_required_utxo = cluster.g_transaction.calculate_min_req_utxo(
            txouts=joined_txouts[0]
        ).value

        assert helpers.is_in_interval(
            min_required_utxo, expected_min_required_utxo[test_scenario], frac=0.15
        )
