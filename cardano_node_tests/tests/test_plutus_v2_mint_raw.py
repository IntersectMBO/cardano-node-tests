"""Tests for minting with Plutus V2 using `transaction build-raw`."""
import logging
from typing import List
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = [
    pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.BABBAGE,
        reason="runs only with Babbage+ TX",
    ),
    pytest.mark.smoke,
]

# approx. fee for Tx size
FEE_MINT_TXSIZE = 400_000


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
    fee_txsize: int = FEE_MINT_TXSIZE,
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
            amount=amount + minting_cost.fee + fee_txsize,
        ),
        *[clusterlib.TxOut(address=issuer_addr.address, amount=a) for a in collateral_amounts],
    ]

    tx_raw_output = cluster_obj.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=2,
        # don't join 'change' and 'collateral' txouts, we need separate UTxOs
        join_txouts=False,
    )

    issuer_balance = cluster_obj.get_address_balance(issuer_addr.address)
    assert (
        issuer_balance
        == issuer_init_balance + amount + minting_cost.fee + fee_txsize + minting_cost.collateral
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    txid = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)
    mint_utxos = cluster_obj.get_utxo(txin=f"{txid}#0")
    collateral_utxos = [
        clusterlib.UTXOData(utxo_hash=txid, utxo_ix=idx, amount=a, address=issuer_addr.address)
        for idx, a in enumerate(collateral_amounts, start=1)
    ]

    return mint_utxos, collateral_utxos, tx_raw_output


@pytest.mark.testnets
class TestNegativeCollateralOutput:
    """Tests for collateral output that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    def test_minting_with_limited_collateral(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a Plutus script with limited collateral amount.

        Expect failure.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script while limiting the usable collateral amount
        * check that the minting failed because insufficient collateral amount was provided
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        collateral_amount = 2_000_000
        token_amount = 5

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_COST, protocol_params=cluster.get_protocol_params()
        )

        # Step 1: fund the token issuer

        mint_utxos, *__ = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
        )

        # Step 2: mint the "qacoin"

        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=mint_utxos[0].utxo_hash,
            utxo_ix=1,
            amount=collateral_amount,
            address=issuer_addr.address,
        )

        policyid = cluster.get_policyid(plutus_common.MINTING_PLUTUS_V1)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V1,
                collaterals=[collateral_utxo],
                execution_units=(
                    plutus_common.MINTING_COST.per_time,
                    plutus_common.MINTING_COST.per_space,
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

        # limit the amount of collateral that can be used and balance the return collateral txout
        total_collateral_amount = minting_cost.min_collateral // 2
        return_collateral_txouts = [
            clusterlib.TxOut(
                payment_addr.address, amount=collateral_amount - total_collateral_amount
            )
        ]

        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + FEE_MINT_TXSIZE,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # it should NOT be possible to mint with a collateral with insufficient funds
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        err_str = str(excinfo.value)
        assert "InsufficientCollateral" in err_str, err_str
