"""Functionality for checking if an issue is blocked and thus blocking a test."""

import logging
import os
import typing as tp

import pytest
from packaging import version

from cardano_node_tests.utils import gh_issue
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


gh_issue.GHIssue.TOKEN = os.environ.get("GITHUB_TOKEN")


class GH:
    """Methods for working with GitHub issues.

    Attributes:
        issue: A GitHub issue number.
        repo: A repository where the issue belongs to. Default: `IntersectMBO/cardano-node`.
        fixed_in: A version of the project where the issue is fixed. On projects other than
            cardano-node, cardano-cli and cardano-db-sync, it is the cardano-node version
            into which the fix was integrated.
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
        # Validate eagerly so an invalid version is reported already when the issue is defined
        if fixed_in:
            try:
                version.parse(fixed_in)
            except version.InvalidVersion as excp:
                msg = f"Invalid `fixed_in` version for issue '{repo}#{issue}': '{fixed_in}'"
                raise ValueError(msg) from excp
        self.message = message
        self.gh_issue = gh_issue.GHIssue(number=self.issue, repo=self.repo)

        self.is_blocked: tp.Callable[[], bool]
        if self.repo == "IntersectMBO/cardano-cli":
            self.is_blocked = self._cli_issue_is_blocked
        elif self.repo == "IntersectMBO/cardano-db-sync":
            self.is_blocked = self._dbsync_issue_is_blocked
        else:
            self.is_blocked = self._issue_is_blocked

    @property
    def _fixed_in_version(self) -> version.Version | None:
        """Parsed `fixed_in` version, or `None` when no `fixed_in` was set.

        Parsed on access, so the check stays correct even when the `fixed_in`
        attribute was changed after init.
        """
        return version.parse(self.fixed_in) if self.fixed_in else None

    def _issue_blocked_in_version(self, product_version: version.Version) -> bool:
        """Check if an issue is blocked in given product version.

        Args:
            product_version: A version of the product to check the issue against.

        Returns:
            Whether the issue is considered blocked.

        Raises:
            ValueError: If the GitHub issue doesn't exist or is not accessible.
        """
        # Assume that the issue is blocked if no GitHub token was provided and so the check
        # cannot be performed.
        if not self.gh_issue.TOKEN:
            LOGGER.warning(
                "No GitHub token provided, cannot check if issue '%s' is blocked",
                f"{self.repo}#{self.issue}",
            )
            return True

        state = self.gh_issue.get_state()

        # Fail early when the issue cannot be found, e.g. because of a typo in the issue
        # number or repo name. Otherwise the test would be silently xfailed forever.
        if state == gh_issue.STATE_UNKNOWN:
            msg = f"Issue '{self.repo}#{self.issue}' doesn't exist or is not accessible"
            raise ValueError(msg)

        # Assume that the issue is blocked when its state could not be determined,
        # e.g. due to an API failure or rate limiting.
        if state is None or state == gh_issue.STATE_FAILURE:
            LOGGER.warning(
                "Could not determine state of issue '%s', assuming it is blocked",
                f"{self.repo}#{self.issue}",
            )
            return True

        # The issue is blocked if it was not closed yet
        if state != "closed":
            return True

        # The issue is blocked if it was fixed or integrated into a product version that is greater
        # than the product version we are currently running.
        if self._fixed_in_version is None:
            return False
        return self._fixed_in_version > product_version

    def _cli_issue_is_blocked(self) -> bool:
        """Check if cardano-cli issue is blocked."""
        return self._issue_blocked_in_version(VERSIONS.cli)

    def _dbsync_issue_is_blocked(self) -> bool:
        """Check if dbsync issue is blocked."""
        return self._issue_blocked_in_version(VERSIONS.dbsync)

    def _issue_is_blocked(self) -> bool:
        """Check if an issue is blocked."""
        return self._issue_blocked_in_version(VERSIONS.node)

    def finish_test(self, force_blocked: bool = False) -> None:
        """Fail or Xfail test with GitHub issue reference.

        Args:
            force_blocked: Treat the issue as blocked without checking its state.

        Raises:
            ValueError: If the GitHub issue doesn't exist or is not accessible.
        """
        reason = f"{self.gh_issue}: {self.message}"
        log_message = f"{self.gh_issue.url} => {self.message}"

        if force_blocked or self.is_blocked():
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
    """Fail or Xfail test with references to multiple GitHub issues.

    Args:
        issues: GitHub issues to report. Must not be empty.

    Raises:
        ValueError: If no issues were provided, or if a referenced GitHub issue doesn't
            exist or is not accessible.
    """

    def _get_outcome(issue: GH) -> tuple[bool, str, str]:
        blocked = issue.is_blocked()
        py_outcome = "XFAIL" if blocked else "FAIL"
        reason = f"{py_outcome}: {issue.gh_issue}: {issue.message}"
        log_message = f"{py_outcome}: {issue.gh_issue.url} => {issue.message}"
        return blocked, reason, log_message

    outcomes = [_get_outcome(i) for i in issues]
    if not outcomes:
        msg = "No issues were provided"
        raise ValueError(msg)

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
