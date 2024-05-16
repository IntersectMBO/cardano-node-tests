"""Tests for blocks production.

Other block production checks may be present in `test_staking.py`.
"""

import logging
import os
import random
import shutil
import signal
import sqlite3
import time
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import issues
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class TestLeadershipSchedule:
    """Tests for cardano-cli leadership-schedule."""

    @pytest.fixture(scope="class")
    def skip_leadership_schedule(self):
        if not clusterlib_utils.cli_has("query leadership-schedule"):
            pytest.skip("The `cardano-cli query leadership-schedule` command is not available.")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.needs_dbsync
    @pytest.mark.parametrize("for_epoch", ("current", "next"))
    def test_pool_blocks(
        self,
        skip_leadership_schedule: None,  # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool: tp.Tuple[clusterlib.ClusterLib, str],
        for_epoch: str,
    ):
        """Check that blocks were minted according to leadership schedule.

        * query leadership schedule for selected pool for current epoch or next epoch
        * wait for epoch that comes after the queried epoch
        * get info about minted blocks in queried epoch for the selected pool
        * compare leadership schedule with blocks that were actually minted
        * compare db-sync records with ledger state dump
        """
        # pylint: disable=unused-argument
        cluster, pool_name = cluster_use_pool
        temp_template = common.get_test_id(cluster)

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_id = cluster.g_stake_pool.get_stake_pool_id(pool_rec["cold_key_pair"].vkey_file)

        if for_epoch == "current":
            # wait for beginning of an epoch
            queried_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        else:
            # wait for stable stake distribution for next epoch, that is last 300 slots of
            # current epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster,
                start=-int(300 * cluster.slot_length),
                stop=-10,
                check_slot=True,
            )
            queried_epoch = cluster.g_query.get_epoch() + 1

        # query leadership schedule for selected pool
        leadership_schedule = cluster.g_query.get_leadership_schedule(
            vrf_skey_file=pool_rec["vrf_key_pair"].skey_file,
            cold_vkey_file=pool_rec["cold_key_pair"].vkey_file,
            for_next=for_epoch != "current",
        )

        # wait for epoch that comes after the queried epoch
        cluster.wait_for_new_epoch(new_epochs=1 if for_epoch == "current" else 2)

        # get info about minted blocks in queried epoch for the selected pool
        minted_blocks = list(
            dbsync_queries.query_blocks(
                pool_id_bech32=pool_id, epoch_from=queried_epoch, epoch_to=queried_epoch
            )
        )
        slots_when_minted = {r.slot_no for r in minted_blocks}

        errors: tp.List[str] = []

        # compare leadership schedule with blocks that were actually minted
        slots_when_scheduled = {r.slot_no for r in leadership_schedule}

        difference_scheduled = slots_when_minted.difference(slots_when_scheduled)
        if difference_scheduled:
            errors.append(
                f"Some blocks were minted in other slots than scheduled: {difference_scheduled}"
            )

        difference_minted = slots_when_scheduled.difference(slots_when_minted)
        if len(difference_minted) > len(leadership_schedule) // 2:
            errors.append(f"Lot of slots missed: {difference_minted}")

        # compare db-sync records with ledger state dump
        ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        clusterlib_utils.save_ledger_state(
            cluster_obj=cluster,
            state_name=temp_template,
            ledger_state=ledger_state,
        )
        blocks_before: tp.Dict[str, int] = ledger_state["blocksBefore"]
        pool_id_dec = helpers.decode_bech32(pool_id)
        minted_blocks_ledger = blocks_before.get(pool_id_dec) or 0
        minted_blocks_db = len(slots_when_minted)
        if minted_blocks_ledger != minted_blocks_db:
            errors.append(
                "Numbers of minted blocks reported by ledger state and db-sync don't match: "
                f"{minted_blocks_ledger} vs {minted_blocks_db}"
            )

        if errors:
            # Xfail if cardano-api GH-269 is still open
            if (
                VERSIONS.node > version.parse("8.1.2")
                and VERSIONS.cluster_era >= VERSIONS.BABBAGE
                and issues.api_269.is_blocked()
            ):
                issues.api_269.finish_test()

            err_joined = "\n".join(errors)
            pytest.fail(f"Errors:\n{err_joined}")

    @allure.link(helpers.get_vcs_link())
    def test_unstable_stake_distribution(
        self,
        skip_leadership_schedule: None,  # noqa: ARG002
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ):
        """Try to query leadership schedule for next epoch when stake distribution is unstable.

        Expect failure.
        """
        # pylint: disable=unused-argument
        common.get_test_id(cluster)

        pool_name = cluster_management.Resources.POOL3
        pool_rec = cluster_manager.cache.addrs_data[pool_name]

        # wait for epoch interval where stake distribution for next epoch is unstable,
        # that is anytime before last 300 slots of current epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster,
            start=5,
            stop=-int(300 * cluster.slot_length + 5),
        )

        # it should NOT be possible to query leadership schedule
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_leadership_schedule(
                vrf_skey_file=pool_rec["vrf_key_pair"].skey_file,
                cold_vkey_file=pool_rec["cold_key_pair"].vkey_file,
                for_next=True,
            )
        err_str = str(excinfo.value)

        if "PastHorizon" in err_str:
            issues.node_4002.finish_test()

        assert "current stake distribution is currently unstable" in err_str, err_str


@pytest.mark.skipif(
    not configuration.BLOCK_PRODUCTION_DB, reason="runs only during block-production testing"
)
class TestCollectData:
    """Tests for collecting data about blocks production."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> tp.List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[
                    f"addr_block_prod_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(20)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_block_production(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs: tp.List[clusterlib.AddressRecord],
    ):
        """Record number of blocks produced by each pool over multiple epochs.

        * register and delegate a stake address to every pool
        * collect block production data over multiple epochs

           - each epoch save ledger state
           - each epoch save block production data to sqlite db
           - transfer funds between multiple addresses to create activity
        """
        # pylint: disable=too-many-statements
        temp_template = common.get_test_id(cluster)
        rand = clusterlib.get_rand_str(5)
        num_epochs = int(os.environ.get("BLOCK_PRODUCTION_EPOCHS") or 50)

        topology = "legacy"
        if configuration.MIXED_P2P:
            topology = "mixed"
        elif configuration.ENABLE_P2P:
            topology = "p2p"

        pool_mapping = {}
        for idx, pn in enumerate(cluster_management.Resources.ALL_POOLS, start=1):
            pool_id = delegation.get_pool_id(
                cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pn
            )
            pool_id_dec = helpers.decode_bech32(pool_id)
            pool_mapping[pool_id_dec] = {"pool_id": pool_id, "pool_idx": idx}

            # delegate to each pool
            delegation.delegate_stake_addr(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                temp_template=temp_template,
                pool_id=pool_id,
            )

        # create sqlite db
        conn = sqlite3.connect(configuration.BLOCK_PRODUCTION_DB)
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS runs(run_id, topology)")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS"
            " blocks(run_id, epoch_no, pool_id, pool_idx, topology, num_blocks)"
        )
        cur.execute("INSERT INTO runs VALUES (?, ?)", (rand, topology))
        conn.commit()
        cur.close()

        def _get_pool_topology(pool_idx: int) -> str:
            if topology == "legacy":
                return "legacy"
            if topology == "mixed":
                return "p2p" if pool_idx % 2 == 0 else "legacy"
            return "p2p"

        def _save_state(curr_epoch: int) -> None:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_epoch{curr_epoch}",
                ledger_state=ledger_state,
            )
            blocks_before: tp.Dict[str, int] = ledger_state["blocksBefore"]

            # save blocks data to sqlite db
            cur = conn.cursor()
            for pool_id_dec, num_blocks in blocks_before.items():
                pool_rec = pool_mapping[pool_id_dec]
                pool_idx: int = pool_rec["pool_idx"]  # type: ignore
                cur.execute(
                    "INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        rand,
                        curr_epoch - 1,
                        pool_rec["pool_id"],
                        pool_idx,
                        _get_pool_topology(pool_idx),
                        num_blocks,
                    ),
                )
            conn.commit()
            cur.close()

        tip = cluster.g_query.get_tip()
        epoch_end = cluster.time_to_epoch_end(tip)
        curr_epoch = int(tip["epoch"])
        curr_time = time.time()
        epoch_end_timestamp = curr_time + epoch_end
        test_end_timestamp = epoch_end_timestamp + (num_epochs * cluster.epoch_length_sec)

        LOGGER.info(f"Checking blocks for {num_epochs} epochs.")
        while curr_time < test_end_timestamp:
            epoch_end = epoch_end_timestamp - curr_time
            if epoch_end < 15:
                LOGGER.info(f"End of epoch {curr_epoch}, saving data.")
                _save_state(curr_epoch)

                curr_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
                epoch_end_timestamp = cluster.time_to_epoch_end() + time.time()

            # send tx
            src_addr, dst_addr = random.sample(payment_addrs, 2)
            destinations = [clusterlib.TxOut(address=dst_addr.address, amount=1_000_000)]
            tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

            cluster.g_transaction.send_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_{int(curr_time)}",
                txouts=destinations,
                tx_files=tx_files,
            )

            time.sleep(2)
            curr_time = time.time()

        # save also data for the last epoch
        _save_state(cluster.g_query.get_epoch())

        conn.close()


@pytest.mark.skipif(not configuration.ENABLE_P2P, reason="runs only on P2P enabled clusters")
class TestDynamicBlockProd:
    """Tests for P2P dynamic block production."""

    producing_node_name = "pool1"
    backup_node_name = "pool2"

    def reconf_for_dynamic(self) -> None:
        """Reconfigure cluster for dynamic block production.

        Reconfigure nodeX to be backup block production node for nodeY.
        """
        state_dir = cluster_nodes.get_cluster_env().state_dir
        supervisor_conf = state_dir / "supervisor.conf"
        nodes_dir = state_dir / "nodes"
        producing_node_dir = nodes_dir / f"node-{self.producing_node_name}"
        backup_node_dir = nodes_dir / f"node-{self.backup_node_name}"
        backup_node_dir_orig = nodes_dir / f"node-{self.backup_node_name}-bak"

        LOGGER.info(
            f"Reconfiguring node '{self.backup_node_name}' to be non-producing backup node"
            f"for '{self.producing_node_name}'."
        )
        shutil.move(backup_node_dir, backup_node_dir_orig)
        shutil.copytree(producing_node_dir, backup_node_dir)

        with open(supervisor_conf, encoding="utf-8") as fp_in:
            supervisor_conf_content = fp_in.read()

        supervisor_conf_content = supervisor_conf_content.replace(
            f"/cardano-node-{self.backup_node_name}",
            f"/cardano-node-{self.backup_node_name} --non-producing-node",
        )

        with open(supervisor_conf, "w", encoding="utf-8") as fp_out:
            fp_out.write(supervisor_conf_content)

        cluster_nodes.reload_supervisor_config()

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> tp.List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        cluster = cluster_singleton

        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[f"addr_dyn_prod_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(20)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *addrs,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_dynamic_block_production(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs: tp.List[clusterlib.AddressRecord],
    ):
        """Check dynamic block production.

        * check that blocks are produced by both nodeX and nodeY pools
        * reconfigure a nodeX to be non-producing backup node for nodeY pool
        * terminate nodeY and send SIGHUP to nodeX so it starts producing blocks on behalf of nodeY
        * check that nodeX has replaced nodeY and is producing blocks on its behalf
        """
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)
        num_epochs = 3

        producing_pool_id = helpers.decode_bech32(
            delegation.get_pool_id(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                pool_name=f"node-{self.producing_node_name}",
            )
        )
        backup_pool_id = helpers.decode_bech32(
            delegation.get_pool_id(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                pool_name=f"node-{self.backup_node_name}",
            )
        )

        def _save_state(curr_epoch: int) -> tp.Dict[str, int]:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_epoch{curr_epoch}",
                ledger_state=ledger_state,
            )
            blocks_before: tp.Dict[str, int] = ledger_state["blocksBefore"]
            return blocks_before

        # Blocks are produced by BFT node in Byron epoch and first Shelley epoch on local cluster
        # that starts in Byron era.
        curr_epoch = cluster.g_query.get_epoch()
        if (
            cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL
            and not cluster_nodes.get_cluster_type().uses_shortcut
            and curr_epoch < 2
        ):
            curr_epoch = cluster.wait_for_new_epoch(new_epochs=2 - curr_epoch)

        # The network needs to be at least in epoch 1
        if curr_epoch < 1:
            curr_epoch = cluster.wait_for_new_epoch(new_epochs=1)

        # Wait for the epoch to be at least half way through and not too close to the end.
        # We want the original pool to have time to forge blocks in this epoch, before it becomes
        # backup node.
        # And we want to have enough time to replace the node's configuration before the epoch ends.
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster,
            start=(cluster.epoch_length_sec // 2),
            stop=-50,
        )

        # The cluster needs respin after this point
        cluster_manager.set_needs_respin()

        # Reconfigure cluster for dynamic block production
        self.reconf_for_dynamic()
        cluster_nodes.restart_all_nodes()

        tip = cluster.g_query.get_tip()
        epoch_end = cluster.time_to_epoch_end(tip)
        curr_epoch = int(tip["epoch"])
        reconf_epoch = curr_epoch
        curr_time = time.time()
        epoch_end_timestamp = curr_time + epoch_end
        test_end_timestamp = epoch_end_timestamp + (num_epochs * cluster.epoch_length_sec)

        blocks_db = {}
        LOGGER.info(f"Checking blocks for {num_epochs} epochs.")
        while curr_time < test_end_timestamp:
            epoch_end = epoch_end_timestamp - curr_time
            if epoch_end < 15:
                LOGGER.info(f"End of epoch {curr_epoch}, saving data.")
                blocks_db[curr_epoch] = _save_state(curr_epoch)

                if curr_epoch == reconf_epoch:
                    assert (
                        backup_pool_id in blocks_db[curr_epoch]
                    ), "The original pool should have forged blocks before it became backup node."
                    assert (
                        producing_pool_id in blocks_db[curr_epoch]
                    ), "Producing pool should forge blocks."

                curr_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
                epoch_end_timestamp = cluster.time_to_epoch_end() + time.time()

                # Replace the node
                if curr_epoch == reconf_epoch + 1:
                    LOGGER.info(
                        f"Replacing node '{self.producing_node_name}' "
                        f"with '{self.backup_node_name}'."
                    )
                    backup_node_status = cluster_nodes.services_status(
                        service_names=[f"nodes:{self.backup_node_name}"]
                    )[0]
                    if not backup_node_status.pid:
                        msg = "Replacement node is not running."
                        raise AssertionError(msg)
                    cluster_nodes.stop_nodes(node_names=[self.producing_node_name])
                    os.kill(backup_node_status.pid, signal.SIGHUP)

            # Send tx
            src_addr, dst_addr = random.sample(payment_addrs, 2)
            destinations = [clusterlib.TxOut(address=dst_addr.address, amount=1_000_000)]
            tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

            cluster.g_transaction.send_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_{int(curr_time)}",
                txouts=destinations,
                tx_files=tx_files,
            )

            time.sleep(2)
            curr_time = time.time()

        # Save also data for the last epoch
        curr_epoch = cluster.g_query.get_epoch()
        blocks_db[curr_epoch] = _save_state(curr_epoch)

        assert (
            backup_pool_id not in blocks_db[curr_epoch]
        ), "The original pool should NOT forge blocks after it became backup node."
        assert producing_pool_id in blocks_db[curr_epoch], "Producing pool should forge blocks."
