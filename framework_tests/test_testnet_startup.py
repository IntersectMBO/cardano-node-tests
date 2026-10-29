"""Unit tests for the testnet startup script, without a running node."""

import os
import pathlib as pl
import socket
import subprocess

import pytest


@pytest.mark.parametrize(
    ("ready_after", "replay_mode", "node_state"),
    [
        (0, "none", "RUNNING"),
        (12, "none", "RUNNING"),
        (180, "none", "RUNNING"),
        (400, "advancing", "RUNNING"),
        (None, "none", "RUNNING"),
        (None, "stalled", "RUNNING"),
        (None, "none", "FATAL"),
        (None, "none", "EXITED"),
    ],
)
def test_wait_for_node_socket(
    tmp_path: pl.Path, ready_after: int | None, replay_mode: str, node_state: str
):
    """Allow long replay, but stop on a failed node or sustained lack of progress."""
    script_path = (
        pl.Path(__file__).resolve().parents[1]
        / "cardano_node_tests/cluster_scripts/testnets/start-cluster"
    )
    script = script_path.read_text().removesuffix('main "$@"\n')
    socket_path = tmp_path / "node.socket"
    pending_socket = tmp_path / "pending.socket"
    with socket.socket(socket.AF_UNIX) as node_socket:
        node_socket.bind(str(pending_socket))
        if ready_after == 0:
            pending_socket.rename(socket_path)

        # Exercise the actual Bash function under its normal strict shell settings.
        # Mock external services and advance startup without real sleeps.
        harness = r"""
STATE_CLUSTER="$TEST_STATE_DIR"
CARDANO_NODE_SOCKET_PATH="$STATE_CLUSTER/node.socket"
NETWORK_MAGIC=2
sleep_count=0
trap 'echo "SLEEPS=$sleep_count"' EXIT
SUPERVISORD_SOCKET_PATH="$STATE_CLUSTER/supervisord.sock"
supervisord() { :; }
supervisorctl() {
  echo "relay1 $NODE_STATE"
  [ "$NODE_STATE" = RUNNING ]
}
sleep() {
  sleep_count=$((sleep_count + 1))
  if [ "$REPLAY_MODE" = advancing ] && [ "$sleep_count" -ge 12 ]; then
    echo "LedgerReplay Replayed block: slot $sleep_count" >> "$STATE_CLUSTER/relay1.stdout"
  elif [ "$REPLAY_MODE" = stalled ]; then
    echo "$sleep_count LedgerReplay Replayed block: slot 42" >> "$STATE_CLUSTER/relay1.stdout"
  fi
  if [ "$sleep_count" -eq "$READY_AFTER" ]; then
    mv "$STATE_CLUSTER/pending.socket" "$CARDANO_NODE_SOCKET_PATH"
  fi
}
cardano-cli() { echo '{"syncProgress":"100.00"}'; }
jq() { echo '100.00'; }
tn_start_node
"""
        result = subprocess.run(
            ["bash", "-c", script + harness],
            env=os.environ
            | {
                "TEST_STATE_DIR": str(tmp_path),
                "READY_AFTER": str(ready_after if ready_after is not None else -1),
                "REPLAY_MODE": replay_mode,
                "NODE_STATE": node_state,
            },
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    if ready_after is None:
        assert result.returncode != 0
        assert "Failed to start the relay node" in result.stderr
        assert "Waiting to make sure the chain is synced" not in result.stdout
        if node_state == "RUNNING":
            assert "no socket or ledger replay progress for 30 minutes" in result.stderr
            expected_sleeps = 181 if replay_mode == "stalled" else 180
            assert f"SLEEPS={expected_sleeps}" in result.stdout
        else:
            assert node_state in result.stderr
            assert "SLEEPS=0" in result.stdout
    else:
        assert result.returncode == 0, result.stderr
        assert f"SLEEPS={ready_after}" in result.stdout
        assert "Waiting to make sure the chain is synced" in result.stdout
