import logging
import time

import pytest

LOGGER = logging.getLogger(__name__)


@pytest.mark.clean_cluster
def test_update_proposal(cluster):
    sleep_time = cluster.slot_length * cluster.epoch_length
    LOGGER.info(f"Waiting 1 epoch to submit proposal ({sleep_time} seconds).")
    time.sleep(sleep_time)
    cluster.submit_update_proposal(["--decentralization-parameter", "0.5"])

    LOGGER.info(f"Update Proposal submited. Sleeping until next epoch ({sleep_time} seconds).")
    time.sleep(sleep_time + 15)

    cluster.refresh_pparams()
    d = cluster.pparams["decentralisationParam"]
    assert d == 0.5, f"Cluster update proposal failed! Exit value: {d}.\nTip:{cluster.get_tip()}"


def test_dummy():
    pass
