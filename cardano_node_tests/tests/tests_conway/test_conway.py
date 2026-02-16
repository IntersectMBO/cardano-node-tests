"""Tests for Conway features that doesn't fit into any more specific file."""

import logging
import pathlib as pl

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.PoolUser:
    """Create a pool user for "use governance"."""
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return common.get_pool_user(
        cluster_manager=cluster_manager,
        name_template=name_template,
        cluster_obj=cluster,
        caching_key=key,
    )


class TestConway:
    """General tests for Conway era."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_genesis_cert_not_available(self, cluster: clusterlib.ClusterLib):
        """Verify genesis key delegation certificate command is not available in Conway era.

        Test that create-genesis-key-delegation-certificate command is removed in Conway era
        governance model.

        * attempt to execute `cardano-cli conway governance`
          `create-genesis-key-delegation-certificate` command
        * verify command fails with "Invalid argument" error
        * confirm command is not available in Conway era CLI
        """
        common.get_test_id(cluster)

        reqc.cip071.start(url=helpers.get_vcs_link())

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "cardano-cli",
                    "conway",
                    "governance",
                    "create-genesis-key-delegation-certificate",
                ],
                add_default_args=False,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "Invalid argument" in exc_value, exc_value

        reqc.cip071.success()

    def _run_test_action_unreg_deposit_addr(
        self,
        cluster: clusterlib.ClusterLib,
        temp_template: str,
        pool_user: clusterlib.PoolUser,
        build_method: str,
        submit_method: str = submit_utils.SubmitMethods.CLI,
    ):
        """Run the actual scenario of the 'test_action_unreg_deposit_addr*' tests."""
        action_deposit_amt = cluster.g_query.get_gov_action_deposit()

        # Create an action
        anchor_url = "https://tinyurl.com/cardano-qa-anchor"
        anchor_data_hash = cluster.g_governance.get_anchor_data_hash(
            file_text=DATA_DIR / "governance_action_anchor.json"
        )

        info_action = cluster.g_governance.action.create_info(
            action_name=temp_template,
            deposit_amt=action_deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
        )

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[info_action.action_file],
            signing_key_files=[pool_user.payment.skey_file],
        )

        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_action",
                src_address=pool_user.payment.address,
                submit_method=submit_method,
                build_method=build_method,
                tx_files=tx_files_action,
            )
        exc_value = str(excinfo.value)
        if build_method in (
            clusterlib_utils.BuildMethods.BUILD,
            clusterlib_utils.BuildMethods.BUILD_EST,
        ):
            with common.allow_unstable_error_messages():
                assert (
                    "Stake credential specified in the proposal is not registered on-chain"
                    in exc_value
                    or "ProposalReturnAccountDoesNotExist" in exc_value  # In node <= 10.1.4
                ), exc_value
        elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
            with common.allow_unstable_error_messages():
                assert "ProposalReturnAccountDoesNotExist" in exc_value, exc_value
        else:
            msg = f"Unexpected build method: {build_method}"
            raise ValueError(msg)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    def test_action_submit_unreg_deposit_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        submit_method: str,
    ):
        """Test submitting an action with an unregistered deposit return address.

        Expect failure.

        The transaction is built using `transaction build-raw`, which does not check
        deposit return address registration. The transaction is expected to fail on submission.
        """
        temp_template = common.get_test_id(cluster)
        return self._run_test_action_unreg_deposit_addr(
            cluster=cluster,
            temp_template=temp_template,
            pool_user=pool_user,
            build_method=clusterlib_utils.BuildMethods.BUILD_RAW,
            submit_method=submit_method,
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    def test_action_build_unreg_deposit_addr(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
    ):
        """Test building a Tx when deposit return address is unregistered.

        Expect failure.

        The `transaction build` command checks deposit return address registration
        during Tx building, causing the build to fail.
        """
        temp_template = common.get_test_id(cluster)
        return self._run_test_action_unreg_deposit_addr(
            cluster=cluster,
            temp_template=temp_template,
            pool_user=pool_user,
            build_method=clusterlib_utils.BuildMethods.BUILD,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_ratify_state_fields(self, cluster: clusterlib.ClusterLib):
        """Query governance ratification state and verify expected output fields.

        Test that query ratify-state command returns all expected fields for Conway governance
        ratification tracking.

        * execute `cardano-cli query ratify-state` command
        * check output contains enactedGovActions field (list of enacted actions)
        * check output contains expiredGovActions field (list of expired actions)
        * check output contains nextEnactState field (upcoming enactment state)
        * check output contains ratificationDelayed field (ratification delay indicator)
        * verify no expected fields are missing from output
        """
        ratify_state = cluster.g_query.get_ratify_state()
        expected_fields = {
            "enactedGovActions",
            "expiredGovActions",
            "nextEnactState",
            "ratificationDelayed",
        }

        missing = expected_fields - set(ratify_state)
        assert not missing, f"Missing expected fields in ratify-state: {missing}"
