"""Tests for `utils.logfiles` ignore rules handling and expected messages checks."""

import os
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


def _write_log(state_dir: pl.Path, name: str, content: str) -> pl.Path:
    """Create a log file with the given content and return its path."""
    logfile = state_dir / name
    logfile.write_text(content, encoding="utf-8")
    return logfile


def test_check_msgs_present(tmp_path: pl.Path):
    """Check that no errors are reported when the expected message is in the log."""
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="foo\nexpected msg\n")

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout", "expected msg")],
        seek_offsets={str(logfile): (0, logfile.stat().st_ino)},
        state_dir=tmp_path,
        timestamp=0.0,
    )
    assert errors == []


def test_check_msgs_missing(tmp_path: pl.Path):
    """Check that a missing expected message is reported."""
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="foo\nbar\n")

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout", "expected msg")],
        seek_offsets={str(logfile): (0, logfile.stat().st_ino)},
        state_dir=tmp_path,
        timestamp=0.0,
    )
    assert len(errors) == 1
    assert "No line matching" in errors[0]


def test_check_msgs_no_files_matched(tmp_path: pl.Path):
    """Check that a glob matching no log file is reported instead of silently passing."""
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="foo\n")

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stderr", "expected msg")],
        seek_offsets={str(logfile): (0, logfile.stat().st_ino)},
        state_dir=tmp_path,
        timestamp=0.0,
    )
    assert errors == [f"No files matched glob '*.stderr' in '{tmp_path}'."]


def test_check_msgs_dotted_dir_in_path(tmp_path: pl.Path):
    """Check that log files are not skipped as rotated because of dots in parent dirs.

    Only the file *name* decides whether a file is a rotated log. A parent directory
    like "build.123" must not cause the file to be treated as rotated and skipped,
    which would silently pass the check.
    """
    state_dir = tmp_path / "build.123"
    state_dir.mkdir()
    logfile = _write_log(state_dir=state_dir, name="node1.stdout", content="foo\n")

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout", "expected msg")],
        seek_offsets={str(logfile): (0, logfile.stat().st_ino)},
        state_dir=state_dir,
        timestamp=0.0,
    )
    assert len(errors) == 1
    assert "No line matching" in errors[0]


def test_check_msgs_rotated_file_skipped(tmp_path: pl.Path):
    """Check that rotated log files in the offsets mapping are not searched directly.

    Rotated files are searched via `_get_rotated_logs` of the live log file, so a
    rotated file name in the offsets mapping is skipped and doesn't produce an error
    on its own.
    """
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="expected msg\n")
    rotated = _write_log(state_dir=tmp_path, name="node1.stdout.1", content="foo\n")

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout*", "expected msg")],
        seek_offsets={
            str(logfile): (0, logfile.stat().st_ino),
            str(rotated): (0, rotated.stat().st_ino),
        },
        state_dir=tmp_path,
        timestamp=0.0,
    )
    assert errors == []


def test_check_msgs_only_rotated_matched(tmp_path: pl.Path):
    """Check that a glob matching only rotated log files is reported.

    Rotated files are filtered out before the "no files matched" check, so a glob
    whose only matches are rotated file names is reported as matching no files.
    """
    rotated = _write_log(state_dir=tmp_path, name="node1.stdout.1", content="expected msg\n")

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout*", "expected msg")],
        seek_offsets={str(rotated): (0, rotated.stat().st_ino)},
        state_dir=tmp_path,
        timestamp=0.0,
    )
    assert len(errors) == 1
    assert "No files matched glob" in errors[0]


def test_check_msgs_dotted_file_name(tmp_path: pl.Path):
    """Check that a live log file with dots and digits in its name is not skipped.

    A name like "node-1.2.stdout" must not be treated as a rotated log file - only
    names *ending* with a dot and digits are rotated logs.
    """
    logfile = _write_log(state_dir=tmp_path, name="node-1.2.stdout", content="expected msg\n")

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout", "expected msg")],
        seek_offsets={str(logfile): (0, logfile.stat().st_ino)},
        state_dir=tmp_path,
        timestamp=0.0,
    )
    assert errors == []


def test_check_msgs_found_in_rotated(tmp_path: pl.Path):
    """Check that the expected message is found in a rotated version of the log file.

    The search of a live log file traverses its rotated versions, so a message that
    was rotated away is still found.
    """
    rotated = _write_log(state_dir=tmp_path, name="node1.stdout.1", content="expected msg\n")
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="foo\n")
    # Make the rotated file older than the live file
    live_mtime = logfile.stat().st_mtime
    os.utime(rotated, (live_mtime - 10, live_mtime - 10))

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout", "expected msg")],
        seek_offsets={str(logfile): (0, logfile.stat().st_ino)},
        state_dir=tmp_path,
        timestamp=0.0,
    )
    assert errors == []


@pytest.mark.parametrize("msg_before_offset", (True, False), ids=("before_offset", "after_offset"))
def test_check_msgs_seek_offset(tmp_path: pl.Path, msg_before_offset: bool):
    """Check that the search starts at the recorded seek offset.

    A message before the offset is not found, a message after the offset is found.
    """
    first_line = "expected msg\n" if msg_before_offset else "foo\n"
    second_line = "bar\n" if msg_before_offset else "expected msg\n"
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content=first_line + second_line)

    errors = logfiles.check_msgs_presence_in_logs(
        regex_pairs=[("*.stdout", "expected msg")],
        seek_offsets={str(logfile): (len(first_line), logfile.stat().st_ino)},
        state_dir=tmp_path,
        timestamp=0.0,
    )
    if msg_before_offset:
        assert len(errors) == 1
        assert "No line matching" in errors[0]
    else:
        assert errors == []


def test_offset_file_roundtrip(tmp_path: pl.Path):
    """Check that the seek offset and inode survive the offset file round trip."""
    offset_file = tmp_path / ".node1.stdout.offset"
    logfiles._write_offset_file(offset_file=offset_file, seek=1234, inode=56)

    assert logfiles._read_offset_file(offset_file=offset_file) == (1234, 56)


@pytest.mark.parametrize(
    ("content", "expected"),
    (
        pytest.param("1234\n", (1234, None), id="missing_inode"),
        pytest.param("garbage\n", (0, None), id="garbage"),
        pytest.param("123\nabc\n", (123, None), id="corrupt_inode"),
        pytest.param(None, (0, None), id="missing_file"),
    ),
)
def test_read_offset_file_fallback(
    tmp_path: pl.Path, content: str | None, expected: tuple[int, int | None]
):
    """Check reading of an incomplete or invalid or missing offset file."""
    offset_file = tmp_path / ".node1.stdout.offset"
    if content is not None:
        offset_file.write_text(content, encoding="utf-8")

    assert logfiles._read_offset_file(offset_file=offset_file) == expected


def test_rotated_logs_seek_by_inode(tmp_path: pl.Path):
    """Check that the seek offset is applied to the file with the matching inode."""
    rotated = _write_log(state_dir=tmp_path, name="node1.stdout.1", content="old content\n")
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="new content\n")
    live_mtime = logfile.stat().st_mtime
    os.utime(rotated, (live_mtime - 10, live_mtime - 10))

    records = logfiles._get_rotated_logs(
        logfile=logfile, seek=5, timestamp=0.0, inode=rotated.stat().st_ino
    )
    assert [(r.logfile, r.seek) for r in records] == [(rotated, 5), (logfile, 0)]


def test_rotated_logs_seek_dropped(tmp_path: pl.Path):
    """Check that the seek offset is dropped when its file was already fully searched.

    When the file the seek offset was recorded for was not modified since the last search,
    it is filtered out by the timestamp check. The seek offset must not be applied to
    another file, as that would skip unsearched content.
    """
    rotated = _write_log(state_dir=tmp_path, name="node1.stdout.1", content="old content\n")
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="new content\n")
    live_mtime = logfile.stat().st_mtime
    os.utime(rotated, (live_mtime - 10, live_mtime - 10))

    records = logfiles._get_rotated_logs(
        logfile=logfile, seek=5, timestamp=live_mtime - 5, inode=rotated.stat().st_ino
    )
    assert [(r.logfile, r.seek) for r in records] == [(logfile, 0)]


def test_rotated_logs_seek_without_inode(tmp_path: pl.Path):
    """Check that without a known inode the seek offset is applied to the oldest file."""
    rotated = _write_log(state_dir=tmp_path, name="node1.stdout.1", content="old content\n")
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="new content\n")
    live_mtime = logfile.stat().st_mtime
    os.utime(rotated, (live_mtime - 10, live_mtime - 10))

    records = logfiles._get_rotated_logs(logfile=logfile, seek=5, timestamp=0.0, inode=None)
    assert [(r.logfile, r.seek) for r in records] == [(rotated, 5), (logfile, 0)]


def _search_cluster_like(logfile: pl.Path) -> list[tuple[pl.Path, str]]:
    """Search the log file for errors the same way `search_cluster_logs` does."""
    seek, timestamp, inode = logfiles._load_search_state(logfile=logfile)
    return logfiles._search_log_lines(
        logfile=logfile,
        rotated_logs=logfiles._get_rotated_logs(
            logfile=logfile, seek=seek, timestamp=timestamp, inode=inode
        ),
        errors_re=logfiles.ERRORS_RE,
    )


def test_search_log_lines_offset_persistence(tmp_path: pl.Path):
    """Check that repeated searches report each error only once."""
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="ok\nerror one\n")

    errors = _search_cluster_like(logfile=logfile)
    assert [e[1] for e in errors] == ["error one"]

    # Second search must start where the first search ended
    errors = _search_cluster_like(logfile=logfile)
    assert errors == []

    with open(logfile, "a", encoding="utf-8") as outfile:
        outfile.write("error two\n")
    # Make sure the log file modification time is newer than the recorded search time
    # even on filesystems with coarse timestamps.
    offset_mtime = logfiles._get_offset_file(logfile=logfile).stat().st_mtime
    os.utime(logfile, (offset_mtime + 10, offset_mtime + 10))

    errors = _search_cluster_like(logfile=logfile)
    assert [e[1] for e in errors] == ["error two"]


def test_search_log_lines_after_rotation(tmp_path: pl.Path):
    """Check that errors at the beginning of a fresh log file are found after rotation.

    After the log file was searched and then rotated without further writes, the recorded
    seek offset belongs to the rotated file. The offset must not be applied to the fresh
    live log file, so errors at its beginning are reported.
    """
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="ok\nerror one\n")

    errors = _search_cluster_like(logfile=logfile)
    assert [e[1] for e in errors] == ["error one"]

    # Rotate: rename the searched file and let its mtime predate the last search
    rotated = tmp_path / "node1.stdout.1"
    logfile.rename(rotated)
    offset_mtime = logfiles._get_offset_file(logfile=logfile).stat().st_mtime
    os.utime(rotated, (offset_mtime - 10, offset_mtime - 10))

    # The fresh live log file has an error at its beginning and is larger than the
    # recorded seek offset, so a misapplied offset would skip the error.
    _write_log(state_dir=tmp_path, name="node1.stdout", content="error two\n" + "padding\n" * 5)
    # Make sure the log file modification time is newer than the recorded search time
    # even on filesystems with coarse timestamps.
    os.utime(logfile, (offset_mtime + 10, offset_mtime + 10))

    errors = _search_cluster_like(logfile=logfile)
    assert [e[1] for e in errors] == ["error two"]


def test_rotated_logs_seek_on_live(tmp_path: pl.Path):
    """Check that the seek offset is applied to the live file when its inode matches.

    The live log file is not the oldest file in the list, so this checks that the inode
    matching is not limited to the oldest file.
    """
    rotated = _write_log(state_dir=tmp_path, name="node1.stdout.1", content="old content\n")
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="new content\n")
    live_mtime = logfile.stat().st_mtime
    os.utime(rotated, (live_mtime - 10, live_mtime - 10))

    records = logfiles._get_rotated_logs(
        logfile=logfile, seek=5, timestamp=0.0, inode=logfile.stat().st_ino
    )
    assert [(r.logfile, r.seek) for r in records] == [(rotated, 0), (logfile, 5)]


def test_search_log_lines_rotation_with_new_content(tmp_path: pl.Path):
    """Check the search through a rotated file with unsearched content and a fresh live file.

    Content that was appended to the log file after the last search and then rotated away
    is searched (starting at the recorded seek offset), together with the whole fresh live
    log file. Already searched content is not reported again. The new search state is
    recorded for the live log file.
    """
    logfile = _write_log(state_dir=tmp_path, name="node1.stdout", content="ok\nerror one\n")

    errors = _search_cluster_like(logfile=logfile)
    assert [e[1] for e in errors] == ["error one"]

    # Append new content and rotate. The renamed file keeps its inode and modification time.
    with open(logfile, "a", encoding="utf-8") as outfile:
        outfile.write("error two\n")
    rotated = tmp_path / "node1.stdout.1"
    logfile.rename(rotated)
    live_content = "error three\n"
    _write_log(state_dir=tmp_path, name="node1.stdout", content=live_content)

    # Make sure the log file modification times are newer than the recorded search time
    # even on filesystems with coarse timestamps, and that the rotated file is older
    # than the live file.
    offset_mtime = logfiles._get_offset_file(logfile=logfile).stat().st_mtime
    os.utime(rotated, (offset_mtime + 5, offset_mtime + 5))
    os.utime(logfile, (offset_mtime + 10, offset_mtime + 10))

    errors = _search_cluster_like(logfile=logfile)
    assert [e[1] for e in errors] == ["error two", "error three"]

    # The new search state belongs to the live log file
    offset_file = logfiles._get_offset_file(logfile=logfile)
    assert logfiles._read_offset_file(offset_file=offset_file) == (
        len(live_content),
        logfile.stat().st_ino,
    )
