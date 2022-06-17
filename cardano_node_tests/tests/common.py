import os
import re
from pathlib import Path
from typing import Any
from typing import NamedTuple

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils.versions import VERSIONS


BUILD_USABLE = (
    VERSIONS.transaction_era >= VERSIONS.MARY and VERSIONS.transaction_era == VERSIONS.cluster_era
)
BUILD_SKIP_MSG = (
    f"cannot use `build` with cluster era '{VERSIONS.cluster_era_name}' "
    f"and TX era '{VERSIONS.transaction_era_name}'"
)


class PytestTest(NamedTuple):
    test_function: str
    test_file: Path
    full: str
    test_class: str = ""
    test_params: str = ""
    stage: str = ""

    def __bool__(self) -> bool:
        return bool(self.test_function)


def hypothesis_settings(max_examples: int = 100) -> Any:
    # pylint: disable=import-outside-toplevel
    import hypothesis

    return hypothesis.settings(
        max_examples=max_examples,
        deadline=None,
        suppress_health_check=(
            hypothesis.HealthCheck.too_slow,
            hypothesis.HealthCheck.function_scoped_fixture,
        ),
    )


def get_current_test() -> PytestTest:
    """Get components (test file, test name, etc.) of current pytest test."""
    curr_test = os.environ.get("PYTEST_CURRENT_TEST") or ""
    if not curr_test:
        return PytestTest(test_function="", test_file=Path("/nonexistent"), full="")

    reg = re.search(
        r"(^.*/test_\w+\.py)(?:::)?(Test\w+)?::(test_\w+)(\[.+\])? *\(?(\w+)?", curr_test
    )
    if not reg:
        raise AssertionError(f"Failed to match '{curr_test}'")

    return PytestTest(
        test_function=reg.group(3),
        test_file=Path(reg.group(1)),
        full=curr_test,
        test_class=reg.group(2) or "",
        test_params=reg.group(4) or "",
        stage=reg.group(5) or "",
    )


def get_test_id(cluster_obj: clusterlib.ClusterLib) -> str:
    """Return unique test ID - function name + assigned cluster instance + random string.

    Log the test ID into cluster manager log file.
    """
    curr_test = get_current_test()
    rand_str = clusterlib.get_rand_str(3)
    test_id = f"{curr_test.test_function}_ci{cluster_obj.cluster_id}_{rand_str}"

    # log test ID to cluster manager log file - getting test ID happens early
    # after the start of a test, so the log entry can be used for determining
    # time of the test start
    cm: cluster_management.ClusterManager = cluster_obj._cluster_manager  # type: ignore
    cm._log(f"c{cm.cluster_instance_num}: got ID `{test_id}` for `{curr_test.full}`")

    return test_id
