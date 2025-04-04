"""Tests for node upgrade."""

import json
import logging
import os
import pathlib as pl
import shutil

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

DATA_DIR = pl.Path(__file__).parent / "data"
UPGRADE_TESTS_STEP = int(os.environ.get("UPGRADE_TESTS_STEP") or 0)

pytestmark = [
    pytest.mark.skipif(not UPGRADE_TESTS_STEP, reason="not upgrade testing"),
]


@pytest.fixture
def payment_addr_locked(
    cluster_manager: cluster_management.ClusterManager,
    cluster_singleton: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Create new payment addresses."""
    cluster = cluster_singleton
    addr = common.get_payment_addr(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
    )
    return addr


@pytest.fixture
def payment_addrs_disposable(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Create new disposable payment addresses."""
    addrs = common.get_payment_addrs(
        name_template=f"{common.get_test_id(cluster)}_disposable",
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
    )
    return addrs


class TestSetup:
    """Tests for setting up cardano network before and during upgrade testing.

    Special tests that run outside of normal test run.
    """

    @pytest.fixture
    def pool_user_singleton(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> clusterlib.PoolUser:
        """Create a pool user for singleton."""
        name_template = common.get_test_id(cluster_singleton)
        return common.get_registered_pool_user(
            name_template=name_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster_singleton,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP < 2, reason="runs only on step >= 2 of upgrade testing")
    def test_ignore_log_errors(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        worker_id: str,
    ):
        """Ignore selected errors in log right after node upgrade."""
        common.get_test_id(cluster_singleton)

        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="ChainDB:Error:.* Invalid snapshot DiskSnapshot .*DeserialiseFailure "
            ".* expected change in the serialization format",
            ignore_file_id=worker_id,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.disabled(reason="The test is not needed when we are already in PV10 on mainnet")
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 2, reason="runs only on step 2 of upgrade testing")
    def test_update_cost_models(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        pool_user_singleton: clusterlib.PoolUser,
    ):
        """Test cost model update."""
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)
        cost_proposal_file = DATA_DIR / "cost_models_pv10.json"

        governance_data = governance_setup.get_default_governance(
            cluster_manager=cluster_manager, cluster_obj=cluster
        )
        governance_utils.wait_delayed_ratification(cluster_obj=cluster)

        proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--cost-model-file",
                value=str(cost_proposal_file),
                name="",  # costModels
            ),
        ]

        with open(cost_proposal_file, encoding="utf-8") as fp:
            cost_models_in = json.load(fp)

        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )

        def _propose_pparams_update(
            name_template: str,
            proposals: list[clusterlib_utils.UpdateProposal],
        ) -> conway_common.PParamPropRec:
            anchor_data = governance_utils.get_default_anchor_data()
            return conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=name_template,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                pool_user=pool_user_singleton,
                proposals=proposals,
                prev_action_rec=prev_action_rec,
            )

        def _check_models(cost_models: dict):
            for m in ("PlutusV1", "PlutusV2", "PlutusV3"):
                if m not in cost_models_in:
                    continue
                assert len(cost_models_in[m]) == len(cost_models[m]), f"Unexpected length for {m}"

        # Propose the action
        prop_rec = _propose_pparams_update(name_template=temp_template, proposals=proposals)
        _check_models(prop_rec.future_pparams["costModels"])

        # Vote & approve the action by CC
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_cc",
            payment_addr=pool_user_singleton.payment,
            action_txid=prop_rec.action_txid,
            action_ix=prop_rec.action_ix,
            approve_cc=True,
        )
        vote_epoch = cluster.g_query.get_epoch()

        # Check ratification
        rat_epoch = cluster.wait_for_epoch(epoch_no=vote_epoch + 1, padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{rat_epoch}"
        )

        rat_action = governance_utils.lookup_ratified_actions(
            gov_state=rat_gov_state, action_txid=prop_rec.action_txid
        )
        assert rat_action, "Action not found in ratified actions"

        next_rat_state = rat_gov_state["nextRatifyState"]
        _check_models(next_rat_state["nextEnactState"]["curPParams"]["costModels"])
        assert not next_rat_state["ratificationDelayed"], "Ratification is delayed unexpectedly"

        # Check enactment
        enact_epoch = cluster.wait_for_epoch(
            epoch_no=vote_epoch + 2, padding_seconds=5, future_is_ok=False
        )
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{enact_epoch}"
        )
        _check_models(enact_gov_state["currentPParams"]["costModels"])

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.disabled(reason="The test is not needed when we are already in PV10 on mainnet")
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing")
    def test_hardfork(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        pool_user_singleton: clusterlib.PoolUser,
    ):
        """Test hard fork."""
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        governance_data = governance_setup.get_default_governance(
            cluster_manager=cluster_manager, cluster_obj=cluster
        )
        governance_utils.wait_delayed_ratification(cluster_obj=cluster)

        # Create an action
        deposit_amt = cluster.g_query.get_gov_action_deposit()
        anchor_data = governance_utils.get_default_anchor_data()
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.HARDFORK,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )

        hardfork_action = cluster.g_conway_governance.action.create_hardfork(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_data.url,
            anchor_data_hash=anchor_data.hash,
            protocol_major_version=10,
            protocol_minor_version=0,
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_singleton.stake.vkey_file,
        )

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[hardfork_action.action_file],
            signing_key_files=[
                pool_user_singleton.payment.skey_file,
            ],
        )

        # Make sure we have enough time to submit the proposal and the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER - 20
        )
        init_epoch = cluster.g_query.get_epoch()

        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_singleton.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_action,
        )

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_gov_state = cluster.g_conway_governance.query.gov_state()
        action_epoch = cluster.g_query.get_epoch()
        conway_common.save_gov_state(
            gov_state=action_gov_state, name_template=f"{temp_template}_action_{action_epoch}"
        )
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        assert prop_action, "Hardfork action not found"
        assert (
            prop_action["proposalProcedure"]["govAction"]["tag"]
            == governance_utils.ActionTags.HARDFORK_INIT.value
        ), "Incorrect action tag"

        action_ix = prop_action["actionId"]["govActionIx"]

        # Vote & approve the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_yes",
            payment_addr=pool_user_singleton.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=True,
            approve_spo=True,
        )

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Epoch changed and it would affect other checks"
        )

        # Check ratification
        rat_epoch = cluster.wait_for_epoch(epoch_no=init_epoch + 1, padding_seconds=5)
        rat_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{rat_epoch}"
        )
        rat_action = governance_utils.lookup_ratified_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        )
        assert rat_action, "Action not found in ratified actions"

        assert rat_gov_state["currentPParams"]["protocolVersion"]["major"] == 9, (
            "Incorrect major version"
        )

        # Check enactment
        enact_epoch = cluster.wait_for_epoch(epoch_no=init_epoch + 2, padding_seconds=5)
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{enact_epoch}"
        )
        assert enact_gov_state["currentPParams"]["protocolVersion"]["major"] == 10, (
            "Incorrect major version"
        )


class TestUpgrade:
    """Tests for node upgrade testing."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP > 2, reason="doesn't run on step > 2 of upgrade testing")
    @pytest.mark.order(-1)
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "for_step",
        (
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 2, reason="doesn't run on step 2 of upgrade testing"
                ),
            ),
            pytest.param(
                3,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 3, reason="doesn't run on step 3 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize("file_type", ("tx", "tx_body"))
    def test_prepare_tx(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs_disposable: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        for_step: int,
        file_type: str,
    ):
        """Prepare transactions that will be submitted in next steps of upgrade testing.

        For testing that transaction created by previous node version and/or in previous era can
        be submitted in next node version and/or next era.
        """
        temp_template = common.get_test_id(cluster)
        build_str = "build" if use_build_cmd else "build_raw"

        src_address = payment_addrs_disposable[0].address
        dst_address = payment_addrs_disposable[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[0].skey_file])

        if use_build_cmd:
            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=destinations,
                fee_buffer=1_000_000,
            )
            out_file_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
        else:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
            tx_raw_output = cluster.g_transaction.build_raw_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
                fee=fee,
            )
            out_file_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )

        copy_files = [
            payment_addrs_disposable[0].skey_file,
            tx_raw_output.out_file,
            out_file_signed,
        ]

        tx_dir = (
            temptools.get_basetemp()
            / cluster_manager.cache.last_checksum
            / f"{UPGRADE_TESTS_STEP}for{for_step}"
            / file_type
            / build_str
        ).resolve()

        if tx_dir.exists():
            shutil.rmtree(tx_dir)
        tx_dir.mkdir(parents=True)

        for f in copy_files:
            shutil.copy(f, tx_dir)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP < 2, reason="runs only on step >= 2 of upgrade testing")
    @pytest.mark.order(5)
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "for_step",
        (
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP != 2, reason="runs only on step 2 of upgrade testing"
                ),
            ),
            pytest.param(
                3,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize(
        "from_step",
        (
            1,
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 2, reason="doesn't run on step 2 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize("file_type", ("tx", "tx_body"))
    def test_submit_tx(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        use_build_cmd: bool,
        for_step: int,
        from_step: int,
        file_type: str,
    ):
        """Submit transaction that was created by previous node version and/or in previous era."""
        temp_template = common.get_test_id(cluster)
        build_str = "build" if use_build_cmd else "build_raw"

        tx_dir = (
            temptools.get_basetemp()
            / cluster_manager.cache.last_checksum
            / f"{from_step}for{for_step}"
            / file_type
            / build_str
        ).resolve()

        if not tx_dir.exists():
            pytest.skip("No tx files found")

        tx_file = next(iter(tx_dir.glob("*.signed")))
        tx_body_file = next(iter(tx_dir.glob("*.body")))
        skey_file = next(iter(tx_dir.glob("*.skey")))

        if file_type == "tx_body":
            tx_file = cluster.g_transaction.sign_tx(
                tx_body_file=tx_body_file,
                tx_name=temp_template,
                signing_key_files=[skey_file],
            )

        cluster.g_transaction.submit_tx_bare(tx_file=tx_file)
        cluster.wait_for_new_block(2)
