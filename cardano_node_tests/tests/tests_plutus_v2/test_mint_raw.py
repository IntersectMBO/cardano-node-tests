"""Tests for minting with Plutus V2 using `transaction build-raw`."""
import json
import logging
from pathlib import Path
from typing import List
from typing import Optional

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus_v2 import mint_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUSV2_UNUSABLE,
    pytest.mark.smoke,
    pytest.mark.plutus,
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


def _build_reference_txin(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    amount: int,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: Optional[clusterlib.AddressRecord] = None,
    datum_file: Optional[Path] = None,
) -> List[clusterlib.UTXOData]:
    """Create a basic txin to use as readonly reference input.

    Uses `cardano-cli transaction build-raw` command for building the transaction.
    """
    dst_addr = dst_addr or cluster_obj.g_address.gen_payment_addr_and_keys(
        name=f"{temp_template}_readonly_input"
    )

    txouts = [
        clusterlib.TxOut(
            address=dst_addr.address,
            amount=amount,
            datum_hash_file=datum_file if datum_file else "",
        )
    ]
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    tx_raw_output = cluster_obj.g_transaction.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
    )

    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    reference_txin = cluster_obj.g_query.get_utxo(txin=f"{txid}#0")
    assert reference_txin, "UTxO not created"

    return reference_txin


@pytest.mark.testnets
class TestMinting:
    """Tests for minting using Plutus smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_reference_script", (True, False), ids=("reference_script", "script_file")
    )
    def test_minting_two_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_reference_script: bool,
        request: FixtureRequest,
    ):
        """Test minting two tokens with a single Plutus script.

        * fund the token issuer and create a UTxO for collateral and possibly reference script
        * check that the expected amount was transferred to token issuer's address
        * mint the tokens using a Plutus script
        * check that the tokens were minted and collateral UTxO was not spent
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        fee_txsize = 600_000

        if use_reference_script:
            execution_cost = plutus_common.MINTING_V2_REF_COST
        else:
            execution_cost = plutus_common.MINTING_V2_COST

        minting_cost = plutus_common.compute_cost(
            execution_cost=execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, reference_utxo, __ = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
            fee_txsize=fee_txsize,
            collateral_utxo_num=2,
            reference_script=plutus_common.MINTING_PLUTUS_V2,
        )
        assert reference_utxo or not use_reference_script, "No reference script UTxO"

        issuer_fund_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name_a = f"qacoina{clusterlib.get_rand_str(4)}".encode().hex()
        token_a = f"{policyid}.{asset_name_a}"
        asset_name_b = f"qacoinb{clusterlib.get_rand_str(4)}".encode().hex()
        token_b = f"{policyid}.{asset_name_b}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_a),
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_b),
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V2 if not use_reference_script else "",
                reference_txin=reference_utxo if use_reference_script else None,
                collaterals=collateral_utxos,
                execution_units=(
                    execution_cost.per_time,
                    execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
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
        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + fee_txsize,
            # ttl is optional in this test
            invalid_hereafter=cluster.g_query.get_slot_no() + 200,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.g_query.get_address_balance(issuer_addr.address)
            == issuer_fund_balance - tx_raw_output_step2.fee
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output_step2)

        token_utxo_a = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token_a
        )
        assert (
            token_utxo_a and token_utxo_a[0].amount == token_amount
        ), "The 'token a' was not minted"

        token_utxo_b = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token_b
        )
        assert (
            token_utxo_b and token_utxo_b[0].amount == token_amount
        ), "The 'token b' was not minted"

        # check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "scenario", ("reference_script", "readonly_reference_input", "different_datum")
    )
    def test_datum_hash_visibility(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        scenario: str,
    ):
        """Test visibility of datum hash on reference inputs by the plutus script.

        * create needed Tx outputs
        * mint token and check that plutus script have visibility of the datum hash
        * check that the token was minted
        * check that the reference UTxO was not spent
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{scenario}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        script_fund = 200_000_000
        token_amount = 5
        fee_txsize = 600_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_CHECK_DATUM_HASH_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer and create the reference script

        mint_utxos, collateral_utxos, reference_utxo, __ = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
            fee_txsize=fee_txsize,
            reference_script=plutus_common.MINTING_CHECK_DATUM_HASH_PLUTUS_V2,
            datum_file=plutus_common.DATUM_42,
        )

        # to check datum hash on readonly reference input
        with_reference_input = scenario != "reference_script"
        different_datum = scenario == "different_datum"
        datum_file = plutus_common.DATUM_43_TYPED if different_datum else plutus_common.DATUM_42

        reference_input = []
        if with_reference_input or different_datum:
            reference_input = _build_reference_txin(
                cluster_obj=cluster,
                temp_template=temp_template,
                payment_addr=payment_addrs[0],
                amount=lovelace_amount,
                datum_file=datum_file,
            )

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(
            plutus_common.MINTING_CHECK_DATUM_HASH_PLUTUS_V2
        )
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"

        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        datum_hash = cluster.g_transaction.get_hash_script_data(
            script_data_file=plutus_common.DATUM_42
        )

        # the redeemer file will be composed by the datum hash
        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump({"bytes": datum_hash}, outfile)

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                reference_txin=reference_utxo,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.MINTING_V2_CHECK_DATUM_HASH_COST.per_time,
                    plutus_common.MINTING_V2_CHECK_DATUM_HASH_COST.per_space,
                ),
                redeemer_file=redeemer_file,
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

        # the plutus script checks if all reference inputs have the same datum hash
        # it will fail if the datums hash are not the same in all reference inputs and
        # succeed if all datums hash match
        if different_datum:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster.g_transaction.send_tx(
                    src_address=payment_addr.address,
                    tx_name=f"{temp_template}_step2",
                    tx_files=tx_files_step2,
                    fee=minting_cost.fee + fee_txsize,
                    txins=mint_utxos,
                    txouts=txouts_step2,
                    mint=plutus_mint_data,
                    readonly_reference_txins=reference_input,
                )

            err_str = str(excinfo.value)
            try:
                assert "Unexpected datum hash at each reference input" in err_str, err_str
            except AssertionError:
                if "The machine terminated because of an error" in err_str:
                    pytest.xfail(
                        "PlutusDebug doesn't return the evaluation error from "
                        "plutus - see node issue #4488"
                    )

            return

        tx_raw_output = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            fee=minting_cost.fee + fee_txsize,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            readonly_reference_txins=reference_input,
        )

        # check that the token was minted
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.g_query.get_utxo(
            utxo=reference_utxo
        ), "Reference UTxO was spent"
