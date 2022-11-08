"""Tests for cardano-cli that doesn't fit into any other test file."""
import json
import logging
from pathlib import Path
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"

pytestmark = pytest.mark.smoke


class TestCLI:
    """Tests for cardano-cli."""

    TX_BODY_FILE = DATA_DIR / "test_tx_metadata_both_tx.body"
    TX_FILE = DATA_DIR / "test_tx_metadata_both_tx.signed"
    TX_BODY_OUT = DATA_DIR / "test_tx_metadata_both_tx_body.out"
    TX_OUT = DATA_DIR / "test_tx_metadata_both_tx.out"

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_WRONG_ERA
    @pytest.mark.testnets
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
    @common.SKIPIF_WRONG_ERA
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
    @common.SKIPIF_WRONG_ERA
    @pytest.mark.testnets
    def test_testnet_whole_utxo(self, cluster: clusterlib.ClusterLib):
        """Check that it is possible to return the whole UTxO on testnets."""
        cluster_type = cluster_nodes.get_cluster_type()
        if cluster_type.type == cluster_nodes.ClusterType.LOCAL:
            pytest.skip("supposed to run on testnet")
        if cluster_type.testnet_type == cluster_nodes.Testnets.testnet:  # type: ignore
            pytest.skip("too expensive to run on the official Testnet")

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
        tx_raw_output = cluster.g_transaction.send_tx(
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

        txid = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
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
    @pytest.mark.testnets
    def test_txid_with_process_substitution(self):
        """Check that it is possible to use 'transaction txid' using process substitution."""
        cmd = (
            f"txFileJSON=$(cat {DATA_DIR / 'unwitnessed.tx'});"
            'cardano-cli transaction txid --tx-file <(echo "${txFileJSON}")'
        )

        try:
            helpers.run_in_bash(command=cmd)
        except AssertionError as err:
            if "cardano-cli: TODO" in str(err) or "Could not JSON decode TextEnvelopeCddl" in str(
                err
            ):
                pytest.xfail("Not possible to use process substitution - see node issue #4235")
            raise

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_sign_tx_with_process_substitution(self, cluster: clusterlib.ClusterLib):
        """Check that it is possible to use 'transaction sign' using process substitution."""
        temp_template = common.get_test_id(cluster)

        cmd = (
            f"tmpKey=$(cat {plutus_common.SIGNING_KEY_GOLDEN});"
            f'cardano-cli transaction sign --tx-file {DATA_DIR / "unwitnessed.tx"}'
            ' --signing-key-file <(echo "${tmpKey}")'
            f" --out-file {temp_template}.signed"
        )

        helpers.run_in_bash(command=cmd)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_WRONG_ERA
    @pytest.mark.testnets
    def test_tx_view(self, cluster: clusterlib.ClusterLib):
        """Check that the output of `transaction view` is as expected."""
        common.get_test_id(cluster)

        tx_body = cluster.g_transaction.view_tx(tx_body_file=self.TX_BODY_FILE)
        tx = cluster.g_transaction.view_tx(tx_file=self.TX_FILE)

        if "return collateral:" in tx_body:
            with open(self.TX_BODY_OUT, encoding="utf-8") as infile:
                tx_body_view_out = infile.read()
            assert tx_body == tx_body_view_out.strip()

        if "return collateral:" in tx:
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
            payment_rec = cluster.g_address.gen_payment_addr_and_keys(
                name=temp_template,
            )
            address = payment_rec.address

        addr_info = cluster.g_address.get_address_info(address=address)

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
            stake_rec = cluster.g_stake_address.gen_stake_addr_and_keys(
                name=temp_template,
            )
            address = stake_rec.address

        addr_info = cluster.g_address.get_address_info(address=address)

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
        payment_rec = cluster.g_address.gen_payment_addr_and_keys(
            name=temp_template,
        )

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=[payment_rec.vkey_file],
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        addr_info = cluster.g_address.get_address_info(address=address)

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
        payment_keys = cluster.g_address.gen_payment_key_pair(
            key_name=f"{temp_template}_extended", extended=True
        )

        with open(payment_keys.vkey_file, encoding="utf-8") as in_file:
            # ignore the first 4 chars, just an informative keyword
            extended_vkey = json.loads(in_file.read().strip()).get("cborHex", "")[4:]

        # get a non-extended verification key using the extended key
        non_extended_key_file = cluster.g_key.gen_non_extended_verification_key(
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
        payment_keys = cluster.g_address.gen_payment_key_pair(
            key_name=f"{temp_template}_extended", extended=True
        )

        # try to get a non-extended verification key using the extended signing key
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_key.gen_non_extended_verification_key(
                key_name=temp_template, extended_verification_key_file=payment_keys.skey_file
            )

        assert "TextEnvelope type error:  Expected one of:" in str(excinfo.value)


@common.SKIPIF_WRONG_ERA
class TestAdvancedQueries:
    """Basic sanity tests for advanced cardano-cli query commands.

    The `query leadership-schedule` is handled by more complex tests `TestLeadershipSchedule`
    as it requires complex setup.
    For `query protocol-state` see `test_protocol_state_keys` smoke test.
    """

    @pytest.fixture
    def pool_ids(self, cluster: clusterlib.ClusterLib) -> List[str]:
        stake_pool_ids = cluster.g_query.get_stake_pools()
        if not stake_pool_ids:
            pytest.skip("No stake pools are available.")
        return stake_pool_ids

    @allure.link(helpers.get_vcs_link())
    def test_ledger_state(self, cluster: clusterlib.ClusterLib):
        """Test `query stake-snapshot`."""
        try:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        except AssertionError as err:
            if "Invalid numeric literal at line" in str(err):
                pytest.xfail(f"expected JSON, got CBOR - see node issue #3859: {err}")
            raise

        assert "lastEpoch" in ledger_state

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_stake_snapshot(self, cluster: clusterlib.ClusterLib, pool_ids: List[str]):
        """Test `query stake-snapshot`."""
        try:
            stake_snapshot = cluster.g_query.get_stake_snapshot(stake_pool_id=pool_ids[0])
        except json.decoder.JSONDecodeError as err:
            pytest.xfail(f"expected JSON, got CBOR - see node issue #3859: {err}")

        assert {
            "activeStakeGo",
            "activeStakeMark",
            "activeStakeSet",
            "poolStakeGo",
            "poolStakeMark",
            "poolStakeSet",
        }.issubset(stake_snapshot)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_pool_params(self, cluster: clusterlib.ClusterLib, pool_ids: List[str]):
        """Test `query pool-params`."""
        try:
            pool_params = cluster.g_query.get_pool_params(stake_pool_id=pool_ids[0])
        except json.decoder.JSONDecodeError as err:
            pytest.xfail(f"expected JSON, got CBOR - see node issue #3859: {err}")

        assert hasattr(pool_params, "retiring")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_tx_mempool_info(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test 'query tx-mempool info'.

        * check that the expected fields are returned
        * check that the slot number returned is the last applied on the ledger plus one
        """
        if not clusterlib_utils.cli_has("query tx-mempool"):
            pytest.skip("CLI command `query tx-mempool` is not available")

        for __ in range(5):
            tx_mempool = cluster.g_query.get_mempool_info()
            last_ledger_slot = cluster.g_query.get_slot_no()

            if last_ledger_slot + 1 == tx_mempool["slot"]:
                break
        else:
            raise AssertionError(
                f"Expected slot number '{last_ledger_slot + 1}', got '{tx_mempool['slot']}'"
            )

        out_file = "/dev/stdout"
        cli_out = cluster.cli(
            ["query", "tx-mempool", "info", "--out-file", out_file, *cluster.magic_args]
        )
        tx_mempool_file = json.loads(cli_out.stdout.rstrip().decode("utf-8"))

        assert {"capacityInBytes", "numberOfTxs", "sizeInBytes", "slot"}.issubset(
            tx_mempool
        ) and tx_mempool == tx_mempool_file, (
            "The output to file doesn't match the expected output:\n"
            f"{tx_mempool_file}\nvs\n{tx_mempool}"
        )
