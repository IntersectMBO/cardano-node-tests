import os
import re
from pathlib import Path
from typing import Any
from typing import NamedTuple

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils.versions import VERSIONS


# common `skipif`s
SKIPIF_BUILD_UNUSABLE = pytest.mark.skipif(
    not (
        VERSIONS.transaction_era >= VERSIONS.MARY
        and VERSIONS.transaction_era == VERSIONS.cluster_era
    ),
    reason=(
        f"cannot use `build` with cluster era '{VERSIONS.cluster_era_name}' "
        f"and TX era '{VERSIONS.transaction_era_name}'"
    ),
)

SKIPIF_WRONG_ERA = pytest.mark.skipif(
    not (
        VERSIONS.cluster_era >= VERSIONS.DEFAULT_CLUSTER_ERA
        and VERSIONS.transaction_era == VERSIONS.cluster_era
    ),
    reason="meant to run with default era or higher, where cluster era == Tx era",
)

SKIPIF_TOKENS_UNUSABLE = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY,
    reason="native tokens are available only in Mary+ eras",
)

SKIPIF_PLUTUS_UNUSABLE = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="Plutus is available only in Alonzo+ eras",
)


SKIPIF_PLUTUSV2_UNUSABLE = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.BABBAGE or configuration.SKIP_PLUTUSV2,
    reason="runs only with Babbage+ TX; needs PlutusV2 cost model",
)


# common parametrization
PARAM_USE_BUILD_CMD = pytest.mark.parametrize(
    "use_build_cmd",
    (
        False,
        pytest.param(True, marks=SKIPIF_BUILD_UNUSABLE),
    ),
    ids=("build_raw", "build"),
)

PARAM_PLUTUS_VERSION = pytest.mark.parametrize(
    "plutus_version",
    (
        "v1",
        pytest.param("v2", marks=SKIPIF_PLUTUSV2_UNUSABLE),
    ),
    ids=("plutus_v1", "plutus_v2"),
)


# intervals for `wait_for_epoch_interval` (negative values are counted from the end of an epoch)
if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL:
    # time buffer at the end of an epoch, enough to do something that takes several transactions
    EPOCH_STOP_SEC_BUFFER = -40
    # time when all ledger state info is available for the current epoch
    EPOCH_START_SEC_LEDGER_STATE = -19
    # time buffer at the end of an epoch after getting ledger state info
    EPOCH_STOP_SEC_LEDGER_STATE = -15
else:
    # we can be more generous on testnets
    EPOCH_STOP_SEC_BUFFER = -200
    EPOCH_START_SEC_LEDGER_STATE = -300
    EPOCH_STOP_SEC_LEDGER_STATE = -200


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
