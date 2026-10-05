"""Unit tests for `cardano_node_tests.tests.common`."""

import json
import pathlib as pl
import typing as tp

import pytest
from _pytest.config import Config

from cardano_node_tests.tests import common
from framework_tests import stubs


class _ClusterTypeStub:
    """Minimal stub of `ClusterType` that provides only `get_cluster_obj`."""

    def get_cluster_obj(self, *, command_era: str = "") -> tp.Any:
        """Return a stub of a `ClusterLib` instance."""
        return stubs.ClusterObjStub(
            cli_coverage={"cardano-cli": {"_count": 1}}, command_era=command_era
        )


class TestGetFixtureClusterObj:
    """Tests for `get_fixture_cluster_obj`."""

    def test_coverage_saved_on_teardown(self, tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Save CLI coverage of the created instance when the registered finalizer runs."""
        coverage_dir = tmp_path / "coverage"
        coverage_dir.mkdir()
        pytest_config = tp.cast(Config, stubs.PytestConfigStub(str(coverage_dir)))
        request = stubs.FixtureRequestStub(config=pytest_config)
        monkeypatch.setattr(common.cluster_nodes, "get_cluster_type", _ClusterTypeStub)

        cluster_obj = common.get_fixture_cluster_obj(
            request=tp.cast(tp.Any, request), command_era="conway"
        )

        assert cluster_obj.command_era == "conway"
        assert not list(coverage_dir.glob("*.json"))

        for finalizer in request.finalizers:
            finalizer()

        coverage_files = list(coverage_dir.glob("cli_coverage_*.json"))
        assert len(coverage_files) == 1
        assert json.loads(coverage_files[0].read_text()) == {"cardano-cli": {"_count": 1}}


# Genesis settings of the `local_fast` and `leios_fast` testnet variants
LOCAL_FAST = {
    "security_param": 10,
    "active_slots_coeff": 0.1,
    "slot_length": 0.2,
    "epoch_length": 1000,
}
LEIOS_FAST = {
    "security_param": 4,
    "active_slots_coeff": 0.05,
    "slot_length": 1,
    "epoch_length": 800,
}


@pytest.fixture
def local_minimums(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the minimums for local testnets, whatever cluster type is configured."""
    monkeypatch.setattr(common, "EPOCH_STOP_SEC_BUFFER_MIN", 40)
    monkeypatch.setattr(common, "EPOCH_LEDGER_STATE_WINDOW_SEC_MIN", 4)
    monkeypatch.setattr(common, "EPOCH_STOP_SEC_LEDGER_STATE", -15)


@pytest.mark.usefixtures("local_minimums")
class TestGetEpochStopSecBuffer:
    """Tests for `get_epoch_stop_sec_buffer`."""

    def test_min_buffer(self):
        """Use the min buffer when blocks are produced fast."""
        cluster_obj = tp.cast(tp.Any, stubs.GenesisClusterStub(**LOCAL_FAST))
        assert common.get_epoch_stop_sec_buffer(cluster_obj=cluster_obj) == -40

    def test_slow_blocks(self):
        """Scale the buffer with the block interval when blocks are produced slowly."""
        cluster_obj = tp.cast(tp.Any, stubs.GenesisClusterStub(**LEIOS_FAST))
        # 12 blocks every 20 sec
        assert common.get_epoch_stop_sec_buffer(cluster_obj=cluster_obj) == -240

    def test_capped_at_half_epoch(self):
        """Never use more than half of the epoch for the buffer."""
        cluster_obj = tp.cast(
            tp.Any, stubs.GenesisClusterStub(**{**LEIOS_FAST, "epoch_length": 300})
        )
        assert common.get_epoch_stop_sec_buffer(cluster_obj=cluster_obj) == -150


@pytest.mark.usefixtures("local_minimums")
class TestGetEpochStartSecLedgerState:
    """Tests for `get_epoch_start_sec_ledger_state`."""

    def test_min_window(self):
        """Use the min window when blocks are produced fast."""
        cluster_obj = tp.cast(tp.Any, stubs.GenesisClusterStub(**LOCAL_FAST))
        # Window is 3 blocks every 2 sec, which is more than the min of 4 sec
        assert common.get_epoch_start_sec_ledger_state(cluster_obj=cluster_obj) == -21

    def test_slow_blocks(self):
        """Scale the window with the block interval when blocks are produced slowly."""
        cluster_obj = tp.cast(tp.Any, stubs.GenesisClusterStub(**LEIOS_FAST))
        # 3 blocks every 20 sec before the stop at -15
        assert common.get_epoch_start_sec_ledger_state(cluster_obj=cluster_obj) == -75

    def test_after_reward_update(self):
        """Shorten the window so it starts only after the reward update is complete."""
        # The reward update is forced at 640 sec and complete at 700 sec. The window
        # would start at 685 sec.
        cluster_obj = tp.cast(
            tp.Any, stubs.GenesisClusterStub(**{**LEIOS_FAST, "epoch_length": 760})
        )
        assert common.get_epoch_start_sec_ledger_state(cluster_obj=cluster_obj) == -60

    def test_not_after_stop(self):
        """Never start the window after the stop."""
        # The reward update is complete at 700 sec, after the stop at 685 sec
        cluster_obj = tp.cast(
            tp.Any, stubs.GenesisClusterStub(**{**LEIOS_FAST, "epoch_length": 700})
        )
        assert common.get_epoch_start_sec_ledger_state(cluster_obj=cluster_obj) == -15
