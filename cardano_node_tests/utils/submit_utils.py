import shutil
import typing as tp

import pytest

from cardano_node_tests.utils import submit_api


class SubmitMethods:
    API: tp.Final[str] = "api"
    CLI: tp.Final[str] = "cli"


# The "submit_method" is a fixtrue defined in `conftest.py`.
PARAM_SUBMIT_METHOD = pytest.mark.parametrize(
    "submit_method",
    (
        SubmitMethods.CLI,
        pytest.param(
            SubmitMethods.API,
            marks=pytest.mark.skipif(
                not shutil.which("cardano-submit-api"),
                reason="`cardano-submit-api` is not available",
            ),
        ),
    ),
    ids=("submit_cli", "submit_api"),
    indirect=True,
)


def is_submit_api_available() -> bool:
    """Check if `cardano-submit-api` is available."""
    return bool(shutil.which("cardano-submit-api") and submit_api.is_running())
