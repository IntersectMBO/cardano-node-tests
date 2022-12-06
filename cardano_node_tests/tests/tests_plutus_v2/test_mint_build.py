"""Tests for minting with Plutus V2 using `transaction build`."""
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
from cardano_node_tests.tests.tests_plutus_v2 import mint_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_BUILD_UNUSABLE,
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
    temp_template: str,
    cluster: clusterlib.ClusterLib,
    amount: int,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: Optional[clusterlib.AddressRecord] = None,
    inline_datum: Optional[Path] = None,
) -> List[clusterlib.UTXOData]:
    """Create a basic txin to use as readonly reference input.

    Uses `cardano-cli transaction build` command for building the transaction.
    """
    dst_addr = dst_addr or cluster.g_address.gen_payment_addr_and_keys(
        name=f"{temp_template}_readonly_input"
    )

    txouts = [
        clusterlib.TxOut(
            address=dst_addr.address,
            amount=amount,
            inline_datum_file=inline_datum if inline_datum else "",
        )
    ]
    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

    tx_output = cluster.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=temp_template,
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=1_000_000,
    )
    tx_signed = cluster.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=temp_template,
    )
    cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)

    reference_txin = clusterlib.filter_utxos(utxos=out_utxos, utxo_ix=utxo_ix_offset)
    assert reference_txin, "UTxO not created"

    return reference_txin


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
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        issuer_init_balance = cluster.g_query.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer and create UTxOs for collaterals and reference script

        mint_utxos, collateral_utxos, reference_utxo, tx_output_step1 = mint_build._fund_issuer(
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

        policyid = cluster.g_transaction.get_policyid(plutus_common.MINTING_PLUTUS_V2)
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
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
        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )
        plutus_costs = cluster.g_transaction.calculate_plutus_script_cost(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        assert cluster.g_query.get_address_balance(
            issuer_addr.address
        ) == issuer_init_balance + minting_cost.collateral + lovelace_amount + (
            reference_utxo.amount if reference_utxo else 0
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.g_query.get_utxo(
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

        plutus_common.check_plutus_costs(
            plutus_costs=plutus_costs,
            expected_costs=[expected_cost],
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "valid_redeemer", (True, False), ids=("right_redeemer", "wrong_redeemer")
    )
    def test_reference_inputs_visibility(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        valid_redeemer: bool,
        request: FixtureRequest,
    ):
        """
        Test visibility of reference inputs by a plutus script.

        * create the necessary Tx outputs
        * create the redeemer with the reference input
        * mint the token and check that the plutus script have visibility of the reference input
        * check that the token was minted
        * check that the reference UTxO was not spent
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_CHECK_REF_INPUTS_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        mint_utxos, collateral_utxos, reference_utxo, __ = mint_build._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
            reference_script=plutus_common.MINTING_CHECK_REF_INPUTS_PLUTUS_V2,
        )

        # for mypy
        assert reference_utxo

        # the redeemer file will be composed by the UTxO of the reference input
        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(
                {
                    "list": [
                        {
                            "constructor": 0,
                            "fields": [
                                {
                                    "constructor": 0,
                                    "fields": [{"bytes": reference_utxo.utxo_hash}],
                                },
                                {"int": reference_utxo.utxo_ix if valid_redeemer else 9},
                            ],
                        }
                    ]
                },
                outfile,
            )

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(
            plutus_common.MINTING_CHECK_REF_INPUTS_PLUTUS_V2
        )
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                reference_txin=reference_utxo,
                collaterals=collateral_utxos,
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

        # if the redeemer is not the expected, script evaluation will fail and should show
        # the expected error message defined by the plutus script
        if not valid_redeemer:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster.g_transaction.build_tx(
                    src_address=payment_addr.address,
                    tx_name=f"{temp_template}_step2",
                    tx_files=tx_files_step2,
                    txins=mint_utxos,
                    txouts=txouts_step2,
                    mint=plutus_mint_data,
                )
            err_str = str(excinfo.value)

            assert "Reference inputs do not match redeemer" in err_str, err_str
            return

        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )
        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # the plutus script checks if the redeemer complies with the reference inputs provided
        # so a successful submit of the tx proves that the script can see the reference inputs
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        # check that the token was minted
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.g_query.get_utxo(
            utxo=reference_utxo
        ), "Reference UTxO was spent"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "valid_redeemer", (True, False), ids=("right_redeemer", "wrong_redeemer")
    )
    def test_reference_scripts_visibility(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        valid_redeemer: bool,
        request: FixtureRequest,
    ):
        """Test visibility of reference inputs by a plutus script.

        * create needed Tx outputs
        * create the redeemer with the script hash
        * mint the token and check that the plutus script has visibility of the reference script
        * check that the token was minted
        * check that the reference UTxO was not spent
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_CHECK_REF_SCRIPTS_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        mint_utxos, collateral_utxos, reference_utxo, __ = mint_build._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
            reference_script=plutus_common.MINTING_CHECK_REF_SCRIPTS_PLUTUS_V2,
        )

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(
            plutus_common.MINTING_CHECK_REF_SCRIPTS_PLUTUS_V2
        )
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"
        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        # the redeemer file will be composed by the script hash
        redeemer_file = f"{temp_template}.redeemer"
        with open(redeemer_file, "w", encoding="utf-8") as outfile:
            json.dump(
                {"bytes": policyid} if valid_redeemer else {"bytes": mint_utxos[0].utxo_hash},
                outfile,
            )

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                reference_txin=reference_utxo,
                collaterals=collateral_utxos,
                redeemer_file=redeemer_file,
                policyid=policyid,
            )
        ]

        tx_files_step2 = clusterlib.TxFiles(signing_key_files=[issuer_addr.skey_file])

        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

        # if the redeemer is not the expected the script evaluation will fail and should show
        # the expected error message defined by the plutus script
        if not valid_redeemer:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster.g_transaction.build_tx(
                    src_address=payment_addr.address,
                    tx_name=f"{temp_template}_step2",
                    tx_files=tx_files_step2,
                    txins=mint_utxos,
                    txouts=txouts_step2,
                    mint=plutus_mint_data,
                )
            err_str = str(excinfo.value)

            assert "Unexpected reference script at each reference input" in err_str, err_str
            return

        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
        )

        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        # the plutus script checks if the redeemer complies with the reference script provided
        # so a successful submit of the tx proves that the script can see the reference script
        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        # check that the token was minted
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.g_query.get_utxo(
            utxo=reference_utxo
        ), "Reference UTxO was spent"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "scenario",
        ("reference_script", "readonly_reference_input", "different_datum"),
    )
    def test_inline_datum_visibility(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        scenario: str,
        request: FixtureRequest,
    ):
        """
        Test visibility of inline datums on reference inputs by a plutus script.

        * create the necessary Tx outputs
        * mint the token and check that the plutus script have visibility of the inline datum
        * check that the token was minted
        * check that the reference UTxO was not spent
        """
        # pylint: disable=too-many-locals
        temp_template = f"{common.get_test_id(cluster)}_{request.node.callspec.id}"
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        lovelace_amount = 2_000_000
        token_amount = 5
        script_fund = 200_000_000

        minting_cost = plutus_common.compute_cost(
            execution_cost=plutus_common.MINTING_V2_CHECK_INLINE_DATUM_COST,
            protocol_params=cluster.g_query.get_protocol_params(),
        )

        # Step 1: fund the token issuer and create the reference script

        mint_utxos, collateral_utxos, reference_utxo, __ = mint_build._fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            minting_cost=minting_cost,
            amount=script_fund,
            reference_script=plutus_common.MINTING_CHECK_INLINE_DATUM_PLUTUS_V2,
            inline_datum=plutus_common.DATUM_42,
        )

        # to check inline datum on readonly reference input
        with_reference_input = scenario != "reference_script"
        different_datum = scenario == "different_datum"
        datum_file = plutus_common.DATUM_43_TYPED if different_datum else plutus_common.DATUM_42

        reference_input = []
        if with_reference_input or different_datum:
            reference_input = _build_reference_txin(
                temp_template=temp_template,
                cluster=cluster,
                payment_addr=payment_addrs[0],
                amount=lovelace_amount,
                inline_datum=datum_file,
            )

        # Step 2: mint the "qacoin"

        policyid = cluster.g_transaction.get_policyid(
            plutus_common.MINTING_CHECK_INLINE_DATUM_PLUTUS_V2
        )
        asset_name = f"qacoin{clusterlib.get_rand_str(4)}".encode().hex()
        token = f"{policyid}.{asset_name}"

        mint_txouts = [
            clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)
        ]

        plutus_mint_data = [
            clusterlib.Mint(
                txouts=mint_txouts,
                reference_txin=reference_utxo,
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

        # the plutus script checks if all reference inputs have the same inline datum
        # it will fail if the inline datums are not the same in all reference inputs and
        # succeed if all inline datums match
        if different_datum:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster.g_transaction.build_tx(
                    src_address=payment_addr.address,
                    tx_name=f"{temp_template}_step2",
                    tx_files=tx_files_step2,
                    txins=mint_utxos,
                    txouts=txouts_step2,
                    mint=plutus_mint_data,
                    readonly_reference_txins=reference_input,
                )
            err_str = str(excinfo.value)
            assert "Unexpected inline datum at each reference input" in err_str, err_str
            return

        tx_output_step2 = cluster.g_transaction.build_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step2",
            tx_files=tx_files_step2,
            txins=mint_utxos,
            txouts=txouts_step2,
            mint=plutus_mint_data,
            readonly_reference_txins=reference_input,
        )

        tx_signed_step2 = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )

        cluster.g_transaction.submit_tx(tx_file=tx_signed_step2, txins=mint_utxos)

        # check that the token was minted
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_step2)
        token_utxo = clusterlib.filter_utxos(
            utxos=out_utxos, address=issuer_addr.address, coin=token
        )
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was NOT minted"

        # check that reference UTxO was NOT spent
        assert not reference_utxo or cluster.g_query.get_utxo(
            utxo=reference_utxo
        ), "Reference UTxO was spent"
