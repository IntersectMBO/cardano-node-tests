import functools
from pathlib import Path
from typing import Any

from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils.versions import VERSIONS


BUILD_USABLE = (
    VERSIONS.transaction_era >= VERSIONS.MARY and VERSIONS.transaction_era == VERSIONS.cluster_era
)
BUILD_SKIP_MSG = (
    f"cannot use `build` with cluster era '{VERSIONS.cluster_era_name}` "
    f"and TX era '{VERSIONS.transaction_era_name}'"
)


@functools.lru_cache
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


def get_pytest_globaltemp(tmp_path_factory: TempdirFactory) -> Path:
    """Return global temporary directory for a single pytest run."""
    pytest_tmp_dir = Path(tmp_path_factory.getbasetemp())
    basetemp = pytest_tmp_dir.parent if configuration.IS_XDIST else pytest_tmp_dir
    basetemp = basetemp / "tmp"
    basetemp.mkdir(exist_ok=True)
    return basetemp
