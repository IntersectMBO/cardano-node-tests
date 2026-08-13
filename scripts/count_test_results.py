#!/usr/bin/env python3
"""Summarize allure test results for preliminary failure analysis.

Usage:
    scripts/count_test_results.py <allure-results-dir> [<allure-results-dir>...]

For each directory, prints per-test counts by status and one line per
failed/broken test (capped, with a '+N more' tail). Missing and empty
directories are reported and skipped.

Result files are grouped per test (by Allure `historyId`, which identifies a
test together with its parameters), so tests with multiple result files are
counted once. Multiple files per test are normal: the initial `--skipall`
pass of `runner/run_tests.sh` registers every collected test with a
"Skipped: collected, not run" result file, and the real testrun then adds a
result file with the real status. The newest non-registration result of a
test wins. (Result directories produced before the `--skipall` fix may also
contain registration files carrying a test's own `skipif` or `skip` reason -
those are treated as real results, which the newest-wins rule resolves
correctly.)

This is a standalone script so that AI failure analysis in CI can get the
counts with a single allowlisted command - the CI allowlist permits only
simple command prefixes, so compound shell snippets (pipes, command
substitution) are rejected there.
"""

import json
import pathlib
import sys
import typing as tp

# Cap the failed/broken enumeration so a mass-failure run cannot flood the
# consumer - AI agent tool output gets truncated around 30kB.
ENUM_LIMIT = 40
LINE_WIDTH = 200

SKIPALL_MSG = "Skipped: collected, not run"


def _plural(count: int, word: str) -> str:
    """Return the count together with the word, pluralized when needed.

    Args:
        count: The number of items.
        word: The singular form of the word.

    Returns:
        E.g. "1 test" or "5 tests".
    """
    return f"{count} {word}{'' if count == 1 else 's'}"


class _TestResult(tp.NamedTuple):
    """Authoritative result of a single test."""

    is_real: bool
    start: float
    status: str
    name: str
    msg_head: str


def _parse_result_file(fpath: pathlib.Path) -> tuple[str, bool, _TestResult]:
    """Parse one allure result file.

    Args:
        fpath: Path to a `*-result.json` file.

    Returns:
        Tuple of (grouping key, whether the file had a historyId, test result).

    Raises:
        OSError: When the file cannot be read.
        ValueError: When the file content is not valid JSON.
        TypeError: When the file content is not a JSON object.
        AttributeError: When a nested field has an unexpected type.
    """
    rec = json.loads(fpath.read_text(encoding="utf-8"))
    if not isinstance(rec, dict):
        err = "not a JSON object"
        raise TypeError(err)

    key = rec.get("historyId")
    has_history_id = bool(key) and isinstance(key, str)
    if not has_history_id:
        params = sorted(
            (str(p.get("name") or ""), str(p.get("value") or ""))
            for p in rec.get("parameters") or []
        )
        key = f"{rec.get('fullName') or rec.get('name') or fpath.name}|{params}"

    message = (rec.get("statusDetails") or {}).get("message") or ""
    start = rec.get("start")
    result = _TestResult(
        is_real=message != SKIPALL_MSG,
        start=start if isinstance(start, (int, float)) else 0,
        status=str(rec.get("status") or "unknown"),
        name=str(rec.get("name") or rec.get("fullName") or "?"),
        msg_head=message.splitlines()[0] if message else "",
    )
    return str(key), has_history_id, result


def _group_records(
    result_files: list[pathlib.Path],
) -> tuple[dict[str, _TestResult], int, int]:
    """Group result files per test, the newest real result of a test winning.

    Args:
        result_files: Allure `*-result.json` files.

    Returns:
        Tuple of (test key to authoritative result mapping, number of files
        without a historyId, number of unreadable files).
    """
    best: dict[str, _TestResult] = {}
    no_history_id = 0
    read_errors = 0
    for fpath in result_files:
        try:
            key, has_history_id, result = _parse_result_file(fpath)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            # Printed to stdout so the error stays next to its directory's
            # summary when the two streams are merged (as in CI).
            print(f"Error: cannot read '{fpath}': {exc}")
            read_errors += 1
            continue
        if not has_history_id:
            no_history_id += 1
        prev = best.get(key)
        if prev is None or (result.is_real, result.start) > (prev.is_real, prev.start):
            best[key] = result
    return best, no_history_id, read_errors


def _print_counts(best: dict[str, _TestResult]) -> None:
    """Print per-test counts by status.

    Args:
        best: Test key to authoritative result mapping.
    """
    counts: dict[str, int] = {}
    for result in best.values():
        counts[result.status] = counts.get(result.status, 0) + 1

    total = len(best)
    passed = counts.get("passed", 0)
    failed = counts.get("failed", 0)
    broken = counts.get("broken", 0)
    skipped = counts.get("skipped", 0)
    print(
        f"Total: {total}, Passed: {passed}, Failed: {failed}, Broken: {broken}, Skipped: {skipped}"
    )
    other = total - passed - failed - broken - skipped
    if other:
        print(f"Other statuses: {other}")

    never_run = sum(1 for r in best.values() if not r.is_real)
    if never_run:
        print(
            f"Note: {_plural(never_run, 'test')} registered by the initial skip pass "
            "only, with no real result - the testrun was likely interrupted before "
            "they could run"
        )


def _print_failures(best: dict[str, _TestResult]) -> None:
    """Print one line per failed/broken test, sorted by test name and capped.

    Args:
        best: Test key to authoritative result mapping.
    """
    failures = sorted(
        (r.name, f"{r.status}: {r.name}: {r.msg_head}"[:LINE_WIDTH])
        for r in best.values()
        if r.status in ("failed", "broken")
    )
    if not failures:
        return

    print("-- failed/broken tests --")
    for _, line in failures[:ENUM_LIMIT]:
        print(line)
    if len(failures) > ENUM_LIMIT:
        print(
            f"... +{len(failures) - ENUM_LIMIT} more (see the result JSON files for the full list)"
        )


def summarize_dir(results_dir: pathlib.Path) -> int:
    """Print a test results summary for one allure results directory.

    Args:
        results_dir: Directory with `*-result.json` allure files.

    Returns:
        0 on success (incl. missing or empty directory, which is reported as
        an informational message), 1 when the directory or some of its result
        files could not be read and the counts may therefore be incomplete.
    """
    print(f"== {results_dir} ==")

    if not results_dir.is_dir():
        print(f"Directory not found: {results_dir}")
        return 0

    # `iterdir` instead of `glob`, as `glob` swallows permission errors and an
    # unlistable directory would be misreported as having no results.
    try:
        result_files = sorted(f for f in results_dir.iterdir() if f.name.endswith("-result.json"))
    except OSError as exc:
        print(f"Error: cannot list '{results_dir}': {exc}")
        return 1
    if not result_files:
        print(
            f"No *-result.json files found in {results_dir} - "
            "the testrun likely did not produce results"
        )
        return 0

    best, no_history_id, read_errors = _group_records(result_files)
    _print_counts(best)
    _print_failures(best)

    if no_history_id:
        print(
            f"Note: {_plural(no_history_id, 'result file')} without historyId - "
            "test grouping is approximate"
        )
    if read_errors:
        print(
            f"Warning: {_plural(read_errors, 'result file')} could not be read - "
            "counts may be incomplete"
        )
        return 1
    return 0


def main() -> int:
    """Summarize each directory given on the command line.

    Returns:
        The highest per-directory return code, or 2 on usage error.
    """
    if len(sys.argv) < 2:
        print(
            f"Usage: {sys.argv[0]} <allure-results-dir> [<allure-results-dir>...]",
            file=sys.stderr,
        )
        return 2

    exit_rc = 0
    for dir_arg in sys.argv[1:]:
        exit_rc = max(exit_rc, summarize_dir(pathlib.Path(dir_arg)))
    return exit_rc


if __name__ == "__main__":
    sys.exit(main())
