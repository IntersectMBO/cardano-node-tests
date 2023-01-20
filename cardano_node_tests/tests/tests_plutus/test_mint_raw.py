"""Tests for minting with Plutus using `transaction build-raw`."""
import datetime
import logging
import shutil
from pathlib import Path
from typing import List

import allure
import pytest
from cardano_clusterlib import clusterlib
from cardano_clusterlib import clusterlib_helpers

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_plutus import mint_raw
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

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


def _check_pretty_utxo(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput
) -> str:
    """Check that pretty printed `query utxo` output looks as expected."""
    err = ""
    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    utxo_out = (
        cluster_obj.cli(
            [
                "query",
                "utxo",
                "--tx-in",
                f"{txid}#0",
                *cluster_obj.magic_args,
            ]
        )
        .stdout.decode("utf-8")
        .split()
    )

    expected_out = [
        "TxHash",
        "TxIx",
        "Amount",
        "--------------------------------------------------------------------------------------",
        txid,
        "0",
        str(tx_raw_output.txouts[0].amount),
        tx_raw_output.txouts[0].coin,
        "+",
        str(tx_raw_output.txouts[1].amount),
        tx_raw_output.txouts[1].coin,
        "+",
        str(tx_raw_output.txouts[2].amount),
        tx_raw_output.txouts[2].coin,
        "+",
        "TxOutDatumNone",
    ]

    if utxo_out != expected_out:
        err = f"Pretty UTxO output doesn't match expected output:\n{utxo_out}\nvs\n{expected_out}"

    return err


class TestMinting:
    """Tests for minting using Plutus smart contracts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @common.PARAM_PLUTUS_VERSION
    def test_minting_two_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test minting two tokens with a single Plutus script.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the tokens using a Plutus script
        * check that the tokens were minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        fee_txsize = 600_000

        plutus_v_record = plutus_common.MINTING_PLUTUS[plutus_version]

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_v_record.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_raw_output_step1 = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
            fee_txsize=fee_txsize,
            collateral_utxo_num=2,
        )

        issuer_fund_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(plutus_v_record.script_file)
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
                script_file=plutus_v_record.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_v_record.execution_cost.per_time,
                    plutus_v_record.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
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

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

        utxo_err = _check_pretty_utxo(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)
        if utxo_err:
            pytest.fail(utxo_err)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "key",
        (
            "normal",
            "extended",
        ),
    )
    def test_witness_redeemer(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        key: str,
    ):
        """Test minting a token with a Plutus script.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script with required signer
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{key}"

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_WITNESS_REDEEMER_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        if key == "normal":
            redeemer_file = plutus_common.DATUM_WITNESS_GOLDEN_NORMAL
            signing_key_golden = plutus_common.SIGNING_KEY_GOLDEN
        else:
            redeemer_file = plutus_common.DATUM_WITNESS_GOLDEN_EXTENDED
            signing_key_golden = plutus_common.SIGNING_KEY_GOLDEN_EXTENDED

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_raw_output_step1 = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
        )

        issuer_fund_balance = cluster.g_query.get_address_balance(issuer_addr.address)

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
                execution_units=(
                    plutus_common.MINTING_WITNESS_REDEEMER_COST.per_time,
                    plutus_common.MINTING_WITNESS_REDEEMER_COST.per_space,
                ),
                redeemer_file=redeemer_file,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, signing_key_golden],
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
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
            required_signers=[signing_key_golden],
        )
        # sign incrementally (just to check that it works)
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=[issuer_addr.skey_file],
            tx_name=f"{temp_template}_step2_sign0",
        )
        tx_signed_step2_inc = cluster.g_transaction.sign_tx(
            tx_file=tx_signed_step2,
            signing_key_files=[signing_key_golden],
            tx_name=f"{temp_template}_step2_sign1",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2_inc, txins=mint_utxos)

        assert (
            cluster.g_query.get_address_balance(issuer_addr.address)
            == issuer_fund_balance - tx_raw_output_step2.fee
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_time_range_minting(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test minting a token with a time constraints Plutus script.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_TIME_RANGE_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_raw_output_step1 = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
        )

        issuer_fund_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 2: mint the "qacoin"

        slot_step2 = cluster.g_query.get_slot_no()
        slots_offset = 200
        timestamp_offset_ms = int(slots_offset * cluster.slot_length + 5) * 1_000

        protocol_version = cluster.g_query.get_protocol_params()["protocolVersion"]["major"]
        if protocol_version > 5:
            # POSIX timestamp + offset
            redeemer_value = int(datetime.datetime.now().timestamp() * 1_000) + timestamp_offset_ms
        else:
            # BUG: https://github.com/input-output-hk/cardano-node/issues/3090
            redeemer_value = 1_000_000_000_000

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
                execution_units=(
                    plutus_common.MINTING_TIME_RANGE_COST.per_time,
                    plutus_common.MINTING_TIME_RANGE_COST.per_space,
                ),
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
        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
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
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize(
        "plutus_version",
        (
            "plutus_v1",
            pytest.param("mix_v2_v1", marks=common.SKIPIF_PLUTUSV2_UNUSABLE),
        ),
    )
    def test_two_scripts_minting(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test minting two tokens with two different Plutus scripts.

        * fund the token issuer and create a UTxO for collaterals
        * check that the expected amount was transferred to token issuer's address
        * mint the tokens using two different Plutus scripts
        * check that the tokens were minted and collateral UTxOs were not spent
        * check transaction view output
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

        script_file1_v1 = plutus_common.MINTING_PLUTUS_V1
        script_file1_v2 = plutus_common.MINTING_PLUTUS_V2

        # this is higher than `plutus_common.MINTING*_COST`, because the script context has changed
        # to include more stuff
        if configuration.ALONZO_COST_MODEL or VERSIONS.cluster_era == VERSIONS.ALONZO:
            minting_cost1_v1 = plutus_common.ExecutionCost(
                per_time=408_545_501, per_space=1_126_016, fixed_cost=94_428
            )
            minting_cost2_v1 = plutus_common.ExecutionCost(
                per_time=427_707_230, per_space=1_188_952, fixed_cost=99_441
            )
        else:
            minting_cost1_v1 = plutus_common.ExecutionCost(
                per_time=297_744_405, per_space=1_126_016, fixed_cost=86_439
            )
            minting_cost2_v1 = plutus_common.ExecutionCost(
                per_time=312_830_204, per_space=1_188_952, fixed_cost=91_158
            )

        minting_cost1_v2 = plutus_common.ExecutionCost(
            per_time=185_595_199, per_space=595_446, fixed_cost=47_739
        )

        if plutus_version == "plutus_v1":
            script_file1 = script_file1_v1
            execution_cost1 = minting_cost1_v1
        elif plutus_version == "mix_v2_v1":
            script_file1 = script_file1_v2
            execution_cost1 = minting_cost1_v2
        else:
            raise AssertionError("Unknown test variant.")

        script_file2 = plutus_common.MINTING_TIME_RANGE_PLUTUS_V1

        protocol_params = cluster.g_query.get_protocol_params()
        minting_cost1 = plutus_common.compute_cost(
            execution_cost=execution_cost1, protocol_params=protocol_params
        )
        minting_cost2 = plutus_common.compute_cost(
            execution_cost=minting_cost2_v1, protocol_params=protocol_params
        )

        fee_step2_total = minting_cost1.fee + minting_cost2.fee + mint_raw.FEE_MINT_TXSIZE

        issuer_init_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount + fee_step2_total),
            # for collaterals
            clusterlib.TxOut(address=issuer_addr.address, amount=minting_cost1.collateral),
            clusterlib.TxOut(address=issuer_addr.address, amount=minting_cost2.collateral),
        ]

        tx_raw_output_step1 = cluster.g_transaction.send_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )

        issuer_step1_balance = cluster.g_query.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance
            == issuer_init_balance
            + lovelace_amount
            + fee_step2_total
            + minting_cost1.collateral
            + minting_cost2.collateral
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoins"

        txid_step1 = cluster.g_transaction.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        mint_utxos = cluster.g_query.get_utxo(txin=f"{txid_step1}#0")
        collateral_utxo1 = cluster.g_query.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxo2 = cluster.g_query.get_utxo(txin=f"{txid_step1}#2")

        slot_step2 = cluster.g_query.get_slot_no()

        # "anyone can mint" qacoin
        policyid1 = cluster.g_transaction.get_policyid(script_file1)
        asset_name1 = f"qacoina{clusterlib.get_rand_str(4)}".encode().hex()
        token1 = f"{policyid1}.{asset_name1}"
        mint_txouts1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token1)
        ]

        # "timerange" qacoin
        slots_offset = 200
        timestamp_offset_ms = int(slots_offset * cluster.slot_length + 5) * 1_000

        protocol_version = cluster.g_query.get_protocol_params()["protocolVersion"]["major"]
        if protocol_version > 5:
            # POSIX timestamp + offset
            redeemer_value_timerange = (
                int(datetime.datetime.now().timestamp() * 1_000) + timestamp_offset_ms
            )
        else:
            # BUG: https://github.com/input-output-hk/cardano-node/issues/3090
            redeemer_value_timerange = 1_000_000_000_000

        policyid2 = cluster.g_transaction.get_policyid(script_file2)
        asset_name2 = f"qacoint{clusterlib.get_rand_str(4)}".encode().hex()
        token2 = f"{policyid2}.{asset_name2}"
        mint_txouts2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token2)
        ]

        # mint the tokens
        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts1,
                script_file=script_file1,
                collaterals=collateral_utxo1,
                execution_units=(
                    execution_cost1.per_time,
                    execution_cost1.per_space,
                ),
                redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            ),
            clusterlib.Mint(
                txouts=mint_txouts2,
                script_file=script_file2,
                collaterals=collateral_utxo2,
                execution_units=(
                    minting_cost2_v1.per_time,
                    minting_cost2_v1.per_space,
                ),
                redeemer_value=str(redeemer_value_timerange),
            ),
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts1,
            *mint_txouts2,
        ]
        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=fee_step2_total,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.g_query.get_address_balance(issuer_addr.address)
            == issuer_init_balance
            + minting_cost1.collateral
            + minting_cost2.collateral
            + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output_step2)

        token_utxo1 = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token1
        )
        assert (
            token_utxo1 and token_utxo1[0].amount == token_amount
        ), "The 'anyone' token was not minted"

        token_utxo2 = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token2
        )
        assert (
            token_utxo2 and token_utxo2[0].amount == token_amount
        ), "The 'timerange' token was not minted"

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

        # check transactions in db-sync
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting_policy_executed_once1(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test that minting policy is executed only once even when the same policy is used twice.

        Test by minting two tokens while using the same Plutus script twice
        with two different redeemers.

        The Plutus script used in this test takes the expected token name as
        redeemer. Even though the redeemer used for minting the first token
        doesn't match the token name, the token gets minted anyway. That's
        because only the last redeemer is used and all the other scripts with
        identical minting policy (and corresponding redeemers) are ignored. So
        it only matters that the last redeemer matches the last token name.

        * fund the token issuer and create a UTxO for collateral - funds for fees and collateral
          are sufficient for just single minting script
        * check that the expected amount was transferred to token issuer's address
        * mint the tokens using two identical Plutus scripts and two redeemers, where the first
          redeemer value is invalid
        * check that the tokens were minted and collateral UTxOs were not spent, i.e. the first
          script and its redeemer were ignored
        * check transaction view output
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_TOKENNAME_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_raw_output_step1 = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
        )

        issuer_init_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 2: mint the "qacoins"

        policyid_tokenname = cluster.g_transaction.get_policyid(
            plutus_common.MINTING_TOKENNAME_PLUTUS_V1
        )

        # qacoinA
        asset_name_a_dec = f"qacoinA{clusterlib.get_rand_str(4)}"
        asset_name_a = asset_name_a_dec.encode("utf-8").hex()
        token_a = f"{policyid_tokenname}.{asset_name_a}"
        mint_txouts_a = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_a)
        ]

        # qacoinB
        asset_name_b_dec = f"qacoinB{clusterlib.get_rand_str(4)}"
        asset_name_b = asset_name_b_dec.encode("utf-8").hex()
        token_b = f"{policyid_tokenname}.{asset_name_b}"
        mint_txouts_b = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_b)
        ]

        # mint the tokens
        plutus_mint_data = [
            # First redeemer and first script are ignored when there are
            # multiple scripts for the same minting policy. Even though we
            # specified execution units for the script, these will not be used.
            # That's why we were able to use the costs for just single script,
            # even when we passed it twice.
            clusterlib.Mint(
                txouts=mint_txouts_a,
                script_file=plutus_common.MINTING_TOKENNAME_PLUTUS_V1,
                # execution units are too low, but it doesn't matter as they get ignored anyway
                execution_units=(1, 1),
                redeemer_value='"ignored_value"',
            ),
            clusterlib.Mint(
                txouts=mint_txouts_b,
                script_file=plutus_common.MINTING_TOKENNAME_PLUTUS_V1,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.MINTING_TOKENNAME_COST.per_time,
                    plutus_common.MINTING_TOKENNAME_COST.per_space,
                ),
                redeemer_value=f'"{asset_name_b_dec}"',
            ),
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts_a,
            *mint_txouts_b,
        ]
        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.g_query.get_address_balance(issuer_addr.address)
            == issuer_init_balance - tx_raw_output_step2.fee
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output_step2)

        token_utxo_a = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token_a
        )
        assert (
            token_utxo_a and token_utxo_a[0].amount == token_amount
        ), f"The '{asset_name_a_dec}' token was not minted"

        token_utxo_b = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token_b
        )
        assert (
            token_utxo_b and token_utxo_b[0].amount == token_amount
        ), f"The '{asset_name_b_dec}' token was not minted"

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

        # check transactions in db-sync
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting_policy_executed_once2(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ):
        """Test that minting policy is executed only once even when the same policy is used twice.

        Test minting two tokens while using one Plutus script and one redeemer.

        The Plutus script used in this test takes the expected token name as
        redeemer. Even though the redeemer doesn't match name of the first
        token, the token get's minted anyway. That's because it is only checked
        that the last token name matches the redeemer, and redeemer for the
        first token is not needed.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the tokens using a redeemer value that doesn't match the name of the first token
        * check that the tokens were minted and collateral UTxOs were not spent, i.e. redeemer for
          the first token was not needed
        * check transaction view output
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_TOKENNAME_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_raw_output_step1 = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
            collateral_utxo_num=2,
        )

        issuer_fund_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_TOKENNAME_PLUTUS_V1)

        # qacoinA
        asset_name_a_dec = f"qacoinA{clusterlib.get_rand_str(4)}"
        asset_name_a = asset_name_a_dec.encode("utf-8").hex()
        token_a = f"{policyid}.{asset_name_a}"

        # qacoinB
        asset_name_b_dec = f"qacoinB{clusterlib.get_rand_str(4)}"
        asset_name_b = asset_name_b_dec.encode("utf-8").hex()
        token_b = f"{policyid}.{asset_name_b}"

        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_a),
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_b),
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_TOKENNAME_PLUTUS_V1,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.MINTING_TOKENNAME_COST.per_time,
                    plutus_common.MINTING_TOKENNAME_COST.per_space,
                ),
                # both tokens will be minted even though the redeemer value
                # matches the name of only the second one
                redeemer_value=f'"{asset_name_b_dec}"',
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        plutus_costs = cluster.g_transaction.calculate_plutus_script_cost(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )

        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
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
        ), f"The '{asset_name_a_dec}' was not minted"

        token_utxo_b = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token_b
        )
        assert (
            token_utxo_b and token_utxo_b[0].amount == token_amount
        ), f"The '{asset_name_b_dec}' was not minted"

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[plutus_common.MINTING_TOKENNAME_COST],
        )

        # check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not shutil.which("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting_context_equivalence(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test context equivalence while minting a token.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * mint the token using the derived redeemer
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_CONTEXT_EQUIVALENCE_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_raw_output_step1 = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
        )

        issuer_fund_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 2: mint the "qacoin"

        invalid_hereafter = cluster.g_query.get_slot_no() + 200

        policyid = cluster.g_transaction.get_policyid(
            plutus_common.MINTING_CONTEXT_EQUIVALENCE_PLUTUS_V1
        )
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, plutus_common.SIGNING_KEY_GOLDEN],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        # generate a dummy redeemer in order to create a txbody from which
        # we can generate a tx and then derive the correct redeemer
        redeemer_file_dummy = Path(f"{temp_template}_dummy_script_context.redeemer")
        clusterlib_utils.create_script_context(
            cluster_obj=cluster, plutus_version=1, redeemer_file=redeemer_file_dummy
        )

        plutus_mint_data_dummy = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_CONTEXT_EQUIVALENCE_PLUTUS_V1,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.MINTING_CONTEXT_EQUIVALENCE_COST.per_time,
                    plutus_common.MINTING_CONTEXT_EQUIVALENCE_COST.per_space,
                ),
                redeemer_file=redeemer_file_dummy,
            )
        ]

        tx_output_dummy = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_dummy_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data_dummy,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
            required_signers=[plutus_common.SIGNING_KEY_GOLDEN],
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
        )
        assert tx_output_dummy

        tx_file_dummy = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_dummy.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_dummy",
        )

        # generate the "real" redeemer
        redeemer_file = Path(f"{temp_template}_script_context.redeemer")

        try:
            clusterlib_utils.create_script_context(
                cluster_obj=cluster,
                plutus_version=1,
                redeemer_file=redeemer_file,
                tx_file=tx_file_dummy,
            )
        except AssertionError as err:
            err_msg = str(err)
            if "DeserialiseFailure" in err_msg:
                pytest.xfail("DeserialiseFailure: see issue #944")
            if "TextEnvelopeTypeError" in err_msg and cluster.use_cddl:
                pytest.xfail(
                    "TextEnvelopeTypeError: `create-script-context` doesn't work with CDDL format"
                )
            else:
                raise

        plutus_mint_data = [plutus_mint_data_dummy[0]._replace(redeemer_file=redeemer_file)]

        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + mint_raw.FEE_MINT_TXSIZE,
            required_signers=[plutus_common.SIGNING_KEY_GOLDEN],
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
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
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.BABBAGE,
        reason="runs only with Babbage+ TX",
    )
    @pytest.mark.parametrize(
        "ttl_offset",
        (100, 1_000, 3_000, 10_000, 100_000, 1000_000, -1, -2),
    )
    @common.PARAM_PLUTUS_VERSION
    def test_ttl_horizon(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        ttl_offset: int,
        plutus_version: str,
    ):
        """Test minting a token with ttl far in the future.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * try to mint a token using a Plutus script when ttl is set far in the future
        * check that minting failed because of 'PastHorizon' failure when ttl is too far
          in the future
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}_{ttl_offset}"

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        fee_txsize = 600_000

        plutus_v_record = plutus_common.MINTING_PLUTUS[plutus_version]

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_v_record.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, __ = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
            fee_txsize=fee_txsize,
        )

        # Step 2: try to mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(plutus_v_record.script_file)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token),
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_v_record.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_v_record.execution_cost.per_time,
                    plutus_v_record.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        # calculate 3k/f
        offset_3kf = round(
            3 * cluster.genesis["securityParam"] / cluster.genesis["activeSlotsCoeff"]
        )

        # use 3k/f + `epoch_length` slots for ttl - this will not meet the `expect_pass` condition
        if ttl_offset == -1:
            ttl_offset = offset_3kf + cluster.epoch_length
        # use 3k/f - 100 slots for ttl - this will meet the `expect_pass` condition
        elif ttl_offset == -2:
            ttl_offset = offset_3kf - 100

        cluster.wait_for_new_block()

        last_slot_init = cluster.g_query.get_slot_no()
        slot_no_3kf = last_slot_init + offset_3kf
        invalid_hereafter = last_slot_init + ttl_offset

        ttl_epoch_info = clusterlib_helpers.get_epoch_for_slot(
            cluster_obj=cluster, slot_no=invalid_hereafter
        )

        # the TTL will pass if it's in epoch 'e' and the slot of the latest applied block + 3k/f
        # is greater than the first slot of 'e'
        expect_pass = slot_no_3kf >= ttl_epoch_info.first_slot

        tx_raw_output_step2 = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + fee_txsize,
            invalid_hereafter=invalid_hereafter,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        err = ""
        try:
            cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        except clusterlib.CLIError as exc:
            err = str(exc)

        last_slot_diff = cluster.g_query.get_slot_no() - last_slot_init
        expect_pass_finish = slot_no_3kf + last_slot_diff >= ttl_epoch_info.first_slot
        if expect_pass != expect_pass_finish:
            # we have hit a boundary and it is hard to say if the test should have passed or not
            assert not err or "TimeTranslationPastHorizon" in err, err
            pytest.skip("Boundary hit, skipping")
            return

        if err:
            assert not expect_pass, f"Valid TTL (offset {ttl_offset} slots) was rejected"
            assert "TimeTranslationPastHorizon" in err, err
        else:
            assert (
                expect_pass
            ), f"TTL too far in the future (offset {ttl_offset} slots) was accepted"


@pytest.mark.testnets
class TestCollateralOutput:
    """Tests for collateral output."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_PLUTUS_VERSION
    def test_duplicated_collateral(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Test minting a token with a Plutus script while using the same collateral input twice.

        Tests https://github.com/input-output-hk/cardano-node/issues/4744

        * fund the token issuer and create a UTxO for collateral and possibly reference script
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script and the same collateral UTxO listed twice
        * check that the token was minted and collateral UTxO was not spent
        """
        temp_template = f"{common.get_test_id(cluster)}_{plutus_version}"

        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        fee_txsize = 600_000

        plutus_v_record = plutus_common.MINTING_PLUTUS[plutus_version]

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_v_record.execution_cost,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer and create UTxOs for collaterals

        mint_utxos, collateral_utxos, *__ = mint_raw._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
            fee_txsize=fee_txsize,
            collateral_utxo_num=2,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(plutus_v_record.script_file)
        asset_name = f"qacoina{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token),
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_v_record.script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_v_record.execution_cost.per_time,
                    plutus_v_record.execution_cost.per_space,
                ),
                redeemer_cbor_file=plutus_common.REDEEMER_42_CBOR,
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
        )

        altered_build_args = tx_raw_output_step2.build_args[:]

        # add a duplicate collateral
        collateral_idx = altered_build_args.index("--tx-in-collateral") + 1
        altered_build_args.insert(collateral_idx + 1, "--tx-in-collateral")
        altered_build_args.insert(collateral_idx + 2, altered_build_args[collateral_idx])

        # change the output file
        tx_body_step2 = Path(f"{tx_raw_output_step2.out_file.stem}_altered.body")
        out_file_idx = altered_build_args.index("--out-file") + 1
        altered_build_args[out_file_idx] = str(tx_body_step2)

        # build the transaction using altered arguments
        cluster.cli(altered_build_args)

        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_body_step2,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"
