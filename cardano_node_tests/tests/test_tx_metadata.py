"""Tests for transactions with metadata."""

import json
import logging
import pathlib as pl

import allure
import cbor2
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent / "data"


class TestMetadata:
    """Tests for transactions with metadata."""

    JSON_METADATA_FILE = DATA_DIR / "tx_metadata.json"
    JSON_METADATA_WRONG_FILE = DATA_DIR / "tx_metadata_wrong.json"
    JSON_METADATA_INVALID_FILE = DATA_DIR / "tx_metadata_invalid.json"
    JSON_METADATA_LONG_FILE = DATA_DIR / "tx_metadata_long.json"
    CBOR_METADATA_FILE = DATA_DIR / "tx_metadata.cbor"
    METADATA_DUPLICATES = "tx_metadata_duplicate_keys*.json"

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        addr = common.get_payment_addr(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            caching_key=helpers.get_current_line_str(),
        )
        return addr

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_tx_wrong_json_metadata_format(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build transaction with incorrectly formatted metadata JSON.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.
        The metadata file is valid JSON but not in expected format (map at top level).

        * Prepare transaction files with wrongly formatted metadata JSON
        * Attempt to build transaction using `build-raw`
        * Check that transaction building fails with metadata format error
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_WRONG_FILE],
        )

        # It should NOT be possible to build a transaction using wrongly formatted metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="wrong_json_format",
                tx_files=tx_files,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "The JSON metadata top level must be a map" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_tx_wrong_json_metadata_format(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build transaction with incorrectly formatted metadata JSON.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.
        The metadata file is valid JSON but not in expected format (map at top level).

        * Prepare transaction files with wrongly formatted metadata JSON
        * Attempt to build transaction using `build`
        * Check that transaction building fails with metadata format error
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_WRONG_FILE],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name="wrong_json_format",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "The JSON metadata top level must be a map" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_tx_invalid_json_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build transaction with invalid (malformed) metadata JSON.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.
        The metadata file contains invalid JSON syntax.

        * Prepare transaction files with invalid JSON metadata file
        * Attempt to build transaction using `build-raw`
        * Check that transaction building fails with JSON parse error
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_INVALID_FILE],
        )

        # It should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="invalid_metadata",
                tx_files=tx_files,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "expecting record key literal" in exc_value  # cardano-cli >= 10.11.1.1
                or "Failed reading: satisfy" in exc_value  # cardano-node < 8.7.0
                or "JSON parse error:" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_tx_invalid_json_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build transaction with invalid (malformed) metadata JSON.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.
        The metadata file contains invalid JSON syntax.

        * Prepare transaction files with invalid JSON metadata file
        * Attempt to build transaction using `build`
        * Check that transaction building fails with JSON parse error
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_INVALID_FILE],
        )

        # It should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name="invalid_metadata",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "expecting record key literal" in exc_value  # cardano-cli >= 10.11.1.1
                or "Failed reading: satisfy" in exc_value  # cardano-node < 8.7.0
                or "JSON parse error:" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_tx_too_long_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build transaction with metadata JSON string exceeding 64 bytes.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * Prepare transaction files with metadata JSON containing string > 64 UTF-8 bytes
        * Attempt to build transaction using `build-raw`
        * Check that transaction building fails with string length error
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_LONG_FILE],
        )

        # It should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="too_long_metadata",
                tx_files=tx_files,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "Text string metadata value must consist of at most 64 UTF8 bytes" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_tx_too_long_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build transaction with metadata JSON string exceeding 64 bytes.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Prepare transaction files with metadata JSON containing string > 64 UTF-8 bytes
        * Attempt to build transaction using `build`
        * Check that transaction building fails with string length error
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_LONG_FILE],
        )

        # It should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name="too_long_metadata",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "Text string metadata value must consist of at most 64 UTF8 bytes" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_tx_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON and verify metadata preservation.

        Uses `cardano-cli transaction build-raw` command.

        * Prepare transaction files with metadata JSON file
        * Send transaction from payment address with metadata attached
        * Load metadata from transaction body CBOR
        * Compare transaction body metadata with original JSON file
        * Check that metadata in TX body matches the original metadata exactly
        * (optional) Check transactions and metadata in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # Dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert json_body_metadata == json_file_metadata, (
            "Metadata in TX body doesn't match the original metadata"
        )

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_build_tx_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON and verify metadata preservation.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Prepare transaction files with metadata JSON file
        * Build, sign and submit transaction from payment address with metadata
        * Load metadata from transaction body CBOR
        * Compare transaction body metadata with original JSON file
        * Check that metadata in TX body matches the original metadata exactly
        * (optional) Check transactions and metadata in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
        )
        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)
        # Dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert json_body_metadata == json_file_metadata, (
            "Metadata in TX body doesn't match the original metadata"
        )

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_tx_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR and verify metadata preservation.

        Uses `cardano-cli transaction build-raw` command.

        * Prepare transaction files with metadata CBOR file
        * Send transaction from payment address with CBOR metadata attached
        * Load metadata from transaction body CBOR
        * Compare transaction body metadata with original CBOR file
        * Check that metadata in TX body matches the original metadata exactly
        * (optional) Check transactions and metadata in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp:
            cbor_file_metadata = cbor2.load(metadata_fp)

        assert cbor_body_metadata.metadata == cbor_file_metadata, (
            "Metadata in TX body doesn't match original metadata"
        )

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_file_metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_build_tx_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR and verify metadata preservation.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Prepare transaction files with metadata CBOR file
        * Build, sign and submit transaction from payment address with CBOR metadata
        * Load metadata from transaction body CBOR
        * Compare transaction body metadata with original CBOR file
        * Check that metadata in TX body matches the original metadata exactly
        * (optional) Check transactions and metadata in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
        )

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp:
            cbor_file_metadata = cbor2.load(metadata_fp)

        assert cbor_body_metadata.metadata == cbor_file_metadata, (
            "Metadata in TX body doesn't match original metadata"
        )

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_file_metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_tx_metadata_both(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with both metadata JSON and CBOR formats combined.

        Uses `cardano-cli transaction build-raw` command.

        * Prepare transaction files with both JSON and CBOR metadata files
        * Send transaction from payment address with both metadata types attached
        * Load metadata from transaction body CBOR
        * Merge expected metadata from both JSON and CBOR files
        * Check that combined metadata in TX body matches merged original metadata
        * Verify transaction view command output matches metadata
        * (optional) Check transactions and metadata in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # Dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp_json:
            json_file_metadata = json.load(metadata_fp_json)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp_cbor:
            cbor_file_metadata = cbor2.load(metadata_fp_cbor)
        cbor_file_metadata = json.loads(json.dumps(cbor_file_metadata))

        assert json_body_metadata == {
            **json_file_metadata,
            **cbor_file_metadata,
        }, "Metadata in TX body doesn't match original metadata"

        # Check `transaction view` command
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        assert json_body_metadata == tx_view_out["metadata"]

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_build_tx_metadata_both(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with both metadata JSON and CBOR formats combined.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Prepare transaction files with both JSON and CBOR metadata files
        * Build, sign and submit transaction with both metadata types attached
        * Load metadata from transaction body CBOR
        * Merge expected metadata from both JSON and CBOR files
        * Check that combined metadata in TX body matches merged original metadata
        * Verify transaction view command output matches metadata
        * (optional) Check transactions and metadata in db-sync
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
            metadata_cbor_files=[self.CBOR_METADATA_FILE],
        )

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee_buffer=1_000_000,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)
        # Dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp_json:
            json_file_metadata = json.load(metadata_fp_json)

        with open(self.CBOR_METADATA_FILE, "rb") as metadata_fp_cbor:
            cbor_file_metadata = cbor2.load(metadata_fp_cbor)
        cbor_file_metadata = json.loads(json.dumps(cbor_file_metadata))

        assert json_body_metadata == {
            **json_file_metadata,
            **cbor_file_metadata,
        }, "Metadata in TX body doesn't match original metadata"

        # Check `transaction view` command
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)
        assert json_body_metadata == tx_view_out["metadata"]

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_tx_duplicate_metadata_keys(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with multiple metadata JSON files containing duplicate keys.

        Uses `cardano-cli transaction build-raw` command.

        * Prepare transaction files with multiple JSON metadata files having duplicate keys
        * Send transaction from payment address with all metadata files attached
        * Load metadata from transaction body CBOR
        * Merge expected metadata from input files (first occurrence wins for duplicates)
        * Check that metadata in TX body matches merged metadata with correct precedence
        * Verify that for duplicate keys the first occurrence is used
        * (optional) Check transactions and metadata in db-sync
        """
        temp_template = common.get_test_id(cluster)

        metadata_json_files = list(DATA_DIR.glob(self.METADATA_DUPLICATES))

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=metadata_json_files,
        )

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address, tx_name=temp_template, tx_files=tx_files
        )
        assert tx_raw_output.fee, "Transaction had no fee"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # Dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        # Merge the input JSON files and alter the result so it matches the expected metadata
        with open(metadata_json_files[0], encoding="utf-8") as metadata_fp:
            json_file_metadata1 = json.load(metadata_fp)
        with open(metadata_json_files[1], encoding="utf-8") as metadata_fp:
            json_file_metadata2 = json.load(metadata_fp)
        json_file_metadata = {**json_file_metadata2, **json_file_metadata1}
        json_file_metadata["5"] = "baz1"

        assert json_body_metadata == json_file_metadata, (
            "Metadata in TX body doesn't match the original metadata"
        )

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_tx_metadata_no_txout(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Send transaction with just metadata, no UTxO is produced.

        * Submit a transaction where all funds available on source address is used for fee
        * Check that no UTxOs are created by the transaction
        * Check that there are no funds left on source address
        * Check that the metadata in TX body matches the original metadata
        """
        temp_template = common.get_test_id(cluster)

        src_record = common.get_payment_addr(
            name_template=temp_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            amount=2_000_000,
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[src_record.skey_file],
            metadata_json_files=[self.JSON_METADATA_FILE],
        )
        fee = cluster.g_query.get_address_balance(src_record.address)
        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=src_record.address, tx_name=temp_template, tx_files=tx_files, fee=fee
        )
        assert not tx_raw_output.txouts, "Transaction has unexpected txouts"

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)
        # Dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert json_body_metadata == json_file_metadata, (
            "Metadata in TX body doesn't match the original metadata"
        )

        # Check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )
