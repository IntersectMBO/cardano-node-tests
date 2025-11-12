"""Tests for minting with Plutus using `transaction build`."""

import logging
import pathlib as pl

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.tests.tests_plutus import mint_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = [
    common.SKIPIF_PLUTUSV3_UNUSABLE,
    pytest.mark.plutus,
]


@pytest.fixture
def cluster(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Mark whole governance and Plutus as "locked"."""
    cluster_obj = cluster_manager.get(
        use_resources=cluster_management.Resources.ALL_POOLS,
        lock_resources=[
            cluster_management.Resources.COMMITTEE,
            cluster_management.Resources.DREPS,
            cluster_management.Resources.PLUTUS,
        ],
    )
    return cluster_obj


@pytest.fixture
def update_cost_model(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> None:
    """Update cost model to include values for the batch5 of Plutus Core built-in functions."""
    pparams = cluster.g_query.get_protocol_params()
    if len(pparams["costModels"]["PlutusV3"]) >= 297:
        return

    temp_template = common.get_test_id(cluster)
    cost_proposal_file = DATA_DIR / "cost_models_list_185_297_v2_v3.json"

    pool_user = common.get_registered_pool_user(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster
    )
    conway_common.update_cost_model(
        cluster_obj=cluster,
        name_template=temp_template,
        governance_data=governance_data,
        cost_proposal_file=cost_proposal_file,
        pool_user=pool_user,
    )


class TestPlutusBatch5V3Builtins:
    """Tests for batch5 of Plutus Core built-in functions."""

    success_scripts = (
        *plutus_common.SUCCEEDING_MINTING_RIPEMD_160_SCRIPTS_V3,
        *plutus_common.SUCCEEDING_MINTING_BITWISE_SCRIPTS_V3,
    )
    fail_scripts = plutus_common.FAILING_MINTING_BITWISE_SCRIPTS_V3

    @pytest.fixture
    def payment_addrs(
        self,
        update_cost_model: None,  # noqa: ARG002
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
            amount=700_000_000,
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

        mint_utxos, collateral_utxos, _tx_output_step1 = mint_build._fund_issuer(
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
    @pytest.mark.long
    @pytest.mark.team_plutus
    @pytest.mark.upgrade_step3
    def test_plutusv3_builtins(
        self,
        update_cost_model: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Test minting with the batch5 of Plutus Core built-in functions."""
        for script in self.success_scripts:
            with subtests.test(script=script.script_file.stem):
                self.run_scenario(
                    cluster_obj=cluster,
                    payment_addrs=payment_addrs,
                    plutus_v_record=script,
                    success_expected=True,
                )

        for script in self.fail_scripts:
            with subtests.test(script=script.script_file.stem):
                self.run_scenario(
                    cluster_obj=cluster,
                    payment_addrs=payment_addrs,
                    plutus_v_record=script,
                    success_expected=False,
                )
