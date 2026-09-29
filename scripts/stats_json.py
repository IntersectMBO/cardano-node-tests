#!/usr/bin/env python3
"""Emit a testrun statistics document for upload to the tcache.

Usage:
    scripts/stats_json.py <allure-results-dir> [options]

Counts the tests in an Allure results directory and writes a small JSON
document describing the testrun. The tcache `/stats` endpoint stores that
document; it never parses a test report itself.

The counting is not done here. `count_test_results.py` already groups Allure
result files by `historyId` so that the initial `--skipall` registration pass
of `runner/run_tests.sh` is not counted twice, and this script reuses that
logic rather than repeating it. Only the extra fields are gathered here: the
run's identity, its timing, the software versions and the CLI coverage. Those
are the fields a JUnit report cannot carry, which is why the tcache keeps this
separate from `/import` and `/history`.
"""

import argparse
import contextlib
import datetime
import json
import os
import pathlib as pl
import subprocess
import sys
import typing as tp
import uuid

# `scripts` is not a package, so the sibling module is imported by path. The
# two private helpers are reused deliberately: duplicating the grouping rules
# would let the two scripts drift, and the printing script's stdout must stay
# byte-identical because the CI failure analysis parses it. Moving the shared
# helpers into a module both scripts import would be tidier and is worth doing
# once the owner of `count_test_results.py` agrees to it.
sys.path.insert(0, str(pl.Path(__file__).resolve().parent))

import count_test_results as counter

SCHEMA = 1
DEFAULT_STEP = "main"
DEFAULT_TESTRUN_NAME = "local"
DEFAULT_PROJECT = "cardano-node-tests"

# Allure timestamps are milliseconds since the epoch.
MS_PER_SECOND = 1000


def _utc_now() -> datetime.datetime:
    """Return the current time as a tz-aware UTC datetime.

    Returns:
        The current UTC time.
    """
    return datetime.datetime.now(tz=datetime.UTC)


def _local_run_id() -> str:
    """Build a run id for a run that has no CI run number.

    Neither the hostname nor the user name goes in, so the id stays safe to
    show on a summary page.

    The suffix only separates two runs started in the same second. `uuid`
    rather than `secrets`: nothing here is a credential, and drawing it from
    `secrets` says it is one.

    Returns:
        A sortable id, e.g. `local-20260929T120000Z-1a2b3c4d`.
    """
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    return f"local-{stamp}-{uuid.uuid4().hex[:8]}"


def _project() -> str:
    """Return the project the testrun belongs to.

    Returns:
        `TCACHE_PROJECT` when set, else the CI repository name, else the
        default.
    """
    override = os.environ.get("TCACHE_PROJECT")
    if override:
        return override
    repository = os.environ.get("GITHUB_REPOSITORY")
    if repository:
        return repository.rsplit("/", maxsplit=1)[-1]
    return DEFAULT_PROJECT


def _identity(step: str) -> dict:
    """Gather the five fields that identify a testrun.

    All five are needed. A run number repeats across projects, the upgrade
    path reports three steps under one run, and `origin` keeps a local run out
    of the CI numbers.

    Args:
        step: The step name, for the upgrade path.

    Returns:
        The identity fields.
    """
    on_ci = bool(os.environ.get("GITHUB_ACTIONS"))
    testrun_name = (
        os.environ.get("TCACHE_TESTRUN_NAME")
        or os.environ.get("CI_TESTRUN_NAME")
        or DEFAULT_TESTRUN_NAME
    )
    # The same scrubbing the workflows already apply before putting the name
    # in a tcache URL: `${CI_TESTRUN_NAME//[!a-zA-Z0-9_-]/}`. It drops dots,
    # so `node-10.5.0` becomes `node-1050`. That is lossy, and the tcache
    # itself accepts dots, but matching the existing calls matters more: if
    # `/stats` kept the dots, the same testrun would carry two different
    # names across the endpoints and could not be joined.
    testrun_name = "".join(c for c in testrun_name if c.isalnum() or c in "_-")
    return {
        "project": _project(),
        "testrun_name": testrun_name or DEFAULT_TESTRUN_NAME,
        "run_id": os.environ.get("GITHUB_RUN_NUMBER") or _local_run_id(),
        "step": step,
        "origin": "ci" if on_ci else "local",
    }


def _tool_version(command: str) -> str | None:
    """Read a tool's version from its `--version` output.

    Args:
        command: The executable to ask.

    Returns:
        The version field, or None when the tool is missing or silent.
    """
    try:
        completed = subprocess.run(
            [command, "--version"], capture_output=True, check=False, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    fields = completed.stdout.split()
    # Same field the shell helper `get_node_version` takes: "<name> <version> ...".
    return fields[1] if len(fields) > 1 else None


def _versions() -> dict:
    """Collect the software versions the counts were produced with.

    Returns:
        A version per tool, with None where the tool is not installed.
    """
    return {
        "cardano_node": _tool_version("cardano-node"),
        "cardano_cli": _tool_version("cardano-cli"),
        "db_sync": _tool_version("cardano-db-sync"),
    }


def _commands(coverage_file: pl.Path | None) -> dict:
    """Read the CLI coverage summary written by `runner/cli_coverage.sh`.

    The coverage report itself is a large nested tree, so only its two summary
    values are taken. `cli_coverage.sh` writes an empty file when the report
    cannot be built, which is a normal outcome rather than an error.

    Args:
        coverage_file: Path to `cli_coverage.json`, or None to skip.

    Returns:
        The invocation count and the coverage percentage, both None when the
        file is missing or empty.
    """
    empty: dict = {"count": None, "coverage_pct": None}
    if coverage_file is None or not coverage_file.is_file():
        return empty
    try:
        report = json.loads(coverage_file.read_text(encoding="utf-8"))
        cli = report["cardano-cli"]
    except (OSError, ValueError, TypeError, KeyError):
        return empty
    return {
        "count": cli.get("_count_cardano-cli"),
        "coverage_pct": cli.get("_coverage_cardano-cli"),
    }


def _timing(results_dir: pl.Path) -> tp.Tuple[str | None, float]:
    """Derive when the testrun started and how long it took.

    The registration pass is skipped, so the span covers the real run only.
    The span is wall clock, not the sum of the per-test durations: tests run
    in parallel under xdist, and that sum is many times the elapsed time.

    Args:
        results_dir: The Allure results directory.

    Returns:
        Tuple of (ISO-8601 start time or None, duration in seconds).
    """
    starts: tp.List[float] = []
    stops: tp.List[float] = []
    for fpath in results_dir.iterdir():
        if not fpath.name.endswith("-result.json"):
            continue
        with contextlib.suppress(OSError, ValueError, TypeError, AttributeError):
            record = json.loads(fpath.read_text(encoding="utf-8"))
            message = (record.get("statusDetails") or {}).get("message") or ""
            if message == counter.SKIPALL_MSG:
                continue
            start, stop = record.get("start"), record.get("stop")
            if isinstance(start, (int, float)) and isinstance(stop, (int, float)):
                starts.append(start)
                stops.append(stop)

    if not starts:
        return None, 0.0
    began = datetime.datetime.fromtimestamp(min(starts) / MS_PER_SECOND, tz=datetime.UTC)
    return began.isoformat(), (max(stops) - min(starts)) / MS_PER_SECOND


def _counts(best: dict) -> dict:
    """Count the grouped results by Allure status.

    `broken` has no JUnit equivalent, which is one reason the tcache stores
    these counts rather than deriving them from a JUnit report. Statuses
    outside the four are left out; the server derives that remainder itself.

    Args:
        best: The authoritative result per test, from `count_test_results`.

    Returns:
        The total and the four status buckets.
    """
    tally: tp.Dict[str, int] = {}
    for result in best.values():
        tally[result.status] = tally.get(result.status, 0) + 1
    return {
        "total": len(best),
        "passed": tally.get("passed", 0),
        "failed": tally.get("failed", 0),
        "broken": tally.get("broken", 0),
        "skipped": tally.get("skipped", 0),
    }


def _quality(best: dict, no_history_id: int, read_errors: int) -> dict:
    """Report how far the counts can be trusted.

    `never_run` counts tests the registration pass registered that never got a
    real result, which means the testrun was interrupted and every count is a
    floor rather than a total. It is a subset of `skipped`, because a
    registration result carries the status `skipped`.

    Args:
        best: The authoritative result per test.
        no_history_id: Result files that carried no `historyId`.
        read_errors: Result files that could not be read.

    Returns:
        The three counters that qualify the counts.
    """
    return {
        "never_run": sum(1 for result in best.values() if not result.is_real),
        "no_history_id": no_history_id,
        "read_errors": read_errors,
    }


def build_document(
    results_dir: pl.Path,
    exit_code: int,
    step: str = DEFAULT_STEP,
    coverage_file: pl.Path | None = None,
    filtered: bool = False,
) -> dict:
    """Build the statistics document for one testrun.

    Args:
        results_dir: The Allure results directory.
        exit_code: pytest's own exit code for the run.
        step: The step name, for the upgrade path.
        coverage_file: Path to `cli_coverage.json`, or None.
        filtered: True when the run covered only a subset of the tests.

    Returns:
        The document to upload.

    Raises:
        NotADirectoryError: When the results directory does not exist.
    """
    if not results_dir.is_dir():
        err = f"Not a directory: {results_dir}"
        raise NotADirectoryError(err)

    result_files = sorted(f for f in results_dir.iterdir() if f.name.endswith("-result.json"))
    best, no_history_id, read_errors = counter._group_records(result_files)
    began, duration = _timing(results_dir)

    document = {
        "schema": SCHEMA,
        **_identity(step),
        "timestamp": began or _utc_now().isoformat(),
        "duration": duration,
        "exit_code": exit_code,
        "filtered": filtered,
        "counts": _counts(best),
        "quality": _quality(best, no_history_id, read_errors),
        "versions": _versions(),
        "commands": _commands(coverage_file),
    }
    return document


def get_args() -> argparse.Namespace:
    """Parse the command line.

    Returns:
        The parsed arguments.
    """
    parser = argparse.ArgumentParser(description=__doc__.partition("\n")[0])
    parser.add_argument("results_dir", type=pl.Path, help="Allure results directory")
    parser.add_argument(
        "-e", "--exit-code", type=int, default=0, help="pytest exit code for the run"
    )
    parser.add_argument("-s", "--step", default=DEFAULT_STEP, help="step name, for upgrade testing")
    parser.add_argument(
        "-c", "--cli-coverage", type=pl.Path, default=None, help="path to cli_coverage.json"
    )
    parser.add_argument(
        "-f", "--filtered", action="store_true", help="the run covered a subset of the tests"
    )
    parser.add_argument(
        "-o", "--output", type=pl.Path, default=None, help="write here instead of stdout"
    )
    return parser.parse_args()


def main() -> int:
    """Write the statistics document.

    Returns:
        0 on success, 1 when the results directory cannot be read.
    """
    args = get_args()
    try:
        document = build_document(
            results_dir=args.results_dir,
            exit_code=args.exit_code,
            step=args.step,
            coverage_file=args.cli_coverage,
            filtered=args.filtered,
        )
    except (NotADirectoryError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(document, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(f"{rendered}\n", encoding="utf-8")
    else:
        print(rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
