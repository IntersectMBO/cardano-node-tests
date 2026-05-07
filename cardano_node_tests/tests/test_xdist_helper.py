"""Dummy test that helps with `pytest-xdist` scheduling.

The `pytest-xdist` plugin needs to schedule two tests per worker in initial batch. This dummy test
gets scheduled as first not long-running test on every pytest worker so the other test can be
an actual long-running test.
"""

import pytest

from cardano_node_tests.utils import helpers

PYTEST_XDIST_WORKER_COUNT = helpers.get_env_int("PYTEST_XDIST_WORKER_COUNT", 0)

pytestmark = pytest.mark.skipif(
    PYTEST_XDIST_WORKER_COUNT == 0, reason="helper test is not needed when running without xdist"
)


@pytest.mark.order(1)
@pytest.mark.parametrize("for_worker", tuple(range(PYTEST_XDIST_WORKER_COUNT)))
def test_dummy(
    for_worker: int,  # noqa: ARG001
) -> None:
    pytest.skip("Dummy helper for `pytest-xdist` scheduling")
