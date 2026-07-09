"""Tests for basic SMASH operations."""

import dataclasses
import http
import logging
import pathlib as pl
import random
import re
import typing as tp

import allure
import pytest
import requests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import smash_utils

LOGGER = logging.getLogger(__name__)

DATA_DIR = pl.Path(__file__).parent / "data"

pytestmark = [
    pytest.mark.skipif(not configuration.HAS_SMASH, reason="SMASH is not available"),
    pytest.mark.smash,
]


def check_request_error(
    err: requests.exceptions.RequestException,
    expected_status: http.HTTPStatus,
    expected_code: str | None,
    expected_description: str,
) -> None:
    """Assert expected HTTP errors in requests, handling both JSON and text responses."""
    response = err.response
    assert response is not None, "No error response"
    assert response.status_code == expected_status

    try:
        error_data = response.json()
        actual_code = error_data.get("code")
        actual_description = error_data.get("description")
    except ValueError:
        # If not JSON, treat the entire response as text
        actual_code = None
        actual_description = response.text.strip()

    assert actual_code == expected_code
    assert actual_description == expected_description


class TestBasicSmash:
    """Basic tests for SMASH service."""

    @pytest.fixture()
    def locked_pool(
        self,
        cluster_lock_pool: tuple[clusterlib.ClusterLib, str],
    ) -> dbsync_types.PoolDataRecord:
        """Get id of locked pool from cluster_lock_pool fixture."""
        cluster_obj, pool_name = cluster_lock_pool
        pools_ids = cluster_obj.g_query.get_stake_pools()
        locked_pool_number = pool_name.replace("node-pool", "")
        pattern = re.compile(r"pool" + re.escape(locked_pool_number) + r"(\D|$)")
        pools = [next(dbsync_queries.query_pool_data(pool_id_bech32=p)) for p in pools_ids]
        locked_pool = next(p for p in pools if p.metadata_url and pattern.search(p.metadata_url))
        locked_pool_data = dbsync_utils.get_pool_data(pool_id_bech32=locked_pool.view)
        assert locked_pool_data is not None, "Locked pool data not found!"
        return locked_pool_data

    @pytest.fixture()
    def smash(
        self,
    ) -> smash_utils.SmashClient | None:
        """Create SMASH client."""
        smash = smash_utils.get_client()
        if smash is None:
            pytest.skip("SMASH client is not available.")
        return smash

    @pytest.fixture()
    def locked_pool_with_public_metadata(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_pool: tuple[clusterlib.ClusterLib, str],
    ) -> tp.Iterator[dbsync_types.PoolDataRecord]:
        """Lock any cluster pool and re-register it with a publicly-hosted metadata URL.

        Cluster-bootstrap pools register with ``http://localhost:.../poolN.json``, but
        db-sync's off-chain pool fetcher refuses to connect to private/loopback/link-local
        addresses (see ``cardano-db-sync/src/Cardano/DbSync/OffChain/Http.hs``), so those
        pools never get an ``off_chain_pool_data`` row. To exercise the success path of
        the fetcher this fixture re-registers the locked pool with a publicly-reachable
        URL pointing at the same content as ``data/pool_metadata.json``. Re-registration
        uses ``deposit=0`` because the pool is already registered. The original metadata
        URL and hash are restored at teardown so the pool is handed back to the cluster
        in the state the bootstrap left it.

        Yields:
            dbsync_types.PoolDataRecord: The locked pool refreshed from db-sync once
            it has ingested the metadata update.
        """
        cluster_obj, pool_name = cluster_lock_pool
        pool_rec = cluster_manager.cache.addrs_data[pool_name]
        pool_owner = clusterlib.PoolUser(payment=pool_rec["payment"], stake=pool_rec["stake"])
        pool_id_bech32 = delegation.get_pool_id(
            cluster_obj=cluster_obj,
            addrs_data=cluster_manager.cache.addrs_data,
            pool_name=pool_name,
        )

        # Snapshot the current on-chain pool data so we can restore it after the test.
        original_pool_data = clusterlib_utils.load_registered_pool_data(
            cluster_obj=cluster_obj, pool_name=pool_name, pool_id=pool_id_bech32
        )

        public_metadata_file = DATA_DIR / "pool_metadata.json"
        public_metadata_hash = cluster_obj.g_stake_pool.gen_pool_metadata_hash(public_metadata_file)
        public_pool_data = dataclasses.replace(
            original_pool_data,
            pool_metadata_url=common.PUBLIC_POOL_METADATA_URL,
            pool_metadata_hash=public_metadata_hash,
        )

        temp_template = common.get_test_id(cluster_obj)

        # Re-register the pool with the public metadata URL. ``deposit=0`` because the
        # pool is already registered; the certificate acts as an update.
        cluster_obj.g_stake_pool.register_stake_pool(
            pool_data=public_pool_data,
            pool_owners=[pool_owner],
            vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
            cold_key_pair=pool_rec["cold_key_pair"],
            tx_name=f"{temp_template}_smash_metadata_public",
            reward_account_vkey_file=pool_rec["reward"].vkey_file,
            deposit=0,
        )

        # Wait until db-sync has ingested the metadata update so the test sees a
        # consistent view of the new URL/hash.
        def _wait_for_dbsync() -> dbsync_types.PoolDataRecord:
            record = dbsync_utils.get_pool_data(pool_id_bech32=pool_id_bech32)
            if record is None or record.metadata_url != common.PUBLIC_POOL_METADATA_URL:
                msg = f"db-sync has not yet ingested the metadata update for pool {pool_id_bech32}"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            return record

        try:
            yield dbsync_utils.retry_query(query_func=_wait_for_dbsync, timeout=120)
        finally:
            # Re-register the pool with its original metadata URL and hash so the
            # cluster_lock_pool resource is handed back in the state we found it.
            # If restoration fails, mark the cluster instance for respin so a
            # subsequent test isn't left with the locked pool stuck on the public URL
            # (the ``locked_pool`` fixture searches by URL pattern containing the pool
            # name and would not match ``common.PUBLIC_POOL_METADATA_URL``).
            with cluster_manager.respin_on_failure():
                cluster_obj.g_stake_pool.register_stake_pool(
                    pool_data=original_pool_data,
                    pool_owners=[pool_owner],
                    vrf_vkey_file=pool_rec["vrf_key_pair"].vkey_file,
                    cold_key_pair=pool_rec["cold_key_pair"],
                    tx_name=f"{temp_template}_smash_metadata_restore",
                    reward_account_vkey_file=pool_rec["reward"].vkey_file,
                    deposit=0,
                )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_fetch_pool_metadata_localhost_rejected(
        self,
        locked_pool: dbsync_types.PoolDataRecord,
    ):
        """Verify db-sync rejects pool metadata served from localhost / private hosts.

        db-sync's off-chain pool-metadata fetcher refuses URLs whose host part is one
        of ``localhost``, ``127.0.0.1``, ``::1``, ``[::1]``, ``10.*``, or ``192.168.*``
        (see ``parseOffChainUrl`` and ``isLocalhostHost`` in
        ``cardano-db-sync/src/Cardano/DbSync/OffChain/Http.hs``). The rejection happens
        at URL parse time, before any network I/O, and is recorded as an
        ``OCFErrUrlParseFail`` whose stored string is of the form
        ``Error Offchain Pool: URL parse error for <url> resulted in :
        "Access to localhost is not allowed"`` — see the ``Show OffChainFetchError``
        instance and ``fetchUrlToString`` in
        ``cardano-db-sync/src/Cardano/DbSync/Types.hs``. A second layer — a restricted
        HTTP manager that rejects any address resolving into a private / loopback /
        link-local range — backs this up at connect time, but for verbatim
        ``localhost`` URLs the string-based check fires first.

        Cluster-bootstrap pools register with ``http://localhost:.../poolN.json``, so for
        those pools the fetch always fails and the result lands in
        ``off_chain_pool_fetch_error`` instead of ``off_chain_pool_data``.

        * Use a cluster-bootstrap pool whose metadata URL points at localhost
        * Wait for db-sync's off-chain fetcher to record the rejection
        * Assert that ``off_chain_pool_fetch_error`` has a row for the pool
        * Assert the recorded error contains db-sync's exact rejection wording
        * Assert that ``off_chain_pool_data`` has no row for this pool
        """
        pool_id_bech32 = locked_pool.view

        def _query_func() -> dbsync_queries.PoolOffChainFetchErrorDBRow:
            fetch_error = next(
                iter(
                    dbsync_queries.query_off_chain_pool_fetch_error(pool_id_bech32=pool_id_bech32)
                ),
                None,
            )
            if fetch_error is None:
                msg = (
                    f"no off-chain pool fetch error row found for pool "
                    f"{pool_id_bech32} (metadata URL {locked_pool.metadata_url!r})"
                )
                raise dbsync_utils.DbSyncNoResponseError(msg)
            return fetch_error

        fetch_error_row = dbsync_utils.retry_query(query_func=_query_func, timeout=360)

        # Match db-sync's exact rejection wording for localhost URLs (verified against
        # `Show OffChainFetchError` in cardano-db-sync/src/Cardano/DbSync/Types.hs).
        expected_substring = "Access to localhost is not allowed"
        assert expected_substring in (fetch_error_row.fetch_error or ""), (
            f"Expected db-sync fetch error for {locked_pool.metadata_url!r} to contain "
            f"{expected_substring!r}, got {fetch_error_row.fetch_error!r}"
        )

        successful_rows = list(
            dbsync_queries.query_off_chain_pool_data(pool_id_bech32=pool_id_bech32)
        )
        assert not successful_rows, (
            f"Expected no successful off_chain_pool_data rows for pool "
            f"{pool_id_bech32} with localhost URL {locked_pool.metadata_url!r}, "
            f"got {len(successful_rows)} row(s)"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_fetch_pool_metadata_public(
        self,
        locked_pool_with_public_metadata: dbsync_types.PoolDataRecord,
        smash: smash_utils.SmashClient,
    ):
        """Test fetching pool metadata from SMASH when the URL is publicly reachable.

        db-sync's off-chain pool fetcher succeeds only for URLs that resolve to
        non-private IP addresses. The ``locked_pool_with_public_metadata`` fixture
        re-registers a cluster-bootstrap pool with ``common.PUBLIC_POOL_METADATA_URL``
        (content equivalent to ``data/pool_metadata.json``) and restores the
        pool's original metadata at teardown.

        * Re-register a locked cluster pool with a publicly-hosted metadata URL (fixture)
        * Wait for db-sync's off-chain fetcher to ingest the metadata
        * Read the metadata as db-sync recorded it
        * Fetch the same pool's metadata from SMASH using pool ID and metadata hash
        * Verify SMASH and db-sync agree on every metadata field
        """
        locked_pool = locked_pool_with_public_metadata
        pool_id = locked_pool.hash
        expected_hash_bytes = bytes.fromhex(locked_pool.metadata_hash)

        # Off-chain metadata appears in db-sync once the fetcher has processed the
        # new pool_metadata_ref row (poll interval ~5 s plus HTTP fetch time).
        def _query_func() -> dbsync_queries.PoolOffChainDataDBRow:
            for pool_metadata in dbsync_queries.query_off_chain_pool_data(
                pool_id_bech32=locked_pool.view
            ):
                if bytes(pool_metadata.hash) == expected_hash_bytes:
                    return pool_metadata
            msg = (
                f"no off-chain pool data record with expected hash "
                f"{locked_pool.metadata_hash} found for pool {pool_id}"
            )
            raise dbsync_utils.DbSyncNoResponseError(msg)

        metadata_dbsync = dbsync_utils.retry_query(query_func=_query_func, timeout=360)

        expected_metadata = smash_utils.PoolMetadata(
            name=metadata_dbsync.json["name"],
            description=metadata_dbsync.json["description"],
            ticker=metadata_dbsync.ticker_name,
            homepage=metadata_dbsync.json["homepage"],
        )
        actual_metadata = smash.get_pool_metadata(
            pool_id=pool_id, pool_meta_hash=metadata_dbsync.hash.hex()
        )
        assert expected_metadata == actual_metadata

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_delist_pool(
        self,
        locked_pool: dbsync_types.PoolDataRecord,
        smash: smash_utils.SmashClient,
        request: pytest.FixtureRequest,
        worker_id: str,
    ):
        """Test delisting a pool from SMASH.

        Test pool delisting functionality and verify delisted pools cannot be queried.

        * Get pool ID from locked pool fixture
        * Register cleanup function to re-enlist pool after test
        * Delist the pool using SMASH API
        * Verify delist response contains correct pool ID
        * Attempt to fetch metadata for delisted pool
        * Check that fetch fails with HTTP 403 Forbidden and "Pool is delisted" message
        * Add log ignore rule for expected "Delisted pool already exists!" error
        * Attempt to re-delist already delisted pool
        * Verify re-delist fails with HTTP 400 Bad Request and DbInsertError
        """
        pool_id = locked_pool.hash

        # Define and register function that ensures pool is re-enlisted after test completion
        def pool_cleanup():
            smash.enlist_pool(pool_id=pool_id)

        request.addfinalizer(pool_cleanup)

        # Delist the pool
        expected_delisted_pool = smash_utils.PoolData(pool_id=pool_id)
        actual_delisted_pool = smash.delist_pool(pool_id=pool_id)
        assert expected_delisted_pool == actual_delisted_pool

        # Check if fetching metadata for a delisted pool returns an error
        try:
            smash.get_pool_metadata(pool_id=pool_id, pool_meta_hash=locked_pool.metadata_hash)
        except requests.exceptions.RequestException as err:
            check_request_error(
                err=err,
                expected_status=http.HTTPStatus.FORBIDDEN,
                expected_code=None,
                expected_description=f"Pool {pool_id} is delisted",
            )

        # Ignore expected errors in logs that would fail test in teardown phase
        err_msg = "Delisted pool already exists!"
        expected_err_regexes = [err_msg]
        logfiles.add_ignore_rule(
            files_glob="smash.stdout",
            regex="|".join(expected_err_regexes),
            ignore_file_id=worker_id,
        )
        # Ensure re-delisting an already delisted pool returns an error
        try:
            smash.delist_pool(pool_id=pool_id)
        except requests.exceptions.RequestException as err:
            check_request_error(
                err=err,
                expected_status=http.HTTPStatus.BAD_REQUEST,
                expected_code="DbInsertError",
                expected_description=err_msg,
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_enlist_pool(
        self,
        locked_pool: dbsync_types.PoolDataRecord,
        smash: smash_utils.SmashClient,
    ):
        """Test enlisting a pool in SMASH.

        Test pool enlisting functionality after delisting and verify enlisted pools can be queried.

        * Get pool ID from locked pool fixture
        * Attempt to enlist already enlisted pool
        * Verify enlist fails with HTTP 404 Not Found and "RecordDoesNotExist" error
        * Delist the pool to prepare for enlist test
        * Verify delisted pool cannot be queried (HTTP 403 Forbidden)
        * Enlist the delisted pool
        * Verify enlist response contains correct pool ID
        * Fetch metadata for newly enlisted pool
        * Verify metadata retrieval succeeds for enlisted pool
        """
        pool_id = locked_pool.hash
        # Ensure enlisting an already enlisted pool returns an error
        try:
            smash.enlist_pool(pool_id=pool_id)
        except requests.exceptions.RequestException as err:
            check_request_error(
                err=err,
                expected_status=http.HTTPStatus.NOT_FOUND,
                expected_code="RecordDoesNotExist",
                expected_description="The requested record does not exist.",
            )

        # Delist the pool
        smash.delist_pool(pool_id=pool_id)
        try:
            smash.get_pool_metadata(pool_id=pool_id, pool_meta_hash=locked_pool.metadata_hash)
        except requests.exceptions.RequestException as err:
            check_request_error(
                err=err,
                expected_status=http.HTTPStatus.FORBIDDEN,
                expected_code=None,
                expected_description=f"Pool {pool_id} is delisted",
            )

        # Enlist the pool
        actual_res_enlist = smash.enlist_pool(pool_id=pool_id)
        expected_res_enlist = smash_utils.PoolData(pool_id=pool_id)
        assert expected_res_enlist == actual_res_enlist

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_reserve_ticker(
        self,
        cluster: clusterlib.ClusterLib,
        smash: smash_utils.SmashClient,
        request: pytest.FixtureRequest,
    ):
        """Test reserving a ticker for a pool in SMASH.

        Test ticker reservation functionality and verify duplicate reservations are rejected.

        * Select random pool ID from cluster
        * Register cleanup function to delete reserved tickers after test completion
        * Reserve 3-character random ticker for the pool
        * Verify reservation response contains correct ticker name
        * Attempt to reserve already-taken ticker for same pool
        * Verify re-reservation fails with HTTP 400 Bad Request and TickerAlreadyReserved error
        """
        pool_id = random.choice(cluster.g_query.get_stake_pools())

        # Register cleanup function that removes ticker from database after test completion
        request.addfinalizer(dbsync_queries.delete_reserved_pool_tickers)

        # Reserve ticker
        ticker = helpers.get_rand_str(length=3)
        actual_response = smash.reserve_ticker(ticker_name=ticker, pool_hash=pool_id)
        expected_response = smash_utils.PoolTicker(name=f"{ticker}")
        assert expected_response == actual_response

        # Reserve already taken ticker
        try:
            smash.reserve_ticker(ticker_name=ticker, pool_hash=pool_id)
        except requests.exceptions.RequestException as err:
            check_request_error(
                err=err,
                expected_status=http.HTTPStatus.BAD_REQUEST,
                expected_code="TickerAlreadyReserved",
                expected_description=f'Ticker name "{ticker}" is already reserved',
            )
