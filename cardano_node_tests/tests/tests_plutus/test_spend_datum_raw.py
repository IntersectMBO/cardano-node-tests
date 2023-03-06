"""Tests for datum while spending with Plutus using `transaction build-raw`."""
import json
import logging
from pathlib import Path
from typing import Dict
from typing import List

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUS_UNUSABLE,
    pytest.mark.smoke,
    pytest.mark.plutus,
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment addresses."""
    test_id = common.get_test_id(cluster)
    addrs = clusterlib_utils.create_payment_addr_records(
        *[f"{test_id}_payment_addr_{i}" for i in range(3)],
        cluster_obj=cluster,
    )

    # fund source address
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=3_000_000_000,
    )

    return addrs


@pytest.mark.testnets
class TestDatum:
    """Tests for datum."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_datum_on_key_credential_address(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test creating UTxO with datum on address with key credentials (non-script address)."""
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

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


@pytest.mark.testnets
class TestNegativeDatum:
    """Tests for Tx output locking using Plutus smart contracts with wrong datum."""

    @pytest.fixture
    def pbt_highest_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> clusterlib.UTXOData:
        """Get UTxO with highest amount of Lovelace.

        Meant for property-based tests, so this expensive operation gets executed only once.
        """
        return cluster.g_query.get_utxo_with_highest_amount(payment_addrs[0].address)

    @pytest.fixture
    def pbt_script_addresses(
        self,
        cluster: clusterlib.ClusterLib,
    ) -> Dict[str, str]:
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
    def test_no_datum_txout(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        address_type: str,
        plutus_version: str,
    ):
        """Test using UTxO without datum hash in place of locked UTxO.

        Expect failure.

        * create a Tx output without a datum hash
        * try to spend the UTxO like it was locked Plutus UTxO
        * check that the expected error was raised
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{address_type}"
        amount = 2_000_000

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
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "NonOutputSupplimentaryDatums" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.text())
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_lock_tx_invalid_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        datum_value: str,
        plutus_version: str,
    ):
        """Test locking a Tx output with an invalid datum.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"
        amount = 2_000_000
        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
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

        err_str = str(excinfo.value)
        assert "JSON object expected. Unexpected value" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_unlock_tx_wrong_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test locking a Tx output and try to spend it with a wrong datum.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"
        amount = 2_000_000

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

        # use a wrong datum to try to unlock the funds
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
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op_2,
                amount=amount,
            )

        err_str = str(excinfo.value)
        assert "NonOutputSupplimentaryDatums" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_unlock_non_script_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Try to spend a non-script UTxO with datum as if it was script locked UTxO.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

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

        # create datum and collateral UTxOs

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
        assert (
            datum_utxo.datum_hash == datum_hash
        ), f"UTxO should have datum hash '{datum_hash}': {datum_utxo}"

        tx_files_redeem = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file, dst_addr.skey_file]
        )

        # try to spend the "locked" UTxO

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_raw._spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                dst_addr=dst_addr,
                script_utxos=[datum_utxo],
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount_redeem,
                tx_files=tx_files_redeem,
            )

        err_str = str(excinfo.value)
        assert "ExtraneousScriptWitnessesUTXOW" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_PLUTUS_VERSION
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_highest_utxo: clusterlib.UTXOData,
        pbt_script_addresses: Dict[str, str],
        datum_value: bytes,
        plutus_version: str,
    ):
        """Try to lock a UTxO with datum that is too big.

        Expect failure on node version < 1.36.0.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"
        amount = 2_000_000

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": datum_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )
        assert plutus_op.execution_cost  # for mypy

        script_address = pbt_script_addresses[plutus_version]

        # create a Tx output with a datum hash at the script address

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
