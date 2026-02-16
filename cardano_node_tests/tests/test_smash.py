"""Tests for basic SMASH operations."""

import http
import logging
import random
import re

import allure
import pytest
import requests
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import smash_utils

LOGGER = logging.getLogger(__name__)

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

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_fetch_pool_metadata(
        self, locked_pool: dbsync_types.PoolDataRecord, smash: smash_utils.SmashClient
    ):
        """Test fetching pool metadata from SMASH.

        Test retrieval of off-chain pool metadata from SMASH server and verify it matches
        db-sync records.

        * get pool ID from locked pool fixture
        * query off-chain pool metadata from db-sync (retry with timeout for availability)
        * extract expected metadata fields (name, description, ticker, homepage)
        * fetch pool metadata from SMASH using pool ID and metadata hash
        * verify SMASH metadata matches db-sync metadata exactly
        """
        pool_id = locked_pool.hash

        # Offchain metadata is inserted into database few minutes after start of a cluster
        def _query_func():
            pool_metadata = next(
                iter(dbsync_queries.query_off_chain_pool_data(pool_id_bech32=locked_pool.view)),
                None,
            )
            if pool_metadata is None:
                msg = f"no off-chain pool data record found for pool {pool_id}"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            return pool_metadata

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

        * get pool ID from locked pool fixture
        * register cleanup function to re-enlist pool after test
        * delist the pool using SMASH API
        * verify delist response contains correct pool ID
        * attempt to fetch metadata for delisted pool
        * check that fetch fails with HTTP 403 Forbidden and "Pool is delisted" message
        * add log ignore rule for expected "Delisted pool already exists!" error
        * attempt to re-delist already delisted pool
        * verify re-delist fails with HTTP 400 Bad Request and DbInsertError
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

        * get pool ID from locked pool fixture
        * attempt to enlist already enlisted pool
        * verify enlist fails with HTTP 404 Not Found and "RecordDoesNotExist" error
        * delist the pool to prepare for enlist test
        * verify delisted pool cannot be queried (HTTP 403 Forbidden)
        * enlist the delisted pool
        * verify enlist response contains correct pool ID
        * fetch metadata for newly enlisted pool
        * verify metadata retrieval succeeds for enlisted pool
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

        * select random pool ID from cluster
        * register cleanup function to delete reserved tickers after test completion
        * reserve 3-character random ticker for the pool
        * verify reservation response contains correct ticker name
        * attempt to reserve already-taken ticker for same pool
        * verify re-reservation fails with HTTP 400 Bad Request and TickerAlreadyReserved error
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
