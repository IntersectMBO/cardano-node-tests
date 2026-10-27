"""Shared fixtures for Dijkstra-era tests."""

import pytest

from cardano_node_tests.utils import clusterlib_utils


@pytest.fixture(scope="module")
def require_sub_cli() -> None:
    """Require CLI support for building and signing sub-transactions."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The `cardano-cli` doesn't support sub-transactions.")
