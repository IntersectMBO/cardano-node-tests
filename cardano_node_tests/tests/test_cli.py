"""Tests for cardano-cli that doesn't fit into any other test file."""
import logging
from pathlib import Path

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


class TestCLI:
    """Tests for cardano-cli."""

    TX_BODY_FILE = DATA_DIR / "test_tx_metadata_both_tx.body"
    TX_FILE = DATA_DIR / "test_tx_metadata_both_tx.signed"
    TX_OUT = DATA_DIR / "test_tx_metadata_both_tx.out"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.DEFAULT_TX_ERA,
        reason="different TX eras doesn't affect this test",
    )
    def test_protocol_mode(self, cluster: clusterlib.ClusterLib):
        """Check the default protocol mode - command works even without specifying protocol mode."""
        if cluster.protocol != clusterlib.Protocols.CARDANO:
            pytest.skip("runs on cluster in full cardano mode")

        common.get_test_id(cluster)

        cluster.cli(
            [
                "query",
                "utxo",
                "--address",
                "addr_test1vpst87uzwafqkxumyf446zr2jsyn44cfpu9fe8yqanyuh6glj2hkl",
                *cluster.magic_args,
            ]
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.DEFAULT_TX_ERA,
        reason="different TX eras doesn't affect this test",
    )
    def test_whole_utxo(self, cluster: clusterlib.ClusterLib):
        """Check that it is possible to return the whole UTxO on local cluster."""
        if cluster.protocol != clusterlib.Protocols.CARDANO:
            pytest.skip("runs on cluster in full cardano mode")

        common.get_test_id(cluster)

        cluster.cli(
            [
                "query",
                "utxo",
                "--whole-utxo",
                *cluster.magic_args,
            ]
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.DEFAULT_TX_ERA,
        reason="different TX eras doesn't affect this test",
    )
    @pytest.mark.skipif(
        cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL,
        reason="supposed to run on testnet",
    )
    def test_testnet_whole_utxo(self, cluster: clusterlib.ClusterLib):
        """Check that it is possible to return the whole UTxO on testnets."""
        common.get_test_id(cluster)

        magic_args = " ".join(cluster.magic_args)
        helpers.run_in_bash(f"cardano-cli query utxo --whole-utxo {magic_args} > /dev/null")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.DEFAULT_TX_ERA,
        reason="different TX eras doesn't affect this test",
    )
    @pytest.mark.testnets
    def test_tx_view(self, cluster: clusterlib.ClusterLib):
        """Check that the output of `transaction view` is as expected."""
        common.get_test_id(cluster)

        tx_body = cluster.view_tx(tx_body_file=self.TX_BODY_FILE)
        tx = cluster.view_tx(tx_file=self.TX_FILE)
        assert tx_body == tx

        with open(self.TX_OUT, encoding="utf-8") as infile:
            tx_view_out = infile.read()
        assert tx == tx_view_out.strip()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(-1)
    @pytest.mark.testnets
    def test_stake_snapshot(self, cluster: clusterlib.ClusterLib):
        """Test the `query stake-snapshot` command."""
        temp_template = common.get_test_id(cluster)

        # make sure the queries can be finished in single epoch
        stop = (
            20 if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL else 200
        )
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=-stop, force_epoch=False
        )

        active_pool_ids = cluster.get_stake_pools()
        if not active_pool_ids:
            pytest.skip("No active pools are available.")
        if len(active_pool_ids) > 200:
            pytest.skip("Skipping on this testnet, there's too many pools.")

        sum_mark = sum_set = sum_go = 0
        for pool_id in active_pool_ids:
            stake_snapshot = cluster.get_stake_snapshot(stake_pool_id=pool_id)
            sum_mark += stake_snapshot["poolStakeMark"]
            sum_set += stake_snapshot["poolStakeSet"]
            sum_go += stake_snapshot["poolStakeGo"]

        # active stake can be lower than sum of stakes, as some pools may not be running
        # and minting blocks
        errors = []
        if sum_mark < stake_snapshot["activeStakeMark"]:
            errors.append(f"active_mark: {sum_mark} < {stake_snapshot['activeStakeMark']}")
        if sum_set < stake_snapshot["activeStakeSet"]:
            errors.append(f"active_set: {sum_set} < {stake_snapshot['activeStakeSet']}")
        if sum_go < stake_snapshot["activeStakeGo"]:
            errors.append(f"active_go: {sum_go} < {stake_snapshot['activeStakeGo']}")

        if errors:
            clusterlib_utils.save_ledger_state(
                cluster_obj=cluster,
                state_name=temp_template,
            )
            err_joined = "\n".join(errors)
            pytest.fail(f"Errors:\n{err_joined}")


@pytest.mark.testnets
class TestAddressInfo:
    """Tests for cardano-cli address info."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_gen", ("static", "dynamic"))
    def test_address_info_payment(self, cluster: clusterlib.ClusterLib, addr_gen: str):
        """Check payment address info."""
        temp_template = common.get_test_id(cluster)

        if addr_gen == "static":
            address = "addr_test1vzp4kj0rmnl5q5046e2yy697fndej56tm35jekemj6ew2gczp74wk"
        else:
            payment_rec = cluster.gen_payment_addr_and_keys(
                name=temp_template,
            )
            address = payment_rec.address

        addr_info = cluster.address_info(address=address)

        assert addr_info.address == address
        assert addr_info.era == "shelley"
        assert addr_info.encoding == "bech32"
        assert addr_info.type == "payment"
        if addr_gen == "static":
            assert addr_info.base16 == "60835b49e3dcff4051f5d6544268be4cdb99534bdc692cdb3b96b2e523"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_gen", ("static", "dynamic"))
    def test_address_info_stake(self, cluster: clusterlib.ClusterLib, addr_gen: str):
        """Check stake address info."""
        temp_template = common.get_test_id(cluster)

        if addr_gen == "static":
            address = "stake_test1uz5mstpskyhpcvaw2enlfk8fa5k335cpd0lfz6chd5c2xpck3nld4"
        else:
            stake_rec = cluster.gen_stake_addr_and_keys(
                name=temp_template,
            )
            address = stake_rec.address

        addr_info = cluster.address_info(address=address)

        assert addr_info.address == address
        assert addr_info.era == "shelley"
        assert addr_info.encoding == "bech32"
        assert addr_info.type == "stake"
        if addr_gen == "static":
            assert addr_info.base16 == "e0a9b82c30b12e1c33ae5667f4d8e9ed2d18d3016bfe916b176d30a307"

    @allure.link(helpers.get_vcs_link())
    def test_address_info_script(self, cluster: clusterlib.ClusterLib):
        """Check script address info."""
        temp_template = common.get_test_id(cluster)

        # create payment address
        payment_rec = cluster.gen_payment_addr_and_keys(
            name=temp_template,
        )

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=[payment_rec.vkey_file],
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        address = cluster.gen_script_addr(addr_name=temp_template, script_file=multisig_script)

        addr_info = cluster.address_info(address=address)

        assert addr_info.address == address
        assert addr_info.era == "shelley"
        assert addr_info.encoding == "bech32"
        assert addr_info.type == "payment"
