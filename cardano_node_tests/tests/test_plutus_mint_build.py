"""Tests for minting with Plutus using `transaction build`."""
import datetime
import logging
import shutil
from pathlib import Path
from typing import List
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# skip all tests if Tx era < alonzo
pytestmark = [
    pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALONZO,
        reason="runs only with Alonzo+ TX",
    ),
    pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
    pytest.mark.smoke,
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


def _fund_issuer(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    minting_cost: plutus_common.ScriptCost,
    amount: int,
    collateral_utxo_num: int = 1,
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Fund the token issuer."""
    single_collateral_amount = minting_cost.collateral // collateral_utxo_num
    collateral_amounts = [single_collateral_amount for c in range(collateral_utxo_num - 1)]
    collateral_subtotal = sum(collateral_amounts)
    collateral_amounts.append(minting_cost.collateral - collateral_subtotal)

    issuer_init_balance = cluster_obj.get_address_balance(issuer_addr.address)

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=amount,
        ),
        *[clusterlib.TxOut(address=issuer_addr.address, amount=a) for a in collateral_amounts],
    ]
    tx_output = cluster_obj.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=2_000_000,
        # don't join 'change' and 'collateral' txouts, we need separate UTxOs
        join_txouts=False,
    )
    tx_signed = cluster_obj.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )
    cluster_obj.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    issuer_balance = cluster_obj.get_address_balance(issuer_addr.address)
    assert (
        issuer_balance == issuer_init_balance + amount + minting_cost.collateral
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    txid = cluster_obj.get_txid(tx_body_file=tx_output.out_file)
    mint_utxos = cluster_obj.get_utxo(txin=f"{txid}#1")
    collateral_utxos = [
        clusterlib.UTXOData(utxo_hash=txid, utxo_ix=idx, amount=a, address=issuer_addr.address)
        for idx, a in enumerate(collateral_amounts, start=2)
    ]

    return mint_utxos, collateral_utxos, tx_output


class TestBuildMinting:
    """Tests for minting using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting_one_token(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a Plutus script.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script
        * check that the token was minted and collateral UTxO was not spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_COST, protocol_params=cluster.get_protocol_params()
        )

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer and create UTXO for collaterals

        mint_utxos, collateral_utxos, tx_output_step1 = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
            collateral_utxo_num=2,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(plutus_common.MINTING_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS,
                collaterals=collateral_utxos,
                redeemer_file=plutus_common.REDEEMER_42,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )
        plutus_cost = cluster.calculate_plutus_script_cost(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + minting_cost.collateral + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        # check expected fees
        expected_fee_step1 = 168_977
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 371_111
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[plutus_common.MINTING_COST],
        )

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_step2)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_time_range_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a time constraints Plutus script.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script
        * check that the token was minted and collateral UTxO was not spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
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
            protocol_params=cluster.get_protocol_params(),
        )

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_output_step1 = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Step 2: mint the "qacoin"

        slot_step2 = cluster.get_slot_no()
        slots_offset = 200
        timestamp_offset_ms = int(slots_offset * cluster.slot_length + 5) * 1_000

        protocol_version = cluster.get_protocol_params()["protocolVersion"]["major"]
        if protocol_version > 5:
            # POSIX timestamp + offset
            redeemer_value = int(datetime.datetime.now().timestamp() * 1_000) + timestamp_offset_ms
        else:
            # BUG: https://github.com/input-output-hk/cardano-node/issues/3090
            redeemer_value = 1_000_000_000_000

        policyid = cluster.get_policyid(plutus_common.MINTING_TIME_RANGE_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_TIME_RANGE_PLUTUS,
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
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
        )
        plutus_cost = cluster.calculate_plutus_script_cost(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + minting_cost.collateral + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        # check expected fees
        expected_fee_step1 = 167_349
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 411_175
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[plutus_common.MINTING_TIME_RANGE_COST],
        )

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_step2)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_two_scripts_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting two tokens with two different Plutus scripts.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collaterals
        * check that the expected amount was transferred to token issuer's address
        * mint the tokens using two different Plutus scripts
        * check that the tokens were minted and collateral UTxOs were not spent
        * check transaction view output
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 500_000_000

        # this is higher than `plutus_common.MINTING*_COST`, because the script context has changed
        # to include more stuff
        minting_cost_two = plutus_common.ExecutionCost(
            per_time=408_545_501, per_space=1_126_016, fixed_cost=94_428
        )
        minting_time_range_cost_two = plutus_common.ExecutionCost(
            per_time=427_707_230, per_space=1_188_952, fixed_cost=99_441
        )

        protocol_params = cluster.get_protocol_params()
        minting_cost1 = plutus_common.compute_cost(
            execution_cost=minting_cost_two, protocol_params=protocol_params
        )
        minting_cost2 = plutus_common.compute_cost(
            execution_cost=minting_time_range_cost_two, protocol_params=protocol_params
        )

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=script_fund),
            # for collaterals
            clusterlib.TxOut(address=issuer_addr.address, amount=minting_cost1.collateral),
            clusterlib.TxOut(address=issuer_addr.address, amount=minting_cost2.collateral),
        ]
        tx_output_step1 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            txouts=txouts_step1,
            fee_buffer=2_000_000,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance
            == issuer_init_balance
            + script_fund
            + minting_cost1.collateral
            + minting_cost2.collateral
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoins"

        txid_step1 = cluster.get_txid(tx_body_file=tx_output_step1.out_file)
        mint_utxos = cluster.get_utxo(txin=f"{txid_step1}#1")
        collateral_utxo1 = cluster.get_utxo(txin=f"{txid_step1}#2")
        collateral_utxo2 = cluster.get_utxo(txin=f"{txid_step1}#3")

        slot_step2 = cluster.get_slot_no()

        # "time range" qacoin
        slots_offset = 200
        timestamp_offset_ms = int(slots_offset * cluster.slot_length + 5) * 1_000

        protocol_version = cluster.get_protocol_params()["protocolVersion"]["major"]
        if protocol_version > 5:
            # POSIX timestamp + offset
            redeemer_value_timerange = (
                int(datetime.datetime.now().timestamp() * 1_000) + timestamp_offset_ms
            )
        else:
            # BUG: https://github.com/input-output-hk/cardano-node/issues/3090
            redeemer_value_timerange = 1_000_000_000_000

        policyid_timerange = cluster.get_policyid(plutus_common.MINTING_TIME_RANGE_PLUTUS)
        asset_name_timerange = f"qacoint{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token_timerange = f"{policyid_timerange}.{asset_name_timerange}"
        mint_txouts_timerange = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_timerange)
        ]

        # "anyone can mint" qacoin
        redeemer_cbor_file = plutus_common.REDEEMER_42_CBOR
        policyid_anyone = cluster.get_policyid(plutus_common.MINTING_PLUTUS)
        asset_name_anyone = f"qacoina{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token_anyone = f"{policyid_anyone}.{asset_name_anyone}"
        mint_txouts_anyone = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token_anyone)
        ]

        # mint the tokens
        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts_timerange,
                script_file=plutus_common.MINTING_TIME_RANGE_PLUTUS,
                collaterals=collateral_utxo1,
                redeemer_value=str(redeemer_value_timerange),
            ),
            clusterlib.Mint(
                txouts=mint_txouts_anyone,
                script_file=plutus_common.MINTING_PLUTUS,
                collaterals=collateral_utxo2,
                redeemer_cbor_file=redeemer_cbor_file,
            ),
        ]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts_timerange,
            *mint_txouts_anyone,
        ]
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
        )
        plutus_cost = cluster.calculate_plutus_script_cost(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            invalid_before=slot_step2 - slots_offset,
            invalid_hereafter=slot_step2 + slots_offset,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance
            + minting_cost1.collateral
            + minting_cost2.collateral
            + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo_timerange = cluster.get_utxo(
            address=issuer_addr.address, coins=[token_timerange]
        )
        assert (
            token_utxo_timerange and token_utxo_timerange[0].amount == token_amount
        ), "The 'timerange' token was not minted"

        token_utxo_anyone = cluster.get_utxo(address=issuer_addr.address, coins=[token_anyone])
        assert (
            token_utxo_anyone and token_utxo_anyone[0].amount == token_amount
        ), "The 'anyone' token was not minted"

        # check expected fees
        expected_fee_step1 = 168_977
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 633_269
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[minting_cost_two, minting_time_range_cost_two],
        )

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_step2)

        # check transactions in db-sync
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        not shutil.which("create-script-context"),
        reason="cannot find `create-script-context` on the PATH",
    )
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting_context_equivalance(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test context equivalence while minting a token.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * generate a dummy redeemer and a dummy Tx
        * derive the correct redeemer from the dummy Tx
        * mint the token using the derived redeemer
        * check that the token was minted and collateral UTxO was not spent
        * check expected Plutus cost
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_CONTEXT_EQUIVALENCE_COST,
            protocol_params=cluster.get_protocol_params(),
        )

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_output_step1 = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Step 2: mint the "qacoin"

        invalid_hereafter = cluster.get_slot_no() + 1_000

        policyid = cluster.get_policyid(plutus_common.MINTING_CONTEXT_EQUIVALENCE_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
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
            cluster_obj=cluster, redeemer_file=redeemer_file_dummy
        )

        plutus_mint_data_dummy = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_CONTEXT_EQUIVALENCE_PLUTUS,
                collaterals=collateral_utxos,
                redeemer_file=redeemer_file_dummy,
            )
        ]

        tx_output_dummy = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_dummy",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data_dummy,
            required_signers=[plutus_common.SIGNING_KEY_GOLDEN],
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
            script_valid=False,
        )
        assert tx_output_dummy

        tx_file_dummy = cluster.sign_tx(
            tx_body_file=tx_output_dummy.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_dummy",
        )

        # generate the "real" redeemer
        redeemer_file = Path(f"{temp_template}_script_context.redeemer")

        try:
            clusterlib_utils.create_script_context(
                cluster_obj=cluster, redeemer_file=redeemer_file, tx_file=tx_file_dummy
            )
        except AssertionError as err:
            err_msg = str(err)
            if "DeserialiseFailure" in err_msg:
                pytest.xfail("DeserialiseFailure: see issue #944")
            if "TextEnvelopeTypeError" in err_msg and cluster.use_cddl:  # noqa: SIM106
                pytest.xfail(
                    "TextEnvelopeTypeError: `create-script-context` doesn't work with CDDL format"
                )
            else:
                raise

        plutus_mint_data = [plutus_mint_data_dummy[0]._replace(redeemer_file=redeemer_file)]

        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            required_signers=[plutus_common.SIGNING_KEY_GOLDEN],
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

        # calculate cost of Plutus script
        plutus_cost_step2 = cluster.calculate_plutus_script_cost(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            required_signers=[plutus_common.SIGNING_KEY_GOLDEN],
            invalid_before=1,
            invalid_hereafter=invalid_hereafter,
        )

        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + minting_cost.collateral + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost_step2,
            expected_cost=[plutus_common.MINTING_CONTEXT_EQUIVALENCE_COST],
        )

        # check tx_view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_output_step2)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        tx_db_record_step2 = dbsync_utils.check_tx(
            cluster_obj=cluster, tx_raw_output=tx_output_step2
        )
        # compare cost of Plutus script with data from db-sync
        if tx_db_record_step2:
            dbsync_utils.check_plutus_cost(
                redeemer_record=tx_db_record_step2.redeemers[0], cost_record=plutus_cost_step2[0]
            )

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

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script with required signer
        * check that the token was minted and collateral UTxO was not spent
        * check expected fees
        * check expected Plutus cost
        * (optional) check transactions in db-sync
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
            protocol_params=cluster.get_protocol_params(),
        )

        if key == "normal":
            redeemer_file = plutus_common.DATUM_WITNESS_GOLDEN_NORMAL
            signing_key_golden = plutus_common.SIGNING_KEY_GOLDEN
        else:
            redeemer_file = plutus_common.DATUM_WITNESS_GOLDEN_EXTENDED
            signing_key_golden = plutus_common.SIGNING_KEY_GOLDEN_EXTENDED

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, tx_output_step1 = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(plutus_common.MINTING_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS,
                collaterals=collateral_utxos,
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
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            required_signers=[signing_key_golden],
        )
        plutus_cost = cluster.calculate_plutus_script_cost(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            required_signers=[signing_key_golden],
        )
        # sign incrementally (just to check that it works)
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=[issuer_addr.skey_file],
            tx_name=f"{temp_template}_step2_sign0",
        )
        tx_signed_step2_inc = cluster.sign_tx(
            tx_file=tx_signed_step2,
            signing_key_files=[signing_key_golden],
            tx_name=f"{temp_template}_step2_sign1",
        )
        cluster.submit_tx(tx_file=tx_signed_step2_inc, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + minting_cost.collateral + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        # check expected fees
        expected_fee_step1 = 167_349
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        expected_fee_step2 = 372_438
        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[plutus_common.MINTING_WITNESS_REDEEMER_COST],
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output_step2)


class TestBuildMintingNegative:
    """Tests for minting with Plutus using `transaction build` that are expected to fail."""

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
            protocol_params=cluster.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, __ = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(plutus_common.MINTING_PLUTUS)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS,
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
        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            required_signers=[plutus_common.SIGNING_KEY_GOLDEN],
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        assert "MissingRequiredSigners" in str(excinfo.value)
