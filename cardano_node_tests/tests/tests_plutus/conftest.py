import os

import pytest
from packaging import version

from cardano_node_tests.utils.versions import VERSIONS


# TODO: remove this once node issue #4924 is fixed
@pytest.fixture(scope="session", autouse=True)
def skip_all_plutus() -> None:
    if (
        os.environ.get("TESTPR") or os.environ.get("GITHUB_ACTIONS")
    ) and VERSIONS.node >= version.parse("1.36.0"):
        pytest.skip("Skipping all Plutus tests on CI until node issue #4924 is fixed")
