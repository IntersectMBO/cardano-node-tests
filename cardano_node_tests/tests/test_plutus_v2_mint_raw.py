"""Tests for minting with Plutus V2 using `transaction build-raw`."""
import json
import logging
from pathlib import Path
from typing import Any
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
from cardano_node_tests.utils import tx_view

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUSV2_UNUSABLE,
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
    reference_script: Optional[Path] = None,
    datum_file: Optional[Path] = None,
) -> Tuple[
    List[clusterlib.UTXOData],
    List[clusterlib.UTXOData],
    Optional[clusterlib.UTXOData],
    clusterlib.TxRawOutput,
]:
    """Fund the token issuer."""
    single_collateral_amount = minting_cost.collateral // collateral_utxo_num
    collateral_amounts = [single_collateral_amount for c in range(collateral_utxo_num - 1)]
    collateral_subtotal = sum(collateral_amounts)
    collateral_amounts.append(minting_cost.collateral - collateral_subtotal)

    issuer_init_balance = cluster_obj.get_address_balance(issuer_addr.address)

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    # for reference script
    reference_amount = 0
    txouts_reference = []
    if reference_script:
        reference_amount = 20_000_000
        txouts_reference = [
            clusterlib.TxOut(
                address=issuer_addr.address,
                amount=reference_amount,
                reference_script_file=reference_script,
                datum_hash_file=datum_file if datum_file else "",
            )
        ]

    txouts_collateral = [
        clusterlib.TxOut(address=issuer_addr.address, amount=a) for a in collateral_amounts
    ]

    txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=amount + minting_cost.fee + fee_txsize,
        ),
        *txouts_reference,
        *txouts_collateral,
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
        == issuer_init_balance
        + amount
        + minting_cost.fee
        + fee_txsize
        + minting_cost.collateral
        + reference_amount
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    txid = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)
    mint_utxos = cluster_obj.get_utxo(txin=f"{txid}#0")

    reference_utxo = None
    if reference_script:
        reference_utxos = cluster_obj.get_utxo(txin=f"{txid}#1")
        assert reference_utxos, "No reference script UTxO"
        reference_utxo = reference_utxos[0]

    collateral_utxos = [
        clusterlib.UTXOData(utxo_hash=txid, utxo_ix=idx, amount=a, address=issuer_addr.address)
        for idx, a in enumerate(collateral_amounts, start=len(txouts) - len(txouts_collateral))
    ]

    return mint_utxos, collateral_utxos, reference_utxo, tx_raw_output


def _build_reference_txin(
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    amount: int,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: Optional[clusterlib.AddressRecord] = None,
    datum_file: Optional[Path] = None,
) -> List[clusterlib.UTXOData]:
    """Create a basic txin to use as readonly reference input.

    Uses `cardano-cli transaction build-raw` command for building the transaction.
    """
    dst_addr = dst_addr or cluster.gen_payment_addr_and_keys(name=f"{temp_template}_readonly_input")

    txouts = [
        clusterlib.TxOut(
            address=dst_addr.address,
            amount=amount,
            datum_hash_file=datum_file if datum_file else "",
        )
    ]
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    tx_raw_output = cluster.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
    )

    txid = cluster.get_txid(tx_body_file=tx_raw_output.out_file)

    reference_txin = cluster.get_utxo(txin=f"{txid}#0")
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
            protocol_params=cluster.get_protocol_params(),
        )

        # Step 1: fund the token issuer

        mint_utxos, collateral_utxos, reference_utxo, __ = _fund_issuer(
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

        issuer_fund_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name_a = f"qacoina{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token_a = f"{policyid}.{asset_name_a}"
        asset_name_b = f"qacoinb{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
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
        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + fee_txsize,
            # ttl is optional in this test
            invalid_hereafter=cluster.get_slot_no() + 200,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_fund_balance - tx_raw_output_step2.fee
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo_a = cluster.get_utxo(address=issuer_addr.address, coins=[token_a])
        assert (
            token_utxo_a and token_utxo_a[0].amount == token_amount
        ), "The 'token a' was not minted"

        token_utxo_b = cluster.get_utxo(address=issuer_addr.address, coins=[token_b])
        assert (
            token_utxo_b and token_utxo_b[0].amount == token_amount
        ), "The 'token b' was not minted"

        # check tx view
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "scenario",
        ("reference_script", "readonly_reference_input", "different_datum"),
        ids=("reference_script", "readonly_reference_input", "different_datum"),
    )
    def test_datum_hash_visibility(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        scenario: str,
        request: FixtureRequest,
    ):
        """
        Test check visibility of datum hash on reference inputs by the plutus script.

        * create the necessary Tx outputs
        * mint the token and check that the plutus script have visibility of the datum hash
        * check that the token was minted
        * check that the reference UTxO was not spent
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        script_fund = 200_000_000
        token_amount = 5
        fee_txsize = 600_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_CHECK_DATUM_HASH_COST,
            protocol_params=cluster.get_protocol_params(),
        )

        # Step 1: fund the token issuer and create the reference script

        mint_utxos, collateral_utxos, reference_utxo, __ = _fund_issuer(
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

        reference_input = ()
        if with_reference_input or different_datum:
            reference_input = _build_reference_txin(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                amount=lovelace_amount,
                datum_file=datum_file,
            )  # type: ignore

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(plutus_common.MINTING_CHECK_DATUM_HASH_PLUTUS_V2)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"

        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        datum_hash = cluster.get_hash_script_data(script_data_file=plutus_common.DATUM_42)

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
                cluster.send_tx(
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

        cluster.send_tx(
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
        token_utxo = cluster.get_utxo(address=issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.get_utxo(
            utxo=reference_utxo
        ), "Reference UTxO was spent"


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
            amount=lovelace_amount,
        )

        # Step 2: mint the "qacoin"

        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=mint_utxos[0].utxo_hash,
            utxo_ix=1,
            amount=collateral_amount,
            address=issuer_addr.address,
        )

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
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5

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
            amount=lovelace_amount,
        )

        # Step 2: mint the "qacoin"

        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=mint_utxos[0].utxo_hash,
            utxo_ix=1,
            amount=minting_cost.collateral,
            address=issuer_addr.address,
        )

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

        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            return_collateral_txouts=return_collateral_txouts if with_return_collateral else (),
            total_collateral_amount=minting_cost.collateral // 2,
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

        # it should NOT be possible to mint with an unbalanced total collateral
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        err_str = str(excinfo.value)
        assert "IncorrectTotalCollateralField" in err_str, err_str


@pytest.mark.testnets
class TestSECP256k1:
    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "test_vector",
        ("positive", "invalid_sig", "invalid_pubkey", "no_msg", "no_pubkey", "no_sig"),
    )
    @pytest.mark.parametrize("algorithm", ("ecdsa", "schnorr"))
    def test_use_secp_builtin_functions(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        test_vector: str,
        algorithm: str,
    ):
        """Test that the two SECP256k1 builtin functions are impossible to use.

        * lock some funds with the dedicated plutus script
        * try to spend the locked UTxO
        * check that is not possible to use SECP256k1
        """
        # pylint: disable=too-many-locals
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{test_vector}_{algorithm}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        fee_txsize = 600_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_REF_COST,
            protocol_params=cluster.get_protocol_params(),
        )

        script_file = (
            plutus_common.SECP256K1_ECDSA_PLUTUS_V2
            if algorithm == "ecdsa"
            else plutus_common.SECP256K1_SCHNORR_PLUTUS_V2
        )

        mint_utxos, collateral_utxos, __, __ = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=lovelace_amount,
            fee_txsize=fee_txsize,
        )

        redeemer_dir = (
            plutus_common.SEPC256K1_ECDSA_DIR
            if algorithm == "ecdsa"
            else plutus_common.SEPC256K1_SCHNORR_DIR
        )

        redeemer_file = redeemer_dir / f"{test_vector}.redeemer"

        # Step 2: mint the "qacoin"

        policyid = cluster.get_policyid(script_file)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                script_file=script_file,
                collaterals=collateral_utxos,
                execution_units=(
                    plutus_common.MINTING_V2_REF_COST.per_time,
                    plutus_common.MINTING_V2_REF_COST.per_space,
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

        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            tx_files=tx_files_step2,
            fee=minting_cost.fee + fee_txsize,
        )

        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        protocol_version = cluster.get_protocol_params()["protocolVersion"]["major"]

        try:
            cluster.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)
        except clusterlib.CLIError as err:
            err_msg = str(err)

            # before protocol_version 8 the SECP256k1 is blocked
            # after that the usage is limited by high cost model

            is_forbidden = "MalformedScriptWitnesses" in err_msg

            is_overspending = (
                "The machine terminated part way through evaluation due to "
                "overspending the budget." in err_msg
            )

            if (is_forbidden or is_overspending) and protocol_version < 8:
                pytest.xfail(
                    "The SECP256k1 builtin functions are not allowed before protocol version 8"
                )
            raise
