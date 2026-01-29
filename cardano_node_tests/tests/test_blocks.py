"""Tests for blocks production.

Other block production checks may be present in `test_staking.py`.
"""

import logging
import os
import random
import re
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
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class TestLeadershipSchedule:
    """Tests for cardano-cli leadership-schedule."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("for_epoch", ("current", "next"))
    def test_pool_blocks(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_pool: tuple[clusterlib.ClusterLib, str],
        for_epoch: str,
    ):
        """Check that blocks were forged according to leadership schedule.

        * query leadership schedule for selected pool for current epoch or next epoch
        * wait for epoch that comes after the queried epoch
        * get info about forged blocks in queried epoch for the selected pool
        * compare leadership schedule with blocks that were actually forged
        * compare log records with ledger state dump
        * (optional) check forged blocks in db-sync
        """
        cluster, pool_name = cluster_use_pool
        pool_short_name = f"{pool_name.replace('node-', '')}"
        temp_template = common.get_test_id(cluster)

        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_id = cluster.g_stake_pool.get_stake_pool_id(pool_rec["cold_key_pair"].vkey_file)

        pool_log = cluster_nodes.get_cluster_env().state_dir / f"{pool_short_name}.stdout"
        seek_offset = helpers.get_eof_offset(pool_log)
        timestamp = time.time()

        if for_epoch == "current":
            # Wait for beginning of an epoch
            queried_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
        else:
            # Wait for stable stake distribution for next epoch, that is last 300 slots of
            # current epoch.
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster,
                start=-int(300 * cluster.slot_length),
                stop=-15,
                check_slot=True,
            )
            queried_epoch = cluster.g_query.get_epoch() + 1

        debug_log_template = (f"{temp_template}_ep{queried_epoch}_pool_{pool_short_name}",)

        # Query leadership schedule for selected pool
        leadership_schedule = cluster.g_query.get_leadership_schedule(
            vrf_skey_file=pool_rec["vrf_key_pair"].skey_file,
            cold_vkey_file=pool_rec["cold_key_pair"].vkey_file,
            for_next=for_epoch != "current",
        )
        with open(f"{debug_log_template}_schedule.txt", "w", encoding="utf-8") as out_fp:
            out_fp.write("\n".join(str(s) for s in leadership_schedule))
        slots_when_scheduled = {r.slot_no for r in leadership_schedule}

        # Wait for epoch that comes after the queried epoch
        cluster.wait_for_epoch(epoch_no=queried_epoch + 1, padding_seconds=10, future_is_ok=False)

        # Get number of forged blocks from ledger
        ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        clusterlib_utils.save_ledger_state(
            cluster_obj=cluster,
            state_name=temp_template,
            ledger_state=ledger_state,
        )
        blocks_before: dict[str, int] = ledger_state["blocksBefore"]
        pool_id_dec = helpers.decode_bech32(pool_id)
        forged_blocks_ledger = blocks_before.get(pool_id_dec) or 0

        errors: list[str] = []

        def _check_logs() -> None:
            # Get info about forged blocks in queried epoch for the selected pool
            forged_lines = logfiles.find_msgs_in_logs(
                regex='"TraceForgedBlock"',
                logfile=pool_log,
                seek_offset=seek_offset,
                timestamp=timestamp,
            )

            tip = cluster.g_query.get_tip()
            ep_num_after_queried = int(tip["epoch"]) - queried_epoch
            ep_length = int(cluster.genesis["epochLength"])
            first_slot_this_ep = int(tip["slot"]) - int(tip["slotInEpoch"])
            first_slot_queried_ep = first_slot_this_ep - (ep_num_after_queried * ep_length)
            last_slot_queried_ep = first_slot_queried_ep + ep_length - 1
            slots_pattern = re.compile(r'"slot",Number (\d+)\.0')
            slots_when_forged = {
                s
                for m in forged_lines
                if (o := slots_pattern.search(m)) is not None
                and first_slot_queried_ep <= (s := int(o.group(1))) <= last_slot_queried_ep
            }
            with open(f"{debug_log_template}_forged.txt", "w", encoding="utf-8") as out_fp:
                out_fp.write(f"{pool_id_dec}: {slots_when_forged}")

            # Compare leadership schedule with blocks that were actually forged
            difference_scheduled = slots_when_forged.difference(slots_when_scheduled)
            if difference_scheduled:
                errors.append(
                    f"Some blocks were forged in other slots than scheduled: {difference_scheduled}"
                )

            difference_forged = slots_when_scheduled.difference(slots_when_forged)
            if len(difference_forged) > len(leadership_schedule) // 5:
                errors.append(f"Lot of slots missed: {difference_forged}")

            # Compare log records with ledger state dump
            forged_blocks_logs = len(slots_when_forged)
            # Some forged block may not be adopted, and so the total number of adopted blocks
            # may be lower than the number of forged blocks.
            if forged_blocks_ledger > forged_blocks_logs:
                errors.append(
                    "Number of forged blocks reported by ledger state "
                    "is higher than number extracted from log file: "
                    f"{forged_blocks_ledger} vs {forged_blocks_logs}"
                )

        def _check_dbsync() -> None:
            # Get info about forged blocks in queried epoch for the selected pool
            forged_blocks = list(
                dbsync_queries.query_blocks(
                    pool_id_bech32=pool_id, epoch_from=queried_epoch, epoch_to=queried_epoch
                )
            )
            slots_when_forged = {r.slot_no for r in forged_blocks}
            with open(f"{debug_log_template}_db_forged.txt", "w", encoding="utf-8") as out_fp:
                out_fp.write(f"{pool_id_dec}: {slots_when_forged}")

            # Compare leadership schedule with blocks that were actually forged
            difference_scheduled = slots_when_forged.difference(slots_when_scheduled)
            if difference_scheduled:
                errors.append(
                    "DB-Sync: Some blocks were forged in other slots than scheduled: "
                    f"{difference_scheduled}"
                )

            difference_forged = slots_when_scheduled.difference(slots_when_forged)
            if len(difference_forged) > len(leadership_schedule) // 3:
                errors.append(f"DB-Sync: Lot of slots missed: {difference_forged}")

            # Compare db-sync records with ledger state dump
            forged_blocks_db = len(slots_when_forged)
            if forged_blocks_ledger != forged_blocks_db:
                errors.append(
                    "DB-Sync: Numbers of forged blocks reported by ledger state "
                    "and db-sync don't match: "
                    f"{forged_blocks_ledger} vs {forged_blocks_db}"
                )

        _check_logs()

        if configuration.HAS_DBSYNC:
            _check_dbsync()

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
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ):
        """Try to query leadership schedule for next epoch when stake distribution is unstable.

        Expect failure.
        """
        common.get_test_id(cluster)

        pool_name = cluster_management.Resources.POOL3
        pool_rec = cluster_manager.cache.addrs_data[pool_name]

        # Wait for epoch interval where stake distribution for next epoch is unstable,
        # that is anytime before last 300 slots of current epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster,
            start=5,
            stop=-int(300 * cluster.slot_length + 5),
        )

        # It should NOT be possible to query leadership schedule
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_leadership_schedule(
                vrf_skey_file=pool_rec["vrf_key_pair"].skey_file,
                cold_vkey_file=pool_rec["cold_key_pair"].vkey_file,
                for_next=True,
            )
        exc_value = str(excinfo.value)

        if "PastHorizon" in exc_value:
            issues.node_4002.finish_test()

        with common.allow_unstable_error_messages():
            assert "current stake distribution is currently unstable" in exc_value, exc_value


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
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=20,
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @pytest.fixture
    def block_production_db(self) -> tp.Generator[sqlite3.Connection, None, None]:
        """Open block production db."""
        conn = sqlite3.connect(configuration.BLOCK_PRODUCTION_DB)
        yield conn
        conn.close()

    @allure.link(helpers.get_vcs_link())
    def test_block_production(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        block_production_db: sqlite3.Connection,
    ):
        """Record number of blocks produced by each pool over multiple epochs.

        * register and delegate a stake address to every pool
        * collect block production data over multiple epochs

           - each epoch save ledger state
           - each epoch save block production data to sqlite db
           - transfer funds between multiple addresses to create activity
        """
        temp_template = common.get_test_id(cluster)
        rand = clusterlib.get_rand_str(5)
        num_epochs = int(os.environ.get("BLOCK_PRODUCTION_EPOCHS") or 50)
        mixed_backends = (
            configuration.MIXED_UTXO_BACKENDS.split() if configuration.MIXED_UTXO_BACKENDS else []
        )

        pool_mapping = {}
        for idx, pn in enumerate(cluster_management.Resources.ALL_POOLS, start=1):
            pool_id = delegation.get_pool_id(
                cluster_obj=cluster, addrs_data=cluster_manager.cache.addrs_data, pool_name=pn
            )
            pool_id_dec = helpers.decode_bech32(pool_id)
            pool_mapping[pool_id_dec] = {"pool_id": pool_id, "pool_idx": idx}

            # Delegate to each pool
            delegation.delegate_stake_addr(
                cluster_obj=cluster,
                addrs_data=cluster_manager.cache.addrs_data,
                temp_template=f"{temp_template}_pool_{idx}",
                pool_id=pool_id,
            )

        # Create sqlite db
        conn = block_production_db
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS runs(run_id, backend)")
        cur.execute(
            "CREATE TABLE IF NOT EXISTS"
            " blocks(run_id, epoch_no, pool_id, pool_idx, backend, num_blocks)"
        )
        cur.execute(
            "INSERT INTO runs VALUES (?, ?)",
            (rand, "mixed" if mixed_backends else configuration.UTXO_BACKEND),
        )
        conn.commit()
        cur.close()

        def _get_pool_utxo_backend(pool_idx: int) -> str:
            if mixed_backends:
                return mixed_backends[(pool_idx - 1) % len(mixed_backends)]
            return configuration.UTXO_BACKEND

        def _save_state(curr_epoch: int) -> None:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_epoch{curr_epoch}",
                ledger_state=ledger_state,
            )
            blocks_before: dict[str, int] = ledger_state["blocksBefore"]

            # Save blocks data to sqlite db
            cur = conn.cursor()
            for pool_id_dec, num_blocks in blocks_before.items():
                pool_rec = pool_mapping[pool_id_dec]
                pool_idx = tp.cast(int, pool_rec["pool_idx"])
                cur.execute(
                    "INSERT INTO blocks VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        rand,
                        curr_epoch - 1,
                        pool_rec["pool_id"],
                        pool_idx,
                        _get_pool_utxo_backend(pool_idx),
                        num_blocks,
                    ),
                )
            conn.commit()
            cur.close()

        tip = cluster.g_query.get_tip()
        epoch_end = cluster.time_to_epoch_end(tip)
        curr_epoch = cluster.g_query.get_epoch(tip=tip)
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

            # Send tx
            src_addr, dst_addr = random.sample(payment_addrs, 2)
            txouts = [clusterlib.TxOut(address=dst_addr.address, amount=1_000_000)]
            tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

            cluster.g_transaction.send_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_{int(curr_time)}",
                txouts=txouts,
                tx_files=tx_files,
            )

            time.sleep(2)
            curr_time = time.time()

        # Save also data for the last epoch
        _save_state(cluster.g_query.get_epoch())


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

        cluster_nodes.reload_supervisor_config(delay=0)

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        cluster = cluster_singleton
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=20,
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_dynamic_block_production(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
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

        def _save_state(curr_epoch: int) -> dict[str, int]:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=f"{temp_template}_epoch{curr_epoch}",
                ledger_state=ledger_state,
            )
            blocks_before: dict[str, int] = ledger_state["blocksBefore"]
            return blocks_before

        # The network needs to be at least in epoch 1
        cluster.wait_for_epoch(epoch_no=1)

        # Wait for the epoch to be at least half way through and not too close to the end.
        # We want the original pool to have time to forge blocks in this epoch, before it becomes
        # backup node.
        # And we want to have enough time to replace the node's configuration before the epoch ends.
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster,
            start=(cluster.epoch_length_sec // 2),
            stop=-(configuration.TX_SUBMISSION_DELAY + 20),
        )
        reconf_epoch = cluster.g_query.get_epoch()

        # The cluster needs respin after this point
        cluster_manager.set_needs_respin()

        # Reconfigure cluster for dynamic block production
        self.reconf_for_dynamic()
        cluster_nodes.restart_all_nodes()

        tip = cluster.g_query.get_tip()
        curr_epoch = cluster.g_query.get_epoch(tip=tip)

        assert reconf_epoch == curr_epoch, (
            "Failed to finish reconfiguration in single epoch, it would affect other checks"
        )

        epoch_end = cluster.time_to_epoch_end(tip)
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
                    assert backup_pool_id in blocks_db[curr_epoch], (
                        "The original pool should have forged blocks before it became backup node."
                    )
                    assert producing_pool_id in blocks_db[curr_epoch], (
                        "Producing pool should forge blocks."
                    )

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
            txouts = [clusterlib.TxOut(address=dst_addr.address, amount=1_000_000)]
            tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

            cluster.g_transaction.send_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_{int(curr_time)}",
                txouts=txouts,
                tx_files=tx_files,
            )

            time.sleep(2)
            curr_time = time.time()

        # Save also data for the last epoch
        curr_epoch = cluster.g_query.get_epoch()
        blocks_db[curr_epoch] = _save_state(curr_epoch)

        assert backup_pool_id not in blocks_db[curr_epoch], (
            "The original pool should NOT forge blocks after it became backup node."
        )
        assert producing_pool_id in blocks_db[curr_epoch], "Producing pool should forge blocks."
