"""Unit tests for submit API mempool retry behavior."""

import functools
import pathlib as pl
import types
from unittest import mock

import pytest
import requests

from cardano_node_tests.utils import submit_api


def _response(status: int, text: str) -> requests.Response:
    """Create a response without making an HTTP request."""
    response = requests.Response()
    response.status_code = status
    response._content = text.encode()
    return response


@pytest.fixture
def submission(tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch) -> types.SimpleNamespace:
    """Provide a fake API session, payload and monotonic clock."""
    cbor_file = tmp_path / "tx.cbor"
    cbor_file.write_bytes(b"transaction")
    clock = types.SimpleNamespace(now=0.0)

    def sleep(delay: float) -> None:
        clock.now += delay

    sleeper = mock.Mock(side_effect=sleep)
    monkeypatch.setattr(
        submit_api, "time", types.SimpleNamespace(monotonic=lambda: clock.now, sleep=sleeper)
    )
    jitter = mock.Mock(side_effect=lambda _low, high: high)
    monkeypatch.setattr(submit_api, "random", types.SimpleNamespace(uniform=jitter))
    post = mock.Mock(return_value=_response(202, '"txid"'))
    monkeypatch.setattr(
        submit_api.http_client, "get_session", lambda: types.SimpleNamespace(post=post)
    )
    return types.SimpleNamespace(
        submit=functools.partial(submit_api.post_cbor, cbor_file=cbor_file, url="http://submit"),
        post=post,
        clock=clock,
        sleep=sleeper,
        jitter=jitter,
    )


def test_submission_success(submission: types.SimpleNamespace) -> None:
    """Return the successful response without sleeping."""
    assert submission.submit() is submission.post.return_value
    assert submission.post.call_count == 1
    assert submission.post.call_args.kwargs["data"] == b"transaction"
    assert submission.post.call_args.kwargs["timeout"] == 180
    submission.sleep.assert_not_called()


def test_exponential_backoff(submission: types.SimpleNamespace) -> None:
    """Match CLI retry delays, capping jittered backoff without a fixed attempt limit."""
    submission.post.side_effect = [_response(400, "MempoolTxTooSlow")] * 7 + [
        submission.post.return_value
    ]
    assert submission.submit(retry_max_delay=5) is submission.post.return_value
    assert submission.sleep.call_args_list == [mock.call(d) for d in (2, 4, 5, 5, 5, 5, 5)]
    assert submission.jitter.call_args_list == [mock.call(d / 2, d) for d in (2, 4, 5, 5, 5, 5, 5)]
    assert [c.kwargs["timeout"] for c in submission.post.call_args_list] == [
        180,
        178,
        174,
        169,
        164,
        159,
        154,
        149,
    ]


def test_retry_budget(submission: types.SimpleNamespace, caplog: pytest.LogCaptureFixture) -> None:
    """Stop rather than sleep beyond the budget, retaining the last rejection."""
    submission.post.side_effect = [
        _response(400, "MempoolTxTooSlow (first rejection)"),
        _response(400, "MempoolTxTooSlow (last rejection)"),
        _response(400, "MempoolTxTooSlow (last rejection)"),
    ]
    with pytest.raises(submit_api.SubmitApiError, match=r"3 attempts.*7s retry budget") as excinfo:
        submission.submit(retry_timeout=7)
    assert "last rejection" in str(excinfo.value)
    assert "first rejection" not in str(excinfo.value)
    assert "retrying submit-api in 2.00s: MempoolTxTooSlow (first rejection)" in caplog.text
    assert "retrying submit-api in 4.00s: MempoolTxTooSlow (last rejection)" in caplog.text
    assert submission.sleep.call_args_list == [mock.call(2), mock.call(4)]
    assert submission.clock.now == 6


def test_default_backoff_cap(submission: types.SimpleNamespace) -> None:
    """Stop increasing the delay at the default thirty-second cap."""
    submission.post.side_effect = [_response(400, "MempoolTxTooSlow")] * 8 + [
        submission.post.return_value
    ]
    assert submission.submit() is submission.post.return_value
    assert submission.sleep.call_args_list == [mock.call(d) for d in (2, 4, 8, 16, 30, 30, 30, 30)]


@pytest.mark.parametrize("cap", [0.5, 30])
def test_lower_jitter_bound(submission: types.SimpleNamespace, cap: float) -> None:
    """Keep equal jitter above zero and respect caps smaller than the initial delay."""
    submission.jitter.side_effect = lambda low, _high: low
    submission.post.side_effect = [_response(400, "MempoolTxTooSlow")] * 3 + [
        submission.post.return_value
    ]
    assert submission.submit(retry_max_delay=cap) is submission.post.return_value
    assert submission.sleep.call_args_list == [mock.call(min(d, cap) / 2) for d in (2, 4, 8)]


def test_slow_attempt_consumes_budget(submission: types.SimpleNamespace) -> None:
    """Count time inside HTTP requests and do not retry after expiry."""

    def reject(*_args: object, **_kwargs: object) -> requests.Response:
        submission.clock.now += 7
        return _response(400, "MempoolTxTooSlow")

    submission.post.side_effect = reject
    with pytest.raises(submit_api.SubmitApiError, match="1 attempts"):
        submission.submit(retry_timeout=7)
    assert submission.post.call_count == 1
    submission.sleep.assert_not_called()


def test_oversleep_consumes_budget(submission: types.SimpleNamespace) -> None:
    """Recheck the deadline after waking up late."""
    submission.post.return_value = _response(400, "MempoolTxTooSlow")
    submission.sleep.side_effect = lambda _delay: setattr(submission.clock, "now", 1000)
    with pytest.raises(submit_api.SubmitApiError, match="1 attempts"):
        submission.submit()
    assert submission.post.call_count == 1


@pytest.mark.parametrize(
    ("status", "text"),
    [(400, "BadInputsUTxO"), (500, "MempoolTxTooSlow"), (202, "MempoolTxTooSlow")],
)
def test_other_responses_return(submission: types.SimpleNamespace, status: int, text: str) -> None:
    """Only retry a 400 response containing the explicit rejection."""
    response = _response(status, text)
    submission.post.return_value = response
    assert submission.submit() is response
    assert submission.post.call_count == 1
    submission.sleep.assert_not_called()


@pytest.mark.parametrize("error", [requests.ReadTimeout("ambiguous"), requests.ConnectionError()])
def test_transport_errors_propagate(submission: types.SimpleNamespace, error: Exception) -> None:
    """Do not retry errors that leave submission status unknown."""
    submission.post.side_effect = error
    with pytest.raises(type(error)) as excinfo:
        submission.submit()
    assert excinfo.value is error
    assert submission.post.call_count == 1
    submission.sleep.assert_not_called()


def test_request_timeout_cap(submission: types.SimpleNamespace) -> None:
    """Preserve the request timeout cap for larger budgets."""
    submission.submit(retry_timeout=900)
    assert submission.post.call_args.kwargs["timeout"] == 300


@pytest.mark.parametrize("setting", ["retry_timeout", "retry_max_delay"])
@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_settings(submission: types.SimpleNamespace, setting: str, value: float) -> None:
    """Reject invalid retry settings before submitting anything."""
    with pytest.raises(ValueError, match=f"{setting} must be finite and positive"):
        submission.submit(**{setting: value})
    submission.post.assert_not_called()
