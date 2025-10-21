"""Tests for minting with Plutus using `transaction build`."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import mint_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUSV3_UNUSABLE,
    pytest.mark.plutus,
]


class TestPlutusBatch5V3Builtins:
    """Tests for batch5 of Plutus Core built-in functions."""

    success_scripts = (
        *plutus_common.SUCCEEDING_MINTING_RIPEMD_160_SCRIPTS_V3,
        *plutus_common.SUCCEEDING_MINTING_BITWISE_SCRIPTS_V3,
    )
    fail_scripts = plutus_common.FAILING_MINTING_BITWISE_SCRIPTS_V3

    @pytest.fixture
    def skip_bootstrap(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> None:
        pparams = cluster.g_query.get_protocol_params()
        if pparams["protocolVersion"]["major"] < 10 or len(pparams["costModels"]["PlutusV3"]) < 297:
            pytest.skip("Needs to run on PV10+ with updated PlutusV3 cost model.")

    @pytest.fixture
    def payment_addrs(
        self,
        skip_bootstrap: None,  # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment address."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=2,
            fund_idx=[0],
            amount=100_000_000,
        )
        return addrs

    def run_scenario(
        self,
        cluster_obj: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_v_record: plutus_common.PlutusScriptData,
        success_expected: bool,
    ):
        """Run an e2e test for a Plutus builtin."""
        temp_template = common.get_test_id(cluster_obj)

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 10_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_v_record.execution_cost,
            protocol_params=cluster_obj.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer and create UTXO for collaterals

        mint_utxos, collateral_utxos, tx_output_step1 = mint_build._fund_issuer(
            cluster_obj=cluster_obj,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster_obj.g_transaction.get_policyid(plutus_v_record.script_file)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_v_record.script_file,
                collaterals=collateral_utxos,
                redeemer_file=plutus_common.REDEEMER_42,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        try:
            tx_output_step2 = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster_obj,
                name_template=f"{temp_template}_step2",
                src_address=payment_addr.address,
                build_method=clusterlib_utils.BuildMethods.BUILD,
                tx_files=tx_files_step2,
                txins=mint_utxos,
                txouts=txouts_step2,
                mint=plutus_mint_data,
            )
        except clusterlib.CLIError as excp:
            if success_expected:
                raise
            if "The machine terminated because of an error" in str(excp):
                return
            raise

        out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "script",
        success_scripts,
        ids=(s.script_file.stem for s in success_scripts),
    )
    @pytest.mark.team_plutus
    @pytest.mark.smoke
    def test_plutus_success(
        self,
        skip_bootstrap: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        script: plutus_common.PlutusScriptData,
    ):
        """Test scenarios that are supposed to succeed."""
        self.run_scenario(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            plutus_v_record=script,
            success_expected=True,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "script",
        fail_scripts,
        ids=(s.script_file.stem for s in fail_scripts),
    )
    @pytest.mark.team_plutus
    @pytest.mark.smoke
    def test_plutus_fail(
        self,
        skip_bootstrap: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        script: plutus_common.PlutusScriptData,
    ):
        """Test scenarios that are supposed to fail."""
        self.run_scenario(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            plutus_v_record=script,
            success_expected=False,
        )
