import logging
import time

LOGGER = logging.getLogger(__name__)


def test_update_proposal(cluster_session):
    """Submit update proposal."""
    cluster_session.refresh_pparams()
    orig_value = cluster_session.pparams["decentralisationParam"]
    sleep_time = cluster_session.slot_length * cluster_session.epoch_length

    if cluster_session.get_last_block_epoch() < 1:
        LOGGER.info(f"Waiting 1 epoch to submit proposal ({sleep_time} seconds).")
        time.sleep(sleep_time)

    def _update_proposal(param_value):
        cluster_session.submit_update_proposal(
            ["--decentralization-parameter", str(param_value)],
            epoch=cluster_session.get_last_block_epoch(),
        )

        LOGGER.info(
            f"Update Proposal submited (param_value={param_value}). "
            f"Sleeping until next epoch ({sleep_time} seconds)."
        )
        time.sleep(sleep_time + 15)

        cluster_session.refresh_pparams()
        d = cluster_session.pparams["decentralisationParam"]
        assert str(d) == str(
            param_value
        ), f"Cluster update proposal failed! Param value: {d}.\nTip:{cluster_session.get_tip()}"

    _update_proposal(0.5)
    # revert to original value
    time.sleep(sleep_time)
    _update_proposal(orig_value)
