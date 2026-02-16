"""Negative tests for minting with Plutus using `transaction build`."""

import datetime
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
from cardano_node_tests.tests.tests_plutus import mint_build
from cardano_node_tests.tests.tests_plutus.mint_build import _fund_issuer
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUS_UNUSABLE,
    common.SKIPIF_BUILD_UNUSABLE,
    pytest.mark.plutus,
]

FundTupleT = tuple[
    list[clusterlib.UTXOData], list[clusterlib.UTXOData], list[clusterlib.AddressRecord]
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Create new payment address."""
    addrs = common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=2,
        fund_idx=[0],
        amount=3_000_000_000,
    )
    return addrs


class TestBuildMintingNegative:
    """Tests for minting with Plutus using `transaction build` that are expected to fail."""

    @pytest.fixture
    def fund_issuer_long_asset_name(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ) -> FundTupleT:
        """Fund the token issuer and create the collateral UTxO."""
        temp_template = common.get_test_id(cluster)

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        mint_utxos, collateral_utxos, __ = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addrs[0],
            issuer_addr=payment_addrs[1],
            minting_cost=minting_cost,
            amount=200_000_000,
        )

        return mint_utxos, collateral_utxos, payment_addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "plutus_version",
        (
            "v1",
            pytest.param("v3", marks=common.SKIPIF_PLUTUSV3_UNUSABLE),
        ),
        ids=("plutus_v1", "plutus_v3"),
    )
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_witness_redeemer_missing_signer(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
        submit_method: str,
    ):
        """Test minting a token with a Plutus script with invalid signers.

        Expect failure.

        * Fund the token issuer and create a UTxO for collateral
        * Check that the expected amount was transferred to token issuer's address
        * Try to mint the token using a Plutus script and a TX with signing key missing for
          the required signer
        * Check that the minting failed because the required signers were not provided
        """
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        plutus_v_record = plutus_common.MINTING_WITNESS_REDEEMER[plutus_version]

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_v_record.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, __ = mint_build._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(plutus_v_record.script_file)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_v_record.script_file,
                collaterals=collateral_utxos,
                redeemer_file=plutus_common.DATUM_WITNESS_GOLDEN_NORMAL,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]
        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            required_signers=[plutus_common.SIGNING_KEY_GOLDEN],
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            submit_utils.submit_tx(
                submit_method=submit_method,
                cluster_obj=cluster,
                tx_file=tx_signed_step2,
                txins=mint_utxos,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "MissingRequiredSigners" in exc_value  # on node version < 8.8.0
                or "MissingVKeyWitnessesUTXOW" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_redeemer_with_simple_minting_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Test minting a token passing a redeemer for a simple minting script.

        Expect failure.

        * Fund the token issuer and create a UTxO for collateral
        * Try to mint the token using a simple script passing a redeemer
        * Check that the minting failed because a Plutus script is expected
        """
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Fund the token issuer and create UTXO for collaterals

        mint_utxos, collateral_utxos, __ = mint_build._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Create simple script
        keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = pl.Path(f"{temp_template}.script")

        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        # Mint the "qacoin"
        policyid = cluster.g_transaction.get_policyid(script)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"

        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=script,
                collaterals=collateral_utxos,
                redeemer_file=plutus_common.REDEEMER_42,
            )
        ]

        tx_files = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files,
                txins=mint_utxos,
                txouts=txouts,
                mint=plutus_mint_data,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                'Error in $: key "description" not found' in exc_value  # In cli 10.1.1.0+
                or "expected a script in the Plutus script language, but it is actually "
                "using SimpleScriptLanguage"
                in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        asset_name=st.text(
            alphabet=st.characters(
                blacklist_categories=["C"], blacklist_characters=[" ", "+", "\xa0"]
            ),
            min_size=33,
            max_size=1_000,
        )
    )
    @common.hypothesis_settings(max_examples=300)
    @pytest.mark.smoke
    def test_asset_name_too_long(
        self,
        cluster: clusterlib.ClusterLib,
        fund_issuer_long_asset_name: FundTupleT,
        asset_name: str,
    ):
        """Test minting token with asset name exceeding maximum length and min-UTXO calculation.

        Expect failure.

        Property-based test using Hypothesis to generate asset names longer than 32 bytes
        (maximum allowed length for Cardano native token asset names).

        Uses `cardano-cli transaction build` command for building the transactions.

        * Use pre-funded token issuer address with collateral UTxO
        * Generate random asset name (minimum 33 characters)
        * Encode asset name to hex and create minting policy
        * Attempt to build transaction minting token with oversized asset name
        * Check that transaction building fails with "bytestring should be no longer than 32
          bytes" error
        * Attempt to calculate minimum required UTXO for transaction output with oversized
          asset name
        * Check that min-UTXO calculation also fails with same error
        """
        temp_template = common.get_test_id(cluster)

        lovelace_amount = 2_000_000
        token_amount = 5

        mint_utxos, collateral_utxos, payment_addrs = fund_issuer_long_asset_name

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_PLUTUS_V1)
        asset_name = asset_name.encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V1,
                collaterals=collateral_utxos,
                redeemer_file=plutus_common.REDEEMER_42,
                policyid=policyid,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_step2,
                txins=mint_utxos,
                txouts=txouts_step2,
                mint=plutus_mint_data,
            )
        min_token_error = str(excinfo.value)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.calculate_min_req_utxo(txouts=mint_txouts)
        min_req_utxo_error = str(excinfo.value)

        with common.allow_unstable_error_messages():
            assert (
                "the bytestring should be no longer than 32 bytes long" in min_token_error
                or "AssetName deserisalisation failed" in min_token_error
            ), min_token_error

        with common.allow_unstable_error_messages():
            assert (
                "the bytestring should be no longer than 32 bytes long" in min_req_utxo_error
                or "AssetName deserisalisation failed" in min_req_utxo_error
            ), min_req_utxo_error

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "plutus_version",
        (
            "v1",
            pytest.param("v3", marks=common.SKIPIF_PLUTUSV3_UNUSABLE),
        ),
        ids=("plutus_v1", "plutus_v3"),
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_time_range_missing_tx_validity(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test minting a token with a time constraints Plutus script and no TX validity.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Fund the token issuer and create a UTxO for collateral
        * Check that the expected amount was transferred to token issuer's address
        * Try to mint the token using a Plutus script and a TX without validity interval
        * Check that the minting failed
        """
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        plutus_v_record = plutus_common.MINTING_TIME_RANGE[plutus_version]

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_v_record.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, __ = mint_build._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Step 2: mint the "qacoin"

        slots_offset = 300
        timestamp_offset_ms = int(slots_offset * cluster.slot_length + 5) * 1_000

        # POSIX timestamp + offset
        redeemer_value = (
            int(datetime.datetime.now(tz=datetime.UTC).timestamp() * 1_000) + timestamp_offset_ms
        )

        policyid = cluster.g_transaction.get_policyid(plutus_v_record.script_file)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_v_record.script_file,
                collaterals=collateral_utxos,
                redeemer_value=str(redeemer_value),
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_step2",
                tx_files=tx_files_step2,
                txins=mint_utxos,
                txouts=txouts_step2,
                mint=plutus_mint_data,
                invalid_hereafter=None,  # Required validity interval is missing here
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "following scripts have execution failures" in exc_value  # In cli 10.1.1.0+
                or "Plutus script evaluation failed" in exc_value
            ), exc_value
