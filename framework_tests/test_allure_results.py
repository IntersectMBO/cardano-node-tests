"""Tests for the shared Allure results summary and its command-line consumers."""

import json
import pathlib
import subprocess

import pytest

from cardano_node_tests.utils import allure_results

_REPO_DIR = pathlib.Path(__file__).resolve().parents[1]
_REGISTRATION = {"message": "Skipped: collected, not run"}


def _write_result(results_dir, filename, **fields):
    """Write an Allure result with the given fields."""
    (results_dir / f"{filename}-result.json").write_text(json.dumps(fields), encoding="utf-8")


def test_grouping_and_timing(tmp_path, monkeypatch):
    """Count retries once, time all real attempts, and read each file only once."""
    _write_result(tmp_path, "first", historyId="retried", status="failed", start=1000, stop=4500)
    _write_result(tmp_path, "retry", historyId="retried", status="passed", start=3000, stop=4000)
    _write_result(
        tmp_path,
        "registered",
        historyId="retried",
        status="skipped",
        start=5000,
        stop=6000,
        statusDetails=_REGISTRATION,
    )
    _write_result(
        tmp_path,
        "never-run",
        historyId="never-run",
        status="skipped",
        start=100,
        stop=200,
        statusDetails=_REGISTRATION,
    )
    _write_result(tmp_path, "no-stop", historyId="no-stop", start=50)
    (tmp_path / "ignored-container.json").write_text("not a result", encoding="utf-8")

    reads = []
    read_text = pathlib.Path.read_text

    def track_read(path, *args, **kwargs):
        reads.append(path)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", track_read)
    summary = allure_results.summarize(tmp_path)

    assert reads == sorted(tmp_path.glob("*-result.json"))
    assert summary.counts == {"total": 3, "passed": 1, "failed": 0, "broken": 0, "skipped": 1}
    assert summary.never_run == 1
    assert summary.results["retried"].status == "passed"
    assert (summary.start_ms, summary.stop_ms) == (1000, 4500)


def test_fallback_grouping(tmp_path):
    """Group missing history IDs by full name and order-independent parameters."""
    params = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
    _write_result(
        tmp_path, "older", fullName="test_case", parameters=params, start=1000, status="passed"
    )
    _write_result(
        tmp_path,
        "newer",
        fullName="test_case",
        parameters=params[::-1],
        start=2000,
        status="broken",
    )
    _write_result(
        tmp_path, "other", fullName="test_case", parameters=params[:1], start=3000, status="passed"
    )

    summary = allure_results.summarize(tmp_path)

    assert summary.no_history_id == 3
    assert summary.counts == {"total": 2, "passed": 1, "failed": 0, "broken": 1, "skipped": 0}


@pytest.mark.parametrize("script", ["count_test_results.py", "stats_json.py"])
@pytest.mark.parametrize("older_checkout", [False, True], ids=["no-pythonpath", "older-checkout"])
def test_script_with_unreadable_result(tmp_path, monkeypatch, script, older_checkout):
    """Use the scripts' own checkout and keep JSON stdout clean."""
    _write_result(
        tmp_path,
        "test",
        historyId="test",
        name="test_case",
        status="failed",
        start=1000,
        stop=2500,
        statusDetails={"message": "first line\nsecond line"},
    )
    (tmp_path / "invalid-result.json").write_text("{", encoding="utf-8")

    if older_checkout:
        checkout = tmp_path / "older-checkout"
        utils = checkout / "cardano_node_tests" / "utils"
        utils.mkdir(parents=True)
        (utils.parent / "__init__.py").touch()
        (utils / "__init__.py").touch()
        # This checkout predates the shared allure_results module.
        monkeypatch.setenv("PYTHONPATH", str(checkout))
    else:
        monkeypatch.delenv("PYTHONPATH", raising=False)
    result = subprocess.run(
        [str(_REPO_DIR / "cardano_node_tests" / script), "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )

    if script == "stats_json.py":
        assert result.returncode == 0
        assert "invalid-result.json" in result.stderr
        document = json.loads(result.stdout)
        assert document["counts"] == {
            "total": 1,
            "passed": 0,
            "failed": 1,
            "broken": 0,
            "skipped": 0,
        }
        assert document["quality"] == {"never_run": 0, "no_history_id": 0, "read_errors": 1}
        assert document["timestamp"] == "1970-01-01T00:00:01+00:00"
        assert document["duration"] == 1.5
    else:
        assert result.returncode == 1
        assert "invalid-result.json" in result.stdout
        assert "Total: 1, Passed: 0, Failed: 1, Broken: 0, Skipped: 0" in result.stdout
        assert "failed: test_case: first line" in result.stdout
        assert "second line" not in result.stdout
