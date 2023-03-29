"""Negative tests for minting with Plutus using `transaction build`."""
import datetime
import json
import logging
from pathlib import Path
from typing import List
from typing import Tuple

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
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUS_UNUSABLE,
    common.SKIPIF_BUILD_UNUSABLE,
    pytest.mark.smoke,
    pytest.mark.plutus,
]

FundTupleT = Tuple[
    List[clusterlib.UTXOData], List[clusterlib.UTXOData], List[clusterlib.AddressRecord]
]


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new payment address."""
    test_id = common.get_test_id(cluster)
    addrs = clusterlib_utils.create_payment_addr_records(
        *[f"{test_id}_payment_addr_{i}" for i in range(2)],
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


class TestBuildMintingNegative:
    """Tests for minting with Plutus using `transaction build` that are expected to fail."""

    @pytest.fixture
    def fund_issuer_long_asset_name(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
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
    @pytest.mark.testnets
    def test_witness_redeemer_missing_signer(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test minting a token with a Plutus script with invalid signers.

        Expect failure.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * try to mint the token using a Plutus script and a TX with signing key missing for
          the required signer
        * check that the minting failed because the required signers were not provided
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_WITNESS_REDEEMER_COST,
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

        policyid = cluster.g_transaction.get_policyid(
            plutus_common.MINTING_WITNESS_REDEEMER_PLUTUS_V1
        )
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_WITNESS_REDEEMER_PLUTUS_V1,
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
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        assert "MissingRequiredSigners" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_redeemer_with_simple_minting_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test minting a token passing a redeemer for a simple minting script.

        Expect failure.

        * fund the token issuer and create a UTxO for collateral
        * try to mint the token using a simple script passing a redeemer
        * check that the minting failed because a Plutus script is expected
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
        script = Path(f"{temp_template}.script")

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

        err_str = str(excinfo.value)
        assert (
            "expected a script in the Plutus script language, but it is actually "
            "using SimpleScriptLanguage" in err_str
        ), err_str

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
    def test_asset_name_too_long(
        self,
        cluster: clusterlib.ClusterLib,
        fund_issuer_long_asset_name: FundTupleT,
        asset_name: str,
    ):
        """Test minting a token and 'calculate-min-required-utxo' with a name longer than allowed.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        lovelace_amount = 2_000_000
        token_amount = 5

        mint_utxos, collateral_utxos, payment_addrs = fund_issuer_long_asset_name

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name = asset_name.encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V2,
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

        assert (
            "the bytestring should be no longer than 32 bytes long" in min_token_error
            or "AssetName deserisalisation failed" in min_token_error
        ), min_token_error

        assert (
            "the bytestring should be no longer than 32 bytes long" in min_req_utxo_error
            or "AssetName deserisalisation failed" in min_req_utxo_error
        ), min_req_utxo_error

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    def test_time_range_missing_tx_validity(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a time constraints Plutus script and no TX validity.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * try to mint the token using a Plutus script and a TX without validity interval
        * check that the minting failed
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_TIME_RANGE_COST,
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
        redeemer_value = int(datetime.datetime.now().timestamp() * 1_000) + timestamp_offset_ms

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_TIME_RANGE_PLUTUS_V1)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_TIME_RANGE_PLUTUS_V1,
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
                invalid_hereafter=None,  # required validity interval is missing here
            )
        err_str = str(excinfo.value)
        assert "Plutus script evaluation failed" in err_str, err_str
