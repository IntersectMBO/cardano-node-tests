import enum
import shutil

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import submit_api


class SubmitMethods(enum.StrEnum):
    API = "api"
    CLI = "cli"


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


def submit_tx(
    *,
    submit_method: str,
    cluster_obj: clusterlib.ClusterLib,
    tx_file: clusterlib.FileType,
    txins: list[clusterlib.UTXOData],
    wait_blocks: int = 2,
) -> None:
    """Submit a transaction using the selected method.

    Args:
        submit_method: A method to use for submitting the transaction.
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        tx_file: A path to signed transaction file.
        txins: An iterable of `clusterlib.UTXOData`, specifying input UTxOs.
        wait_blocks: A number of new blocks to wait for (default = 2).
    """
    if submit_method == SubmitMethods.CLI:
        cluster_obj.g_transaction.submit_tx(tx_file=tx_file, txins=txins, wait_blocks=wait_blocks)
    elif submit_method == SubmitMethods.API:
        submit_api.submit_tx(
            cluster_obj=cluster_obj, tx_file=tx_file, txins=txins, wait_blocks=wait_blocks
        )
    else:
        msg = f"Unknown submit method: {submit_method}"
        raise ValueError(msg)
