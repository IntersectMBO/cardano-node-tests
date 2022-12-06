"""Negative tests for minting with Plutus V2 using `transaction build-raw`."""
import logging
from typing import List

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import mint_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

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
    """Create new payment address."""
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
class TestNegativeCollateralOutput:
    """Tests for collateral output that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    def test_minting_with_limited_collateral(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a Plutus script with limited collateral amount.

        Expect failure.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script while limiting the usable collateral amount
        * check that the minting failed because insufficient collateral amount was provided
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        collateral_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, *__ = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
        )

        # Step 2: mint the "qacoin"

        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=mint_utxos[0].utxo_hash,
            utxo_ix=1,
            amount=collateral_amount,
            address=issuer_addr.address,
        )

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V2,
                collaterals=[collateral_utxo],
                execution_units=(
                    plutus_common.MINTING_COST.per_time,
                    plutus_common.MINTING_COST.per_space,
                ),
                redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        # limit the amount of collateral that can be used and balance the return collateral txout
        total_collateral_amount = minting_cost.min_collateral // 2
        return_collateral_txouts = [
            clusterlib.TxOut(
                payment_addr.address, amount=collateral_amount - total_collateral_amount
            )
        ]

        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # it should NOT be possible to mint with a collateral with insufficient funds
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        err_str = str(excinfo.value)
        assert "InsufficientCollateral" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "with_return_collateral",
        (True, False),
        ids=("with_return_collateral", "without_return_collateral"),
    )
    def test_minting_with_unbalanced_total_collateral(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        with_return_collateral: bool,
        request: FixtureRequest,
    ):
        """Test minting a token with a Plutus script with unbalanced total collateral.

        Expect failure.
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, *__ = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
        )

        # Step 2: mint the "qacoin"

        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=mint_utxos[0].utxo_hash,
            utxo_ix=1,
            amount=minting_cost.collateral,
            address=issuer_addr.address,
        )

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V2,
                collaterals=[collateral_utxo],
                execution_units=(
                    plutus_common.MINTING_COST.per_time,
                    plutus_common.MINTING_COST.per_space,
                ),
                redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        return_collateral_txouts = [
            clusterlib.TxOut(payment_addr.address, amount=minting_cost.collateral)
        ]

        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            return_collateral_txouts=return_collateral_txouts if with_return_collateral else (),
            total_collateral_amount=minting_cost.collateral // 2,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # it should NOT be possible to mint with an unbalanced total collateral
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        err_str = str(excinfo.value)
        assert "IncorrectTotalCollateralField" in err_str, err_str
