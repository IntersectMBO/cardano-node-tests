import logging
import os
import time

import pytest

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory):
    curdir = os.getcwd()
    tmp_path = tmp_path_factory.mktemp("test_update_proposal")
    try:
        os.chdir(tmp_path)
        yield tmp_path
    finally:
        os.chdir(curdir)


pytestmark = pytest.mark.usefixtures("temp_dir")


@pytest.mark.clean_cluster
def test_update_proposal(cluster):
    """Submit update proposal."""
    param_value = 0.5
    sleep_time = cluster.slot_length * cluster.epoch_length

    LOGGER.info(f"Waiting 1 epoch to submit proposal ({sleep_time} seconds).")
    time.sleep(sleep_time)

    cluster.submit_update_proposal(
        cli_args=["--decentralization-parameter", str(param_value)], epoch=1,
    )

    LOGGER.info(
        f"Update Proposal submited (param_value={param_value}). "
        f"Sleeping until next epoch ({sleep_time} seconds)."
    )
    time.sleep(sleep_time + 15)

    d = cluster.get_protocol_params()["decentralisationParam"]
    assert str(d) == str(
        param_value
    ), f"Cluster update proposal failed! Param value: {d}.\nTip:{cluster.get_tip()}"
