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
        """Check that the `create-genesis-key-delegation-certificate` command is not available."""
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
        err_str = str(excinfo.value)
        assert "Invalid argument" in err_str, err_str

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
        err_str = str(excinfo.value)
        if build_method in (
            clusterlib_utils.BuildMethods.BUILD,
            clusterlib_utils.BuildMethods.BUILD_EST,
        ):
            assert (
                "Stake credential specified in the proposal is not registered on-chain" in err_str
                or "ProposalReturnAccountDoesNotExist" in err_str  # In node <= 10.1.4
            ), err_str
        elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
            assert "ProposalReturnAccountDoesNotExist" in err_str, err_str
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
        """Check that 'cardano-cli query ratify-state' output has the expected fields."""
        ratify_state = cluster.g_query.get_ratify_state()
        expected_fields = {
            "enactedGovActions",
            "expiredGovActions",
            "nextEnactState",
            "ratificationDelayed",
        }

        missing = expected_fields - set(ratify_state)
        assert not missing, f"Missing expected fields in ratify-state: {missing}"


class TestNegativeLegacyGovernance:
    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("era", ["shelley", "mary", "alonzo", "babbage"])
    @pytest.mark.smoke
    def test_mixed_legacy_govaction_and_conway_vote_cert_fails(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        era: str,
    ):
        """Mixing a legacy governance action with a Conway vote-delegation cert must fail."""
        temp_template = common.get_test_id(cluster)

        payment_rec = pool_user.payment
        stake_rec = pool_user.stake

        pool_ids = cluster.g_query.get_stake_pools()
        assert pool_ids, "No stake pools available on this testnet"
        pool_id = pool_ids[0]

        era_api = getattr(cluster.g_compatible, era)
        legacy_prop = era_api.governance.action.gen_pparams_update(
            name=temp_template,
            epoch=cluster.g_query.get_epoch(),
            genesis_vkey_file=cluster.g_genesis.genesis_keys.genesis_vkeys[0],
            cli_args=["--max-block-body-size", "65536"],
        )

        conway_vote = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_vote",
            stake_vkey_file=stake_rec.vkey_file,
            stake_pool_id=pool_id,
            always_abstain=True,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[legacy_prop, conway_vote],
            signing_key_files=[payment_rec.skey_file, stake_rec.skey_file],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=payment_rec.address,
                tx_name=f"{temp_template}_mixed_fail",
                tx_files=tx_files,
            )

        err = str(excinfo.value)

        print("\n >>>>> ERROR START")
        print(err)
        print(" >>>>> ERROR END\n")

        assert "TextEnvelope type error" in err, err
        assert "Expected: CertificateConway" in err, err
        assert "Actual: UpdateProposalShelley" in err, err
