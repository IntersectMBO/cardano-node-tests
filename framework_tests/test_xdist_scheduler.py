"""Tests for the custom pytest-xdist scheduler plugin."""

import os
import pathlib as pl
import subprocess
import sys

import pytest

REPO_ROOT = pl.Path(__file__).parents[1]

# Enough for the inner runs, which spawn a real xdist worker.
INNER_TIMEOUT = 300

INI_FILE = """[pytest]
addopts =
markers =
    smoke: smoke test
    long: long running test
    xdist_group(name): group tests together
    xdist_split(*names): spread tests across workers
"""

CONFTEST_FILE = 'pytest_plugins = ("cardano_node_tests.pytest_plugins.xdist_scheduler",)\n'

TESTS_FILE = """import pytest


@pytest.mark.smoke
def test_smoke():
    pass


@pytest.mark.long
def test_long():
    pass


@pytest.mark.xdist_group("grp")
def test_group():
    pass


@pytest.mark.xdist_split("res")
def test_split():
    pass


@pytest.mark.xdist_group("grp")
@pytest.mark.smoke
@pytest.mark.xdist_split("res")
@pytest.mark.long
def test_all_markers():
    pass


def test_plain():
    pass


@pytest.mark.xdist_group("outer")
class TestNested:
    @pytest.mark.xdist_group("inner")
    def test_nested_groups(self):
        pass
"""

# The nodeids the inner pytest run collects, without the suffixes the scheduler adds.
ORIG_NODEIDS = (
    "test_scheduled.py::test_smoke",
    "test_scheduled.py::test_long",
    "test_scheduled.py::test_group",
    "test_scheduled.py::test_split",
    "test_scheduled.py::test_all_markers",
    "test_scheduled.py::test_plain",
    "test_scheduled.py::TestNested::test_nested_groups",
)


@pytest.fixture
def inner_testdir(tmp_path: pl.Path) -> pl.Path:
    """Create a standalone pytest project that loads the scheduler plugin."""
    (tmp_path / "pytest.ini").write_text(INI_FILE, encoding="utf-8")
    (tmp_path / "conftest.py").write_text(CONFTEST_FILE, encoding="utf-8")
    (tmp_path / "test_scheduled.py").write_text(TESTS_FILE, encoding="utf-8")
    return tmp_path


def _run(testdir: pl.Path, *args: str) -> str:
    """Run pytest in `testdir` and return its combined output."""
    pythonpath = os.pathsep.join([str(REPO_ROOT), os.environ.get("PYTHONPATH", "")])
    env = {
        **os.environ,
        "PYTHONPATH": pythonpath.rstrip(os.pathsep),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    # Keep both streams: a usage error in the inner run (e.g. a missing plugin) is
    # reported on stderr, and would otherwise be lost.
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            "pytest.ini",
            "-p",
            "no:cacheprovider",
            *args,
        ],
        capture_output=True,
        check=False,
        cwd=testdir,
        encoding="utf-8",
        env=env,
        timeout=INNER_TIMEOUT,
    )
    return f"{proc.stdout}{proc.stderr}"


def _collect(testdir: pl.Path, *args: str) -> str:
    """Run `pytest --collect-only` in `testdir` and return its output."""
    return _run(testdir, "--collect-only", "-q", *args)


class TestNodeidSuffixes:
    """Tests for the nodeid suffixes the scheduler needs for its scopes."""

    def test_suffixes_added(self, inner_testdir: pl.Path):
        """Check that a suffix is added for each marker the scheduler recognizes."""
        collected = _collect(inner_testdir).splitlines()
        assert "test_scheduled.py::test_smoke@smoke" in collected
        assert "test_scheduled.py::test_long@long" in collected
        assert "test_scheduled.py::test_group@grp" in collected
        assert "test_scheduled.py::test_split@split=res" in collected
        # An unmarked test needs no scope, so its nodeid stays untouched
        assert "test_scheduled.py::test_plain" in collected

    def test_suffix_order(self, inner_testdir: pl.Path):
        """Check the order of the suffixes on a test that has all the markers.

        `_split_scope` takes the group name from the first suffix and expects the
        `long` marker in the last one.
        """
        collected = _collect(inner_testdir).splitlines()
        assert "test_scheduled.py::test_all_markers@grp@smoke@split=res@long" in collected

    def test_closest_group_wins(self, inner_testdir: pl.Path):
        """Check that only the closest `xdist_group` marker forms the group scope."""
        collected = _collect(inner_testdir).splitlines()
        assert "test_scheduled.py::TestNested::test_nested_groups@inner" in collected

    def test_deselect_from_file(self, inner_testdir: pl.Path):
        """Check that the suffixes don't break deselection by original nodeid.

        The scheduler rewrites `item._nodeid`, while `pytest-select` matches the
        `--deselect-from-file` entries against it. The scheduler hook must therefore run
        after the selection plugins, otherwise only the tests without any of the
        scheduler's markers would get deselected.
        """
        deselect_file = inner_testdir / "deselected.txt"
        deselect_file.write_text("\n".join(ORIG_NODEIDS), encoding="utf-8")

        collected = _collect(inner_testdir, f"--deselect-from-file={deselect_file}")
        assert f"({len(ORIG_NODEIDS)} deselected)" in collected, collected

    def test_no_loadgroup_suffix(self, inner_testdir: pl.Path):
        """Check that xdist's own `loadgroup` suffixing doesn't add a second suffix.

        `pytest_xdist_make_scheduler` ignores `--dist`, so xdist's `loadgroup`
        scheduling never runs, but its worker-side nodeid suffixing would still add a
        group suffix of its own in front of the one added here. Unlike this module,
        xdist joins the names of all the `xdist_group` markers of the test.
        """
        out = _run(inner_testdir, "-v", "--no-header", "-n", "1", "--dist=loadgroup")
        assert "test_scheduled.py::test_group@grp " in out, out
        assert "@grp@grp" not in out, out
        assert "test_scheduled.py::TestNested::test_nested_groups@inner " in out, out
        assert "inner_outer" not in out, out
