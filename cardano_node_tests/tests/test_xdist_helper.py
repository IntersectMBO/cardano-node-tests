"""Dummy test that helps with `pytest-xdist` scheduling.

The `pytest-xdist` plugin needs to schedule two tests per worker in initial batch. This dummy test
gets scheduled as first not long-running test on every pytest worker so the other test can be
an actual long-running test.
"""
import os

import pytest


PYTEST_XDIST_WORKER_COUNT = int(os.environ.get("PYTEST_XDIST_WORKER_COUNT") or 0)

pytestmark = pytest.mark.skipif(
    PYTEST_XDIST_WORKER_COUNT == 0, reason="helper test is not needed when running without xdist"
)


@pytest.mark.order(1)
@pytest.mark.parametrize("for_worker", tuple(range(PYTEST_XDIST_WORKER_COUNT)))
def test_dummy(for_worker: int) -> None:  # noqa: ARG001
    # pylint: disable=unused-argument
    pytest.skip("Dummy helper for `pytest-xdist` scheduling")
