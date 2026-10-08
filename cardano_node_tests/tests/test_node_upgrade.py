"""Tests for node upgrade."""

import json
import logging
import shutil

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import markers
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.tests.tests_dijkstra import bls
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import faucet
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils.versions import VERSIONS
from cardano_node_tests.utils.versions import EraName

LOGGER = logging.getLogger(__name__)

UPGRADE_TESTS_STEP = helpers.get_env_int("UPGRADE_TESTS_STEP", 0)

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
    addr = addrs_common.get_payment_addr(
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
    addrs = addrs_common.get_payment_addrs(
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
        return addrs_common.get_registered_pool_user(
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
        """Ignore selected errors in log right after node upgrade.

        This prevents false test failures from expected upgrade-related log messages.
        """
        common.get_test_id(cluster_singleton)

        # The error should be present only when upgrading pre UTxO-HD release.
        # The UTxO-HD was added in 10.4.1, so when we are upgrading from 10.4.1+ release to
        # 10.5.0+ release, the error should not be there.
        # Here we are comparing the version of "upgraded" release, not the version we are upgrading
        # from.
        if VERSIONS.node < version.parse("10.5.0"):
            logfiles.add_ignore_rule(
                files_glob="*.stdout",
                regex="ChainDB:Warning:.* Invalid snapshot DiskSnapshot .*MetadataFileDoesNotExist",
                ignore_file_id=worker_id,
            )
        # The error should be present only when upgrading pre LSM release.
        # The LSM was added in 10.7.0, so when we are upgrading from 10.7.0+ release to
        # 10.8.0+ release, the error should not be there.
        # Here we are comparing the version of "upgraded" release, not the version we are upgrading
        # from.
        if VERSIONS.node < version.parse("10.8.0"):
            logfiles.add_ignore_rule(
                files_glob="*.stdout",
                regex="tablesCodecVersion",
                ignore_file_id=worker_id,
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 2, reason="runs only on step 2 of upgrade testing")
    def test_update_cost_models(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        pool_user_singleton: clusterlib.PoolUser,
    ):
        """Test cost model update.

        Test updating Plutus cost models after node upgrade. Runs only on step 2 of upgrade
        testing sequence.

        * Load cost model proposal from JSON file (PlutusV1, PlutusV2 and PlutusV3 models)
        * Get default governance data (DReps, committee members, pools)
        * Submit cost model update governance action
        * Vote and ratify the cost model update
        * Wait for enactment
        * Verify updated cost models are active
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)
        cost_proposal_file = common.COST_PROPOSAL_FILE

        governance_data = governance_setup.get_default_governance(
            cluster_manager=cluster_manager, cluster_obj=cluster
        )
        conway_common.update_cost_model(
            cluster_obj=cluster,
            name_template=temp_template,
            governance_data=governance_data,
            cost_proposal_file=cost_proposal_file,
            pool_user=pool_user_singleton,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing")
    def test_hardfork(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        pool_user_singleton: clusterlib.PoolUser,
    ):
        """Test hard fork.

        Test protocol version hard fork using governance action after node upgrade. Runs only
        on step 3 of upgrade testing sequence.

        * Get current protocol version and calculate target version (current + 1)
        * Skip if already at last supported protocol version
        * Skip if ExperimentalHardForksEnabled is needed (node version < target protocol
          version) but not enabled in node config
        * Get default governance data (DReps, committee members, pools)
        * Wait for any delayed ratification to complete
        * Create hardfork governance action with target protocol version
        * Submit hardfork action transaction
        * Vote on hardfork action (Constitutional Committee, DReps, SPOs)
        * Wait for ratification
        * Wait for enactment epoch
        * Verify protocol version changed to target version
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        prot_ver_init = clusterlib_utils.get_protocol_version(cluster_obj=cluster)
        prot_ver_target = prot_ver_init + 1

        if VERSIONS.MAP.get(prot_ver_target) is None:
            pytest.skip(
                "The target protocol version needs to be known. "
                f"Current protocol version: {prot_ver_init}, "
                f"target protocol version: {prot_ver_target}."
            )

        with open(
            cluster_nodes.get_cluster_env().state_dir / "config-pool1.json", encoding="utf-8"
        ) as in_json:
            is_experimental_enabled = bool(json.load(in_json).get("ExperimentalHardForksEnabled"))
        # Experimental hard forks are needed when the node version is lower than the target
        # protocol version, e.g. for PV11 with node < 11.0.0, or for PV12 with node < 12.0.0.
        if not is_experimental_enabled and VERSIONS.node < version.parse(f"{prot_ver_target}.0.0"):
            pytest.skip(
                "Enabled experimental hard-forks are needed for this node version "
                f"and target protocol version {prot_ver_target}."
            )

        governance_data = governance_setup.get_default_governance(
            cluster_manager=cluster_manager, cluster_obj=cluster
        )
        governance_utils.wait_delayed_ratification(cluster_obj=cluster)

        # Create an action
        deposit_amt = cluster.g_query.get_gov_action_deposit()
        anchor_data = governance_utils.get_default_anchor_data()
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.HARDFORK,
            gov_state=cluster.g_query.get_gov_state(),
        )

        hardfork_action = cluster.g_governance.action.create_hardfork(
            action_name=temp_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_data.url,
            anchor_data_hash=anchor_data.hash,
            protocol_major_version=prot_ver_target,
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
            cluster_obj=cluster,
            start=1,
            stop=common.get_epoch_stop_sec_buffer(cluster_obj=cluster) - 20,
        )
        init_epoch = cluster.g_query.get_epoch()

        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=pool_user_singleton.payment.address,
            build_method=clusterlib_utils.BuildMethods.BUILD,
            tx_files=tx_files_action,
        )

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_gov_state = cluster.g_query.get_gov_state()
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
            approve_drep=True if prot_ver_init > 9 else None,
            approve_spo=True,
        )

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Epoch changed and it would affect other checks"
        )

        # Check ratification
        rat_epoch = cluster.wait_for_epoch(epoch_no=init_epoch + 1, padding_seconds=5)
        rat_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{rat_epoch}"
        )
        rat_action = governance_utils.lookup_ratified_actions(
            state=rat_gov_state, action_txid=action_txid
        )
        assert rat_action, "Action not found in ratified actions"

        assert rat_gov_state["currentPParams"]["protocolVersion"]["major"] == prot_ver_init, (
            "Incorrect major version"
        )

        # Check enactment
        enact_epoch = cluster.wait_for_epoch(epoch_no=init_epoch + 2, padding_seconds=5)
        enact_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{enact_epoch}"
        )
        assert enact_gov_state["currentPParams"]["protocolVersion"]["major"] == prot_ver_target, (
            "Incorrect major version"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing")
    @pytest.mark.skipif(
        VERSIONS.cluster_era_name != EraName.DIJKSTRA,
        reason="runs only in Dijkstra era",
    )
    def test_register_pools_bls_keys(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        worker_id: str,
    ):
        """Register BLS keys of the cluster pools after the hard fork to Dijkstra.

        The cluster pools were registered before Dijkstra, so they have no BLS keys. Runs
        only on step 3 of upgrade testing sequence, after the hard fork to Dijkstra.

        * Skip if the node doesn't support BLS keys
        * Skip if the pools already have BLS keys registered, i.e. the cluster was already
          running in Dijkstra before the upgrade
        * Check that the pool start scripts pick up a BLS key when there is one
        * Generate a BLS key pair for every cluster pool
        * Fund the pool owners, who pay for the re-registration transactions
        * Re-register every pool with its BLS key, keeping the other pool parameters
        * Put the BLS keys where the pool start scripts pick them up and restart the nodes
        * Wait for the next epoch and check that every pool has its BLS key registered
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)
        state_dir = cluster_nodes.get_cluster_env().state_dir
        pool_names = cluster_management.Resources.ALL_POOLS
        addrs_data = cluster_manager.cache.addrs_data

        node_help = helpers.run_command(
            "cardano-node run --help", ignore_fail=True, merge_stderr=True
        ).decode()
        if "--shelley-bls-key" not in node_help:
            pytest.skip("The node doesn't support BLS keys.")

        pool_ids = {
            p: delegation.get_pool_id(cluster_obj=cluster, addrs_data=addrs_data, pool_name=p)
            for p in pool_names
        }

        # When the cluster was already running in Dijkstra before the upgrade, the pools
        # already have their BLS keys registered
        if all(
            bls.get_registered_bls_key(cluster_obj=cluster, pool_id=pool_id)
            for pool_id in pool_ids.values()
        ):
            pytest.skip("The pools already have BLS keys registered.")

        # The start scripts check for the BLS key file on every node start, so a script
        # generated before the key existed passes the key once it is in place.
        for pool_name in pool_names:
            start_script = state_dir / f"cardano-node-{pool_name.replace('node-', '')}"
            assert "--shelley-bls-key" in start_script.read_text(), (
                f"The start script '{start_script}' doesn't pick up a BLS key"
            )

        bls_key_pairs = {
            p: cluster.g_node.gen_bls_key_pair(node_name=f"{temp_template}_{p}") for p in pool_names
        }

        # The pool owners pay for the re-registration transactions
        faucet.fund_from_faucet(
            *[addrs_data[p]["payment"] for p in pool_names],
            cluster_obj=cluster,
            all_faucets=addrs_data,
            amount=100_000_000,
            tx_name=f"{temp_template}_fund_owners",
            force=True,
        )

        # The hard fork to Dijkstra records the VRF key hashes of the pools, so the known
        # ledger issue with pools from the genesis doesn't apply here and must not be xfailed
        for pool_name in pool_names:
            bls.reregister_cluster_pool(
                cluster_obj=cluster,
                pool_rec=addrs_data[pool_name],
                pool_name=pool_name,
                pool_id=pool_ids[pool_name],
                bls_skey_file=bls_key_pairs[pool_name].skey_file,
                tx_name=f"{temp_template}_{pool_name}_rereg",
                allow_xfail=False,
            )

        # The node reads its BLS key only on startup, so the nodes need a restart
        for pool_name, key_pair in bls_key_pairs.items():
            pool_data_dir = state_dir / "nodes" / pool_name
            shutil.copy(key_pair.skey_file, pool_data_dir / "bls.skey")
            shutil.copy(key_pair.vkey_file, pool_data_dir / "bls.vkey")

        # Restarting the nodes drops the connections between them
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="MuxBearerClosed",
            ignore_file_id=worker_id,
        )
        cluster_nodes.restart_all_nodes(delay=5)
        cluster.wait_for_new_block(new_blocks=2)

        # The pool update takes effect on the next epoch boundary
        cluster.wait_for_new_epoch(padding_seconds=5)
        errors = []
        for pool_name, key_pair in bls_key_pairs.items():
            registered_key = bls.get_bls_pub_key(
                bls_key_state=bls.get_registered_bls_key(
                    cluster_obj=cluster, pool_id=pool_ids[pool_name]
                )
            )
            if registered_key != bls.get_vkey_hex(vkey_file=key_pair.vkey_file):
                errors.append(f"The pool '{pool_name}' has unexpected BLS key: {registered_key}")
        assert not errors, "\n".join(errors)


class TestUpgrade:
    """Tests for node upgrade testing."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP > 2, reason="doesn't run on step > 2 of upgrade testing")
    @pytest.mark.order(-1)
    @pytest.mark.upgrade_step1
    @pytest.mark.upgrade_step2
    @pytest.mark.upgrade_step3
    @markers.PARAM_BUILD_METHOD_NO_EST
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
        build_method: str,
        for_step: int,
        file_type: str,
    ):
        """Prepare transactions that will be submitted in next steps of upgrade testing.

        For testing that transaction created by previous node version and/or in previous era can
        be submitted in next node version and/or next era. Runs on steps 1-2, creates
        transactions for steps 2-3.

        * Create simple transaction sending 2 ADA from source to destination address
        * Build transaction using parametrized method (build or build-raw)
        * For build method: use fee buffer of 1000000 lovelace
        * For build-raw method: calculate fee first, then build transaction
        * Sign transaction with source address signing key
        * Save either signed tx file or unsigned tx body file based on parametrization
        * Save to state directory for use in later upgrade testing steps
        * Verify saved file exists and has non-zero size
        """
        temp_template = common.get_test_id(cluster)
        build_str = build_method

        src_address = payment_addrs_disposable[0].address
        dst_address = payment_addrs_disposable[1].address

        txouts = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[0].skey_file])

        if build_method == clusterlib_utils.BuildMethods.BUILD:
            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=txouts,
                fee_buffer=1_000_000,
            )
        elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=src_address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
            )
            tx_raw_output = cluster.g_transaction.build_raw_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
                fee=fee,
            )
        elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
            tx_raw_output = cluster.g_transaction.build_estimate_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        else:
            msg = f"Unsupported build method: {build_method}"
            raise ValueError(msg)

        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_{build_method}_signed",
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
    @markers.PARAM_BUILD_METHOD_NO_EST
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
        build_method: str,
        for_step: int,
        from_step: int,
        file_type: str,
    ):
        """Submit transaction that was created by previous node version and/or in previous era.

        Test cross-version/cross-era transaction compatibility by submitting transactions
        created in earlier upgrade steps. Runs on steps 2-3.

        * Locate transaction file created in previous step (from state directory)
        * Load signed tx file or unsigned tx body file based on parametrization
        * If loading tx body: sign the transaction body with appropriate signing keys
        * Submit the transaction to the upgraded node
        * Wait for transaction to be included in a block
        * Verify transaction was successfully submitted and processed
        * Verify transaction inputs were spent
        * Demonstrates backward compatibility of transaction format across versions/eras
        """
        temp_template = common.get_test_id(cluster)
        build_str = build_method

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

        txins = cluster.g_transaction.view_tx_dict(tx_file=tx_file).get("inputs") or []
        assert txins, "No inputs found in the transaction"
        clusterlib_utils.check_txins_spent(cluster_obj=cluster, txins=txins)
