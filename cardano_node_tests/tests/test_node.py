import logging
import time

import pytest

LOGGER = logging.getLogger(__name__)


def test_update_proposal(cluster_session):
    if cluster_session.get_current_epoch_no() < 1:
        sleep_time = cluster_session.slot_length * cluster_session.epoch_length
        LOGGER.info(f"Waiting 1 epoch to submit proposal ({sleep_time} seconds).")
        time.sleep(sleep_time)

    # TODO - revert back?
    cluster_session.submit_update_proposal(["--decentralization-parameter", "0.5"])

    LOGGER.info(f"Update Proposal submited. Sleeping until next epoch ({sleep_time} seconds).")
    time.sleep(sleep_time + 15)

    cluster_session.refresh_pparams()
    d = cluster_session.pparams["decentralisationParam"]
    assert (
        d == 0.5
    ), f"Cluster update proposal failed! Exit value: {d}.\nTip:{cluster_session.get_tip()}"


@pytest.mark.clean_cluster
def test_dummy_clean():
    pass


def test_dummy():
    pass
