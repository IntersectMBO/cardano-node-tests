"""Global HTTP client."""

import requests

_session = None


def get_session() -> requests.Session:
    """Get a session object."""
    global _session  # noqa: PLW0603

    if _session is None:
        _session = requests.Session()
    return _session
