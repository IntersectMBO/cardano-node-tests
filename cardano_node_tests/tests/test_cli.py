"""Tests for cardano-cli that doesn't fit into any other test file."""

import datetime
import json
import logging
import os
import pathlib as pl
import string
import time

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent / "data"

pytestmark = common.SKIPIF_WRONG_ERA


class TestCLI:
    """Tests for cardano-cli."""

    TX_BODY_FILE = DATA_DIR / "test_tx_metadata_both_tx.body"
    TX_BODY_FILE_CONWAY = DATA_DIR / "test_tx_metadata_both_tx_conway.body"
    TX_FILE = DATA_DIR / "test_tx_metadata_both_tx.signed"
    TX_BODY_OUT_JSON = DATA_DIR / "test_tx_metadata_both_tx_body_json.out"
    TX_OUT_JSON = DATA_DIR / "test_tx_metadata_both_tx_json.out"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_protocol_mode(self, cluster: clusterlib.ClusterLib):
        """Check the default protocol mode - command works even without specifying protocol mode."""
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
    @pytest.mark.parametrize(
        "command",
        (
            "protocol-parameters",
            "tip",
            "stake-pools",
            "stake-distribution",
            "stake-address-info",
            "utxo",
            "ledger-state",
            "protocol-state",
            "stake-snapshot",
            "leadership-schedule",
            "kes-period-info",
            "pool-state",
            "tx-mempool",
            "slot-number",
        ),
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_toplevel_queries(self, cluster: clusterlib.ClusterLib, command: str):
        """Check that various queries are available in top level."""
        common.get_test_id(cluster)

        try:
            cluster.cli(
                ["cardano-cli", "query", command],
                add_default_args=False,
            )
        except clusterlib.CLIError as exc:
            str_exc = str(exc)
            if "Invalid argument `query'" in str_exc:
                issues.cli_953.finish_test()
            elif f"Usage: cardano-cli query {command}" not in str_exc:
                raise

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_calculate_min_fee(self, cluster: clusterlib.ClusterLib):
        """Check the `calculate-min-fee` command."""
        common.get_test_id(cluster)
        max_fee = 172_000
        cluster.create_pparams_file()

        try:
            out = (
                cluster.cli(
                    [
                        "transaction",
                        "calculate-min-fee",
                        "--output-text",
                        "--protocol-params-file",
                        str(cluster.pparams_file),
                        "--tx-body-file",
                        str(self.TX_BODY_FILE_CONWAY),
                        "--witness-count",
                        "1",
                        "--tx-in-count",
                        "1",
                        "--tx-out-count",
                        "1",
                        *cluster.magic_args,
                    ]
                )
                .stdout.decode("utf-8")
                .strip()
            )
        except clusterlib.CLIError as err:
            if "Missing: --reference-script-size" in str(err):
                issues.cli_715.finish_test()
            raise

        fee, coin = out.split()
        assert int(fee) <= max_fee, f"Unexpected fee: {fee} > {max_fee}"
        assert coin == "Lovelace", f"Unexpected coin: {coin} != Lovelace"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_txid_with_process_substitution(self, cluster: clusterlib.ClusterLib):
        """Check that it is possible to pass Tx file using process substitution."""
        common.get_test_id(cluster)

        cmd = (
            f"txFileJSON=$(cat {DATA_DIR / 'unwitnessed.tx'});"
            'cardano-cli latest transaction txid --tx-file <(echo "${txFileJSON}")'
        )

        try:
            helpers.run_in_bash(command=cmd)
        except RuntimeError as err:
            if "cardano-cli: TODO" in str(err) or "Could not JSON decode TextEnvelopeCddl" in str(
                err
            ):
                issues.node_4235.finish_test()
            raise

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_sign_tx_with_process_substitution(self, cluster: clusterlib.ClusterLib):
        """Check that it is possible to pass skey file using process substitution."""
        temp_template = common.get_test_id(cluster)

        cmd = (
            f"tmpKey=$(cat {plutus_common.SIGNING_KEY_GOLDEN});"
            f"cardano-cli latest transaction sign --tx-file {DATA_DIR / 'unwitnessed.tx'}"
            ' --signing-key-file <(echo "${tmpKey}")'
            f" --out-file {temp_template}.signed"
        )

        helpers.run_in_bash(command=cmd)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_tx_view(self, cluster: clusterlib.ClusterLib):
        """Check that the output of `transaction view` is as expected."""
        common.get_test_id(cluster)

        tx_body = cluster.g_transaction.view_tx(tx_body_file=self.TX_BODY_FILE)

        if '"redeemers":' not in tx_body:
            pytest.skip("unsupported old output format")
        if '"datums":' not in tx_body:
            pytest.skip("unsupported old output format")

        with open(self.TX_BODY_OUT_JSON, encoding="utf-8") as infile:
            tx_body_golden = infile.read()
        assert tx_body == tx_body_golden.strip()

        tx = cluster.g_transaction.view_tx(tx_file=self.TX_FILE)

        if '"governance actions":' in tx:
            issues.cli_799.finish_test()

        with open(self.TX_OUT_JSON, encoding="utf-8") as infile:
            tx_golden = infile.read()
        assert tx == tx_golden.strip()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_query_tip(self, cluster: clusterlib.ClusterLib):
        """Test `query tip`."""
        common.get_test_id(cluster)

        tip_out = cluster.g_query.get_tip()

        errors = []

        # Prior to node 1.36.0 the fields 'slotInEpoch' and 'slotsToEpochEnd' did not exist
        if "slotInEpoch" not in tip_out:
            expected_out = {
                "block",
                "epoch",
                "era",
                "hash",
                "slot",
                "syncProgress",
            }
        else:
            expected_out = {
                "block",
                "epoch",
                "era",
                "hash",
                "slot",
                "slotInEpoch",
                "slotsToEpochEnd",
                "syncProgress",
            }

            # Check that 'slotInEpoch' is never greater than epoch length
            if tip_out["slotInEpoch"] > cluster.epoch_length:
                errors.append("'slotInEpoch' is greater than epoch length")

            # Check that 'slotsToEpochEnd' is the difference between epoch length and 'slotInEpoch'
            if tip_out["slotsToEpochEnd"] > cluster.epoch_length - tip_out["slotInEpoch"]:
                errors.append("'slotsToEpochEnd' doesn't have the expected value")

        # Check that 'query tip' is returning the expected fields
        if set(tip_out.keys()) != expected_out:
            errors.append(
                f"Unexpected fields in 'query tip' output: {set(tip_out.keys())} != {expected_out}"
            )

        # Check that 'slot' is never greater than the total number of slots
        if tip_out["slot"] > (tip_out["epoch"] + 1) * cluster.epoch_length - cluster.slots_offset:
            errors.append("'slot' is greater than total number of slots")

        # Check that 'era' is the expected
        expected_era = VERSIONS.cluster_era_name.title()
        if tip_out["era"] != expected_era:
            errors.append(
                f"'era' doesn't have the expected value: {tip_out['era']} != {expected_era}"
            )

        if errors:
            errors_str = "\n".join(errors)
            raise AssertionError(errors_str)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_ledger_peer_snapshot(self, cluster: clusterlib.ClusterLib):
        """Test `cardano-cli query ledger-peer-snapshot`.

        * ensure the command runs successfully
        * ensure minimal expected keys exist
        """
        common.get_test_id(cluster)

        peer_snapshot = cluster.g_query.get_ledger_peer_snapshot()

        expected_keys = {"bigLedgerPools", "slotNo", "version"}
        missing_keys = expected_keys - set(peer_snapshot)
        assert not missing_keys

        pools = peer_snapshot.get("bigLedgerPools", [])
        assert isinstance(pools, list)
        if pools:
            assert all("relativeStake" in p for p in pools)


class TestAddressInfo:
    """Tests for cardano-cli address info."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("addr_gen", ("static", "dynamic"))
    @pytest.mark.smoke
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
    @pytest.mark.smoke
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
    @pytest.mark.smoke
    def test_address_info_script(self, cluster: clusterlib.ClusterLib):
        """Check script address info."""
        temp_template = common.get_test_id(cluster)

        # Create payment address
        payment_rec = cluster.g_address.gen_payment_addr_and_keys(
            name=temp_template,
        )

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=[payment_rec.vkey_file],
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # Create script address
        address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        addr_info = cluster.g_address.get_address_info(address=address)

        assert addr_info.address == address
        assert addr_info.era == "shelley"
        assert addr_info.encoding == "bech32"
        assert addr_info.type == "payment"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_address_info_payment_with_outfile(self, cluster: clusterlib.ClusterLib):
        """Compare payment address info with and without outfile provided."""
        common.get_test_id(cluster)

        # Just a static address to preform the test
        address = "addr_test1vzp4kj0rmnl5q5046e2yy697fndej56tm35jekemj6ew2gczp74wk"

        # Get address information
        cli_out = cluster.cli(["address", "info", "--address", str(address)])
        address_info_no_outfile = json.loads(cli_out.stdout.rstrip().decode("utf-8"))

        # Get address information using an output file
        out_file = "/dev/stdout"
        cli_out = cluster.cli(
            ["address", "info", "--address", str(address), "--out-file", out_file]
        )
        address_info_with_outfile = json.loads(cli_out.stdout.rstrip().decode("utf-8"))

        # Check if the information obtained by the two methods is the same
        assert address_info_no_outfile == address_info_with_outfile, (
            "Address information doesn't match"
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(address=st.text(alphabet=common.ADDR_ALPHABET, min_size=1))
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_address_info_with_invalid_address(self, cluster: clusterlib.ClusterLib, address: str):
        """Try to use 'address info' with invalid address (property-based test).

        Expect failure.
        """
        common.get_test_id(cluster)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_address.get_address_info(address=address)
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "Invalid address" in exc_value, exc_value


class TestAddressBuild:
    """Tests for cardano-cli address build."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("payment", ("vkey", "vkey_file", "script_file"))
    @pytest.mark.parametrize(
        "stake",
        (
            None,
            "vkey",
            "vkey_file",
            "script_file",
            "address",
        ),
    )
    @pytest.mark.smoke
    def test_address_build(
        self,
        cluster: clusterlib.ClusterLib,
        payment: str,
        stake: str,
    ):
        """Check `address build` with all valid input options."""
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        payment_vkey_file = DATA_DIR / "golden_payment.vkey"

        stake_vkey_file = DATA_DIR / "golden_stake.vkey"
        stake_address = "stake_test1uz5mstpskyhpcvaw2enlfk8fa5k335cpd0lfz6chd5c2xpck3nld4"

        script_file = DATA_DIR / "golden_sig.script"

        payment_vkey = None
        if payment == "vkey":
            with open(payment_vkey_file, encoding="utf-8") as infile:
                # Ignore the first 4 chars, just an informative keyword
                payment_vkey = helpers.encode_bech32(
                    prefix="addr_vk", data=json.loads(infile.read().strip()).get("cborHex", "")[4:]
                )

        stake_vkey = None
        if stake == "vkey":
            with open(stake_vkey_file, encoding="utf-8") as infile:
                # Ignore the first 4 chars, just an informative keyword
                stake_vkey = helpers.encode_bech32(
                    prefix="stake_vk", data=json.loads(infile.read().strip()).get("cborHex", "")[4:]
                )

        address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template,
            payment_vkey=payment_vkey,
            payment_vkey_file=payment_vkey_file if payment == "vkey_file" else None,
            payment_script_file=script_file if payment == "script_file" else None,
            stake_vkey=stake_vkey if stake == "vkey" else None,
            stake_vkey_file=stake_vkey_file,
            stake_script_file=script_file if stake == "script_file" else None,
            stake_address=stake_address if stake == "address" else None,
        )

        expected_address = {
            "addr_test1vqxu3ct3ykqk2ycag4z3h70z5xlyf2tadpxw9am4kae5ycc95yzhp",
            "addr_test1wzya6tknq2m908c5q68a0fxd6eg0q0qc0yzg8cx0lza2p2ggggmzy",
            "addr_test1qqxu3ct3ykqk2ycag4z3h70z5xlyf2tadpxw9am4kae5ycaghrqnes"
            "9cvw7qwlstcm40m3hn9ap6g5fmwqmckvxwk7usca596d",
            "addr_test1zzya6tknq2m908c5q68a0fxd6eg0q0qc0yzg8cx0lza2p2dghrqnes"
            "9cvw7qwlstcm40m3hn9ap6g5fmwqmckvxwk7ustneaan",
            "addr_test1yqxu3ct3ykqk2ycag4z3h70z5xlyf2tadpxw9am4kae5ycufm5hdxq"
            "4k2703gp5067jvm4js7q7ps7gys0svl7965z5sdkur7k",
            "addr_test1xzya6tknq2m908c5q68a0fxd6eg0q0qc0yzg8cx0lza2p2vfm5hdxq"
            "4k2703gp5067jvm4js7q7ps7gys0svl7965z5s7c3meg",
            "addr_test1qqxu3ct3ykqk2ycag4z3h70z5xlyf2tadpxw9am4kae5ycafhqkrpv"
            "fwrse6u4n87nvwnmfdrrfsz6l7j943wmfs5vrsr9mps4",
            "addr_test1zzya6tknq2m908c5q68a0fxd6eg0q0qc0yzg8cx0lza2p2dfhqkrpv"
            "fwrse6u4n87nvwnmfdrrfsz6l7j943wmfs5vrsstkeht",
        }

        assert address in expected_address, "The generated address doesn't have the expected value"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("option", ("vkey", "vkey_file", "script_file"))
    @hypothesis.given(key=st.text(alphabet=common.ADDR_ALPHABET, min_size=1))
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_invalid_payment_info(
        self,
        cluster: clusterlib.ClusterLib,
        option: str,
        key: str,
    ):
        """Try to use 'address build' with invalid payment information (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        vkey_file = None
        if option == "vkey_file":
            vkey_file = f"{temp_template}.vkey"
            vkey_file_content = {
                "type": "PaymentVerificationKeyShelley_ed25519",
                "description": "Payment Verification Key",
                "cborHex": key,
            }

            with open(vkey_file, "w", encoding="utf-8") as outfile:
                json.dump(vkey_file_content, outfile)

        script_file = None
        if option == "script_file":
            script_file = f"{temp_template}.script"
            script_file_content = {
                "type": "sig",
                "keyHash": key,
            }

            with open(script_file, "w", encoding="utf-8") as outfile:
                json.dump(script_file_content, outfile)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_address.gen_payment_addr(
                addr_name=temp_template,
                payment_vkey=key if option == "vkey" else None,
                payment_vkey_file=vkey_file,
                payment_script_file=script_file,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "Invalid key" in exc_value or "Syntax error in script" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "option",
        (
            "vkey",
            "vkey_file",
            "script_file",
            "address",
        ),
    )
    @hypothesis.given(key=st.text(alphabet=common.ADDR_ALPHABET, min_size=1))
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_invalid_stake_info(
        self,
        cluster: clusterlib.ClusterLib,
        option: str,
        key: str,
    ):
        """Try to use 'address build' with invalid stake address information (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        vkey_file = None
        if option == "vkey_file":
            vkey_file = f"{temp_template}.vkey"
            vkey_file_content = {
                "type": "StakeVerificationKeyShelley_ed25519",
                "description": "Stake Verification Key",
                "cborHex": key,
            }

            with open(vkey_file, "w", encoding="utf-8") as outfile:
                json.dump(vkey_file_content, outfile)

        script_file = None
        if option == "script_file":
            script_file = f"{temp_template}.script"
            script_file_content = {
                "type": "sig",
                "keyHash": key,
            }

            with open(script_file, "w", encoding="utf-8") as outfile:
                json.dump(script_file_content, outfile)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_address.gen_payment_addr(
                addr_name=temp_template,
                payment_vkey="addr_vk1rauy20dp8fu7zgnw9asmehg55n38l9n4pj7xv9clx8vduyylwgtsyl0n9m",
                stake_vkey=key if option == "vkey" else None,
                stake_vkey_file=vkey_file,
                stake_script_file=script_file,
                stake_address=key if option == "address" else None,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "Invalid key" in exc_value
                or "Syntax error in script" in exc_value
                or "invalid address" in exc_value
            ), exc_value


class TestAddressKeyHash:
    """Tests for cardano-cli address key-hash."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("option", ("vkey", "vkey_file"))
    @pytest.mark.smoke
    def test_valid_verification_key(self, cluster: clusterlib.ClusterLib, option: str):
        """Check `address key-hash` with valid verification key."""
        common.get_test_id(cluster)

        vkey_file = DATA_DIR / "golden_payment.vkey"

        expected_hash = "0dc8e171258165131d45451bf9e2a1be44a97d684ce2f775b7734263"

        vkey = None
        if option == "vkey":
            with open(vkey_file, encoding="utf-8") as infile:
                # Ignore the first 4 chars, just an informative keyword
                vkey = helpers.encode_bech32(
                    prefix="addr_vk", data=json.loads(infile.read().strip()).get("cborHex", "")[4:]
                )

        vkey_hash = cluster.g_address.get_payment_vkey_hash(
            payment_vkey=vkey,
            payment_vkey_file=vkey_file if option == "vkey_file" else None,
        )

        assert vkey_hash == expected_hash, f"Unexpected vkey hash: {vkey_hash} != {expected_hash}"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("option", ("vkey", "vkey_file"))
    @hypothesis.given(vkey=st.text(alphabet=common.ADDR_ALPHABET, min_size=1))
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_invalid_verification_key(self, cluster: clusterlib.ClusterLib, option: str, vkey: str):
        """Try to use `address key-hash` with invalid verification key (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        vkey_file = None
        if option == "vkey_file":
            vkey_file = f"{temp_template}.redeemer"
            vkey_file_content = {
                "type": "PaymentVerificationKeyShelley_ed25519",
                "description": "Payment Verification Key",
                "cborHex": vkey,
            }

            with open(vkey_file, "w", encoding="utf-8") as outfile:
                json.dump(vkey_file_content, outfile)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_address.get_payment_vkey_hash(
                payment_vkey=vkey if option == "vkey" else None,
                payment_vkey_file=vkey_file,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "Invalid key" in exc_value, exc_value


class TestKey:
    """Tests for cardano-cli key."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_non_extended_key_valid(self, cluster: clusterlib.ClusterLib):
        """Check that the non-extended verification key is according the verification key."""
        temp_template = common.get_test_id(cluster)

        # Get an extended verification key
        payment_keys = cluster.g_address.gen_payment_key_pair(
            key_name=f"{temp_template}_extended", extended=True
        )

        with open(payment_keys.vkey_file, encoding="utf-8") as in_file:
            # Ignore the first 4 chars, just an informative keyword
            extended_vkey = json.loads(in_file.read().strip()).get("cborHex", "")[4:]

        # Get a non-extended verification key using the extended key
        non_extended_key_file = cluster.g_key.gen_non_extended_verification_key(
            key_name=temp_template, extended_verification_key_file=payment_keys.vkey_file
        )

        with open(non_extended_key_file, encoding="utf-8") as in_file:
            # Ignore the first 4 chars, just an informative keyword
            non_extended_vkey = json.loads(in_file.read().strip()).get("cborHex", "")[4:]

        assert extended_vkey.startswith(non_extended_vkey)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_stake_non_extended_key(self, cluster: clusterlib.ClusterLib):
        """Get a stake non-extended-key from a stake extended key."""
        temp_template = common.get_test_id(cluster)

        stake_extended_key_file = DATA_DIR / "stake.evkey"

        # Get a stake non-extended-key from a stake extended key
        try:
            cluster.g_key.gen_non_extended_verification_key(
                key_name=temp_template, extended_verification_key_file=stake_extended_key_file
            )
        except clusterlib.CLIError as err:
            if "key non-extended-key  Error: Invalid key." in str(err):
                issues.node_4914.finish_test()
            raise

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_non_extended_key_error(self, cluster: clusterlib.ClusterLib):
        """Try to get a non-extended verification key with a signing key file.

        Expect failure. Should only allow extended verification key files.
        """
        temp_template = common.get_test_id(cluster)

        # Get an extended key
        payment_keys = cluster.g_address.gen_payment_key_pair(
            key_name=f"{temp_template}_extended", extended=True
        )

        # Try to get a non-extended verification key using the extended signing key
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_key.gen_non_extended_verification_key(
                key_name=temp_template, extended_verification_key_file=payment_keys.skey_file
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "Error: Invalid key." in exc_value
                or "TextEnvelope type error:  Expected one of:" in exc_value
            ), exc_value


class TestQueryUTxO:
    """Tests for cardano-cli query utxo."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_whole_utxo(self, cluster: clusterlib.ClusterLib):
        """Check that it is possible to return the whole UTxO on local cluster."""
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
        VERSIONS.transaction_era != VERSIONS.DEFAULT_CLUSTER_ERA,
        reason="works only with the latest TX era",
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_pretty_utxo(
        self, cluster_manager: cluster_management.ClusterManager, cluster: clusterlib.ClusterLib
    ):
        """Check that pretty printed `query utxo` output looks as expected."""
        temp_template = common.get_test_id(cluster)
        amount1 = 2_000_000
        amount2 = 2_500_000

        # Create source and destination payment addresses
        payment_addrs = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_src",
            f"{temp_template}_dst",
            cluster_obj=cluster,
        )

        # Fund source addresses
        clusterlib_utils.fund_from_faucet(
            payment_addrs[0],
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
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
                    "--output-text",
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
    @pytest.mark.parametrize("invalid_param", ("tx_hash", "tx_ix"))
    @hypothesis.given(filter_str=st.text(alphabet=string.ascii_letters, min_size=1))
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_tx_in_invalid_data(
        self, cluster: clusterlib.ClusterLib, filter_str: str, invalid_param: str
    ):
        """Try to use 'query utxo' with invalid 'tx-in' (property-based test).

        Expect failure.
        """
        common.get_test_id(cluster)

        tx_hash = "a4c141cfae907aa1c4b418f65f384a6d860d52786b412481bc63733acfab1541"

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "query",
                    "utxo",
                    "--tx-in",
                    f"{filter_str}#0" if invalid_param == "tx_hash" else f"{tx_hash}#{filter_str}",
                    *cluster.magic_args,
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            if invalid_param == "tx_hash":
                assert (
                    "expecting hexadecimal digit" in exc_value
                    or "expecting transaction id (hexadecimal)" in exc_value
                ), exc_value
            else:
                assert "expecting digit" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(filter_str=st.text(alphabet=common.ADDR_ALPHABET, min_size=1))
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_address_invalid_data(self, cluster: clusterlib.ClusterLib, filter_str: str):
        """Try to use 'query utxo' with invalid 'address' (property-based test).

        Expect failure.
        """
        common.get_test_id(cluster)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "query",
                    "utxo",
                    "--address",
                    filter_str,
                    *cluster.magic_args,
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "invalid address" in exc_value, exc_value


class TestStakeAddressKeyHash:
    """Tests for cardano-cli stake-address key-hash."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("option", ("vkey", "vkey_file"))
    @pytest.mark.smoke
    def test_valid_verification_key(self, cluster: clusterlib.ClusterLib, option: str):
        """Check `stake-address key-hash` with valid verification key."""
        common.get_test_id(cluster)

        vkey_file = DATA_DIR / "golden_stake.vkey"

        expected_hash = "a8b8c13cc0b863bc077e0bc6eafdc6f32f43a4513b70378b30ceb7b9"

        vkey = None
        if option == "vkey":
            with open(vkey_file, encoding="utf-8") as infile:
                # Ignore the first 4 chars, just an informative keyword
                vkey = helpers.encode_bech32(
                    prefix="stake_vk", data=json.loads(infile.read().strip()).get("cborHex", "")[4:]
                )

        vkey_hash = cluster.g_stake_address.get_stake_vkey_hash(
            stake_vkey=vkey,
            stake_vkey_file=vkey_file if option == "vkey_file" else None,
        )

        assert vkey_hash == expected_hash, f"Unexpected vkey hash: {vkey_hash} != {expected_hash}"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("option", ("vkey", "vkey_file"))
    @hypothesis.given(vkey=st.text(alphabet=common.ADDR_ALPHABET, min_size=1))
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_invalid_verification_key(self, cluster: clusterlib.ClusterLib, option: str, vkey: str):
        """Try to use `stake-address key-hash` with invalid verification key (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        vkey_file = None
        if option == "vkey_file":
            vkey_file = f"{temp_template}.redeemer"
            vkey_file_content = {
                "type": "StakeVerificationKeyShelley_ed25519",
                "description": "Stake Verification Key",
                "cborHex": vkey,
            }

            with open(vkey_file, "w", encoding="utf-8") as outfile:
                json.dump(vkey_file_content, outfile)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_stake_address.get_stake_vkey_hash(
                stake_vkey=vkey if option == "vkey" else None,
                stake_vkey_file=vkey_file,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "Invalid key" in exc_value, exc_value


class TestAdvancedQueries:
    """Basic sanity tests for advanced cardano-cli query commands.

    The `query leadership-schedule` is handled by more complex tests `TestLeadershipSchedule`
    as it requires complex setup.
    For `query protocol-state` see `test_protocol_state_keys` smoke test.
    """

    def _check_stake_snapshot(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_obj: clusterlib.ClusterLib,
        option: str,
        temp_template: str,
    ):
        pool_ids = cluster_obj.g_query.get_stake_pools()
        if not pool_ids:
            pytest.skip("No stake pools are available.")

        expected_pool_ids = []
        stake_snapshot = {}
        try:
            if option == "single_pool":
                # Make sure the queries can be finished in single epoch
                clusterlib_utils.wait_for_epoch_interval(
                    cluster_obj=cluster_obj,
                    start=1,
                    stop=-3,
                )

                expected_pool_ids = [pool_ids[0]]
                stake_snapshot = cluster_obj.g_query.get_stake_snapshot(
                    stake_pool_ids=expected_pool_ids
                )
            elif option == "multiple_pools":
                expected_pool_ids = [pool_ids[0], pool_ids[1]]
                stake_snapshot = cluster_obj.g_query.get_stake_snapshot(
                    stake_pool_ids=expected_pool_ids
                )
            elif option == "all_pools":
                # Sleep till the end of epoch for stable stake distribution
                clusterlib_utils.wait_for_epoch_interval(
                    cluster_obj=cluster_obj,
                    start=common.EPOCH_START_SEC_LEDGER_STATE,
                    stop=common.EPOCH_STOP_SEC_LEDGER_STATE,
                )
                # Get up-to-date list of available pools
                expected_pool_ids = [
                    cluster_obj.g_stake_pool.get_stake_pool_id(
                        cluster_manager.cache.addrs_data[p]["cold_key_pair"].vkey_file
                    )
                    for p in cluster_management.Resources.ALL_POOLS
                ]
                stake_snapshot = cluster_obj.g_query.get_stake_snapshot(all_stake_pools=True)
            else:
                msg = f"Unknown option: {option}"
                raise ValueError(msg)
        except json.decoder.JSONDecodeError:
            issues.node_3859.finish_test()

        expected_pool_ids_mapping = {p: helpers.decode_bech32(bech32=p) for p in expected_pool_ids}

        def _dump_on_error():
            if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL:
                clusterlib_utils.save_ledger_state(
                    cluster_obj=cluster_obj, state_name=temp_template
                )

            with open(f"{temp_template}_stake_snapshot.json", "w", encoding="utf-8") as fp_out:
                json.dump(stake_snapshot, fp_out, indent=2)

        errors = []
        total_stake_errors = []
        if "pools" in stake_snapshot:
            if not {
                "stakeGo",
                "stakeMark",
                "stakeSet",
            }.issubset(stake_snapshot["total"]):
                errors.append(
                    f"Missing some expected keys in 'total' field: {stake_snapshot['total'].keys()}"
                )

            sum_mark = 0
            sum_set = 0
            sum_go = 0

            for pool_data in stake_snapshot["pools"].values():
                if not {
                    "stakeGo",
                    "stakeMark",
                    "stakeSet",
                }.issubset(pool_data):
                    errors.append(
                        f"Missing some expected keys in 'pools' field: {pool_data.keys()}"
                    )

                sum_mark += pool_data["stakeMark"]
                sum_set += pool_data["stakeSet"]
                sum_go += pool_data["stakeGo"]

            if option == "all_pools":
                expected_pool_ids_dec = set(expected_pool_ids_mapping.values())

                out_pool_ids_dec = set(stake_snapshot["pools"].keys())
                # Retired pools and newly created ones may not yet be on the snapshot
                if not expected_pool_ids_dec.issubset(out_pool_ids_dec):
                    errors.append(
                        f"Expected pools: {expected_pool_ids_dec}\nVS\n"
                        f"Reported pools: {out_pool_ids_dec}\n"
                        "Difference: "
                        f"{expected_pool_ids_dec.symmetric_difference(out_pool_ids_dec)}"
                    )
                # Active stake can be lower than sum of stakes, as some pools may not be running
                # and forging blocks
                if sum_mark < stake_snapshot["total"]["stakeMark"]:
                    total_stake_errors.append(
                        f"active_mark: {sum_mark} < {stake_snapshot['total']['stakeMark']}"
                    )
                if sum_set < stake_snapshot["total"]["stakeSet"]:
                    total_stake_errors.append(
                        f"active_set: {sum_set} < {stake_snapshot['total']['stakeSet']}"
                    )
                if sum_go < stake_snapshot["total"]["stakeGo"]:
                    total_stake_errors.append(
                        f"active_go: {sum_go} < {stake_snapshot['total']['stakeGo']}"
                    )
            # Check stake distribution on dbsync
            # The stake distribution is extracted from the "set" snapshot of the ledger
            elif option == "single_pool" and configuration.HAS_DBSYNC:
                current_epoch = cluster_obj.g_query.get_epoch()

                pool_stake_snapshots = next(iter(stake_snapshot["pools"].values()))

                # Check stake 'set' snapshot
                db_set_sum = sum(
                    r.amount
                    for r in dbsync_queries.query_epoch_stake(
                        pool_id_bech32=pool_ids[0], epoch_number=current_epoch
                    )
                )
                snapshot_set_sum = pool_stake_snapshots["stakeSet"] or 0

                if db_set_sum != snapshot_set_sum:
                    errors.append(
                        "The epoch stake distribution in dbsync doesn't match stake 'set' snapshot"
                    )

                # Check stake 'go' snapshot
                db_go_sum = sum(
                    r.amount
                    for r in dbsync_queries.query_epoch_stake(
                        pool_id_bech32=pool_ids[0], epoch_number=current_epoch - 1
                    )
                )
                snapshot_go_sum = pool_stake_snapshots["stakeGo"] or 0

                if db_go_sum != snapshot_go_sum:
                    errors.append(
                        "The epoch stake distribution in dbsync doesn't match stake 'go' snapshot"
                    )
        elif not {
            "activeStakeGo",
            "activeStakeMark",
            "activeStakeSet",
            "poolStakeGo",
            "poolStakeMark",
            "poolStakeSet",
        }.issubset(stake_snapshot):
            errors.append(f"Missing some expected keys: {stake_snapshot.keys()}")

        if errors:
            _dump_on_error()
            err_joined = "\n".join(errors)
            pytest.fail(f"Errors:\n{err_joined}")
        elif total_stake_errors:
            err_joined = "\n".join(total_stake_errors)
            node_4895 = issues.node_4895.copy()
            node_4895.message = f"Unexpected values for total stake:\n{err_joined}"
            node_4895.finish_test()

    @pytest.fixture
    def pool_ids(self, cluster: clusterlib.ClusterLib) -> list[str]:
        stake_pool_ids = cluster.g_query.get_stake_pools()
        if not stake_pool_ids:
            pytest.skip("No stake pools are available.")
        return stake_pool_ids

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_ledger_state(self, cluster: clusterlib.ClusterLib):
        """Test `query ledger-state`."""
        common.get_test_id(cluster)

        try:
            ledger_state = clusterlib_utils.get_ledger_state(cluster_obj=cluster)
        except RuntimeError as err:
            if "Invalid numeric literal at line" in str(err):
                issues.node_3859.finish_test()
            raise

        assert "lastEpoch" in ledger_state

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "option",
        (
            "single_pool",
            "multiple_pools",
            pytest.param(
                "all_pools",
                marks=common.SKIPIF_ON_TESTNET,
            ),
        ),
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_stake_snapshot(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        option: str,
    ):
        """Test `query stake-snapshot`.

        See also `TestLedgerState.test_stake_snapshot` for more scenarios.
        """
        temp_template = common.get_test_id(cluster)
        self._check_stake_snapshot(
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            option=option,
            temp_template=temp_template,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_pool_params(self, cluster: clusterlib.ClusterLib, pool_ids: list[str]):
        """Test `query pool-params`."""
        common.get_test_id(cluster)

        try:
            pool_params = cluster.g_query.get_pool_params(stake_pool_id=pool_ids[0])
        except json.decoder.JSONDecodeError:
            issues.node_3859.finish_test()
            raise

        assert hasattr(pool_params, "retiring")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "with_out_file",
        (True, False),
        ids=("with_out_file", "without_out_file"),
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_tx_mempool_info(
        self,
        cluster: clusterlib.ClusterLib,
        with_out_file: bool,
    ):
        """Test 'query tx-mempool info'.

        * check that the expected fields are returned
        * check that the slot number returned is the last applied on the ledger plus one
        """
        common.get_test_id(cluster)

        last_ledger_slot = 0
        tx_mempool = {}
        for __ in range(5):
            if with_out_file:
                out_file = "/dev/stdout"
                cli_out = cluster.cli(
                    ["query", "tx-mempool", "info", "--out-file", out_file, *cluster.magic_args]
                )
                tx_mempool = json.loads(cli_out.stdout.rstrip().decode("utf-8"))
            else:
                tx_mempool = cluster.g_query.get_mempool_info()

            last_ledger_slot = cluster.g_query.get_slot_no()

            if last_ledger_slot + 1 == tx_mempool["slot"]:
                break
        else:
            msg = f"Expected slot number '{last_ledger_slot + 1}', got '{tx_mempool['slot']}'"
            raise AssertionError(msg)

        assert {"capacityInBytes", "numberOfTxs", "sizeInBytes", "slot"}.issubset(tx_mempool)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_pool_state(self, cluster: clusterlib.ClusterLib, pool_ids: list[str]):
        """Test `query pool-state`."""
        common.get_test_id(cluster)

        pool_params = cluster.g_query.get_pool_state(stake_pool_id=pool_ids[0])

        assert hasattr(pool_params, "retiring")


class TestPing:
    """Tests for `cardano-cli ping`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_ping_localhost(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test `cardano-cli ping` on local node using host and port."""
        common.get_test_id(cluster)

        count = 5
        instance_ports = cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            instance_num=cluster_nodes.get_instance_num()
        )
        port = instance_ports.pool1 or instance_ports.relay1

        cli_out = cluster.cli(
            [
                "cardano-cli",
                "ping",
                "--count",
                str(count),
                "--host",
                "localhost",
                "--port",
                str(port),
                "--magic",
                str(cluster.network_magic),
                "--json",
                "--quiet",
            ],
            timeout=30,
            add_default_args=False,
        )

        err_str = cli_out.stderr.rstrip().decode("utf-8")
        if "UnknownVersionInRsp" in err_str:
            issues.node_5324.finish_test()

        ping_data = json.loads(cli_out.stdout.rstrip().decode("utf-8"))

        last_pong = ping_data["pongs"][-1]
        assert last_pong["cookie"] == count - 1, f"Expected cookie {count - 1}, got {last_pong}"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_ping_unix_socket(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test `cardano-cli ping` on local node using unix socket."""
        common.get_test_id(cluster)
        count = 5
        ignore_file_id = "ping_unix_socket"

        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="MuxError MuxUnknownMiniProtocol .* MiniProtocolNum 8",
            ignore_file_id=ignore_file_id,
            # Ignore errors for next 20 seconds
            skip_after=time.time() + 20,
        )

        try:
            cli_out = cluster.cli(
                [
                    "cardano-cli",
                    "ping",
                    "--count",
                    str(count),
                    "--unixsock",
                    os.environ.get("CARDANO_NODE_SOCKET_PATH") or "",
                    "--magic",
                    str(cluster.network_magic),
                    "--json",
                    "--quiet",
                ],
                timeout=30,
                add_default_args=False,
            )
        except clusterlib.CLIError as exc:
            exc_str = str(exc)
            if "MuxError MuxBearerClosed" in exc_str:
                issues.node_5245.finish_test()
            # cardano-ping reports user friendly error on misconfiguration on cardano-cli 9.4.1.1+
            if "Unix sockets only support queries" in exc_str:
                return
            raise
        else:
            logfiles.clean_ignore_rules(ignore_file_id=ignore_file_id)

        err_str = cli_out.stderr.rstrip().decode("utf-8")
        if "UnknownVersionInRsp" in err_str:
            issues.node_5324.finish_test()

        out_str = cli_out.stdout.rstrip().decode("utf-8")
        if not (out_str and out_str[0] == "{"):
            issues.cli_49.finish_test()

        ping_data = json.loads(out_str)

        last_pong = ping_data["pongs"][-1]
        assert last_pong["cookie"] == count - 1, f"Expected cookie {count - 1}, got {last_pong}"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_ping_tip(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test querying tip using `cardano-cli ping` on local node using host and port."""
        common.get_test_id(cluster)

        instance_ports = cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            instance_num=cluster_nodes.get_instance_num()
        )
        port = instance_ports.pool1 or instance_ports.relay1

        cli_out = cluster.cli(
            [
                "cardano-cli",
                "ping",
                "--host",
                "localhost",
                "--port",
                str(port),
                "--magic",
                str(cluster.network_magic),
                "--json",
                "--tip",
            ],
            timeout=30,
            add_default_args=False,
        )

        ping_data = cli_out.stdout.rstrip().decode("utf-8")
        ping_json = json.loads(ping_data.split("\n")[-1])

        tip_ping = ping_json["tip"][-1]["slotNo"]
        tip_cluster = cluster.g_query.get_slot_no()
        assert abs(tip_ping - tip_cluster) <= 100, (
            f"Expected tip close to {tip_cluster}, got {tip_ping}"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_ping_version(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test querying versions using `cardano-cli ping` on local node using host and port."""
        common.get_test_id(cluster)

        instance_ports = cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            instance_num=cluster_nodes.get_instance_num()
        )
        port = instance_ports.pool1 or instance_ports.relay1

        cli_out = None
        exc_str = ""
        try:
            cli_out = cluster.cli(
                [
                    "cardano-cli",
                    "ping",
                    "--host",
                    "localhost",
                    "--port",
                    str(port),
                    "--magic",
                    str(cluster.network_magic),
                    "--query-versions",
                ],
                timeout=30,
                add_default_args=False,
            )
        except clusterlib.CLIError as exc:
            exc_str = str(exc)
            if "UnknownVersionInRsp" not in exc_str:
                raise

        if exc_str:
            if VERSIONS.cli_git_rev == VERSIONS.git_rev:
                # The CLI and node revisions match, so the `UnknownVersionInRsp` error
                # should not happen.
                issues.network_5281.finish_test()

            # When the CLI and node revisions differ, the error is expected and acceptable.
            return

        assert cli_out is not None
        ping_data = cli_out.stdout.rstrip().decode("utf-8")
        assert "NodeToNodeVersion" in ping_data, ping_data


class TestQuerySlotNumber:
    """Tests for `cardano-cli query slot-number`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_slot_number(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test `query slot-number`."""
        common.get_test_id(cluster)

        timestamp = datetime.datetime.now(datetime.UTC)
        slot_number = cluster.g_query.get_slot_number(timestamp=timestamp)

        # In case the test runs on epoch boundary, the tip could still be in the previous epoch.
        # Wait for new block to be sure that the tip is up-to-date.
        cluster.wait_for_new_block()
        tip_out = cluster.g_query.get_tip()

        # Check that 'slot' returned is never greater than the total number of slots
        assert slot_number <= (tip_out["epoch"] + 1) * cluster.epoch_length - cluster.slots_offset

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_slot_number_invalid_format(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test `query slot-number` with a timestamp invalid format.

        Expect failure.
        """
        common.get_test_id(cluster)

        timestamp_str = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.query_cli(["slot-number", timestamp_str])
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "Malformed timestamp" in exc_value  # In cardano-cli >= 10.14.0.0
                or "parseTimeOrError" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("time_val", ("above", "bellow"))
    @pytest.mark.smoke
    def test_slot_number_out_of_range(
        self,
        cluster: clusterlib.ClusterLib,
        time_val: str,
    ):
        """Test `query slot-number` with a timestamp out of range.

        Expect failure.
        """
        common.get_test_id(cluster)

        now = datetime.datetime.now(datetime.UTC)

        timestamp = now.replace(year=now.year * 4) if time_val == "above" else now.replace(year=1)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_query.get_slot_number(timestamp=timestamp)
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "PastHorizon" in exc_value, exc_value
