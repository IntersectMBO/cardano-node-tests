"""Read and summarize the Allure result files from a test run."""

import collections
import dataclasses
import json
import pathlib
import typing as tp

_SKIPALL_MSG = "Skipped: collected, not run"


@dataclasses.dataclass(frozen=True)
class TestResult:
    """Result of one attempt at a test."""

    is_real: bool
    start: float
    status: str
    name: str
    msg_head: str


@dataclasses.dataclass(frozen=True)
class ResultSummary:
    """Grouped results, count quality, and timing bounds in epoch milliseconds."""

    results: dict[str, TestResult]
    counts: dict[str, int]
    never_run: int
    no_history_id: int
    read_errors: dict[pathlib.Path, str]
    start_ms: float | None
    stop_ms: float | None


def _read_record(fpath: pathlib.Path) -> dict[str, tp.Any]:
    """Read one Allure result JSON object."""
    record = json.loads(fpath.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        err = "not a JSON object"
        raise TypeError(err)
    return record


def _test_key(record: dict[str, tp.Any], fpath: pathlib.Path) -> tuple[str, bool]:
    """Return the test grouping key and whether it came from a historyId."""
    key = record.get("historyId")
    if isinstance(key, str) and key:
        return key, True
    params = sorted(
        (str(p.get("name") or ""), str(p.get("value") or ""))
        for p in record.get("parameters") or []
    )
    return f"{record.get('fullName') or record.get('name') or fpath.name}|{params}", False


def summarize(results_dir: pathlib.Path) -> ResultSummary:
    """Read each Allure result file once and summarize the run.

    The newest non-registration result per test wins. Timing includes all
    non-registration attempts with both timestamps, even if some other field
    prevents counting the result.

    Args:
        results_dir: Directory containing `*-result.json` files.

    Returns:
        The summary, including diagnostics for individual unreadable files.

    Raises:
        OSError: If the directory cannot be listed.
    """
    # Unlike glob, iterdir reports permission errors instead of an empty result.
    result_files = sorted(f for f in results_dir.iterdir() if f.name.endswith("-result.json"))
    best: dict[str, TestResult] = {}
    no_history_id = 0
    read_errors: dict[pathlib.Path, str] = {}
    start_ms: float | None = None
    stop_ms: float | None = None

    for fpath in result_files:
        try:
            record = _read_record(fpath)
            message = (record.get("statusDetails") or {}).get("message") or ""
            is_real = message != _SKIPALL_MSG
            start, stop = record.get("start"), record.get("stop")
            if is_real and isinstance(start, (int, float)) and isinstance(stop, (int, float)):
                start_ms = start if start_ms is None else min(start_ms, start)
                stop_ms = stop if stop_ms is None else max(stop_ms, stop)

            key, has_history_id = _test_key(record, fpath)
            result = TestResult(
                is_real=is_real,
                start=start if isinstance(start, (int, float)) else 0,
                status=str(record.get("status") or "unknown"),
                name=str(record.get("name") or record.get("fullName") or "?"),
                msg_head=message.splitlines()[0] if message else "",
            )
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            read_errors[fpath] = str(exc)
            continue

        if not has_history_id:
            no_history_id += 1
        prev = best.get(key)
        if prev is None or (result.is_real, result.start) > (prev.is_real, prev.start):
            best[key] = result

    tally = collections.Counter(result.status for result in best.values())
    return ResultSummary(
        results=best,
        counts={
            "total": len(best),
            "passed": tally["passed"],
            "failed": tally["failed"],
            "broken": tally["broken"],
            "skipped": tally["skipped"],
        },
        never_run=sum(1 for result in best.values() if not result.is_real),
        no_history_id=no_history_id,
        read_errors=read_errors,
        start_ms=start_ms,
        stop_ms=stop_ms,
    )
