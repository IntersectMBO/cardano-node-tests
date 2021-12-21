import inspect
from typing import Any

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS


BUILD_USABLE = (
    VERSIONS.transaction_era >= VERSIONS.MARY and VERSIONS.transaction_era == VERSIONS.cluster_era
)
BUILD_SKIP_MSG = (
    f"cannot use `build` with cluster era '{VERSIONS.cluster_era_name}' "
    f"and TX era '{VERSIONS.transaction_era_name}'"
)


@helpers.callonce
def hypothesis_settings() -> Any:
    # pylint: disable=import-outside-toplevel
    import hypothesis

    return hypothesis.settings(
        deadline=None,
        suppress_health_check=(
            hypothesis.HealthCheck.too_slow,
            hypothesis.HealthCheck.function_scoped_fixture,
        ),
    )


def get_test_id(cluster_obj: clusterlib.ClusterLib) -> str:
    """Return unique test ID - function name + assigned cluster instance + random string.

    Log the test ID into cluster manager log file.
    """
    calling_frame = inspect.currentframe().f_back  # type: ignore
    func_name = calling_frame.f_code.co_name  # type: ignore
    rand_str = clusterlib.get_rand_str(3)
    test_id = f"{func_name}_ci{cluster_obj.cluster_id}_{rand_str}"

    # log test ID to cluster manager log file - getting test ID happens early
    # after the start of a test, so the log entry can be used for determining
    # time of the test start
    i_self = calling_frame.f_locals.get("self")  # type: ignore
    if i_self:
        code_id = f"{i_self.__class__.__module__}.{i_self.__class__.__qualname__}"
    else:
        code_id = calling_frame.f_globals["__name__"]  # type: ignore
    cm: cluster_management.ClusterManager = cluster_obj._cluster_manager  # type: ignore
    cm._log(f"c{cm.cluster_instance_num}: got ID `{test_id}` for `{code_id}.{func_name}`")

    return test_id
