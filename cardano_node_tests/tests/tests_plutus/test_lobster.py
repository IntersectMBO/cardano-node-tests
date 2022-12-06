"""Tests for the "Lobster Challenge".

See https://github.com/input-output-hk/lobster-challenge
"""
import logging
import random
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)

pytestmark = [
    common.SKIPIF_PLUTUS_UNUSABLE,
    pytest.mark.plutus,
]

DATA_DIR = Path(__file__).parent.parent / "data"
LOBSTER_DIR = DATA_DIR / "plutus" / "lobster"

LOBSTER_PLUTUS = LOBSTER_DIR / "lobster.plutus"
NFT_MINT_PLUTUS = LOBSTER_DIR / "nft-mint-policy.plutus"
OTHER_MINT_PLUTUS = LOBSTER_DIR / "other-mint-policy.plutus"

LOBSTER_DATUM_HASH = "45b0cfc220ceec5b7c1c62c4d4193d38e4eba48e8815729ce75f9c0ab0e4c1c0"


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
    amount: int,
    collateral_amount: int,
) -> Tuple[List[clusterlib.UTXOData], List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Fund the token issuer."""
    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=amount,
        ),
        clusterlib.TxOut(address=issuer_addr.address, amount=collateral_amount),
    ]
    tx_output = cluster_obj.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        tx_files=tx_files,
        txouts=txouts,
        fee_buffer=2_000_000,
        # don't join 'change' and 'collateral' txouts, we need separate UTxOs
        join_txouts=False,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_step1",
    )
    cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    out_issuer_utxos = clusterlib.filter_utxos(
        utxos=out_utxos, address=issuer_addr.address, coin=clusterlib.DEFAULT_COIN
    )
    assert (
        clusterlib.calculate_utxos_balance(out_issuer_utxos) == amount + collateral_amount
    ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

    mint_utxos = [out_issuer_utxos[0]]
    collateral_utxos = [out_issuer_utxos[1]]

    return mint_utxos, collateral_utxos, tx_output


def _mint_lobster_nft(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    issuer_addr: clusterlib.AddressRecord,
    mint_utxos: List[clusterlib.UTXOData],
    collateral_utxos: List[clusterlib.UTXOData],
    nft_amount: int,
    lovelace_amount: int,
) -> Tuple[str, List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Mint the LobsterNFT token."""
    lobster_policyid = cluster_obj.g_transaction.get_policyid(NFT_MINT_PLUTUS)
    asset_name = b"LobsterNFT".hex()
    lobster_nft_token = f"{lobster_policyid}.{asset_name}"

    mint_txouts = [
        clusterlib.TxOut(
            address=issuer_addr.address,
            amount=nft_amount,
            coin=lobster_nft_token,
        )
    ]

    plutus_mint_data = [
        clusterlib.Mint(
            txouts=mint_txouts,
            script_file=NFT_MINT_PLUTUS,
            collaterals=collateral_utxos,
            redeemer_value="[]",
        )
    ]

    tx_files = clusterlib.TxFiles(
        signing_key_files=[issuer_addr.skey_file],
    )
    txouts = [
        clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
        *mint_txouts,
    ]
    tx_output = cluster_obj.g_transaction.build_tx(
        src_address=issuer_addr.address,
        tx_name=f"{temp_template}_mint_nft",
        tx_files=tx_files,
        txins=mint_utxos,
        txouts=txouts,
        mint=plutus_mint_data,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_mint_nft",
    )
    cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=mint_utxos)

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    lovelace_utxos = clusterlib.filter_utxos(
        utxos=out_utxos, address=issuer_addr.address, coin=clusterlib.DEFAULT_COIN
    )
    token_utxos = clusterlib.filter_utxos(
        utxos=out_utxos, address=issuer_addr.address, coin=lobster_nft_token
    )

    # check expected balances

    # Skip change UTxO. Change txout created by `transaction build` used to be UTxO with index 0,
    # now it is the last UTxO.
    utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(utxos=out_utxos, txouts=tx_output.txouts)
    utxos_without_change = lovelace_utxos[1:] if utxo_ix_offset else lovelace_utxos[:-1]

    assert (
        clusterlib.calculate_utxos_balance(utxos_without_change) == lovelace_amount
    ), f"Incorrect Lovelace balance for token issuer address `{issuer_addr.address}`"
    assert (
        clusterlib.calculate_utxos_balance(token_utxos, coin=lobster_nft_token) == nft_amount
    ), f"Incorrect token balance for token issuer address `{issuer_addr.address}`"

    return lobster_nft_token, token_utxos, tx_output


def _deploy_lobster_nft(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    token_utxos: List[clusterlib.UTXOData],
    lobster_nft_token: str,
    nft_amount: int,
    lovelace_amount: int,
) -> Tuple[str, List[clusterlib.UTXOData], clusterlib.TxRawOutput]:
    """Deploy the LobsterNFT token to script address."""
    script_address = cluster_obj.g_address.gen_payment_addr(
        addr_name=f"{temp_template}_deploy_nft", payment_script_file=LOBSTER_PLUTUS
    )

    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file, issuer_addr.skey_file],
    )
    txouts = [
        clusterlib.TxOut(
            address=script_address, amount=lovelace_amount, datum_hash=LOBSTER_DATUM_HASH
        ),
        clusterlib.TxOut(
            address=script_address,
            amount=nft_amount,
            coin=lobster_nft_token,
            datum_hash=LOBSTER_DATUM_HASH,
        ),
    ]

    funds_txin = cluster_obj.g_query.get_utxo_with_highest_amount(address=payment_addr.address)
    tx_output = cluster_obj.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_deploy_nft",
        tx_files=tx_files,
        txins=[*token_utxos, funds_txin],
        txouts=txouts,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_deploy_nft",
    )
    cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=token_utxos)

    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    lovelace_utxos = clusterlib.filter_utxos(
        utxos=out_utxos, address=script_address, coin=clusterlib.DEFAULT_COIN
    )
    token_utxos = clusterlib.filter_utxos(
        utxos=out_utxos, address=script_address, coin=lobster_nft_token
    )

    # check expected balances
    assert (
        clusterlib.calculate_utxos_balance(lovelace_utxos) == lovelace_amount
    ), f"Incorrect Lovelace balance for token issuer address `{script_address}`"
    assert (
        clusterlib.calculate_utxos_balance(token_utxos, coin=lobster_nft_token) == nft_amount
    ), f"Incorrect token balance for token issuer address `{script_address}`"

    return script_address, token_utxos, tx_output


@common.SKIPIF_BUILD_UNUSABLE
class TestLobsterChallenge:
    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_lobster_name(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test the Lobster Challenge.

        Uses `cardano-cli transaction build` command for building the transactions.

        * fund token issuer and create a UTxO for collateral
        * mint the LobsterNFT token
        * deploy the LobsterNFT token to address of lobster spending script
        * generate random votes and determine the expected final value
        * perform voting and check that the final value matches the expected value
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = common.get_test_id(cluster)
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        votes_num = 50
        names_num = 1219
        io_random_seed = 42

        issuer_fund = 200_000_000
        lovelace_setup_amount = 1_724_100
        lovelace_vote_amount = 2_034_438
        collateral_amount = 20_000_000
        nft_amount = 1

        # Step 1: fund the token issuer and create UTXO for collaterals

        mint_utxos, collateral_utxos, tx_output_step1 = _fund_issuer(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            amount=issuer_fund,
            collateral_amount=collateral_amount,
        )

        # Step 2: mint the LobsterNFT token

        lobster_nft_token, token_utxos_step2, tx_output_step2 = _mint_lobster_nft(
            cluster_obj=cluster,
            temp_template=temp_template,
            issuer_addr=issuer_addr,
            mint_utxos=mint_utxos,
            collateral_utxos=collateral_utxos,
            nft_amount=nft_amount,
            lovelace_amount=lovelace_setup_amount,
        )

        # Step 3: deploy the LobsterNFT token to script address

        script_address, token_utxos_step3, tx_output_step3 = _deploy_lobster_nft(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addr=payment_addr,
            issuer_addr=issuer_addr,
            token_utxos=token_utxos_step2,
            lobster_nft_token=lobster_nft_token,
            nft_amount=nft_amount,
            lovelace_amount=lovelace_setup_amount,
        )

        tx_outputs_all = [tx_output_step1, tx_output_step2, tx_output_step3]

        # Step 4: prepare for voting

        # there's 50 votes, each vote is int between 1 and 100
        votes = [random.randint(1, 100) for __ in range(votes_num)]
        _votes_sum = sum(votes)
        # Add "random" seed to the sum of all votes. Taking the remainder after
        # division by the number of potential names (`names_num`) gives us the
        # final counter value.
        # The final counter value is used as an index. Looking into the list of
        # names, we can see the name the index points to. We don't need to do
        # that in automated test, we will just check that the final counter
        # value matches the expected counter value.
        expected_counter_val = (io_random_seed + _votes_sum) % names_num
        LOGGER.info(f"Final counter value: {expected_counter_val}")
        votes.append(expected_counter_val)

        # Step 5: vote

        other_policyid = cluster.g_transaction.get_policyid(OTHER_MINT_PLUTUS)
        asset_name_counter = b"LobsterCounter".hex()
        asset_name_votes = b"LobsterVotes".hex()
        counter_token = f"{other_policyid}.{asset_name_counter}"
        votes_token = f"{other_policyid}.{asset_name_votes}"

        vote_utxos = token_utxos_step3
        vote_counter = 0
        utxo_counter_token: Optional[clusterlib.UTXOData] = None
        for vote_num, vote_val in enumerate(votes, start=1):
            # normal votes
            if vote_num <= votes_num:
                vote_counter += vote_val
                mint_val = vote_val
            # final IO vote
            else:
                # set new counter value to `(seed + counter value) % number of names`
                # and burn excessive LobsterCounter tokens
                mint_val = vote_val - vote_counter
                vote_counter = vote_val

            txouts = [
                # Lovelace amount
                clusterlib.TxOut(
                    address=script_address,
                    amount=lovelace_vote_amount,
                    datum_hash=LOBSTER_DATUM_HASH,
                ),
                # LobsterNFT token
                clusterlib.TxOut(
                    address=script_address,
                    amount=nft_amount,
                    coin=lobster_nft_token,
                    datum_hash=LOBSTER_DATUM_HASH,
                ),
                # LobsterCounter token
                clusterlib.TxOut(
                    address=script_address,
                    amount=vote_counter,
                    coin=counter_token,
                    datum_hash=LOBSTER_DATUM_HASH,
                ),
                # LobsterVotes token
                clusterlib.TxOut(
                    address=script_address,
                    amount=vote_num,
                    coin=votes_token,
                    datum_hash=LOBSTER_DATUM_HASH,
                ),
            ]

            mint_txouts = [
                # mint new LobsterCounter tokens
                clusterlib.TxOut(
                    address=script_address,
                    amount=mint_val,
                    coin=counter_token,
                    datum_hash=LOBSTER_DATUM_HASH,
                ),
                # mint 1 new LobsterVotes token
                clusterlib.TxOut(
                    address=script_address,
                    amount=1,
                    coin=votes_token,
                    datum_hash=LOBSTER_DATUM_HASH,
                ),
            ]
            mint_script_data = [
                clusterlib.Mint(
                    txouts=mint_txouts,
                    script_file=OTHER_MINT_PLUTUS,
                    redeemer_value="[]",
                )
            ]

            txin_script_data = [
                clusterlib.ScriptTxIn(
                    txins=vote_utxos,
                    script_file=LOBSTER_PLUTUS,
                    collaterals=collateral_utxos,
                    datum_value="[]",
                    redeemer_value="[]",
                )
            ]

            tx_files = clusterlib.TxFiles(
                signing_key_files=[payment_addr.skey_file, issuer_addr.skey_file],
            )
            funds_txin = cluster.g_query.get_utxo_with_highest_amount(address=payment_addr.address)
            tx_output_vote = cluster.g_transaction.build_tx(
                src_address=payment_addr.address,
                tx_name=f"{temp_template}_voting_{vote_num}",
                txins=[funds_txin],
                tx_files=tx_files,
                txouts=txouts,
                script_txins=txin_script_data,
                mint=mint_script_data,
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output_vote.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_voting_{vote_num}",
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=vote_utxos)

            tx_outputs_all.append(tx_output_vote)

            out_utxos_vote = cluster.g_query.get_utxo(tx_raw_output=tx_output_vote)
            utxo_ix_offset = clusterlib_utils.get_utxo_ix_offset(
                utxos=out_utxos_vote, txouts=tx_output_vote.txouts
            )
            vote_utxos = clusterlib.filter_utxos(utxos=out_utxos_vote, utxo_ix=utxo_ix_offset)

            # check expected balances
            utxo_counter_tokens = [u for u in vote_utxos if u.coin == counter_token]
            utxo_counter_token = None
            try:
                utxos_lovelace = [u for u in vote_utxos if u.coin == clusterlib.DEFAULT_COIN][0]
                utxo_votes_token = [u for u in vote_utxos if u.coin == votes_token][0]
                # when `vote_counter` is not 0 (that can happen for final vote), there needs to be
                # a counter token
                if vote_counter:
                    utxo_counter_token = utxo_counter_tokens[0]
            except IndexError:
                LOGGER.error(f"Unexpected vote UTxOs in vote number {vote_num}: {vote_utxos}")
                raise

            assert (
                utxos_lovelace.amount == lovelace_vote_amount
            ), f"Incorrect Lovelace balance for script address `{script_address}`"

            assert (
                utxo_votes_token.amount == vote_num
            ), f"Incorrect LobsterVotes token balance for script address `{script_address}`"

            assert (
                utxo_counter_token is None or utxo_counter_token.amount == vote_counter
            ), f"Incorrect LobsterCounter token balance for script address `{script_address}`"

        # final counter value can be 0
        if expected_counter_val == 0:
            assert (
                not utxo_counter_tokens
            ), "No LobsterCounter token expected when final counter value is 0"
        else:
            assert (
                utxo_counter_token and utxo_counter_token.amount == expected_counter_val
            ), "Final balance of LobsterCounter token doesn't match the expected balance"

        # check transactions in db-sync
        for tx_out_rec in tx_outputs_all:
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_rec)
