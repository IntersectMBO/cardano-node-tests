"""Tests for cardano-cli that doesn't fit into any other test file."""
import json
import logging
from pathlib import Path

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"


@pytest.mark.smoke
class TestCLI:
    """Tests for cardano-cli."""

    TX_BODY_FILE = DATA_DIR / "test_tx_metadata_both_tx.body"
    TX_FILE = DATA_DIR / "test_tx_metadata_both_tx.signed"
    TX_BODY_OUT = DATA_DIR / "test_tx_metadata_both_tx_body.out"
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
    @pytest.mark.testnets
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.LAST_KNOWN_ERA,
        reason="works only with the latest TX era",
    )
    def test_pretty_utxo(
        self, cluster_manager: cluster_management.ClusterManager, cluster: clusterlib.ClusterLib
    ):
        """Check that pretty printed `query utxo` output looks as expected."""
        temp_template = common.get_test_id(cluster)
        amount1 = 2_000_000
        amount2 = 2_500_000

        # create source and destination payment addresses
        payment_addrs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_src",
            f"{temp_template}_dst",
            cluster_obj=cluster,
        )

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            payment_addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=amount1 + amount2 + 10_000_000,
        )

        src_address = payment_addrs[0].address
        dst_address = payment_addrs[1].address

        txouts = [
            clusterlib.TxOut(address=dst_address, amount=amount1),
            clusterlib.TxOut(address=dst_address, amount=amount2),
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])
        tx_raw_output = cluster.send_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )

        utxo_out = (
            cluster.cli(
                [
                    "query",
                    "utxo",
                    "--address",
                    dst_address,
                    *cluster.magic_args,
                ]
            )
            .stdout.decode("utf-8")
            .split()
        )

        txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)
        expected_out = [
            "TxHash",
            "TxIx",
            "Amount",
            "--------------------------------------------------------------------------------"
            "------",
            txid,
            "0",
            str(amount1),
            "lovelace",
            "+",
            "TxOutDatumNone",
            txid,
            "1",
            str(amount2),
            "lovelace",
            "+",
            "TxOutDatumNone",
        ]

        assert utxo_out == expected_out

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

        if "reference inputs:" in tx_body:
            with open(self.TX_BODY_OUT, encoding="utf-8") as infile:
                tx_body_view_out = infile.read()
            assert tx_body == tx_body_view_out.strip()

        if "reference inputs:" in tx:
            with open(self.TX_OUT, encoding="utf-8") as infile:
                tx_view_out = infile.read()
            assert tx == tx_view_out.strip()
        elif "witnesses:" not in tx:
            assert tx == tx_body


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

        addr_info = cluster.get_address_info(address=address)

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

        addr_info = cluster.get_address_info(address=address)

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
        address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        addr_info = cluster.get_address_info(address=address)

        assert addr_info.address == address
        assert addr_info.era == "shelley"
        assert addr_info.encoding == "bech32"
        assert addr_info.type == "payment"

    @allure.link(helpers.get_vcs_link())
    def test_address_info_payment_with_outfile(self, cluster: clusterlib.ClusterLib):
        """Compare payment address info with and without outfile provided."""
        # just a static address to preform the test
        address = "addr_test1vzp4kj0rmnl5q5046e2yy697fndej56tm35jekemj6ew2gczp74wk"

        # get address information
        cli_out = cluster.cli(["address", "info", "--address", str(address)])
        address_info_no_outfile = json.loads(cli_out.stdout.rstrip().decode("utf-8"))

        # get address information using an output file
        out_file = "/dev/stdout"
        cli_out = cluster.cli(
            ["address", "info", "--address", str(address), "--out-file", out_file]
        )
        address_info_with_outfile = json.loads(cli_out.stdout.rstrip().decode("utf-8"))

        # check if the information obtained by the two methods is the same
        assert (
            address_info_no_outfile == address_info_with_outfile
        ), "Address information doesn't match"


@pytest.mark.testnets
class TestKey:
    """Tests for cardano-cli key."""

    @allure.link(helpers.get_vcs_link())
    def test_non_extended_key_valid(self, cluster: clusterlib.ClusterLib):
        """Check that the non-extended verification key is according the verification key."""
        temp_template = common.get_test_id(cluster)

        # get an extended verification key
        payment_keys = cluster.gen_payment_key_pair(
            key_name=f"{temp_template}_extended", extended=True
        )

        with open(payment_keys.vkey_file, encoding="utf-8") as in_file:
            # ignore the first 4 chars, just an informative keyword
            extended_vkey = json.loads(in_file.read().strip()).get("cborHex", "")[4:]

        # get a non-extended verification key using the extended key
        non_extended_key_file = cluster.gen_non_extended_verification_key(
            key_name=temp_template, extended_verification_key_file=payment_keys.vkey_file
        )

        with open(non_extended_key_file, encoding="utf-8") as in_file:
            # ignore the first 4 chars, just an informative keyword
            non_extended_vkey = json.loads(in_file.read().strip()).get("cborHex", "")[4:]

        assert extended_vkey.startswith(non_extended_vkey)

    @allure.link(helpers.get_vcs_link())
    def test_non_extended_key_error(self, cluster: clusterlib.ClusterLib):
        """Try to get a non-extended verification key with a signing key file.

        Expect failure. Should only allow extended verification key files.
        """
        temp_template = common.get_test_id(cluster)

        # get an extended key
        payment_keys = cluster.gen_payment_key_pair(
            key_name=f"{temp_template}_extended", extended=True
        )

        # try to get a non-extended verification key using the extended signing key
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.gen_non_extended_verification_key(
                key_name=temp_template, extended_verification_key_file=payment_keys.skey_file
            )

        assert "TextEnvelope type error:  Expected one of:" in str(excinfo.value)
