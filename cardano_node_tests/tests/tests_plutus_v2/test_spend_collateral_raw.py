"""Tests for collateral while spending with Plutus V2 using `transaction build-raw`."""
import logging
from typing import List

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import spend_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)

pytestmark = [
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
        amount=3_000_000_000,
    )

    return addrs


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

        plutus_op = spend_raw.PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        amount = 2_000_000
        amount_for_collateral = (
            redeem_cost.collateral * 4 if use_return_collateral else redeem_cost.collateral
        )
        return_collateral_amount = amount_for_collateral - redeem_cost.collateral

        # fund the script address and create a UTxO for collateral

        script_utxos, collateral_utxos, *__ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op,
            amount=amount,
            redeem_cost=redeem_cost,
            collateral_amount=amount_for_collateral,
        )

        dst_init_balance = cluster.g_query.get_address_balance(dst_addr.address)

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

        tx_output_redeem = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
            script_valid=False,
            return_collateral_txouts=txouts_return_collateral if use_return_collateral else (),
            total_collateral_amount=redeem_cost.collateral if use_total_collateral else None,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed_redeem,
            txins=collateral_utxos,
        )

        # check that the right amount of collateral was spent
        dst_balance = cluster.g_query.get_address_balance(dst_addr.address)
        assert (
            dst_balance == dst_init_balance - redeem_cost.collateral
        ), "The collateral amount charged was wrong `{collateral_utxos[0].address}`"

        if use_return_collateral:
            txid_redeem = cluster.g_transaction.get_txid(tx_body_file=tx_output_redeem.out_file)
            return_col_utxos = cluster.g_query.get_utxo(
                txin=f"{txid_redeem}#{tx_output_redeem.txouts_count}"
            )
            assert return_col_utxos, "Return collateral UTxO was NOT created"

            assert (
                clusterlib.calculate_utxos_balance(utxos=return_col_utxos)
                == return_collateral_amount
            ), f"Incorrect balance for collateral return address `{dst_addr.address}`"

        # check "transaction view"
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_redeem)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
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

        plutus_op = spend_raw.PLUTUS_OP_ALWAYS_FAILS

        # for mypy
        assert plutus_op.execution_cost
        assert plutus_op.datum_file
        assert plutus_op.redeemer_cbor_file

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        amount = 2_000_000
        token_amount = 100

        amount_for_collateral = redeem_cost.collateral * 4
        return_collateral_amount = amount_for_collateral - redeem_cost.collateral

        # create the token
        token_rand = clusterlib.get_rand_str(5)
        token = clusterlib_utils.new_tokens(
            *[f"qacoin{token_rand}".encode().hex()],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{token_rand}",
            token_mint_addr=payment_addr,
            issuer_addr=payment_addr,
            amount=token_amount,
        )
        tokens_rec = [plutus_common.Token(coin=token[0].token, amount=token[0].amount)]

        # fund the script address and create a UTxO for collateral
        script_utxos, collateral_utxos, *__ = spend_raw._fund_script(
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

        tx_output_redeem = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_redeem,
            tx_files=tx_files_redeem,
            fee=redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
            script_txins=plutus_txins,
            script_valid=False,
            return_collateral_txouts=txouts_return_collateral,
            total_collateral_amount=redeem_cost.collateral,
            join_txouts=False,
        )
        tx_signed_redeem = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_redeem.out_file,
            signing_key_files=tx_files_redeem.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.g_transaction.submit_tx(
            tx_file=tx_signed_redeem,
            txins=collateral_utxos,
        )

        # check that the right amount of collateral was spent and that the tokens were returned

        txid_redeem = cluster.g_transaction.get_txid(tx_body_file=tx_output_redeem.out_file)
        return_col_utxos = cluster.g_query.get_utxo(
            txin=f"{txid_redeem}#{tx_output_redeem.txouts_count}"
        )
        assert return_col_utxos, "Return collateral UTxO was NOT created"

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
