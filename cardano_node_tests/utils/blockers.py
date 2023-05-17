"""Functionality for checking if an issue is blocked and thus blocking a test."""
import logging
import os

import pytest
from packaging import version

from cardano_node_tests.utils import gh_issue
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


if os.environ.get("GH_TOKEN"):
    gh_issue.GHIssue.TOKEN = os.environ.get("GH_TOKEN")


class GH:
    """GitHub issues blocker."""

    def __init__(
        self,
        issue: int,
        repo: str = "input-output-hk/cardano-node",
        fixed_in: str = "",
        check_on_devel: bool = True,
    ) -> None:
        self.issue = issue
        self.repo = repo
        self.fixed_in = fixed_in
        self.check_on_devel = check_on_devel
        self.repo_name = repo.split("/")[-1]
        self.gh_issue = gh_issue.GHIssue(number=self.issue, repo=self.repo)

    def is_blocked(self) -> bool:
        """Check if issue is blocked."""
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

    def finish_test(self, message: str) -> None:
        """Fail or Xfail test with GitHub issue reference."""
        reason = f"{self.gh_issue}: {message}"

        # Xfail if the issue is still blocked
        if self.is_blocked():
            pytest.xfail(reason)
        # Fail if the issue is supposed to be fixed
        else:
            pytest.fail(reason)

    def __repr__(self) -> str:
        return f"<GH: issue='{self.repo}#{self.issue}', fixed_in='{self.fixed_in}'>"
