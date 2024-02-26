"""Negative tests for minting with Plutus V2 using `transaction build`."""
import logging
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import mint_build
from cardano_node_tests.utils import clusterlib_utils
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
) -> tp.List[clusterlib.AddressRecord]:
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
    @pytest.mark.parametrize(
        "with_return_collateral",
        (True, False),
        ids=("with_return_collateral", "without_return_collateral"),
    )
    @common.PARAM_PLUTUS2ONWARDS_VERSION
    def test_minting_with_unbalanced_total_collateral(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: tp.List[clusterlib.AddressRecord],
        with_return_collateral: bool,
        plutus_version: str,
    ):
        """Test minting a token with a Plutus script with unbalanced total collateral.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        plutus_v_record = plutus_common.MINTING_PLUTUS[plutus_version]

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_v_record.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, *__ = mint_build._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=mint_utxos[0].utxo_hash,
            utxo_ix=2,
            amount=minting_cost.collateral,
            address=issuer_addr.address,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(plutus_v_record.script_file)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_v_record.script_file,
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

        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            return_collateral_txouts=return_collateral_txouts if with_return_collateral else (),
            total_collateral_amount=minting_cost.collateral // 2,
            mint=plutus_mint_data,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # it should NOT be possible to mint with an unbalanced total collateral
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        err_str = str(excinfo.value)
        assert "IncorrectTotalCollateralField" in err_str, err_str
