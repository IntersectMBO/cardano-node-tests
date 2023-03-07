"""Tests for datum while spending with Plutus using `transaction build`."""
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
from cardano_clusterlib import txtools

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import spend_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
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
        amount=1_000_000_000,
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
        """Test creating UTxO with datum on address with key credentials (non-script address).

        Uses `cardano-cli transaction build` command for building the transactions.
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

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        datum_utxo = clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0]
        assert datum_utxo.datum_hash, f"UTxO should have datum hash: {datum_utxo}"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_embed_datum_without_pparams(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test 'build --tx-out-datum-embed' without providing protocol params file."""
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
        )

        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=plutus_op.script_file
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        utxos = cluster.g_query.get_utxo(address=payment_addrs[0].address)
        txin = txtools.filter_utxo_with_highest_amount(utxos=utxos)

        out_file = f"{temp_template}_tx.body"

        cli_args = [
            "transaction",
            "build",
            "--tx-in",
            f"{txin.utxo_hash}#{txin.utxo_ix}",
            "--tx-out",
            f"{script_address}+2000000",
            "--tx-out-datum-embed-file",
            str(plutus_op.datum_file),
            "--change-address",
            payment_addrs[0].address,
            "--out-file",
            out_file,
            "--testnet-magic",
            str(cluster.network_magic),
            *cluster.g_transaction.tx_era_arg,
        ]

        cluster.cli(cli_args)

        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_signed",
        )

        try:
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=[txin])
        except clusterlib.CLIError as err:
            if "PPViewHashesDontMatch" in str(err):
                pytest.xfail("build cmd requires protocol params - see node issue #4058")
            raise


@pytest.mark.testnets
class TestNegativeDatum:
    """Tests for Tx output locking using Plutus smart contracts with wrong datum."""

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
            clusterlib.TxOut(address=redeem_address, amount=amount + redeem_cost.fee),
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

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_fund)
        utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
            utxos=out_utxos, txouts=tx_output_fund.txouts
        )

        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
        collateral_utxos = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset + 1)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount,
                submit_tx=False,
            )
        err_str = str(excinfo.value)

        if address_type == "script_address":
            assert "txin does not have a script datum" in err_str, err_str
        else:
            assert (
                "not a Plutus script witnessed tx input" in err_str
                or "points to a script hash that is not known" in err_str
            ), err_str

        # check expected fees
        expected_fee_fund = 199_087
        assert helpers.is_in_interval(tx_output_fund.fee, expected_fee_fund, frac=0.15)

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

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump(f'{{"{datum_value}"}}', outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_fund_script(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                plutus_op=plutus_op,
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

        plutus_op_1 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42_TYPED,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        script_utxos, collateral_utxos, __ = spend_build._build_fund_script(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addrs[0],
            dst_addr=payment_addrs[1],
            plutus_op=plutus_op_1,
        )

        # use a wrong datum to try to unlock the funds
        plutus_op_2 = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=plutus_common.DATUM_42,
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            execution_cost=plutus_common.ALWAYS_SUCCEEDS[plutus_version].execution_cost,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addrs[0],
                dst_addr=payment_addrs[1],
                script_utxos=script_utxos,
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op_2,
                amount=2_000_000,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert (
            "The Plutus script witness has the wrong datum (according to the UTxO)." in err_str
        ), err_str

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
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        tx_output = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=temp_template,
            tx_files=tx_files,
            txouts=txouts,
        )
        tx_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        datum_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=dst_addr.address, datum_hash=datum_hash
        )[0]
        collateral_utxos = clusterlib.filter_utxos(
            utxos=out_utxos, address=payment_addr.address, utxo_ix=datum_utxo.utxo_ix + 1
        )
        assert (
            datum_utxo.datum_hash == datum_hash
        ), f"UTxO should have datum hash '{datum_hash}': {datum_utxo}"

        # try to spend the "locked" UTxO

        with pytest.raises(clusterlib.CLIError) as excinfo:
            spend_build._build_spend_locked_txin(
                temp_template=temp_template,
                cluster_obj=cluster,
                payment_addr=payment_addr,
                dst_addr=dst_addr,
                script_utxos=[datum_utxo],
                collateral_utxos=collateral_utxos,
                plutus_op=plutus_op,
                amount=amount_redeem,
                submit_tx=False,
            )

        err_str = str(excinfo.value)
        assert "points to a script hash that is not known" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(datum_value=st.binary(min_size=65))
    @common.hypothesis_settings(max_examples=100)
    @common.PARAM_PLUTUS_VERSION
    def test_too_big(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        pbt_script_addresses: Dict[str, str],
        datum_value: bytes,
        plutus_version: str,
    ):
        """Try to lock a UTxO with datum that is too big.

        Expect failure on node version < 1.36.0.
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{common.unique_time_str()}"

        datum_file = f"{temp_template}.datum"
        with open(datum_file, "w", encoding="utf-8") as outfile:
            json.dump({"constructor": 0, "fields": [{"bytes": datum_value.hex()}]}, outfile)

        plutus_op = plutus_common.PlutusOp(
            script_file=plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file,
            datum_file=Path(datum_file),
            redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
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
            amount=4_500_000,
            plutus_op=plutus_op,
        )

        err_str = ""
        try:
            cluster.g_transaction.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=f"{temp_template}_step1",
                tx_files=tx_files,
                txouts=[script_txout],
                fee_buffer=2_000_000,
            )
        except clusterlib.CLIError as exc:
            err_str = str(exc)

        assert (
            not err_str or "must consist of at most 64 bytes" in err_str  # in node version < 1.36.0
        ), err_str
