"""Unit tests for `scripts/stats_json.py`.

The properties that matter are that the counts match what
`count_test_results.py` reports, that an interrupted testrun is visible as
`never_run`, and that the duration is the wall clock span rather than the sum
of the per-test durations.
"""

import importlib.util
import json
import pathlib as pl
import sys
import types
import typing as tp

import pytest

SCRIPTS_DIR = pl.Path(__file__).parents[1] / "scripts"

# Allure timestamps are milliseconds since the epoch.
BASE_MS = 1_700_000_000_000
SECOND_MS = 1000


def _load_stats_json() -> types.ModuleType:
    """Import the script by path, since `scripts` is not a package."""
    spec = importlib.util.spec_from_file_location("stats_json", SCRIPTS_DIR / "stats_json.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["stats_json"] = module
    spec.loader.exec_module(module)
    return module


stats_json = _load_stats_json()


def _result(
    results_dir: pl.Path,
    name: str,
    status: str,
    start: int,
    stop: int,
    message: str = "",
) -> None:
    """Write one Allure result file."""
    record: tp.Dict[str, tp.Any] = {
        "name": name,
        "fullName": name,
        "historyId": name,
        "status": status,
        "start": start,
        "stop": stop,
    }
    if message:
        record["statusDetails"] = {"message": message}
    # The file name only has to end in `-result.json`; uniqueness comes from
    # the prefix, the same way allure writes one file per attempt.
    path = results_dir / f"{name}-{start}-result.json"
    path.write_text(json.dumps(record), encoding="utf-8")


@pytest.fixture
def results_dir(tmp_path: pl.Path) -> pl.Path:
    """Build a results dir shaped like a real run.

    `run_tests.sh` runs pytest twice into one directory. The first pass
    registers every collected test as skipped. The real run then adds a second
    file per test. Every test here therefore has two files, except the one
    that models an interrupted run.
    """
    rdir = tmp_path / "allure-results"
    rdir.mkdir()
    registered = ["pass_one", "pass_two", "fails", "broke", "skips", "never_ran"]
    for name in registered:
        _result(rdir, name, "skipped", BASE_MS, BASE_MS, stats_json.counter.SKIPALL_MSG)

    _result(rdir, "pass_one", "passed", BASE_MS + SECOND_MS, BASE_MS + 5 * SECOND_MS)
    _result(rdir, "pass_two", "passed", BASE_MS + 2 * SECOND_MS, BASE_MS + 4 * SECOND_MS)
    _result(rdir, "fails", "failed", BASE_MS + SECOND_MS, BASE_MS + 9 * SECOND_MS)
    _result(rdir, "broke", "broken", BASE_MS + SECOND_MS, BASE_MS + 3 * SECOND_MS)
    _result(
        rdir, "skips", "skipped", BASE_MS + SECOND_MS, BASE_MS + 2 * SECOND_MS, "Skipped: no db"
    )
    # `never_ran` keeps only its registration file.
    return rdir


class TestCounts:
    """Tests for the count block."""

    def test_groups_the_registration_pass_away(self, results_dir: pl.Path) -> None:
        """Count each test once, though every test has two result files."""
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["counts"] == {
            "total": 6,
            "passed": 2,
            "failed": 1,
            "broken": 1,
            "skipped": 2,
        }

    def test_matches_the_counting_script(self, results_dir: pl.Path) -> None:
        """Report the same totals `count_test_results.py` would."""
        files = sorted(f for f in results_dir.iterdir() if f.name.endswith("-result.json"))
        best, _, _ = stats_json.counter._group_records(files)

        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["counts"]["total"] == len(best)

    def test_broken_is_counted(self, results_dir: pl.Path) -> None:
        """Keep the Allure status that JUnit has no equivalent for."""
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["counts"]["broken"] == 1


class TestQuality:
    """Tests for the counters that say how far the counts can be trusted."""

    def test_reports_an_interrupted_testrun(self, results_dir: pl.Path) -> None:
        """Count a test that was registered and never ran."""
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["quality"]["never_run"] == 1

    def test_never_run_is_part_of_skipped(self, results_dir: pl.Path) -> None:
        """A registration result carries the status `skipped`, so it is a subset."""
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["quality"]["never_run"] <= document["counts"]["skipped"]

    def test_counts_an_unreadable_file(self, results_dir: pl.Path) -> None:
        """Report a file that could not be read, rather than hiding it."""
        (results_dir / "junk-result.json").write_text("not json", encoding="utf-8")

        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["quality"]["read_errors"] == 1


class TestTiming:
    """Tests for the timestamp and the duration."""

    def test_duration_is_the_wall_clock_span(self, results_dir: pl.Path) -> None:
        """Measure elapsed time, not the sum of the per-test durations.

        Tests run in parallel under xdist, so the sum is far larger than the
        time the testrun actually took.
        """
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        # Real results span BASE+1s to BASE+9s. The per-test durations add up
        # to 20s, which is what a naive sum would report.
        assert document["duration"] == pytest.approx(8.0)

    def test_the_registration_pass_is_left_out_of_the_span(self, results_dir: pl.Path) -> None:
        """Start the clock at the first real result, not at registration."""
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["timestamp"].endswith("+00:00")
        assert "1970" not in document["timestamp"]

    def test_an_empty_directory_gives_a_zero_duration(self, tmp_path: pl.Path) -> None:
        """Do not fail on a testrun that produced no results."""
        empty = tmp_path / "empty"
        empty.mkdir()

        document = stats_json.build_document(results_dir=empty, exit_code=1)

        assert document["duration"] == 0.0
        assert document["counts"]["total"] == 0


class TestIdentity:
    """Tests for the five fields that identify a testrun."""

    def test_a_local_run_is_marked_local(
        self, results_dir: pl.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Keep a developer's run out of the CI numbers."""
        monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
        monkeypatch.delenv("GITHUB_RUN_NUMBER", raising=False)

        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["origin"] == "local"
        assert document["run_id"].startswith("local-")

    def test_a_ci_run_uses_the_run_number(
        self, results_dir: pl.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Take the identity from the CI environment when it is there."""
        monkeypatch.setenv("GITHUB_ACTIONS", "true")
        monkeypatch.setenv("GITHUB_RUN_NUMBER", "4242")
        monkeypatch.setenv("GITHUB_REPOSITORY", "IntersectMBO/cardano-node-tests")
        monkeypatch.setenv("CI_TESTRUN_NAME", "node-10.5.0")

        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["origin"] == "ci"
        assert document["run_id"] == "4242"
        assert document["project"] == "cardano-node-tests"
        # Dots are dropped, matching the scrub the workflows already apply
        # before every other tcache call. See `_identity`.
        assert document["testrun_name"] == "node-1050"

    def test_the_testrun_name_matches_the_existing_scrub(
        self, results_dir: pl.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Scrub exactly as the workflows do, so the name joins across endpoints.

        The workflows use `${CI_TESTRUN_NAME//[!a-zA-Z0-9_-]/}` before every
        other tcache call. Keeping the dots here would give the same testrun
        two different names.
        """
        monkeypatch.setenv("CI_TESTRUN_NAME", "node/10.5.0 rc1")

        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["testrun_name"] == "node1050rc1"

    def test_the_run_id_carries_no_machine_detail(
        self, results_dir: pl.Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A local run id must be safe to show on a summary page."""
        monkeypatch.delenv("GITHUB_RUN_NUMBER", raising=False)

        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert "/" not in document["run_id"]
        assert document["run_id"].count("-") == 2


class TestCliCoverage:
    """Tests for the CLI coverage summary."""

    def test_reads_the_two_summary_values(self, results_dir: pl.Path, tmp_path: pl.Path) -> None:
        """Take the aggregates, not the whole nested report."""
        coverage = tmp_path / "cli_coverage.json"
        coverage.write_text(
            json.dumps(
                {"cardano-cli": {"_count_cardano-cli": 213130, "_coverage_cardano-cli": 31.0}}
            ),
            encoding="utf-8",
        )

        document = stats_json.build_document(
            results_dir=results_dir, exit_code=0, coverage_file=coverage
        )

        assert document["commands"] == {"count": 213130, "coverage_pct": 31.0}

    def test_an_empty_coverage_file_is_not_an_error(
        self, results_dir: pl.Path, tmp_path: pl.Path
    ) -> None:
        """`cli_coverage.sh` writes an empty file when it cannot build the report."""
        coverage = tmp_path / "cli_coverage.json"
        coverage.write_text("", encoding="utf-8")

        document = stats_json.build_document(
            results_dir=results_dir, exit_code=0, coverage_file=coverage
        )

        assert document["commands"] == {"count": None, "coverage_pct": None}

    def test_reads_the_tool_name_from_the_report(
        self, results_dir: pl.Path, tmp_path: pl.Path
    ) -> None:
        """Do not hard-code the tool, so another tool's report still works."""
        coverage = tmp_path / "cli_coverage.json"
        coverage.write_text(
            json.dumps({"some-tool": {"_count_some-tool": 7, "_coverage_some-tool": 12.5}}),
            encoding="utf-8",
        )

        document = stats_json.build_document(
            results_dir=results_dir, exit_code=0, coverage_file=coverage
        )

        assert document["commands"] == {"count": 7, "coverage_pct": 12.5}

    @pytest.mark.parametrize("bad", ["31", None, True, float("inf"), float("nan")])
    def test_a_non_numeric_coverage_value_becomes_none(
        self, results_dir: pl.Path, tmp_path: pl.Path, bad: tp.Any
    ) -> None:
        """The report is written by another script, so its values are checked."""
        coverage = tmp_path / "cli_coverage.json"
        coverage.write_text(
            json.dumps(
                {"cardano-cli": {"_count_cardano-cli": 5, "_coverage_cardano-cli": bad}},
                allow_nan=True,
            ),
            encoding="utf-8",
        )

        document = stats_json.build_document(
            results_dir=results_dir, exit_code=0, coverage_file=coverage
        )

        assert document["commands"]["coverage_pct"] is None
        assert document["commands"]["count"] == 5

    def test_a_malformed_coverage_report_is_not_an_error(
        self, results_dir: pl.Path, tmp_path: pl.Path
    ) -> None:
        """A report with an unexpected shape must not stop the upload."""
        coverage = tmp_path / "cli_coverage.json"
        coverage.write_text(json.dumps({"cardano-cli": "not-an-object"}), encoding="utf-8")

        document = stats_json.build_document(
            results_dir=results_dir, exit_code=0, coverage_file=coverage
        )

        assert document["commands"] == {"count": None, "coverage_pct": None}

    def test_a_missing_coverage_file_is_not_an_error(self, results_dir: pl.Path) -> None:
        """The coverage step is best effort, so its output may not exist."""
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        assert document["commands"] == {"count": None, "coverage_pct": None}


class TestDocument:
    """Tests for the document as a whole."""

    def test_carries_the_exit_code_and_step(self, results_dir: pl.Path) -> None:
        """Record the pytest result and which upgrade step this was."""
        document = stats_json.build_document(
            results_dir=results_dir, exit_code=1, step="step2", filtered=True
        )

        assert document["exit_code"] == 1
        assert document["step"] == "step2"
        assert document["filtered"] is True

    def test_is_small_and_serialisable(self, results_dir: pl.Path) -> None:
        """The tcache caps the upload well below its own body limit."""
        document = stats_json.build_document(results_dir=results_dir, exit_code=0)

        rendered = json.dumps(document)

        assert len(rendered) < 4096
        assert json.loads(rendered) == document

    def test_refuses_a_missing_directory(self, tmp_path: pl.Path) -> None:
        """Fail loudly rather than upload a document full of zeroes."""
        with pytest.raises(NotADirectoryError):
            stats_json.build_document(results_dir=tmp_path / "nope", exit_code=0)
