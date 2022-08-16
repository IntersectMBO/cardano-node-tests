"""Tests for node upgrade."""
import logging
import os

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles

LOGGER = logging.getLogger(__name__)

BASE_REVISION = os.environ.get("BASE_REVISION")
UPGRADE_REVISION = os.environ.get("UPGRADE_REVISION")

pytestmark = pytest.mark.skipif(
    not (BASE_REVISION and UPGRADE_REVISION and configuration.DEV_CLUSTER_RUNNING),
    reason="not upgrade testing",
)


class TestUpgrade:
    @allure.link(helpers.get_vcs_link())
    # this test needs to run first
    @pytest.mark.order(4)
    # not really a smoke test, but upgrade job runs only smoke tests and this is better than adding
    # special handling for just this test
    @pytest.mark.smoke
    def test_ignore_log_errors(
        self,
        cluster: clusterlib.ClusterLib,
        worker_id: str,
    ):
        """Ignore selected errors in log right after node upgrade."""
        common.get_test_id(cluster)

        # Ignore ledger replay when upgrading from node version 1.34.1.
        # The error message appears only right after the node is upgraded. This ignore rule has
        # effect only in this test.
        if BASE_REVISION == "1.34.1":
            logfiles.add_ignore_rule(
                files_glob="*.stdout",
                regex="ChainDB:Error:.* Invalid snapshot DiskSnapshot .*DeserialiseFailure 168",
                ignore_file_id=worker_id,
            )
