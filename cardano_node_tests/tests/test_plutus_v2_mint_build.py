"""Tests for minting with Plutus V2 using `transaction build`."""
import logging
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

import allure
import pytest
from _pytest.fixtures import FixtureRequest
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
    reference_script: Optional[Path] = None,
) -> Tuple[
    List[clusterlib.UTXOData],
    List[clusterlib.UTXOData],
    Optional[clusterlib.UTXOData],
    clusterlib.TxRawOutput,
]:
    """Fund the token issuer."""
    issuer_init_balance = cluster_obj.get_address_balance(issuer_addr.address)

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=amount,
        ),
        # for collateral
        clusterlib.TxOut(address=issuer_addr.address, amount=minting_cost.collateral),
    ]

    reference_amount = 0
    if reference_script:
        reference_amount = 10_000_000
        # for reference UTxO
        txouts.append(
            clusterlib.TxOut(
                address=issuer_addr.address,
                amount=reference_amount,
                reference_script_file=reference_script,
            )
        )

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
        issuer_balance == issuer_init_balance + amount + minting_cost.collateral + reference_amount
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    txid = cluster_obj.get_txid(tx_body_file=tx_output.out_file)
    mint_utxos = cluster_obj.get_utxo(txin=f"{txid}#1")
    collateral_utxos = cluster_obj.get_utxo(txin=f"{txid}#2")

    reference_utxo = None
    if reference_script:
        reference_utxos = cluster_obj.get_utxo(txin=f"{txid}#3")
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    return mint_utxos, collateral_utxos, reference_utxo, tx_output


@pytest.mark.testnets
class TestBuildMinting:
    """Tests for minting using Plutus smart contracts and `transaction build`."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_reference_script", (True, False), ids=("reference_script", "script_file")
    )
    def test_minting_one_token(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_reference_script: bool,
        request: FixtureRequest,
    ):
        """Test minting a token with a Plutus script.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund the token issuer and create a UTxO for collateral and possibly reference script
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a Plutus script
        * check that the token was minted and collateral UTxO was not spent
        * check expected fees
        * check expected Plutus cost
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_COST,
            protocol_params=cluster.get_protocol_params(),
        )

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer and create UTxOs for collaterals and reference script

        mint_utxos, collateral_utxos, reference_utxo, tx_output_step1 = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
            reference_script=plutus_common.MINTING_PLUTUS_V2 if use_reference_script else None,
        )
        assert reference_utxo or not use_reference_script, "No reference script UTxO"

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V2 if not use_reference_script else "",
                reference_txin=reference_utxo if use_reference_script else None,
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

        assert cluster.get_address_balance(
            issuer_addr.address
        ) == issuer_init_balance + minting_cost.collateral + lovelace_amount + (
            reference_utxo.amount if reference_utxo else 0
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # TODO: query single UTxO
        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.get_utxo(
            utxo=reference_utxo
        ), "Reference UTxO was spent"

        # check expected fees
        if use_reference_script:
            expected_fee_step1 = 252_929
            expected_fee_step2 = 230_646
            expected_cost = plutus_common.MINTING_V2_REF_COST
        else:
            expected_fee_step1 = 167_437
            expected_fee_step2 = 304_694
            expected_cost = plutus_common.MINTING_V2_COST

        assert helpers.is_in_interval(tx_output_step2.fee, expected_fee_step2, frac=0.15)
        assert helpers.is_in_interval(tx_output_step1.fee, expected_fee_step1, frac=0.15)

        plutus_common.check_plutus_cost(
            plutus_cost=plutus_cost,
            expected_cost=[expected_cost],
        )


@pytest.mark.testnets
class TestNegativeCollateralOutput:
    """Tests for collateral output that are expected to fail."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "with_return_collateral",
        (True, False),
        ids=("with_return_collateral", "without_return_collateral"),
    )
    def test_minting_with_unbalanced_total_collateral(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        with_return_collateral: bool,
        request: FixtureRequest,
    ):
        """Test minting a token with a Plutus script with unbalanced total collateral.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_COST,
            protocol_params=cluster.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, *__ = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
        )

        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=mint_utxos[0].utxo_hash,
            utxo_ix=2,
            amount=minting_cost.collateral,
            address=issuer_addr.address,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=plutus_common.MINTING_PLUTUS_V2,
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

        return_collateral_txouts = [
            clusterlib.TxOut(payment_addr.address, amount=minting_cost.collateral)
        ]

        tx_output_step2 = cluster.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            return_collateral_txouts=return_collateral_txouts if with_return_collateral else (),
            total_collateral_amount=minting_cost.collateral // 2,
            mint=plutus_mint_data,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # it should NOT be possible to mint with an unbalanced total collateral
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        err_str = str(excinfo.value)
        assert "IncorrectTotalCollateralField" in err_str, err_str
