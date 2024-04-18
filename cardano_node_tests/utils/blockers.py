"""Functionality for checking if an issue is blocked and thus blocking a test."""

import logging
import os
import typing as tp

import pytest
from packaging import version

from cardano_node_tests.utils import gh_issue
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


if os.environ.get("GITHUB_TOKEN"):
    gh_issue.GHIssue.TOKEN = os.environ.get("GITHUB_TOKEN")


class GH:
    """Methods for working with GitHub issues.

    Attributes:
        issue: A GitHub issue number.
        repo: A repository where the issue belongs to. Default: `IntersectMBO/cardano-node`.
        fixed_in: A version of the project where the issue is fixed. Ignored on unknown projects.
        message: A message to be added to blocking outcome.
    """

    def __init__(
        self,
        issue: int,
        repo: str = "IntersectMBO/cardano-node",
        fixed_in: str = "",
        message: str = "",
    ) -> None:
        self.issue = issue
        self.repo = repo
        self.fixed_in = fixed_in
        self.message = message
        self.repo_name = repo.split("/")[-1]
        self.gh_issue = gh_issue.GHIssue(number=self.issue, repo=self.repo)

        self._project = None
        if self.repo == "IntersectMBO/cardano-node":
            self._project = "node"
        elif self.repo == "IntersectMBO/cardano-cli":
            self._project = "cli"
        elif self.repo == "IntersectMBO/cardano-db-sync":
            self._project = "dbsync"

    def is_blocked(self) -> bool:
        """Check if issue is blocked."""
        if self._project == "node":
            return self._node_issue_is_blocked()
        if self._project == "cli":
            return self._cli_issue_is_blocked()
        if self._project == "dbsync":
            return self._dbsync_issue_is_blocked()
        return self._issue_is_blocked()

    def _node_issue_is_blocked(self) -> bool:
        """Check if node issue is blocked."""
        # Assume that the issue is blocked if no Github token was provided and so the check
        # cannot be performed.
        if not self.gh_issue.TOKEN:
            LOGGER.warning(
                "No GitHub token provided, cannot check if issue '%s' is blocked",
                f"{self.repo}#{self.issue}",
            )
            return True

        # The issue is blocked if it is was not closed yet
        if not self.gh_issue.is_closed():
            return True

        # The issue is blocked if it was fixed in a node version that is greater than
        # the node version we are currently running.
        if self.fixed_in and version.parse(self.fixed_in) > VERSIONS.node:
            return True

        return False

    def _cli_issue_is_blocked(self) -> bool:
        """Check if generic issue is blocked."""
        # Assume that the issue is blocked if no Github token was provided and so the check
        # cannot be performed.
        if not self.gh_issue.TOKEN:
            LOGGER.warning(
                "No GitHub token provided, cannot check if issue '%s' is blocked",
                f"{self.repo}#{self.issue}",
            )
            return True

        # The issue is blocked if it is was not closed yet
        if not self.gh_issue.is_closed():
            return True

        # The issue is blocked if it was fixed in a cli version that is greater than
        # the cli version we are currently running.
        if self.fixed_in and version.parse(self.fixed_in) > VERSIONS.cli:
            return True

        return False

    def _dbsync_issue_is_blocked(self) -> bool:
        """Check if dbsync issue is blocked."""
        # Assume that the issue is blocked if no Github token was provided and so the check
        # cannot be performed.
        if not self.gh_issue.TOKEN:
            LOGGER.warning(
                "No GitHub token provided, cannot check if issue '%s' is blocked",
                f"{self.repo}#{self.issue}",
            )
            return True

        # The issue is blocked if it is was not closed yet
        if not self.gh_issue.is_closed():
            return True

        # The issue is blocked if it was fixed in a dbsync version that is greater than
        # the dbsync version we are currently running.
        if self.fixed_in and version.parse(self.fixed_in) > VERSIONS.dbsync:
            return True

        return False

    def _issue_is_blocked(self) -> bool:
        """Check if generic issue is blocked."""
        # Assume that the issue is blocked if no Github token was provided and so the check
        # cannot be performed.
        if not self.gh_issue.TOKEN:
            LOGGER.warning(
                "No GitHub token provided, cannot check if issue '%s' is blocked",
                f"{self.repo}#{self.issue}",
            )
            return True

        # The issue is blocked if it is was not closed yet
        if not self.gh_issue.is_closed():
            return True

        # The issue is blocked if the fix was integrated into a node version that is greater than
        # the node version we are currently running.
        if self.fixed_in and version.parse(self.fixed_in) > VERSIONS.node:
            return True

        return False

    def finish_test(self) -> None:
        """Fail or Xfail test with GitHub issue reference."""
        reason = f"{self.gh_issue}: {self.message}"
        log_message = f"{self.gh_issue.url} => {self.message}"

        if self.is_blocked():
            LOGGER.warning(f"XFAIL: {log_message}")
            pytest.xfail(reason)
        else:
            LOGGER.error(f"FAIL: {log_message}")
            pytest.fail(reason)

    def copy(self) -> "GH":
        """Return a copy of the object."""
        return GH(
            issue=self.issue,
            repo=self.repo,
            fixed_in=self.fixed_in,
            message=self.message,
        )

    def __repr__(self) -> str:
        return f"<GH: issue='{self.repo}#{self.issue}', fixed_in='{self.fixed_in}'>"


def finish_test(issues: tp.Iterable[GH]) -> None:
    """Fail or Xfail test with references to multiple GitHub issues."""

    def _get_outcome(issue: GH) -> tp.Tuple[bool, str, str]:
        blocked = issue.is_blocked()
        py_outcome = "XFAIL" if blocked else "FAIL"
        reason = f"{py_outcome}: {issue.gh_issue}: {issue.message}"
        log_message = f"{py_outcome}: {issue.gh_issue.url} => {issue.message}"
        return blocked, reason, log_message

    outcomes = [_get_outcome(i) for i in issues]

    should_fail = False
    for blocked, __, log_message in outcomes:
        if blocked:
            LOGGER.warning(log_message)
        else:
            should_fail = True
            LOGGER.error(log_message)

    reasons = "; ".join(o[1] for o in outcomes)
    if should_fail:
        pytest.fail(reasons)
    else:
        pytest.xfail(reasons)
