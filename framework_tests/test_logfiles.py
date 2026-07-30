"""Tests for `utils.logfiles` ignore rules handling."""

import pathlib as pl
import re

import pytest

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools


@pytest.fixture
def cluster_env(tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch) -> cluster_nodes.ClusterEnv:
    """Return a `ClusterEnv` pointing to a temp state dir.

    Patch `get_cluster_env` and `get_basetemp` so that both the ignore rules files
    and the lock file land under `tmp_path`.
    """
    state_dir = tmp_path / "state-cluster0"
    state_dir.mkdir()
    env = cluster_nodes.ClusterEnv(
        socket_path=state_dir / "relay1.socket",
        state_dir=state_dir,
        work_dir=tmp_path,
        instance_num=0,
        cluster_era="conway",
        command_era="conway",
    )
    monkeypatch.setattr(cluster_nodes, "get_cluster_env", lambda: env)
    monkeypatch.setattr(temptools, "get_basetemp", lambda: tmp_path)
    return env


def test_ignore_rules_roundtrip(cluster_env: cluster_nodes.ClusterEnv):
    """Check that a rule written by `add_ignore_rule` is parsed back unchanged.

    The regex contains ";;", the separator used in the rules file format. The parser
    must split only on the first two separators, so the regex survives the round trip
    (and comes back without a trailing newline).
    """
    logfiles.add_ignore_rule(
        files_glob="*.stdout", regex="err;;or.*fai;;l", ignore_file_id="test_id"
    )

    rules = logfiles._get_ignore_rules(cluster_env=cluster_env, timestamp=1000.0)
    assert rules == [("*.stdout", "err;;or.*fai;;l")]


@pytest.mark.parametrize(
    ("skip_after", "expected_kept"),
    (
        pytest.param(0.0, True, id="no_expiry"),
        pytest.param(999.0, False, id="expired"),
        pytest.param(1000.0, True, id="boundary"),
        pytest.param(1001.0, True, id="not_expired"),
    ),
)
def test_ignore_rules_expiry(
    cluster_env: cluster_nodes.ClusterEnv, skip_after: float, expected_kept: bool
):
    """Check rule expiration against the timestamp of the last log check.

    A rule expires only when `0 < skip_after < timestamp`. A rule with `skip_after=0.0`
    never expires.
    """
    logfiles.add_ignore_rule(
        files_glob="*", regex="foo", ignore_file_id="test_id", skip_after=skip_after
    )

    rules = logfiles._get_ignore_rules(cluster_env=cluster_env, timestamp=1000.0)
    assert rules == ([("*", "foo")] if expected_kept else [])


def test_ignore_rules_skip_lines_without_separator(cluster_env: cluster_nodes.ClusterEnv):
    """Check that lines without the ";;" separator are skipped without an error."""
    rules_file = cluster_env.state_dir / f"{logfiles.ERRORS_IGNORE_FILE_NAME}_test_id"
    rules_file.write_text("\nnot a rule\n*.stdout;;0.0;;valid\n", encoding="utf-8")

    rules = logfiles._get_ignore_rules(cluster_env=cluster_env, timestamp=1000.0)
    assert rules == [("*.stdout", "valid")]


def test_ignore_rules_multiple_files(cluster_env: cluster_nodes.ClusterEnv):
    """Check that rules are collected from all ignore files in the state dir."""
    logfiles.add_ignore_rule(files_glob="*.stdout", regex="foo", ignore_file_id="id1")
    logfiles.add_ignore_rule(files_glob="*.stderr", regex="bar", ignore_file_id="id2")

    rules = logfiles._get_ignore_rules(cluster_env=cluster_env, timestamp=1000.0)
    assert sorted(rules) == [("*.stderr", "bar"), ("*.stdout", "foo")]


def test_ignore_rules_append_same_file(cluster_env: cluster_nodes.ClusterEnv):
    """Check that rules with the same id are appended to a single file and all parsed."""
    logfiles.add_ignore_rule(files_glob="*.stdout", regex="foo", ignore_file_id="id1")
    logfiles.add_ignore_rule(files_glob="*.stderr", regex="bar", ignore_file_id="id1")

    rules_files = list(cluster_env.state_dir.glob(f"{logfiles.ERRORS_IGNORE_FILE_NAME}_*"))
    assert len(rules_files) == 1

    rules = logfiles._get_ignore_rules(cluster_env=cluster_env, timestamp=1000.0)
    assert rules == [("*.stdout", "foo"), ("*.stderr", "bar")]


def test_clean_ignore_rules(cluster_env: cluster_nodes.ClusterEnv):
    """Check that `clean_ignore_rules` removes only the file with the given id."""
    logfiles.add_ignore_rule(files_glob="*", regex="foo", ignore_file_id="id1")
    logfiles.add_ignore_rule(files_glob="*", regex="bar", ignore_file_id="id2")

    logfiles.clean_ignore_rules(ignore_file_id="id1")

    assert not (cluster_env.state_dir / f"{logfiles.ERRORS_IGNORE_FILE_NAME}_id1").exists()
    rules = logfiles._get_ignore_rules(cluster_env=cluster_env, timestamp=1000.0)
    assert rules == [("*", "bar")]


@pytest.mark.parametrize(
    ("ignore_rules", "regexes", "expected"),
    (
        pytest.param([("node1.stdout", "foo")], [], "foo", id="glob_match"),
        pytest.param([("*.stderr", "foo")], [], "nothing_to_ignore", id="glob_no_match"),
        pytest.param([], [], "nothing_to_ignore", id="empty"),
        pytest.param([("*.stdout", "foo")], ["foo"], "foo", id="dedup"),
    ),
)
def test_get_ignore_regex(ignore_rules: list[tuple[str, str]], regexes: list[str], expected: str):
    """Check combining of global regexes and file-specific ignore rules.

    Rules are applied only when their file glob matches the log file name, duplicates
    are folded, and an empty result falls back to a placeholder regex.
    """
    regex = logfiles._get_ignore_regex(
        ignore_rules=ignore_rules, regexes=regexes, logfile=pl.Path("/tmp/node1.stdout")
    )
    assert regex == expected


def test_get_ignore_regex_multiple():
    """Check that multiple applicable regexes are combined into an alternation."""
    regex = logfiles._get_ignore_regex(
        ignore_rules=[("*.stdout", "foo")], regexes=["bar"], logfile=pl.Path("/tmp/node1.stdout")
    )
    assert sorted(regex.split("|")) == ["bar", "foo"]


def test_empty_regex_matches_everything(cluster_env: cluster_nodes.ClusterEnv):
    """Pin the hazard of an empty regex in an ignore rule.

    An empty regex round-trips through the rules file and becomes an empty branch in
    the combined alternation, which matches every line - i.e. it suppresses all errors
    for the matching files. This documents the current behavior; `add_ignore_rule`
    does not reject empty regexes.
    """
    logfiles.add_ignore_rule(files_glob="*", regex="", ignore_file_id="id1")

    rules = logfiles._get_ignore_rules(cluster_env=cluster_env, timestamp=1000.0)
    assert rules == [("*", "")]

    regex = logfiles._get_ignore_regex(
        ignore_rules=rules, regexes=["foo"], logfile=pl.Path("/tmp/node1.stdout")
    )
    assert re.search(regex, "arbitrary line")
