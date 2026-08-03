"""Unit tests for `cardano_node_tests.utils.blockers`.

The tests must not touch the GitHub API. The issue state is controlled by monkeypatching
`GHIssue.get_state` and the token by monkeypatching `GHIssue.TOKEN`.
"""

import pytest
from packaging import version

from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import gh_issue

PRODUCT_VERSION = version.parse("2.0.0")


@pytest.fixture
def gh_token(monkeypatch: pytest.MonkeyPatch):
    """Set a dummy GitHub token so the blocked check is not skipped."""
    monkeypatch.setattr(gh_issue.GHIssue, "TOKEN", "dummy_token")


def _set_state(monkeypatch: pytest.MonkeyPatch, state: str) -> None:
    """Make `GHIssue.get_state` return the given state without any API call."""
    monkeypatch.setattr(gh_issue.GHIssue, "get_state", lambda *_a, **_kw: state)


def _forbid_get_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make any `GHIssue.get_state` call fail the test."""
    monkeypatch.setattr(
        gh_issue.GHIssue,
        "get_state",
        lambda *_a, **_kw: pytest.fail("get_state must not be called"),
    )


class TestFixedIn:
    """Tests for the `fixed_in` handling."""

    def test_invalid_fixed_in(self):
        """Report an invalid `fixed_in` version already when the issue is defined."""
        with pytest.raises(ValueError, match="Invalid `fixed_in` version for issue 'r/r#1'"):
            blockers.GH(issue=1, repo="r/r", fixed_in="not-a-version")

    @pytest.mark.usefixtures("gh_token")
    def test_fixed_in_changed_after_copy(self, monkeypatch: pytest.MonkeyPatch):
        """Respect a `fixed_in` value that was changed after init."""
        _set_state(monkeypatch, gh_issue.STATE_CLOSED)
        issue = blockers.GH(issue=1, fixed_in="1.0.0")
        assert issue._issue_blocked_in_version(PRODUCT_VERSION) is False

        issue_copy = issue.copy()
        issue_copy.fixed_in = "3.0.0"
        assert issue_copy._issue_blocked_in_version(PRODUCT_VERSION) is True


class TestDispatch:
    """Tests for the `is_blocked` dispatch based on repo."""

    @pytest.mark.parametrize(
        ("repo", "expected"),
        [
            ("IntersectMBO/cardano-cli", "_cli_issue_is_blocked"),
            ("IntersectMBO/cardano-db-sync", "_dbsync_issue_is_blocked"),
            ("IntersectMBO/cardano-node", "_issue_is_blocked"),
            ("IntersectMBO/ouroboros-consensus", "_issue_is_blocked"),
        ],
    )
    def test_is_blocked_dispatch(self, repo: str, expected: str):
        """Select the version check that corresponds to the repo."""
        issue = blockers.GH(issue=1, repo=repo)
        assert issue.is_blocked.__name__ == expected


@pytest.mark.usefixtures("gh_token")
class TestIsBlocked:
    """Tests for the issue blocked check."""

    def test_open_issue_is_blocked(self, monkeypatch: pytest.MonkeyPatch):
        """Treat an open issue as blocked."""
        _set_state(monkeypatch, "open")
        issue = blockers.GH(issue=1)
        assert issue._issue_blocked_in_version(PRODUCT_VERSION) is True

    def test_closed_issue_is_not_blocked(self, monkeypatch: pytest.MonkeyPatch):
        """Treat a closed issue without `fixed_in` as not blocked."""
        _set_state(monkeypatch, gh_issue.STATE_CLOSED)
        issue = blockers.GH(issue=1)
        assert issue._issue_blocked_in_version(PRODUCT_VERSION) is False

    def test_closed_fixed_in_future_version(self, monkeypatch: pytest.MonkeyPatch):
        """Treat a closed issue as blocked when the fix is in a newer product version."""
        _set_state(monkeypatch, gh_issue.STATE_CLOSED)
        issue = blockers.GH(issue=1, fixed_in="3.0.0")
        assert issue._issue_blocked_in_version(PRODUCT_VERSION) is True

    def test_closed_fixed_in_current_version(self, monkeypatch: pytest.MonkeyPatch):
        """Treat a closed issue as not blocked when the fix is in the current version."""
        _set_state(monkeypatch, gh_issue.STATE_CLOSED)
        issue = blockers.GH(issue=1, fixed_in="2.0.0")
        assert issue._issue_blocked_in_version(PRODUCT_VERSION) is False

    def test_nonexistent_issue(self, monkeypatch: pytest.MonkeyPatch):
        """Raise an error when the issue cannot be found, instead of xfailing forever."""
        _set_state(monkeypatch, gh_issue.STATE_UNKNOWN)
        issue = blockers.GH(issue=1, repo="r/r")
        with pytest.raises(ValueError, match="Issue 'r/r#1' doesn't exist"):
            issue._issue_blocked_in_version(PRODUCT_VERSION)

    def test_undetermined_state_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """Assume blocked and warn when the issue state could not be determined."""
        _set_state(monkeypatch, gh_issue.STATE_FAILURE)
        issue = blockers.GH(issue=1)
        assert issue._issue_blocked_in_version(PRODUCT_VERSION) is True
        assert "Could not determine state" in caplog.text

    def test_no_token_is_blocked(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """Assume blocked and warn when no GitHub token is available."""
        monkeypatch.setattr(gh_issue.GHIssue, "TOKEN", None)
        _forbid_get_state(monkeypatch)
        issue = blockers.GH(issue=1)
        assert issue._issue_blocked_in_version(PRODUCT_VERSION) is True
        assert "No GitHub token provided" in caplog.text


@pytest.mark.usefixtures("gh_token")
class TestFinishTest:
    """Tests for `GH.finish_test` and the module-level `finish_test`."""

    def test_blocked_issue_xfails(self, monkeypatch: pytest.MonkeyPatch):
        """Xfail the test when the issue is blocked."""
        _set_state(monkeypatch, "open")
        issue = blockers.GH(issue=1, message="msg1")
        with pytest.raises(pytest.xfail.Exception, match="msg1"):
            issue.finish_test()

    def test_unblocked_issue_fails(self, monkeypatch: pytest.MonkeyPatch):
        """Fail the test when the issue is not blocked."""
        _set_state(monkeypatch, gh_issue.STATE_CLOSED)
        issue = blockers.GH(issue=1, message="msg1")
        with pytest.raises(pytest.fail.Exception, match="msg1"):
            issue.finish_test()

    def test_force_blocked_skips_state_check(self, monkeypatch: pytest.MonkeyPatch):
        """Xfail without checking the issue state when `force_blocked` is used."""
        _forbid_get_state(monkeypatch)
        issue = blockers.GH(issue=1, message="msg1")
        with pytest.raises(pytest.xfail.Exception, match="msg1"):
            issue.finish_test(force_blocked=True)

    def test_no_issues(self):
        """Reject an empty issues collection."""
        with pytest.raises(ValueError, match="No issues were provided"):
            blockers.finish_test(issues=[])

    def test_all_blocked_xfails(self, monkeypatch: pytest.MonkeyPatch):
        """Xfail the test when all issues are blocked."""
        _set_state(monkeypatch, "open")
        issues = [blockers.GH(issue=1, message="msg1"), blockers.GH(issue=2, message="msg2")]
        with pytest.raises(pytest.xfail.Exception, match=r"msg1.*msg2"):
            blockers.finish_test(issues=issues)

    def test_some_unblocked_fails(self, monkeypatch: pytest.MonkeyPatch):
        """Fail the test when at least one issue is not blocked."""
        states = {1: "open", 2: gh_issue.STATE_CLOSED}
        monkeypatch.setattr(gh_issue.GHIssue, "get_state", lambda self: states[self.number])
        issues = [blockers.GH(issue=1, message="msg1"), blockers.GH(issue=2, message="msg2")]
        with pytest.raises(pytest.fail.Exception, match=r"XFAIL.*msg1.*FAIL.*msg2"):
            blockers.finish_test(issues=issues)
