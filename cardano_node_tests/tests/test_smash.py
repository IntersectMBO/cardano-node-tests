"""Tests for basic transactions."""

import dataclasses
import itertools
import logging
import random
import re

import json
from http import HTTPStatus
import allure
import pytest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.utils import logfiles
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils 
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import smash_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class TestBasicSmash:
    """Basic tests for SMASH service."""

    @pytest.fixture()
    def pool_id(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> str:
        """Get random pool id from node's current set of stake pool ids."""
        pools_ids = cluster.g_query.get_pools_ids()
        return random.choice(pools_ids)

    @pytest.fixture(scope="session")
    def smash(
        self,
    ) -> smash_utils.SmashClient:
        """Create SMASH client."""
        smash = smash_utils.get_client()
        return smash

    def test_fetch_pool_metadata(
            self, 
            pool_id,
            smash: smash_utils.SmashClient
        ):
        metadata_dbsync = next(iter(dbsync_queries.query_off_chain_pool_data(pool_id)), None)
        expected_metadata = smash_utils.PoolMetadata(
                name=metadata_dbsync.json["name"],
                description=metadata_dbsync.json["description"],
                ticker=metadata_dbsync.ticker_name,
                homepage=metadata_dbsync.json["homepage"]
            )
        actual_metadata = smash.get_pool_metadata(pool_id, metadata_dbsync.hash.hex())
        assert expected_metadata == actual_metadata

    def test_delist_pool(
            self, 
            pool_id,
            smash: smash_utils.SmashClient,
            worker_id: str,
        ):
        pool_data = dbsync_utils.get_pool_data(pool_id)
        expected_delisted_pool = smash_utils.PoolData(pool_id=pool_data.hash)
        actual_delisted_pool = smash.delist_pool(pool_id)
        assert expected_delisted_pool == actual_delisted_pool

        # Check if it is possible to fetch metadata for blacklisted pool
        res_metadata = smash.get_pool_metadata(pool_id, pool_data.metadata_hash)
        assert HTTPStatus.FORBIDDEN == res_metadata.response.status_code
        assert f"Pool {pool_data.hash} is delisted" == res_metadata.response.text

        expected_err_regexes = ["Delisted pool already exists!"]
        logfiles.add_ignore_rule(
            files_glob="smash.stdout",
            regex="|".join(expected_err_regexes),
            ignore_file_id=worker_id,
        )
        # Check behavior for delisting already delisted pool
        try:
            smash.delist_pool(pool_id)
        except Exception as err:
            res = err.response.json()
            expected_error = smash_utils.DbInsertError(code='DbInsertError', description='Delisted pool already exists!')
            actual_error = smash_utils.DbInsertError(code=res["code"], description=res["description"])
            assert HTTPStatus.BAD_REQUEST == err.response.status_code
            assert expected_error == actual_error
        finally:
            smash.enlist_pool(pool_id)