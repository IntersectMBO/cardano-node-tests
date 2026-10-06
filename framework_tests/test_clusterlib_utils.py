"""Unit tests for `cardano_node_tests.utils.clusterlib_utils`.

The tests must not depend on project-specific binaries (`cardano-cli`, ...) being present.
"""

import json
import pathlib as pl
import typing as tp

import cbor2
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from framework_tests import stubs

KEY_HASH1 = "9e1156acae8bd72bc1815d0be9fcb64e2d50e61f4204c45b901dad6b"
KEY_HASH2 = "7c2086ea4ebaa880c6e6c70604c0deb37ffbaa0567aec0bea8564055"
POOL_ID1 = "2e35bc3cae0fa3b642932a8e45602318027772192a2e215a537a6a8a"


def write_script(*, script: dict, dest_dir: pl.Path) -> pl.Path:
    """Write a script into a file and return its path."""
    script_file = dest_dir / "script.json"
    with open(script_file, "w", encoding="utf-8") as fp_out:
        json.dump(script, fp_out, indent=4)
    return script_file


def write_tx_body(*, aux_data: tp.Any, dest_dir: pl.Path) -> pl.Path:
    """Write a Tx body file with the given auxiliary data and return its path."""
    # A Tx body is a 4-element array - body, witnesses, validity flag and auxiliary data
    cbor_body = cbor2.dumps([{}, {}, True, aux_data])
    body_file = dest_dir / "tx.body"
    with open(body_file, "w", encoding="utf-8") as fp_out:
        json.dump(
            {"type": "Unwitnessed Tx ConwayEra", "description": "", "cborHex": cbor_body.hex()},
            fp_out,
        )
    return body_file


class TestLoadTxMetadata:
    """Tests for `load_tx_metadata`.

    The metadata map is stored under CBOR tag 259, whose content `cbor2` decodes into
    immutable containers. The loaded metadata must still be mutable and JSON serializable.
    """

    def test_metadata(self, tmp_path: pl.Path):
        """Load metadata that nests a map and a list."""
        metadata = {1: "foo", 2: [1, 2, {3: "bar"}]}
        body_file = write_tx_body(aux_data=cbor2.CBORTag(259, {0: metadata}), dest_dir=tmp_path)

        loaded = clusterlib_utils.load_tx_metadata(tx_body_file=body_file)

        assert loaded.metadata == metadata
        assert isinstance(loaded.metadata, dict)
        assert isinstance(loaded.metadata[2], list)
        assert isinstance(loaded.metadata[2][2], dict)
        # The metadata must be JSON serializable, so keys can be converted to strings
        assert json.loads(json.dumps(loaded.metadata)) == {"1": "foo", "2": [1, 2, {"3": "bar"}]}

    def test_metadata_with_set(self, tmp_path: pl.Path):
        """Load metadata that nests a set, which is converted to a list."""
        body_file = write_tx_body(aux_data=cbor2.CBORTag(259, {0: {1: {"foo"}}}), dest_dir=tmp_path)

        loaded = clusterlib_utils.load_tx_metadata(tx_body_file=body_file)

        assert loaded.metadata == {1: ["foo"]}
        assert json.dumps(loaded.metadata)

    def test_no_metadata(self, tmp_path: pl.Path):
        """Load metadata from a Tx body that has no auxiliary data."""
        body_file = write_tx_body(aux_data=None, dest_dir=tmp_path)

        loaded = clusterlib_utils.load_tx_metadata(tx_body_file=body_file)

        assert loaded.metadata == {}
        assert loaded.aux_data == []


class TestGetReferenceScriptSize:
    """Tests for `get_reference_script_size`.

    The expected sizes are the sizes of the scripts as serialized by the ledger. The `sig` and
    `any` sizes were confirmed against `FeeTooSmallUTxO` ledger errors on the Preview testnet,
    where `minFeeRefScriptCostPerByte` is 15: the fee was short by exactly 32 * 15 for the `sig`
    script and by 167 * 15 for the `any` script below.
    """

    def test_sig(self, tmp_path: pl.Path):
        """Get size of a `sig` script."""
        script_file = write_script(script={"keyHash": KEY_HASH1, "type": "sig"}, dest_dir=tmp_path)
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 32

    def test_any_with_slot(self, tmp_path: pl.Path):
        """Get size of an `any` script that nests `sig` scripts and a slot condition."""
        script_file = write_script(
            script={
                "scripts": [
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"slot": 100, "type": "after"},
                ],
                "type": "any",
            },
            dest_dir=tmp_path,
        )
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 167

    def test_all(self, tmp_path: pl.Path):
        """Get size of an `all` script."""
        script_file = write_script(
            script={
                "scripts": [
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                ],
                "type": "all",
            },
            dest_dir=tmp_path,
        )
        # 2 bytes for the outer array and tag, 1 byte for the inner array, 2 * 32 bytes for the
        # nested `sig` scripts
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 67

    def test_at_least(self, tmp_path: pl.Path):
        """Get size of an `atLeast` script."""
        script_file = write_script(
            script={
                "required": 2,
                "scripts": [
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                ],
                "type": "atLeast",
            },
            dest_dir=tmp_path,
        )
        # One more byte than the `all` script above, for the `required` value
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 68

    def test_before(self, tmp_path: pl.Path):
        """Get size of a `before` script."""
        script_file = write_script(script={"slot": 100, "type": "before"}, dest_dir=tmp_path)
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 4

    def test_plutus(self, tmp_path: pl.Path):
        """Get size of a Plutus script, which is the size of the bare script."""
        plutus_bytes = b"\x01\x02\x03\x04\x05"
        script_file = write_script(
            script={
                "type": "PlutusScriptV3",
                "description": "",
                "cborHex": cbor2.dumps(plutus_bytes).hex(),
            },
            dest_dir=tmp_path,
        )
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == len(
            plutus_bytes
        )

    def test_unsupported_type(self, tmp_path: pl.Path):
        """Fail on an unknown simple script type."""
        script_file = write_script(script={"type": "unknown"}, dest_dir=tmp_path)
        with pytest.raises(ValueError, match="Unsupported simple script type: unknown"):
            clusterlib_utils.get_reference_script_size(script_file=script_file)


class TestLedgerStateSnapshot:
    """Tests for reading the stake distribution snapshots of the ledger state.

    The `esSnapshots` entries have three formats to support: the `swd*` records of
    cardano-node 10.7+, the flat records before that, and the `snapShot` wrapper that
    a Leios enabled node puts around either of them.
    """

    ACTIVE_STAKE: tp.ClassVar[dict] = {
        f"keyHash-{KEY_HASH1}": {"swdDelegation": POOL_ID1, "swdStake": 10},
        f"keyHash-{KEY_HASH2}": {"swdDelegation": POOL_ID1, "swdStake": 20},
    }
    LEIOS_SNAPSHOT: tp.ClassVar[dict] = {
        "epochNo": 6,
        "leiosCommitteeSize": 900,
        "snapShot": {"activeStake": ACTIVE_STAKE, "stakePoolsSnapShot": {}},
    }

    def test_unwrap_leios(self):
        """Strip the `snapShot` wrapper of a Leios enabled node."""
        assert clusterlib_utils.unwrap_snapshot(ledger_snapshot=self.LEIOS_SNAPSHOT) == {
            "activeStake": self.ACTIVE_STAKE,
            "stakePoolsSnapShot": {},
        }

    def test_unwrap_unwrapped(self):
        """Keep a snapshot that has no `snapShot` wrapper as it is."""
        snapshot = {"activeStake": self.ACTIVE_STAKE}
        assert clusterlib_utils.unwrap_snapshot(ledger_snapshot=snapshot) == snapshot

    def test_stake_rec_leios(self):
        """Get the stake record from a wrapped snapshot."""
        stake_rec = clusterlib_utils.get_stake_rec(stake_snapshot=self.LEIOS_SNAPSHOT)
        assert stake_rec == self.ACTIVE_STAKE

    def test_stake_rec_active_stake(self):
        """Get the stake record from an unwrapped cardano-node 10.7+ snapshot."""
        stake_rec = clusterlib_utils.get_stake_rec(
            stake_snapshot={"activeStake": self.ACTIVE_STAKE}
        )
        assert stake_rec == self.ACTIVE_STAKE

    def test_stake_rec_legacy(self):
        """Get the stake record from a snapshot that predates `activeStake`."""
        stake = {f"keyHash-{KEY_HASH1}": 10}
        assert clusterlib_utils.get_stake_rec(stake_snapshot={"stake": stake}) == stake

    def test_stake_rec_unknown(self):
        """Fail on a snapshot that holds no stake record."""
        with pytest.raises(KeyError, match="Neither 'activeStake' nor 'stake' found"):
            clusterlib_utils.get_stake_rec(stake_snapshot={"epochNo": 6})

    def test_delegations_leios(self):
        """Get the delegations from a wrapped snapshot."""
        delegations = clusterlib_utils.get_snapshot_delegations(ledger_snapshot=self.LEIOS_SNAPSHOT)
        assert delegations == {POOL_ID1: [KEY_HASH1, KEY_HASH2]}

    def test_delegations_active_stake(self):
        """Get the delegations from an unwrapped cardano-node 10.7+ snapshot."""
        delegations = clusterlib_utils.get_snapshot_delegations(
            ledger_snapshot={"activeStake": self.ACTIVE_STAKE}
        )
        assert delegations == {POOL_ID1: [KEY_HASH1, KEY_HASH2]}

    def test_delegations_legacy(self):
        """Get the delegations from a snapshot that predates `activeStake`."""
        delegations = clusterlib_utils.get_snapshot_delegations(
            ledger_snapshot={"delegations": {f"keyHash-{KEY_HASH1}": POOL_ID1}}
        )
        assert delegations == {POOL_ID1: [KEY_HASH1]}

    def test_delegations_unknown(self):
        """Fail on a snapshot that holds no delegations."""
        with pytest.raises(KeyError, match="Neither 'stakePoolsSnapShot' nor 'delegations' found"):
            clusterlib_utils.get_snapshot_delegations(ledger_snapshot={"epochNo": 6})

    def test_snapshot_rec_leios(self):
        """Sum the stake amounts of a wrapped snapshot."""
        stake_rec = clusterlib_utils.get_stake_rec(stake_snapshot=self.LEIOS_SNAPSHOT)
        hashes = clusterlib_utils.get_snapshot_rec(ledger_snapshot=stake_rec)
        assert hashes == {KEY_HASH1: 10, KEY_HASH2: 20}


class EpochClusterStub:
    """Minimal stub of `ClusterLib` that provides the current epoch and records epoch waits."""

    def __init__(self, epoch: int) -> None:
        self.g_query = self
        self.epoch = epoch
        self.waited_for: list[int] = []

    def get_epoch(self) -> int:
        """Return the current epoch."""
        return self.epoch

    def wait_for_epoch(self, epoch_no: int, padding_seconds: int = 0) -> int:
        """Record the epoch to wait for and pretend it was reached."""
        del padding_seconds
        self.waited_for.append(epoch_no)
        self.epoch = epoch_no
        return epoch_no


class TestFirstRewards:
    """Tests for waiting for the first reward distribution."""

    @pytest.mark.parametrize(
        ("epoch", "expected"),
        [
            (0, clusterlib_utils.FIRST_REWARDS_EPOCH),
            (clusterlib_utils.FIRST_REWARDS_EPOCH - 1, 1),
            (clusterlib_utils.FIRST_REWARDS_EPOCH, 0),
            (clusterlib_utils.FIRST_REWARDS_EPOCH + 1, 0),
        ],
    )
    def test_get_epochs_to_rewards(self, epoch: int, expected: int):
        """Count the epochs left until the first rewards, never a negative number."""
        cluster_obj: tp.Any = EpochClusterStub(epoch=epoch)
        assert clusterlib_utils.get_epochs_to_rewards(cluster_obj=cluster_obj) == expected

    @pytest.mark.parametrize(
        ("epoch", "expected_waits"),
        [
            (clusterlib_utils.FIRST_REWARDS_EPOCH - 1, [clusterlib_utils.FIRST_REWARDS_EPOCH]),
            (clusterlib_utils.FIRST_REWARDS_EPOCH, []),
            (clusterlib_utils.FIRST_REWARDS_EPOCH + 1, []),
        ],
    )
    def test_wait_for_rewards(self, epoch: int, expected_waits: list[int]):
        """Wait for the first rewards epoch only when it was not reached yet."""
        cluster_obj: tp.Any = EpochClusterStub(epoch=epoch)
        clusterlib_utils.wait_for_rewards(cluster_obj=cluster_obj)
        assert cluster_obj.waited_for == expected_waits


class TestGenesisWindows:
    """Tests for the windows and intervals derived from the genesis."""

    @staticmethod
    def _get_cluster_obj(*, security_param: int, active_slots_coeff: float) -> tp.Any:
        return stubs.GenesisClusterStub(
            security_param=security_param,
            active_slots_coeff=active_slots_coeff,
            slot_length=1,
            epoch_length=1000,
        )

    @pytest.mark.parametrize(
        ("security_param", "active_slots_coeff", "expected"),
        (
            (10, 0.1, 300),
            (4, 0.05, 240),
            # In float arithmetic, `3k/f` is 299.99999999999994 and rounds down to 299
            (7, 0.07, 300),
        ),
    )
    def test_stability_window(self, security_param: int, active_slots_coeff: float, expected: int):
        """Compute `3k/f` exactly."""
        cluster_obj = self._get_cluster_obj(
            security_param=security_param, active_slots_coeff=active_slots_coeff
        )
        assert clusterlib_utils.get_stability_window(cluster_obj=cluster_obj) == expected

    @pytest.mark.parametrize(
        ("security_param", "active_slots_coeff", "expected"),
        (
            (10, 0.1, 400),
            (4, 0.05, 320),
            # In float arithmetic, `4k/f` is 240.00000000000003 and rounds up to 241
            (21, 0.35, 240),
            # Rounded up like in the ledger
            (1, 0.3, 14),
        ),
    )
    def test_randomness_stabilisation_window(
        self, security_param: int, active_slots_coeff: float, expected: int
    ):
        """Compute `4k/f` exactly, rounded up."""
        cluster_obj = self._get_cluster_obj(
            security_param=security_param, active_slots_coeff=active_slots_coeff
        )
        assert (
            clusterlib_utils.get_randomness_stabilisation_window(cluster_obj=cluster_obj)
            == expected
        )

    def test_block_interval_sec(self):
        """Compute the mean block interval from the slot length and active slot coefficient."""
        cluster_obj = stubs.GenesisClusterStub(
            security_param=10, active_slots_coeff=0.1, slot_length=0.2, epoch_length=1000
        )
        assert (
            clusterlib_utils.get_block_interval_sec(cluster_obj=tp.cast(tp.Any, cluster_obj)) == 2
        )


class SimulatedChainStub:
    """Stub of `ClusterLib` with a simulated clock and chain, for `wait_for_epoch_interval`.

    The current slot (wall clock) advances by sleeping and by waiting for blocks. The tip is
    the slot of the last forged block, and new blocks are forged every `block_interval` slots.
    """

    epoch_length = 100
    slot_length = 1.0
    epoch_length_sec = 100.0
    slots_offset = 0

    def __init__(self, *, now: float, tip_slot: int, block_interval: int = 20) -> None:
        self.now = now
        self.tip_slot = tip_slot
        self.block_interval = block_interval
        self.blocks_waited = 0
        self.slot_number_fails = False
        self.slot_number_fails_after_epoch = False
        # When stalled, no new blocks are forged and the tip doesn't move
        self.stalled = False
        self.g_query = self

    def get_epoch(self) -> int:
        return self.tip_slot // self.epoch_length

    def get_slot_number(self, timestamp: tp.Any) -> int:
        """Return the current slot, rounded up like the timestamp passed by the caller."""
        assert timestamp.tzinfo is not None, "The timestamp must be timezone aware"
        if self.slot_number_fails:
            msg = "PastHorizon"
            raise clusterlib.CLIError(msg)
        return int(self.now) + 1

    def time_from_epoch_start(self, tip: dict | None = None) -> float:
        """Compute the time the same way as `ClusterLib` does."""
        tip = tip or {"epoch": self.get_epoch(), "slot": self.tip_slot}
        slots_to_go = (int(tip["epoch"]) + 1) * self.epoch_length - (int(tip["slot"]) - 1)
        return float(self.epoch_length_sec - slots_to_go * self.slot_length)

    def sleep(self, secs: float) -> None:
        self.now += secs

    def wait_for_new_block(self, new_blocks: int = 1) -> None:
        for __ in range(new_blocks):
            self.blocks_waited += 1
            if self.stalled:
                self.now += self.block_interval
                continue
            self.tip_slot = max(self.tip_slot, int(self.now)) + self.block_interval
            self.now = self.tip_slot

    def wait_for_new_epoch(self) -> None:
        next_epoch = self.get_epoch() + 1
        while self.get_epoch() < next_epoch:
            self.wait_for_new_block()
        if self.slot_number_fails_after_epoch:
            self.slot_number_fails = True


class TestWaitForEpochInterval:
    """Tests for `wait_for_epoch_interval`."""

    @pytest.fixture
    def chain(self, monkeypatch: pytest.MonkeyPatch) -> tp.Callable[..., SimulatedChainStub]:
        def _chain(**kwargs: tp.Any) -> SimulatedChainStub:
            chain_obj = SimulatedChainStub(**kwargs)
            monkeypatch.setattr(clusterlib_utils.time, "sleep", chain_obj.sleep)
            return chain_obj

        return _chain

    def test_in_interval_no_block_wait(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Don't wait for a new block when already in the interval."""
        chain_obj = chain(now=150, tip_slot=145)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=10, stop=-20
        )
        assert chain_obj.blocks_waited == 0
        assert chain_obj.now == 150

    def test_sleep_until_start(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Sleep until the start of the interval, based on wall clock."""
        chain_obj = chain(now=105, tip_slot=102)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=30, stop=-20
        )
        assert chain_obj.blocks_waited == 0
        assert 130 <= chain_obj.now <= 132

    def test_tip_in_previous_epoch(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Wait until the tip is in the epoch of the interval."""
        chain_obj = chain(now=205, tip_slot=190)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=1, stop=-20
        )
        assert chain_obj.blocks_waited == 1
        assert chain_obj.get_epoch() == 2

    def test_after_interval(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Wait for the next epoch when already past the interval."""
        chain_obj = chain(now=190, tip_slot=185)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=1, stop=-20
        )
        assert chain_obj.get_epoch() == 2
        assert 200 <= chain_obj.now <= 280

    def test_check_slot_uses_tip(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Use the tip, with an up-to-date slot number, when the slot needs to be checked."""
        chain_obj = chain(now=150, tip_slot=145)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=10, stop=-20, check_slot=True
        )
        assert chain_obj.blocks_waited == 1

    def test_slot_number_query_fails(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Fall back to the tip when the slot number for the current time can't be queried."""
        chain_obj = chain(now=150, tip_slot=145)
        chain_obj.slot_number_fails = True
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=10, stop=-20
        )
        assert chain_obj.blocks_waited == 1

    def test_first_block_after_stop(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Wait for the next epoch when the first block of the epoch comes after the interval."""
        chain_obj = chain(now=205, tip_slot=190, block_interval=60)
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=10, stop=50
        )
        assert chain_obj.get_epoch() == 3
        assert 310 <= chain_obj.now <= 350

    def test_force_epoch(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Fail when the interval cannot be reached in the current epoch."""
        chain_obj = chain(now=190, tip_slot=185)
        with pytest.raises(RuntimeError, match="Cannot reach the given interval"):
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=tp.cast(tp.Any, chain_obj), start=1, stop=-20, force_epoch=True
            )

    def test_tip_doesnt_reach_epoch(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Fail when the tip doesn't reach the current epoch."""
        chain_obj = chain(now=205, tip_slot=190)
        chain_obj.stalled = True
        with pytest.raises(RuntimeError, match="The tip didn't reach the current epoch"):
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=tp.cast(tp.Any, chain_obj), start=1, stop=-20
            )
        assert chain_obj.blocks_waited == clusterlib_utils.TIP_EPOCH_MAX_BLOCKS

    def test_slot_number_query_fails_later(self, chain: tp.Callable[..., SimulatedChainStub]):
        """Switch to the tip when the wall clock query starts failing in the middle of the wait."""
        chain_obj = chain(now=190, tip_slot=185)
        chain_obj.slot_number_fails_after_epoch = True
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=tp.cast(tp.Any, chain_obj), start=1, stop=-20
        )
        assert chain_obj.get_epoch() == 2
        assert chain_obj.slot_number_fails
