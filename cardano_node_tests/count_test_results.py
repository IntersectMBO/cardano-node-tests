#!/usr/bin/env python3
"""Summarize allure test results for preliminary failure analysis.

Usage:
    count-test-results <allure-results-dir> [<allure-results-dir>...]

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

import pathlib
import sys

if not __package__:
    # Prefer this checkout over any installed copy when run as a script.
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from cardano_node_tests.utils import allure_results

# Cap the failed/broken enumeration so a mass-failure run cannot flood the
# consumer - AI agent tool output gets truncated around 30kB.
ENUM_LIMIT = 40
LINE_WIDTH = 200


def _plural(count: int, word: str) -> str:
    """Return the count together with the word, pluralized when needed.

    Args:
        count: The number of items.
        word: The singular form of the word.

    Returns:
        E.g. "1 test" or "5 tests".
    """
    return f"{count} {word}{'' if count == 1 else 's'}"


def _print_counts(summary: allure_results.ResultSummary) -> None:
    """Print per-test counts by status.

    Args:
        summary: The grouped test results and counts.
    """
    counts = summary.counts
    total = counts["total"]
    passed = counts["passed"]
    failed = counts["failed"]
    broken = counts["broken"]
    skipped = counts["skipped"]
    print(
        f"Total: {total}, Passed: {passed}, Failed: {failed}, Broken: {broken}, Skipped: {skipped}"
    )
    other = total - passed - failed - broken - skipped
    if other:
        print(f"Other statuses: {other}")

    never_run = summary.never_run
    if never_run:
        print(
            f"Note: {_plural(never_run, 'test')} registered by the initial skip pass "
            "only, with no real result - the testrun was likely interrupted before "
            "they could run"
        )


def _print_failures(best: dict[str, allure_results.TestResult]) -> None:
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

    try:
        summary = allure_results.summarize(results_dir)
    except OSError as exc:
        print(f"Error: cannot list '{results_dir}': {exc}")
        return 1
    if not (summary.results or summary.read_errors):
        print(
            f"No *-result.json files found in {results_dir} - "
            "the testrun likely did not produce results"
        )
        return 0

    for fpath, error in summary.read_errors.items():
        print(f"Error: cannot read '{fpath}': {error}")
    _print_counts(summary)
    _print_failures(summary.results)

    if summary.no_history_id:
        print(
            f"Note: {_plural(summary.no_history_id, 'result file')} without historyId - "
            "test grouping is approximate"
        )
    if summary.read_errors:
        print(
            f"Warning: {_plural(len(summary.read_errors), 'result file')} could not be read - "
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
