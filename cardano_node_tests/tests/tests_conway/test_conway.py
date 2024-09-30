"""Tests for Conway features that doesn't fit into any more specific file."""

import logging

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


class TestConway:
    """General tests for Conway era."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_genesis_cert_not_available(self, cluster: clusterlib.ClusterLib):
        """Check that the `create-genesis-key-delegation-certificate` command is not available."""
        common.get_test_id(cluster)

        reqc.cip071.start(url=helpers.get_vcs_link())

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "cardano-cli",
                    "conway",
                    "governance",
                    "create-genesis-key-delegation-certificate",
                ],
                add_default_args=False,
            )
        err_str = str(excinfo.value)
        assert "Invalid argument" in err_str, err_str

        reqc.cip071.success()
