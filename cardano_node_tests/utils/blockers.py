"""Functionality for checking if an issue is blocked and thus blocking a test."""
import logging
import os
from typing import Iterable
from typing import Tuple

import pytest
from packaging import version

from cardano_node_tests.utils import gh_issue
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


if os.environ.get("GH_TOKEN"):
    gh_issue.GHIssue.TOKEN = os.environ.get("GH_TOKEN")


class GH:
    """Methods for working with cardano cluster using `cardano-cli`..

    Attributes:
        issue: A GitHub issue number.
        repo: A repository where the issue belongs to. Default: `input-output-hk/cardano-node`.
        fixed_in: A version of the project where the issue is fixed. Ignored on unknown projects.
        message: A message to be added to blocking outcome.
        check_on_devel: A boolean flag indicating if the issue should be checked on devel versions
            of the project. Default: `True`.
    """

    def __init__(
        self,
        issue: int,
        repo: str = "input-output-hk/cardano-node",
        fixed_in: str = "",
        message: str = "",
        check_on_devel: bool = True,
    ) -> None:
        self.issue = issue
        self.repo = repo
        self.fixed_in = fixed_in
        self.message = message
        self.check_on_devel = check_on_devel
        self.repo_name = repo.split("/")[-1]
        self.gh_issue = gh_issue.GHIssue(number=self.issue, repo=self.repo)

        self._project = None
        if self.repo == "input-output-hk/cardano-node":
            self._project = "node"
        elif self.repo == "input-output-hk/cardano-db-sync":
            self._project = "dbsync"

    def is_blocked(self) -> bool:
        """Check if issue is blocked."""
        if self._project == "node":
            return self._node_issue_is_blocked()
        if self._project == "dbsync":
            return self._dbsync_issue_is_blocked()
        return self._issue_is_blocked()

    def _node_issue_is_blocked(self) -> bool:
        """Check if node issue is blocked."""
        # Assume that the issue is blocked if we are not supposed to check the issue on
        # devel versions of node and we are running a devel version.
        # This can be useful when the issue was fixed in a released version of node, but
        # the fix was not merged into the master branch yet.
        if VERSIONS.node_is_devel and not self.check_on_devel:
            return True

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

        # If here, the issue is already closed

        # We assume all fixes are merged into the master branch. Therefore if we are running
        # devel version of node, the issue is not blocked if it was closed.
        if VERSIONS.node_is_devel:
            return False

        # The issue is blocked if it was fixed in a node version that is greater than
        # the node version we are currently running.
        if self.fixed_in and version.parse(self.fixed_in) > VERSIONS.node:
            return True

        return False

    def _dbsync_issue_is_blocked(self) -> bool:
        """Check if dbsync issue is blocked."""
        # Assume that the issue is blocked if we are not supposed to check the issue on
        # devel versions of dbsync and we are running a devel version.
        # This can be useful when the issue was fixed in a released version of dbsync, but
        # the fix was not merged into the master branch yet.
        if VERSIONS.dbsync_is_devel and not self.check_on_devel:
            return True

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

        # If here, the issue is already closed

        # We assume all fixes are merged into the master branch. Therefore if we are running
        # devel version of dbsync, the issue is not blocked if it was closed.
        if VERSIONS.dbsync_is_devel:
            return False

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

        return False

    def finish_test(self) -> None:
        """Fail or Xfail test with GitHub issue reference."""
        reason = f"{self.gh_issue}: {self.message}"

        if self.is_blocked():
            pytest.xfail(reason)
        else:
            pytest.fail(reason)

    def __repr__(self) -> str:
        return f"<GH: issue='{self.repo}#{self.issue}', fixed_in='{self.fixed_in}'>"


def finish_test(issues: Iterable[GH]) -> None:
    """Fail or Xfail test with references to GitHub issues."""

    def _get_outcome(issue: GH) -> Tuple[bool, str]:
        blocked = issue.is_blocked()
        py_method = "XFAIL" if blocked else "FAIL"
        reason = f"{py_method}: {issue.gh_issue}: {issue.message}"
        return blocked, reason

    outcomes = [_get_outcome(i) for i in issues]

    should_fail = False
    for blocked, __ in outcomes:
        if not blocked:
            should_fail = True
            break

    reasons = "; ".join(reason for __, reason in outcomes)

    if should_fail:
        pytest.fail(reasons)
    else:
        pytest.xfail(reasons)
