"""Unit tests for `cardano_node_tests.utils.gh_issue`.

The tests must not touch the GitHub API. The `GHIssue._get_github` classmethod is
monkeypatched with a fake that returns canned issue states.
"""

import types
import typing as tp

import github
import pytest

from cardano_node_tests.utils import gh_issue


class _FakeGithub:
    """Fake `github.Github` returning canned issue states and counting API calls."""

    def __init__(self, responses: tp.Sequence[str | Exception]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def get_repo(self, _repo: str) -> tp.Self:
        return self

    def get_issue(self, _number: int) -> types.SimpleNamespace:
        self.calls += 1
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return types.SimpleNamespace(state=response)


@pytest.fixture
def clean_cache(monkeypatch: pytest.MonkeyPatch):
    """Give each test a fresh issue cache."""
    monkeypatch.setattr(gh_issue.GHIssue, "issue_cache", {})


def _set_github(monkeypatch: pytest.MonkeyPatch, fake: _FakeGithub | None) -> None:
    """Make `GHIssue` use the given fake GitHub instance."""
    monkeypatch.setattr(gh_issue.GHIssue, "_get_github", classmethod(lambda _cls: fake))


@pytest.mark.usefixtures("clean_cache")
class TestGetState:
    """Tests for `GHIssue.get_state`."""

    def test_state_cached(self, monkeypatch: pytest.MonkeyPatch):
        """Retrieve the state once and serve subsequent calls from the cache."""
        fake = _FakeGithub(responses=["CLOSED"])
        _set_github(monkeypatch, fake)
        issue = gh_issue.GHIssue(number=1, repo="r/r")
        assert issue.get_state() == gh_issue.STATE_CLOSED
        assert issue.get_state() == gh_issue.STATE_CLOSED
        assert fake.calls == 1

    def test_unknown_issue_cached(self, monkeypatch: pytest.MonkeyPatch):
        """Cache the state of a nonexistent issue, it cannot appear later."""
        fake = _FakeGithub(responses=[github.UnknownObjectException(status=404)])
        _set_github(monkeypatch, fake)
        issue = gh_issue.GHIssue(number=1, repo="r/r")
        assert issue.get_state() == gh_issue.STATE_UNKNOWN
        assert issue.get_state() == gh_issue.STATE_UNKNOWN
        assert fake.calls == 1

    def test_transient_failure_not_cached(self, monkeypatch: pytest.MonkeyPatch):
        """Don't cache a failed state retrieval, the next call may succeed."""
        fake = _FakeGithub(responses=[RuntimeError("API is down"), "OPEN"])
        _set_github(monkeypatch, fake)
        issue = gh_issue.GHIssue(number=1, repo="r/r")
        assert issue.get_state() == gh_issue.STATE_FAILURE
        assert not gh_issue.GHIssue.issue_cache
        assert issue.get_state() == "open"
        assert fake.calls == 2

    def test_no_github_instance(self, monkeypatch: pytest.MonkeyPatch):
        """Report a state retrieval failure when the GitHub instance is not available."""
        _set_github(monkeypatch, None)
        issue = gh_issue.GHIssue(number=1, repo="r/r")
        assert issue.get_state() == gh_issue.STATE_FAILURE
