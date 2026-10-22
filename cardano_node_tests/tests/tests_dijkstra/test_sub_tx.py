"""Tests for Dijkstra transactions with embedded sub-transactions."""

import dataclasses
import json
import pathlib as pl

import allure
import cbor2
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST,
    reason="Sub-transactions are available only in Dijkstra+ eras",
)


@pytest.fixture(scope="module")
def require_sub_cli() -> None:
    """Require CLI support for building and signing sub-transactions."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The `cardano-cli` doesn't support sub-transactions.")


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactions:
    """Test building and submitting transactions containing signed sub-transactions."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache two funded source addresses and a destination address."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=3,
            fund_idx=[0, 1],
            caching_key=helpers.get_current_line_str(),
            min_amount=10_000_000,
        )

    @pytest.fixture
    def swap_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache funded Alice, Bob, Carol, and token issuer addresses."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=4,
            caching_key=helpers.get_current_line_str(),
            min_amount=50_000_000,
        )

    @pytest.fixture
    def babel_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache user, providers, recipient, and issuer; fund only the issuer."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=5,
            fund_idx=[4],
            caching_key=helpers.get_current_line_str(),
            min_amount=50_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("output_kind", ("assets", "inline_datum", "reference_script"))
    def test_min_utxo_sponsorship(
        self,
        cluster: clusterlib.ClusterLib,
        swap_addrs: list[clusterlib.AddressRecord],
        output_kind: str,
    ):
        """Sponsor child outputs at their minimum ADA without changing signed terms.

        * Prepare one token-bearing input and two asset-bearing child outputs,
          optionally carrying a large inline datum or native reference script.
        * Calculate each output's minimum ADA using current protocol parameters.
          The child cannot fund both outputs; the sponsor covers the deficit and fee.
        * Build a valid baseline, then reduce one child output by one lovelace and
          add that lovelace to parent change, preserving complete-batch balance.
        * Check node rejection leaves all inputs unspent and creates no outputs,
          despite sufficient ADA elsewhere in the batch.
        * Submit the original signed child and verify exact-minimum outputs,
          attached data/scripts, and the sponsor's exact contribution.
        """
        temp_template = common.get_test_id(cluster)
        user, sponsor, recipient, issuer = swap_addrs
        fee = 1_000_000

        # Mint two tokens, one for each independently checked child output.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=2,
        )[0].token

        # Size the actual output contents, not an estimate based only on asset count.
        output_template = clusterlib.TxOut(address=user.address, amount=2_000_000)
        datum = {"bytes": "ab" * 512}
        script = {
            "type": "all",
            "scripts": [
                {
                    "type": "sig",
                    "keyHash": cluster.g_address.get_payment_vkey_hash(
                        payment_vkey_file=issuer.vkey_file
                    ),
                }
            ]
            * 32,
        }
        if output_kind == "inline_datum":
            datum_file = pl.Path(f"{temp_template}_datum.json")
            datum_file.write_text(json.dumps(datum), encoding="utf-8")
            output_template = dataclasses.replace(output_template, inline_datum_file=datum_file)
        elif output_kind == "reference_script":
            script_file = pl.Path(f"{temp_template}_reference.script")
            script_file.write_text(json.dumps(script), encoding="utf-8")
            output_template = dataclasses.replace(
                output_template, reference_script_file=script_file
            )

        child_txouts = []
        for addr in (user, recipient):
            ada_output = dataclasses.replace(output_template, address=addr.address)
            # Matching attributes join the ADA and token records into one UTxO.
            asset_output = dataclasses.replace(ada_output, amount=1, coin=token)
            minimum = cluster.g_transaction.calculate_min_req_utxo(
                txouts=[ada_output, asset_output]
            ).value
            ada_output = dataclasses.replace(ada_output, amount=minimum)
            assert (
                cluster.g_transaction.calculate_min_req_utxo(
                    txouts=[ada_output, asset_output]
                ).value
                == minimum
            ), "Minimum ADA changed after setting the output amount"
            child_txouts.extend([ada_output, asset_output])

        # The input funds only one plain asset-bearing UTxO, not both child outputs.
        source_txouts = [
            clusterlib.TxOut(address=user.address, amount=2_000_000),
            clusterlib.TxOut(address=user.address, amount=2, coin=token),
        ]
        source_minimum = cluster.g_transaction.calculate_min_req_utxo(txouts=source_txouts).value
        source_txouts[0] = dataclasses.replace(source_txouts[0], amount=source_minimum)
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=source_txouts,
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        user_inputs = clusterlib.filter_utxos(
            utxos=cluster.g_query.get_utxo(tx_raw_output=setup_output), address=user.address
        )
        sponsor_utxos = cluster.g_query.get_utxo(address=sponsor.address)
        asset_refs = {
            (u.utxo_hash, u.utxo_ix) for u in sponsor_utxos if u.coin != clusterlib.DEFAULT_COIN
        }
        sponsor_inputs = [u for u in sponsor_utxos if (u.utxo_hash, u.utxo_ix) not in asset_refs]
        child_ada = sum(t.amount for t in child_txouts if t.coin == clusterlib.DEFAULT_COIN)
        deficit = child_ada - clusterlib.calculate_utxos_balance(user_inputs)
        assert deficit > 0, "The child does not need ADA sponsorship"
        sponsor_change = clusterlib.calculate_utxos_balance(sponsor_inputs) - deficit - fee
        assert sponsor_change > child_ada, "Not enough surplus ADA for the negative control"
        inputs_before = cluster.g_query.get_utxo(utxo=[*user_inputs, *sponsor_inputs])

        def _build_batch(
            name: str, txouts: list[clusterlib.TxOut], change: int
        ) -> tuple[clusterlib.SubTxRawOutput, pl.Path, clusterlib.TxRawOutput, pl.Path]:
            """Sign fixed child outputs and wrap them without automatic balancing."""
            name_template = f"{temp_template}_{name}"
            child_output = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{name_template}_child.body", txins=user_inputs, txouts=txouts
            )
            child_signed = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=child_output.out_file,
                tx_name=name_template,
                signing_key_files=[user.skey_file],
            )
            parent_output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=name_template,
                txins=sponsor_inputs,
                txouts=[clusterlib.TxOut(address=sponsor.address, amount=change)],
                tx_files=clusterlib.TxFiles(signing_key_files=[sponsor.skey_file]),
                fee=fee,
                sub_transaction_files=[child_signed],
            )
            parent_signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_output.out_file,
                signing_key_files=[sponsor.skey_file],
                tx_name=name_template,
            )
            child_cbor = json.loads(child_signed.read_text(encoding="utf-8"))["cborHex"]
            parent_cbor = json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"]
            assert child_cbor in parent_cbor, "The sponsor changed the signed child"
            assert clusterlib.calculate_utxos_balance([*user_inputs, *sponsor_inputs]) == (
                sum(t.amount for t in child_output.txouts if t.coin == clusterlib.DEFAULT_COIN)
                + change
                + fee
            ), "The batch is not balanced in ADA"
            return child_output, child_signed, parent_output, parent_signed

        # A balanced parent cannot repair an individual child's undersized output.
        child_output, child_signed, parent_output, parent_signed = _build_batch(
            "minimum", child_txouts, sponsor_change
        )
        child_bytes = child_signed.read_bytes()
        invalid_txouts = list(child_txouts)
        invalid_txouts[0] = dataclasses.replace(
            invalid_txouts[0], amount=invalid_txouts[0].amount - 1
        )
        invalid_child, invalid_signed_child, invalid_parent, invalid_signed = _build_batch(
            "below_minimum", invalid_txouts, sponsor_change + 1
        )
        with pytest.raises(clusterlib.CLIError, match="OutputTooSmallUTxO"):
            cluster.g_transaction.submit_tx_bare(tx_file=invalid_signed)

        invalid_child_id = cluster.g_sub_transaction.get_txid(
            signed_sub_tx_file=invalid_signed_child
        )
        assert cluster.g_query.get_utxo(utxo=[*user_inputs, *sponsor_inputs]) == inputs_before
        assert not cluster.g_query.get_utxo(tx_raw_output=invalid_parent)
        assert not cluster.g_query.get_utxo(
            txin=[f"{invalid_child_id}#{ix}" for ix in range(invalid_child.txouts_count)]
        )

        # The unchanged valid baseline must settle with both outputs exactly at minimum ADA.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=sponsor_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*user_inputs, *sponsor_inputs])
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        child_utxos = cluster.g_query.get_utxo(
            txin=[f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        )
        assert child_output.txouts_count == 2
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "Confirmed child outputs differ from the signed minimum-ADA outputs"
        for utxo in child_utxos:
            if utxo.coin != clusterlib.DEFAULT_COIN:
                continue
            if output_kind == "inline_datum":
                assert utxo.inline_datum == datum, "The child's inline datum changed"
            elif output_kind == "reference_script":
                assert utxo.reference_script, "The child's reference script is missing"
                # Node queries return a CBOR envelope, not the original native-script JSON.
                clusterlib_utils.check_reference_script_policyid(
                    name_template=f"{temp_template}_check_{utxo.utxo_ix}",
                    cluster_obj=cluster,
                    script_file=output_template.reference_script_file,
                    script_data=utxo.reference_script["script"],
                )

        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert [(u.address, u.amount, u.coin) for u in parent_utxos] == [
            (sponsor.address, sponsor_change, clusterlib.DEFAULT_COIN)
        ], "The sponsor did not pay exactly the child's deficit and parent fee"
        assert child_signed.read_bytes() == child_bytes

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)
        # Child outputs are checked through the node; only the parent has a db-sync helper.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_babel_payment(
        self,
        cluster: clusterlib.ClusterLib,
        babel_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Pay a recipient using a token offer and provider-funded ADA and fees.

        * Give the user a token-bearing input but no separate ADA-only input.
        * Publish a signed child fixing the recipient payment, token change, and
          provider compensation. The child needs additional ADA, not collateral.
        * Model a provider declining the offer without any on-chain reservation.
        * Build a valid wrapper, then submit variants lacking fee funding, using
          insufficient provider ADA, or claiming excess token compensation.
          Check node rejection leaves all inputs and outputs unchanged.
        * Settle the original child without another user signature and verify
          exact recipient and provider outputs and an ADA-denominated parent fee.
        """
        temp_template = common.get_test_id(cluster)
        user, provider, poor_provider, recipient, issuer = babel_addrs
        user_ada = 4_000_000
        payment_amount = 3_000_000
        output_ada = 2_000_000
        provider_ada = 10_000_000
        compensation = 5
        fee = 1_000_000
        ada_deficit = payment_amount + 2 * output_ada - user_ada

        # Prepare one multi-asset user UTxO and dedicated ADA inputs for the providers.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=2 * compensation,
        )[0].token
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=[
                # Joining these records gives the user's tokens their required ADA.
                clusterlib.TxOut(address=user.address, amount=user_ada),
                clusterlib.TxOut(address=user.address, amount=2 * compensation, coin=token),
                clusterlib.TxOut(address=provider.address, amount=provider_ada),
                clusterlib.TxOut(address=poor_provider.address, amount=output_ada),
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup_output)
        user_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=user.address)
        provider_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=provider.address)
        poor_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=poor_provider.address)

        user_utxos = cluster.g_query.get_utxo(address=user.address)
        asset_refs = {
            (u.utxo_hash, u.utxo_ix) for u in user_utxos if u.coin != clusterlib.DEFAULT_COIN
        }
        assert user_inputs and all((u.utxo_hash, u.utxo_ix) in asset_refs for u in user_utxos), (
            "The user has a separate ADA-only input"
        )

        # The signed outputs set the complete terms, including the provider's tokens.
        child_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_child.body",
            txins=user_inputs,
            txouts=[
                clusterlib.TxOut(address=recipient.address, amount=payment_amount),
                clusterlib.TxOut(address=user.address, amount=output_ada),
                clusterlib.TxOut(address=user.address, amount=compensation, coin=token),
                clusterlib.TxOut(address=provider.address, amount=output_ada),
                clusterlib.TxOut(address=provider.address, amount=compensation, coin=token),
            ],
        )
        child_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=child_output.out_file,
            tx_name=temp_template,
            signing_key_files=[user.skey_file],
        )
        published_bytes = child_signed.read_bytes()
        child_cbor = json.loads(published_bytes)["cborHex"]
        child = cbor2.loads(bytes.fromhex(child_cbor))
        assert not {2, 13, 16, 17}.intersection(child[0]), (
            "The user's child contains fee or collateral fields"
        )
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        child_refs = [f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        all_inputs = [*user_inputs, *provider_inputs, *poor_inputs]
        inputs_before = cluster.g_query.get_utxo(utxo=all_inputs)

        # Declining an uneconomic offer is an off-chain choice, not a ledger action.
        declined_offer = pl.Path(f"{temp_template}_declined_offer.signed")
        declined_offer.write_bytes(published_bytes)
        assert cluster.g_query.get_utxo(utxo=all_inputs) == inputs_before
        assert not cluster.g_query.get_utxo(txin=child_refs)

        def _build_wrapper(
            name: str,
            src: clusterlib.AddressRecord,
            txins: list[clusterlib.UTXOData],
            change: int,
            extra_tokens: int = 0,
        ) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Wrap the fixed child without balancing away the condition under test."""
            txouts = [clusterlib.TxOut(address=src.address, amount=change)]
            if extra_tokens:
                txouts.append(
                    clusterlib.TxOut(address=src.address, amount=extra_tokens, coin=token)
                )
            tx_output = cluster.g_transaction.build_raw_tx(
                src_address=src.address,
                tx_name=f"{temp_template}_{name}",
                txins=txins,
                txouts=txouts,
                tx_files=clusterlib.TxFiles(signing_key_files=[src.skey_file]),
                fee=fee,
                sub_transaction_files=[child_signed],
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_output.out_file,
                signing_key_files=[src.skey_file],
                tx_name=f"{temp_template}_{name}",
            )
            signed_cbor = json.loads(signed.read_text(encoding="utf-8"))["cborHex"]
            assert child_cbor in signed_cbor, "The provider changed the signed terms"
            assert cbor2.loads(bytes.fromhex(signed_cbor))[0][2] == fee
            return tx_output, signed

        # Start with a valid baseline, then isolate each funding or compensation error.
        change = provider_ada - ada_deficit - fee
        parent_output, parent_signed = _build_wrapper("valid", provider, provider_inputs, change)
        for name, src, txins, invalid_change, extra_tokens in (
            ("missing_fee_funding", provider, provider_inputs, change + fee, 0),
            ("insufficient_provider_ada", poor_provider, poor_inputs, output_ada, 0),
            ("excess_token_compensation", provider, provider_inputs, change, 1),
        ):
            with subtests.test(scenario=name):
                invalid_output, invalid_signed = _build_wrapper(
                    name, src, txins, invalid_change, extra_tokens
                )
                with pytest.raises(clusterlib.CLIError, match="ValueNotConservedUTxO"):
                    cluster.g_transaction.submit_tx_bare(tx_file=invalid_signed)

                assert cluster.g_query.get_utxo(utxo=all_inputs) == inputs_before
                assert not cluster.g_query.get_utxo(txin=child_refs)
                assert not cluster.g_query.get_utxo(tx_raw_output=invalid_output)

        # A willing provider can still settle the unchanged offer and pay the fee in ADA.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=provider_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*user_inputs, *provider_inputs])
        assert cluster.g_query.get_utxo(utxo=poor_inputs) == poor_inputs

        child_utxos = cluster.g_query.get_utxo(txin=child_refs)
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "The recipient, user change, or provider compensation differs from the signed terms"

        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert [(u.address, u.amount, u.coin) for u in parent_utxos] == [
            (provider.address, change, clusterlib.DEFAULT_COIN)
        ], "Provider change does not account for the child's ADA deficit and parent fee"
        assert (
            user_ada + provider_ada
            == sum(
                u.amount for u in [*child_utxos, *parent_utxos] if u.coin == clusterlib.DEFAULT_COIN
            )
            + fee
        )
        assert child_signed.read_bytes() == published_bytes

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)
        # Child outputs are checked through the node; only the parent has a db-sync helper.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("winner", ("bob", "carol"))
    @pytest.mark.parametrize("counterparty_child", (False, True), ids=("parent", "children"))
    def test_open_swap(
        self,
        cluster: clusterlib.ClusterLib,
        swap_addrs: list[clusterlib.AddressRecord],
        winner: str,
        counterparty_child: bool,
    ):
        """Settle a published offer with either of two independent counterparties.

        * Prepare Alice's 12 ADA input and five tokens each for Bob and Carol.
        * Alice signs and publishes an unguarded offer returning 2 ADA and demanding
          five tokens. Neither counterparty needs another signature from Alice.
        * Bob and Carol independently assemble wrappers using the published file,
          supplying tokens in the parent or in a complementary signed child.
        * Confirm one wrapper and check exact signed outputs and batch conservation.
        * Submit the competing wrapper and check rejection due to Alice's spent
          input, unchanged counterparty inputs, and no additional outputs.
        """
        temp_template = common.get_test_id(cluster)
        alice, bob, carol, issuer = swap_addrs
        swap_amount = 10_000_000
        output_ada = 2_000_000
        counterparty_ada = 20_000_000
        token_amount = 5
        fee = 1_000_000

        # Mint a fresh asset and prepare distinct pre-batch inputs for all participants.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=2 * token_amount,
        )[0].token

        # Join each counterparty's ADA and token records into one multi-asset UTxO.
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=[
                clusterlib.TxOut(address=alice.address, amount=swap_amount + output_ada),
                *[
                    txout
                    for addr in (bob, carol)
                    for txout in (
                        clusterlib.TxOut(address=addr.address, amount=counterparty_ada),
                        clusterlib.TxOut(address=addr.address, amount=token_amount, coin=token),
                    )
                ],
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup_output)
        alice_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=alice.address)

        # Publish Alice's fixed terms once, without any counterparty key or guard.
        # Her ADA and token records likewise form a single multi-asset output.
        offer_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_offer.body",
            txins=alice_inputs,
            txouts=[
                clusterlib.TxOut(address=alice.address, amount=output_ada),
                clusterlib.TxOut(address=alice.address, amount=token_amount, coin=token),
            ],
        )
        offer_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=offer_output.out_file,
            tx_name=f"{temp_template}_offer",
            signing_key_files=[alice.skey_file],
        )
        published_offer = pl.Path(f"{temp_template}_published_offer.signed")
        published_bytes = offer_signed.read_bytes()
        published_offer.write_bytes(published_bytes)
        offer_cbor = json.loads(published_bytes)["cborHex"]
        offer_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=published_offer)

        # Each assembler sees only the published offer, not Alice's signing key.
        def _assemble(
            counterparty: clusterlib.AddressRecord,
            name: str,
        ) -> tuple[clusterlib.TxRawOutput, pl.Path, clusterlib.SubTxRawOutput | None]:
            """Supply the missing tokens and claim the offered ADA independently."""
            name_template = f"{temp_template}_{name}"
            token_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=counterparty.address)
            child_output = None
            child_files = [published_offer]

            if counterparty_child:
                # The second child supplies tokens and receives Alice's 10 ADA.
                child_output = cluster.g_sub_transaction.build_raw_tx_bare(
                    out_file=f"{name_template}_child.body",
                    txins=token_inputs,
                    txouts=[
                        clusterlib.TxOut(
                            address=counterparty.address,
                            amount=counterparty_ada + swap_amount,
                        )
                    ],
                )
                child_files.append(
                    cluster.g_sub_transaction.sign_tx(
                        sub_tx_file=child_output.out_file,
                        tx_name=f"{name_template}_child",
                        signing_key_files=[counterparty.skey_file],
                    )
                )
                # Cached addresses may hold assets from previous swaps. Use only
                # ADA-only UTxOs for the separate parent fee, preserving those assets.
                address_utxos = cluster.g_query.get_utxo(address=counterparty.address)
                asset_refs = {
                    (u.utxo_hash, u.utxo_ix)
                    for u in address_utxos
                    if u.coin != clusterlib.DEFAULT_COIN
                }
                parent_inputs = [
                    u for u in address_utxos if (u.utxo_hash, u.utxo_ix) not in asset_refs
                ]
                assert parent_inputs, "No ADA-only inputs available for the parent fee"
                parent_amount = clusterlib.calculate_utxos_balance(parent_inputs) - fee
            else:
                # The parent itself supplies tokens and claims the ADA, less its fee.
                parent_inputs = token_inputs
                parent_amount = counterparty_ada + swap_amount - fee

            # File-only assembly avoids balancing or modifying the published offer.
            parent_output = cluster.g_transaction.build_raw_tx(
                src_address=counterparty.address,
                tx_name=name_template,
                txins=parent_inputs,
                txouts=[clusterlib.TxOut(address=counterparty.address, amount=parent_amount)],
                tx_files=clusterlib.TxFiles(signing_key_files=[counterparty.skey_file]),
                fee=fee,
                sub_transaction_files=child_files,
            )
            parent_signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_output.out_file,
                signing_key_files=[counterparty.skey_file],
                tx_name=name_template,
            )
            assert offer_cbor in json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"], (
                "The wrapper changed Alice's published offer"
            )
            return parent_output, parent_signed, child_output

        wrappers = {"bob": _assemble(bob, "bob"), "carol": _assemble(carol, "carol")}
        winning_output, winning_signed, winning_child = wrappers[winner]
        losing_output, losing_signed, losing_child = wrappers["carol" if winner == "bob" else "bob"]

        # The offer and counterparty contribution balance only as a complete swap.
        winning_components: list[clusterlib.SubTxRawOutput | clusterlib.TxRawOutput] = [
            offer_output,
            winning_output,
        ]
        if winning_child:
            winning_components.append(winning_child)
        for coin in (clusterlib.DEFAULT_COIN, token):
            input_amount = clusterlib.calculate_utxos_balance(
                [u for component in winning_components for u in component.txins], coin=coin
            )
            output_amount = sum(
                t.amount
                for component in winning_components
                for t in component.txouts
                if t.coin == coin
            )
            assert input_amount == output_amount + (fee if coin == clusterlib.DEFAULT_COIN else 0)

        losing_inputs = [*losing_output.txins, *(losing_child.txins if losing_child else [])]
        losing_before = cluster.g_query.get_utxo(utxo=losing_inputs)
        assert losing_before, "The competing wrapper has no unspent counterparty inputs"

        # Either assembler can settle first without asking Alice to sign its wrapper.
        cluster.g_transaction.submit_tx(tx_file=winning_signed, txins=winning_output.txins)
        assert not cluster.g_query.get_utxo(
            utxo=[u for component in winning_components for u in component.txins]
        ), "Some inputs of the successful swap remain unspent"

        def _check_outputs(
            txid: str, component: clusterlib.TxRawOutput | clusterlib.SubTxRawOutput
        ) -> None:
            """Check every output under its originating transaction ID."""
            utxos = cluster.g_query.get_utxo(
                txin=[f"{txid}#{ix}" for ix in range(component.txouts_count)]
            )
            assert sorted((u.address, u.amount, u.coin) for u in utxos) == sorted(
                (t.address, t.amount, t.coin) for t in component.txouts
            ), "Confirmed swap outputs differ from the signed terms"

        _check_outputs(offer_id, offer_output)
        _check_outputs(
            cluster.g_transaction.get_txid(tx_body_file=winning_output.out_file), winning_output
        )
        if winning_child:
            _check_outputs(
                cluster.g_sub_transaction.get_txid(sub_tx_file=winning_child.out_file),
                winning_child,
            )

        # A stale wrapper must fail atomically, leaving its counterparty's inputs untouched.
        with pytest.raises(clusterlib.CLIError, match="BadInputsUTxO"):
            cluster.g_transaction.submit_tx_bare(tx_file=losing_signed)

        assert cluster.g_query.get_utxo(utxo=losing_inputs) == losing_before
        assert not cluster.g_query.get_utxo(tx_raw_output=losing_output)
        if losing_child:
            losing_child_id = cluster.g_sub_transaction.get_txid(sub_tx_file=losing_child.out_file)
            assert not cluster.g_query.get_utxo(
                txin=[f"{losing_child_id}#{ix}" for ix in range(losing_child.txouts_count)]
            )
        _check_outputs(offer_id, offer_output)
        assert published_offer.read_bytes() == published_bytes

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=winning_output)
        # Only the parent has an established representation in the db-sync helpers.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=winning_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_sponsored_payment(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Settle Alice's independently signed payment with a sponsor's fee.

        * Alice signs a balanced child payment without fee or collateral fields.
        * The sponsor builds and signs a parent containing only its own change.
        * Check the serialized child is unchanged and only the sponsor witnesses
          the parent. Alice never signs the parent.
        * Submit and check every parent and child input is spent, the recipient
          and Alice receive the signed outputs under the child ID, and the sponsor
          receives its input value minus exactly the parent fee as change.
        """
        temp_template = common.get_test_id(cluster)
        sponsor, alice, recipient = payment_addrs
        payment_amount = 2_000_000
        fee = 1_000_000

        # Build Alice's balanced payment and sign it independently of the sponsor.
        child_data = clusterlib.collect_data_for_build(
            clusterlib_obj=cluster,
            src_address=alice.address,
            txouts=[clusterlib.TxOut(address=recipient.address, amount=payment_amount)],
        )
        assert child_data.txins, "No inputs available for Alice's payment"
        child_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_child.body",
            txins=child_data.txins,
            txouts=child_data.txouts,
        )
        child_body_bytes = child_output.out_file.read_bytes()
        child_id = cluster.g_sub_transaction.get_txid(sub_tx_file=child_output.out_file)
        child_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=child_output.out_file,
            tx_name=temp_template,
            signing_key_files=[alice.skey_file],
        )

        # Record the signed child and check that Alice contributes no fee or collateral.
        child_signed_bytes = child_signed.read_bytes()
        child_cbor = json.loads(child_signed_bytes)["cborHex"]
        child = cbor2.loads(bytes.fromhex(child_cbor))
        # CDDL body keys: fee, collateral inputs, collateral return, total collateral.
        assert not {2, 13, 16, 17}.intersection(child[0]), (
            "Alice's child contains fee or collateral fields"
        )
        assert cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed) == child_id
        child_input_amount = clusterlib.calculate_utxos_balance(child_output.txins)
        assert sum(t.amount for t in child_output.txouts) == child_input_amount, (
            "Alice's child is not independently balanced"
        )

        # Wrap the signed child in a parent funded and signed only by the sponsor.
        parent_files = clusterlib.TxFiles(signing_key_files=[sponsor.skey_file])
        parent_output = cluster.g_transaction.build_raw_tx(
            src_address=sponsor.address,
            tx_name=temp_template,
            tx_files=parent_files,
            fee=fee,
            sub_transactions=[
                clusterlib.SignedSubTx(out_file=child_signed, tx_raw_output=child_output)
            ],
        )
        parent_signed = cluster.g_transaction.sign_tx(
            tx_body_file=parent_output.out_file,
            signing_key_files=parent_files.signing_key_files,
            tx_name=temp_template,
        )

        # Check child byte preservation and the separate child and parent witnesses.
        parent_cbor = json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"]
        parent = cbor2.loads(bytes.fromhex(parent_cbor))
        assert child_cbor in parent_cbor, "Assembly or signing changed the signed child bytes"
        assert child_output.out_file.read_bytes() == child_body_bytes
        assert child_signed.read_bytes() == child_signed_bytes
        assert parent_output.signed_sub_tx_files == [child_signed]
        sponsor_vkey = cbor2.loads(
            bytes.fromhex(json.loads(sponsor.vkey_file.read_text(encoding="utf-8"))["cborHex"])
        )
        alice_vkey = cbor2.loads(
            bytes.fromhex(json.loads(alice.vkey_file.read_text(encoding="utf-8"))["cborHex"])
        )
        assert sponsor_vkey != alice_vkey, "Alice and the sponsor must use independent keys"
        assert {w[0] for w in child[1][0]} == {alice_vkey}, "Alice must sign the child only"
        assert {w[0] for w in parent[1][0]} == {sponsor_vkey}, (
            "Only the sponsor may witness the parent"
        )
        assert parent[0][2] == fee == parent_output.fee
        parent_id = cluster.g_transaction.get_txid(tx_body_file=parent_output.out_file)
        assert cluster.g_transaction.get_txid(tx_file=parent_signed) == parent_id

        # Submit the complete batch and verify that all spending inputs are consumed.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=parent_output.txins)

        assert not cluster.g_query.get_utxo(utxo=[*parent_output.txins, *child_output.txins]), (
            "Parent or child inputs were not spent"
        )

        # Query payment and change under the child's independently computed ID.
        child_utxos = cluster.g_query.get_utxo(
            txin=[f"{child_id}#{ix}" for ix in range(child_output.txouts_count)]
        )
        assert sorted((u.address, u.amount, u.coin) for u in child_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in child_output.txouts
        ), "Confirmed child outputs differ from Alice's signed payment and change"
        assert (
            clusterlib.calculate_utxos_balance(
                clusterlib.filter_utxos(utxos=child_utxos, address=recipient.address)
            )
            == payment_amount
        )
        assert (
            clusterlib.calculate_utxos_balance(
                clusterlib.filter_utxos(utxos=child_utxos, address=alice.address)
            )
            == child_input_amount - payment_amount
        )

        # The sponsor's sole output must return its inputs minus exactly the parent fee.
        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert len(parent_utxos) == 1, "Expected only the sponsor's change output"
        assert parent_utxos[0].address == sponsor.address
        assert parent_utxos[0].utxo_hash == parent_id
        assert parent_utxos[0].amount == (
            clusterlib.calculate_utxos_balance(parent_output.txins) - fee
        ), "The sponsor did not pay exactly the parent fee"

        # Check node agreement and optional parent indexing in db-sync.
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)

        # Child indexing is not supported by the existing db-sync helpers.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_build_raw_tx(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Build and submit a transaction containing a signed sub-transaction.

        * Collect inputs and balanced outputs, including change, for the sub-transaction,
          then build and sign it.
        * Build the top-level transaction with automatically selected inputs from a
          separate source address and the signed sub-transaction.
        * Check the embedded child and the batch balance with the top-level fee.
        * Sign and submit the top-level transaction, checking that signing preserves
          both transaction IDs.
        * Verify the child's inputs are spent and its payment and change outputs exist.
        * Verify the top-level source's change and transaction presence on all nodes.
        """
        temp_template = common.get_test_id(cluster)

        top_src, sub_src, dst_addr = payment_addrs

        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=2_000_000)]
        sub_tx_files = clusterlib.TxFiles(signing_key_files=[sub_src.skey_file])

        data_for_sub_tx = clusterlib.collect_data_for_build(
            clusterlib_obj=cluster,
            src_address=sub_src.address,
            txouts=txouts,
            tx_files=sub_tx_files,
        )
        assert data_for_sub_tx.txins, "No inputs available for the sub-transaction"

        sub_tx_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_sub_tx.body",
            txins=data_for_sub_tx.txins,
            txouts=data_for_sub_tx.txouts,
        )
        sub_tx_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=sub_tx_output.out_file,
            tx_name=temp_template,
            signing_key_files=sub_tx_files.signing_key_files,
        )

        sub_txid = cluster.g_sub_transaction.get_txid(sub_tx_file=sub_tx_output.out_file)
        assert sub_txid == cluster.g_sub_transaction.get_txid(signed_sub_tx_file=sub_tx_signed), (
            "Signing changed the sub-transaction ID"
        )

        tx_files = clusterlib.TxFiles(signing_key_files=[top_src.skey_file])
        top_txouts = [clusterlib.TxOut(address=dst_addr.address, amount=3_000_000)]

        tx_output = cluster.g_transaction.build_raw_tx(
            src_address=top_src.address,
            tx_name=temp_template,
            txouts=top_txouts,
            tx_files=tx_files,
            fee=1_000_000,
            sub_transactions=[
                clusterlib.SignedSubTx(out_file=sub_tx_signed, tx_raw_output=sub_tx_output)
            ],
        )

        signed_sub_cbor = json.loads(sub_tx_signed.read_text(encoding="utf-8"))["cborHex"]
        top_cbor = json.loads(tx_output.out_file.read_text(encoding="utf-8"))["cborHex"]
        assert signed_sub_cbor in top_cbor, (
            "Signed sub-transaction is missing from the top-level body"
        )
        assert tx_output.signed_sub_tx_files == [sub_tx_signed]

        total_input = clusterlib.calculate_utxos_balance([*tx_output.txins, *sub_tx_output.txins])
        total_output = sum(t.amount for t in (*tx_output.txouts, *sub_tx_output.txouts))
        assert total_input == total_output + tx_output.fee, (
            "The top-level transaction does not balance the whole batch"
        )

        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        assert cluster.g_transaction.get_txid(
            tx_body_file=tx_output.out_file
        ) == cluster.g_transaction.get_txid(tx_file=out_file_signed), (
            "Signing changed the transaction ID"
        )

        cluster.g_transaction.submit_tx(
            tx_file=out_file_signed,
            txins=tx_output.txins,
        )

        assert not cluster.g_query.get_utxo(utxo=sub_tx_output.txins), (
            "Sub-transaction inputs were not spent"
        )
        sub_out_utxos = cluster.g_query.get_utxo(
            txin=[f"{sub_txid}#{ix}" for ix in range(sub_tx_output.txouts_count)]
        )
        assert sorted((u.address, u.amount, u.coin) for u in sub_out_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in sub_tx_output.txouts
        ), "Sub-transaction payment or change outputs are missing or incorrect"

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert clusterlib.filter_utxos(utxos=out_utxos, address=top_src.address)[
            0
        ].amount == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - sum(
            t.amount for t in top_txouts
        )

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=tx_output)
