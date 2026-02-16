"""Tests for datum while spending with Plutus using `transaction build-raw`."""

import json
import logging
import pathlib as pl

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_raw
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUS_UNUSABLE,
    pytest.mark.plutus,
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    addrs = common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=3,
        fund_idx=[0],
        amount=3_000_000_000,
    )
    return addrs


class TestDatum:
    """Tests for datum."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_datum_on_key_credential_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test creating UTxO with datum on address with key credentials (non-script address).

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        * Create transaction output with datum hash on non-script payment address
        * Build raw transaction with calculated fee
        * Sign and submit transaction
        * Query created UTxO and verify datum hash is present
        * (optional) Check transaction records in db-sync
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        txouts = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount,
                datum_hash_file=plutus_common.DATUM_42_TYPED,
            )
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        datum_utxo = clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0]
        assert datum_utxo.datum_hash, f"UTxO should have datum hash: {datum_utxo}"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


class TestNegativeDatum:
    """Tests for Tx output locking using Plutus smart contracts with wrong datum."""

    @pytest.fixture
    def pbt_highest_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ) -> clusterlib.UTXOData:
        """Get UTxO with highest amount of Lovelace.

        Meant for property-based tests, so this expensive operation gets executed only once.
        """
        return cluster.g_query.get_utxo_with_highest_amount(payment_addrs[0].address)

    @pytest.fixture
    def pbt_script_addresses(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> dict[str, str]:
        """Get Plutus script addresses.

        Meant for property-based tests, so this expensive operation gets executed only once.
        """
        temp_template = common.get_test_id(cluster)

        script_address_v1 = cluster.g_address.gen_payment_addr(
            addr_name=temp_template,
            payment_script_file=plutus_common.ALWAYS_SUCCEEDS["v1"].script_file,
        )
        script_address_v2 = cluster.g_address.gen_payment_addr(
            addr_name=temp_template,
            payment_script_file=plutus_common.ALWAYS_SUCCEEDS["v2"].script_file,
        )
        return {"v1": script_address_v1, "v2": script_address_v2}

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("address_type", ("script_address", "key_address"))
    @common.PARAM_PLUTUS_VERSION
    @pytest.mark.smoke
    def test_no_datum_txout(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        address_type: str,
        plutus_version: str,
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        Expect failure.

        * Create a Tx output without a datum hash
        * Try to spend the UTxO like it was locked Plutus UTxO
        * Check that the expected error was raised
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_file=plutus_common.REDEEMER_42_TYPED,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        if address_type == "script_address":
            redeem_address = cluster.g_address.gen_payment_addr(
                addr_name=temp_template, payment_script_file=plutus_op.script_file
            )
        else:
            redeem_address = payment_addrs[2].address

        redeem_cost = plutus_common.compute_cost(
            execution_cost=plutus_op.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        txouts = [
            clusterlib.TxOut(
                address=redeem_address,
                amount=amount + redeem_cost.fee + spend_raw.FEE_REDEEM_TXSIZE,
            ),
            clusterlib.TxOut(address=payment_addr.address, amount=redeem_cost.collateral),
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output_fund = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )
        txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_fund.out_file)
        script_utxos = cluster.g_query.get_utxo(txin=f"{txid}#0")
        collateral_utxos = cluster.g_query.get_utxo(txin=f"{txid}#1")

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "NonOutputSupplimentaryDatums" in exc_value
                or "NotAllowedSupplementalDatums" in exc_value  # on node version >= 8.6.0
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_lock_tx_invalid_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        datum_value: str,
        plutus_version: str,
    ):
        """Test locking a Tx output with an invalid datum (property-based test).

        Expect failure.

        Property-based test using Hypothesis to generate random text strings as invalid datum
        values to test JSON parsing and validation.

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        * Generate random text string as invalid datum value
        * Create malformed datum file with invalid JSON format
        * Attempt to build raw transaction with invalid datum file
        * Check that transaction building fails with JSON object error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        amount = 2_000_000
        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=pl.Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._fund_script(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                plutus_op=plutus_op,
                amount=amount,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "JSON object expected. Unexpected value" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    @pytest.mark.smoke
    def test_unlock_tx_wrong_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking a Tx output and try to spend it with a wrong datum.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        * Lock funds at script address with typed datum (datum 42 typed)
        * Attempt to spend locked UTxO using different datum format (datum 42 untyped)
        * Check that spending fails with supplementary datums error
        """
        temp_template = common.get_test_id(cluster)
        amount = 1_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        plutus_op_1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op_1.execution_cost  # for mypy

        script_utxos, collateral_utxos, __ = spend_raw._fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=dst_addr,
            plutus_op=plutus_op_1,
            amount=amount,
        )

        # Use a wrong datum to try to unlock the funds
        plutus_op_2 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op_2,
                amount=amount,
            )

        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "NonOutputSupplimentaryDatums" in exc_value
                or "NotAllowedSupplementalDatums" in exc_value  # on node version >= 8.6.0
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    @pytest.mark.smoke
    def test_unlock_non_script_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Try to spend a non-script UTxO with datum as if it was script locked UTxO.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        * Create regular UTxO at payment address with datum hash (non-script address)
        * Create collateral UTxO
        * Attempt to spend regular UTxO with Plutus script witness and redeemer
        * Check that spending fails with MissingScriptWitnessesUTXOW error
        """
        temp_template = common.get_test_id(cluster)

        amount_fund = 4_000_000
        amount_redeem = 2_000_000
        amount_collateral = 2_000_000

        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        datum_file = plutus_common.DATUM_42_TYPED

        datum_hash = cluster.g_transaction.get_hash_script_data(script_data_file=datum_file)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=datum_file,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        # Create datum and collateral UTxOs

        txouts = [
            clusterlib.TxOut(
                address=dst_addr.address,
                amount=amount_fund,
                datum_hash=datum_hash,
            ),
            clusterlib.TxOut(
                address=payment_addr.address,
                amount=amount_collateral,
            ),
        ]
        tx_files_fund = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files_fund,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        datum_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=dst_addr.address, datum_hash=datum_hash
        )[0]
        collateral_utxos = clusterlib.filter_utxos(
            utxos=out_utxos, address=payment_addr.address, utxo_ix=datum_utxo.utxo_ix + 1
        )
        assert datum_utxo.datum_hash == datum_hash, (
            f"UTxO should have datum hash '{datum_hash}': {datum_utxo}"
        )

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file, dst_addr.skey_file]
        )

        # Try to spend the "locked" UTxO

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=[datum_utxo],
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount_redeem,
                tx_files=tx_files_redeem,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "ExtraneousScriptWitnessesUTXOW" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    @pytest.mark.smoke
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        pbt_script_addresses: dict[str, str],
        datum_value: bytes,
        plutus_version: str,
    ):
        """Try to lock a UTxO with datum that is too big (property-based test).

        Property-based test using Hypothesis to generate random binary data >= 65 bytes to test
        datum size limits (maximum datum size is 64 bytes).

        Uses `cardano-cli transaction build-raw` command for building the transactions.

        * Generate random binary datum value (minimum 65 bytes)
        * Create datum file with oversized binary data
        * Attempt to build raw transaction locking UTxO with oversized datum
        * Check that transaction building fails with size error (on node < 1.36.0)
        * Expect failure on node version < 1.36.0.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        amount = 2_000_000

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": datum_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=pl.Path(datum_file),
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        script_address = pbt_script_addresses[plutus_version]

        # Create a Tx output with a datum hash at the script address

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
        )

        script_txout = plutus_common.txout_factory(
            address=script_address,
            amount=amount,
            plutus_op=plutus_op,
        )

        err_str = ""
        try:
            cluster.g_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_step1_tx.body",
                txins=[pbt_highest_utxo],
                txouts=[script_txout],
                tx_files=tx_files,
                fee=1,
            )
        except clusterlib.CLIError as exc:
            err_str = str(exc)

        assert (
            not err_str or "must consist of at most 64 bytes" in err_str  # on node version < 1.36.0
        ), err_str
