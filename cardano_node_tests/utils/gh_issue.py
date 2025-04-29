"""Functionality for working with GitHub issues."""

import logging
import typing as tp

import github

LOGGER = logging.getLogger(__name__)


class GHIssue:
    """GitHub issue."""

    TOKEN: tp.ClassVar[str | None] = None

    issue_cache: tp.ClassVar[dict[str, str]] = {}

    _github_instance: tp.ClassVar[github.Github | None] = None
    _github_instance_error: tp.ClassVar[bool] = False

    @classmethod
    def _get_github(cls) -> github.Github | None:
        """Get GitHub instance."""
        if cls._github_instance is not None:
            return cls._github_instance

        if cls._github_instance_error:
            return None

        try:
            # Max 60 req/hr without token
            cls._github_instance = (
                github.Github(auth=github.Auth.Token(cls.TOKEN)) if cls.TOKEN else github.Github()
            )
        except Exception:
            LOGGER.exception("Failed to get GitHub instance")
            cls._github_instance_error = True
            return None

        return cls._github_instance

    def __init__(self, number: int, repo: str) -> None:
        self.number = number
        self.repo = repo

    @property
    def github(self) -> github.Github | None:
        return self._get_github()

    @property
    def url(self) -> str:
        return f"https://github.com/{self.repo}/issues/{self.number}"

    def get_state(self) -> str | None:
        """Get issue state."""
        if not self.github:
            LOGGER.error("Failed to get GitHub instance")
            return None

        identifier = f"{self.repo}#{self.number}"
        cached_state = self.issue_cache.get(identifier)

        if cached_state is None:
            assert self.github  # for pytype
            try:
                cached_state = self.github.get_repo(self.repo).get_issue(self.number).state.lower()
            except github.UnknownObjectException:
                LOGGER.exception("Unknown issue '%s'", identifier)
                cached_state = "unknown"
            except Exception:
                LOGGER.exception("Failed to get issue '%s'", identifier)
                cached_state = "get_state_failure"
            self.issue_cache[identifier] = cached_state

        return cached_state

    def is_closed(self) -> bool:
        """Check if issue is closed."""
        return self.get_state() == "closed"

    def __repr__(self) -> str:
        return f"<GHIssue: {self.repo}#{self.number}>"
