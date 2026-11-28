"""Tests for db-sync's off-chain voting anchor data size limit.

cardano-db-sync fetches off-chain vote data (gov-action and DRep anchors) up to a fixed size
(``httpGetOffChainVoteDataSingle`` in cardano-db-sync ``OffChain/Http.hs``). Content up to
``OFFCHAIN_VOTE_MAX_BYTES`` is downloaded and stored in ``off_chain_vote_data``; larger content is
rejected with a ``Size error`` recorded in ``off_chain_vote_fetch_error`` and no data row.

Both sides of that boundary are exercised through an info action whose anchor is generated at the
target size and served from the internal web server (db-sync fetches it over HTTP).
"""

import json
import logging
import pathlib as pl
import typing as tp

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_service_manager as db_sync
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import web
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = [
    pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.CONWAY,
        reason="runs only with Tx era >= Conway",
    ),
    pytest.mark.dbsync_config,
]

# db-sync's off-chain vote data size cap; content of this many bytes is stored, one more is not.
OFFCHAIN_VOTE_MAX_BYTES = 3_000_000
# A conformant CIP-108 anchor is padded up to the target size, so the accepted case decodes to
# is_valid=TRUE just like the standard anchor.
CONFORMANT_ANCHOR_FILE = DATA_DIR / "ga_anchor.json"


def _anchor_json_of_size(*, size: int, unique: str) -> str:
    """Return a conformant anchor serialized to exactly ``size`` bytes.

    Built from ``ga_anchor.json``, made unique with ``unique`` (so each run gets its own hash and
    ``off_chain_vote_data`` row) and padded via ``body.motivation`` to hit the exact byte size.
    """
    anchor = json.loads(CONFORMANT_ANCHOR_FILE.read_text(encoding="utf-8"))
    anchor["body"]["title"] = f"off-chain vote size test {unique}"
    anchor["body"]["motivation"] = ""
    pad = size - len(json.dumps(anchor).encode("utf-8"))
    if pad < 0:
        msg = f"Base anchor already exceeds target size {size} B"
        raise ValueError(msg)
    # `motivation` is free text and ASCII, so each padding char adds exactly one byte.
    anchor["body"]["motivation"] = "a" * pad
    serialized = json.dumps(anchor)
    assert len(serialized.encode("utf-8")) == size, "anchor padding did not hit the target size"
    return serialized


class TestOffChainVoteSize:
    """Tests for the off-chain voting anchor data size boundary."""

    @pytest.fixture
    def cluster_singleton_governance(
        self,
        cluster_manager: cluster_management.ClusterManager,
    ) -> governance_utils.GovClusterT:
        """Lock the whole cluster instance and set up governance.

        These tests restart db-sync with the ``--allow-private-offchain-urls`` flag, a
        cluster-wide change; locking the whole instance keeps it isolated from tests running in
        parallel while still allowing governance actions to be submitted.
        """
        cluster_obj = cluster_manager.get(lock_resources=[cluster_management.Resources.CLUSTER])
        governance_data = governance_setup.get_default_governance(
            cluster_manager=cluster_manager, cluster_obj=cluster_obj
        )
        governance_utils.wait_delayed_ratification(cluster_obj=cluster_obj)
        return cluster_obj, governance_data

    @pytest.fixture
    def pool_user_ug(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_singleton_governance: governance_utils.GovClusterT,
    ) -> clusterlib.PoolUser:
        """Create a pool user for "use governance"."""
        cluster, __ = cluster_singleton_governance
        key = helpers.get_current_line_str()
        name_template = common.get_test_id(cluster)
        # Re-fund when the balance drops below `min_amount` so each proposal can cover the gov
        # action deposit (it is never returned here).
        return common.get_registered_pool_user(
            name_template=name_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            caching_key=key,
            amount=400_000_000,
            min_amount=350_000_000,
        )

    @pytest.fixture
    def allow_private_offchain_urls(
        self,
        cluster_singleton_governance: governance_utils.GovClusterT,  # noqa: ARG002
    ) -> tp.Generator[None]:
        """Enable db-sync's ``--allow-private-offchain-urls`` for the duration of the test.

        The anchor is served from the internal (localhost) web server, which db-sync's off-chain
        fetcher rejects by default. The flag is enabled on the locked singleton cluster and
        disabled again on teardown, so the cluster-wide default (localhost rejected, as asserted
        by ``test_smash.py::test_fetch_pool_metadata_localhost_rejected``) is restored for other
        tests.
        """
        manager = db_sync.DBSyncManager()
        manager.set_allow_private_offchain_urls(enable=True)
        yield
        manager.set_allow_private_offchain_urls(enable=False)

    def _submit_info_action_with_anchor(
        self,
        *,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        request: FixtureRequest,
        name_template: str,
        anchor_size: int,
    ) -> tuple[str, str, str]:
        """Submit an info action carrying an anchor of ``anchor_size`` bytes.

        The anchor is served from the internal web server (unpublished on teardown). Returns the
        anchor data hash, the anchor url, and the served anchor JSON.
        """
        anchor_json = _anchor_json_of_size(size=anchor_size, unique=helpers.get_rand_str(8))
        anchor_file = pl.Path(f"{name_template}_anchor.json")
        anchor_file.write_text(anchor_json, encoding="utf-8")

        anchor_url = web.publish(file_path=anchor_file, published_name=f"{name_template}.json")
        request.addfinalizer(lambda: web.unpublish(url=anchor_url))
        anchor_data_hash = cluster.g_governance.get_anchor_data_hash(file_text=anchor_file)

        deposit_amt = cluster.g_query.get_gov_action_deposit()
        info_action = cluster.g_governance.action.create_info(
            action_name=name_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
        )
        tx_files_action = clusterlib.TxFiles(
            proposal_files=[info_action.action_file],
            signing_key_files=[pool_user.payment.skey_file],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{name_template}_action",
            src_address=pool_user.payment.address,
            build_method=clusterlib_utils.BuildMethods.BUILD,
            tx_files=tx_files_action,
        )

        return anchor_data_hash, anchor_url, anchor_json

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_within_size_limit(
        self,
        cluster_singleton_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
        allow_private_offchain_urls: None,  # noqa: ARG002
        request: FixtureRequest,
    ):
        """Test that an anchor at the size limit is stored.

        * Submit an info action with a conformant anchor of exactly ``OFFCHAIN_VOTE_MAX_BYTES``.
        * Verify db-sync stores ``off_chain_vote_data`` with ``is_valid = TRUE`` and the raw bytes.
        """
        cluster, __ = cluster_singleton_governance
        temp_template = common.get_test_id(cluster)

        anchor_data_hash, _anchor_url, anchor_json = self._submit_info_action_with_anchor(
            cluster=cluster,
            pool_user=pool_user_ug,
            request=request,
            name_template=temp_template,
            anchor_size=OFFCHAIN_VOTE_MAX_BYTES,
        )

        reqc.db015.start(url=helpers.get_vcs_link())

        def _query_func() -> dbsync_types.OffChainVoteDataRecord:
            data = dbsync_utils.get_action_data(data_hash=anchor_data_hash)
            if data is None:
                msg = f"No off_chain_vote_data for anchor hash {anchor_data_hash} in db-sync yet"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            return data

        db_data = tp.cast(
            "dbsync_types.OffChainVoteDataRecord",
            dbsync_utils.retry_query(query_func=_query_func, timeout=360),
        )
        assert db_data.is_valid is True, f"Unexpected is_valid: {db_data.is_valid}"
        assert db_data.bytes == anchor_json.encode("utf-8").hex(), (
            "Stored bytes do not match the served anchor"
        )
        reqc.db015.success()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_over_size_limit(
        self,
        cluster_singleton_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
        allow_private_offchain_urls: None,  # noqa: ARG002
        request: FixtureRequest,
    ):
        """Test that an anchor just over the size limit is rejected.

        * Submit an info action with an anchor of ``OFFCHAIN_VOTE_MAX_BYTES + 1`` bytes.
        * Verify db-sync records a ``Size error`` in ``off_chain_vote_fetch_error`` and stores no
          ``off_chain_vote_data`` row.
        """
        cluster, __ = cluster_singleton_governance
        temp_template = common.get_test_id(cluster)

        anchor_data_hash, anchor_url, _anchor_json = self._submit_info_action_with_anchor(
            cluster=cluster,
            pool_user=pool_user_ug,
            request=request,
            name_template=temp_template,
            anchor_size=OFFCHAIN_VOTE_MAX_BYTES + 1,
        )

        reqc.db021.start(url=helpers.get_vcs_link())

        def _query_func() -> str:
            anchor_id = dbsync_queries.query_voting_anchor_id(url=anchor_url)
            if anchor_id is None:
                msg = f"Voting anchor for url {anchor_url} not in db-sync yet"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            errors = list(
                dbsync_queries.query_off_chain_vote_fetch_error(voting_anchor_id=anchor_id)
            )
            if not errors:
                msg = f"No off_chain_vote_fetch_error for anchor {anchor_id} in db-sync yet"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            return errors[-1].fetch_error or ""

        fetch_error = tp.cast("str", dbsync_utils.retry_query(query_func=_query_func, timeout=360))
        assert "Size error" in fetch_error, f"Unexpected fetch error: {fetch_error}"

        # The over-limit anchor must not be stored.
        stored = list(dbsync_queries.query_off_chain_vote_data(data_hash=anchor_data_hash))
        assert stored == [], f"Over-limit anchor was unexpectedly stored: {anchor_data_hash}"
        reqc.db021.success()
