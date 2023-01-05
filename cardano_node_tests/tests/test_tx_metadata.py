"""Tests for transactions with metadata."""
import json
import logging
from pathlib import Path

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
DATA_DIR = Path(__file__).parent / "data"


@pytest.mark.testnets
@pytest.mark.smoke
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
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addr = clusterlib_utils.create_payment_addr_records(
                f"addr_test_metadata_ci{cluster_manager.cluster_instance_num}_0",
                cluster_obj=cluster,
            )[0]
            fixture_cache.value = addr

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return addr

    @allure.link(helpers.get_vcs_link())
    def test_tx_wrong_json_metadata_format(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with wrong format of metadata JSON.

        The metadata file is a valid JSON, but not in a format that is expected.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_WRONG_FILE],
        )

        # it should NOT be possible to build a transaction using wrongly formatted metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="wrong_json_format",
                tx_files=tx_files,
            )
        assert "The JSON metadata top level must be a map" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_tx_wrong_json_metadata_format(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with wrong format of metadata JSON.

        Uses `cardano-cli transaction build` command for building the transactions.

        The metadata file is a valid JSON, but not in a format that is expected.
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
        assert "The JSON metadata top level must be a map" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_tx_invalid_json_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with invalid metadata JSON.

        The metadata file is an invalid JSON.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_INVALID_FILE],
        )

        # it should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="invalid_metadata",
                tx_files=tx_files,
            )
        assert "Failed reading: satisfy" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_tx_invalid_json_metadata(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with invalid metadata JSON.

        Uses `cardano-cli transaction build` command for building the transactions.

        The metadata file is an invalid JSON.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_INVALID_FILE],
        )

        # it should NOT be possible to build a transaction using an invalid metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name="invalid_metadata",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        assert "Failed reading: satisfy" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_tx_too_long_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with metadata JSON longer than 64 bytes."""
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_LONG_FILE],
        )

        # it should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx(
                src_address=payment_addr.address,
                tx_name="too_long_metadata",
                tx_files=tx_files,
            )
        assert "Text string metadata value must consist of at most 64 UTF8 bytes" in str(
            excinfo.value
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_tx_too_long_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Try to build a transaction with metadata JSON longer than 64 bytes.

        Uses `cardano-cli transaction build` command for building the transactions.
        """
        common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
            metadata_json_files=[self.JSON_METADATA_LONG_FILE],
        )

        # it should NOT be possible to build a transaction using too long metadata JSON
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name="too_long_metadata",
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        assert "Text string metadata value must consist of at most 64 UTF8 bytes" in str(
            excinfo.value
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON.

        * check that the metadata in TX body matches the original metadata
        * (optional) check transactions in db-sync
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
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.dbsync
    def test_build_tx_metadata_json(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata JSON.

        Uses `cardano-cli transaction build` command for building the transactions.

        * check that the metadata in TX body matches the original metadata
        * (optional) check transactions in db-sync
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
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR.

        Check that the metadata in TX body matches the original metadata.
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

        assert (
            cbor_body_metadata.metadata == cbor_file_metadata
        ), "Metadata in TX body doesn't match original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_file_metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.dbsync
    def test_build_tx_metadata_cbor(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with metadata CBOR.

        Uses `cardano-cli transaction build` command for building the transactions.

        Check that the metadata in TX body matches the original metadata.
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

        assert (
            cbor_body_metadata.metadata == cbor_file_metadata
        ), "Metadata in TX body doesn't match original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_file_metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_both(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with both metadata JSON and CBOR.

        Check that the metadata in TX body matches the original metadata.
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
        # dump it as JSON, so keys are converted to strings
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

        # check `transaction view` command
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        assert json_body_metadata == tx_view_out["metadata"]

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.dbsync
    def test_build_tx_metadata_both(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with both metadata JSON and CBOR.

        Uses `cardano-cli transaction build` command for building the transactions.

        Check that the metadata in TX body matches the original metadata.
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
        # dump it as JSON, so keys are converted to strings
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

        # check `transaction view` command
        tx_view_out = tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output)
        assert json_body_metadata == tx_view_out["metadata"]

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    def test_tx_duplicate_metadata_keys(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Send transaction with multiple metadata JSON files and with duplicate keys.

        * check that the metadata in TX body matches the original metadata
        * check that in case of duplicate keys the first occurrence is used
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
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        # merge the input JSON files and alter the result so it matches the expected metadata
        with open(metadata_json_files[0], encoding="utf-8") as metadata_fp:
            json_file_metadata1 = json.load(metadata_fp)
        with open(metadata_json_files[1], encoding="utf-8") as metadata_fp:
            json_file_metadata2 = json.load(metadata_fp)
        json_file_metadata = {**json_file_metadata2, **json_file_metadata1}
        json_file_metadata["5"] = "baz1"

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_tx_metadata_no_txout(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
    ):
        """Send transaction with just metadata, no UTxO is produced.

        * submit a transaction where all funds available on source address is used for fee
        * check that no UTxOs are created by the transaction
        * check that there are no funds left on source address
        * check that the metadata in TX body matches the original metadata
        """
        temp_template = common.get_test_id(cluster)

        src_record = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_0", cluster_obj=cluster
        )[0]
        clusterlib_utils.fund_from_faucet(
            src_record,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
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
        # dump it as JSON, so keys are converted to strings
        json_body_metadata = json.loads(json.dumps(cbor_body_metadata.metadata))

        with open(self.JSON_METADATA_FILE, encoding="utf-8") as metadata_fp:
            json_file_metadata = json.load(metadata_fp)

        assert (
            json_body_metadata == json_file_metadata
        ), "Metadata in TX body doesn't match the original metadata"

        # check TX and metadata in db-sync if available
        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"
