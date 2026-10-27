"""Open swaps, Babel payments, and multi-party clearing with signed children."""

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


@pytest.mark.usefixtures("require_sub_cli")
class TestSubTransactionWorkflows:
    """Settle independently published offers through third-party assembly."""

    @pytest.fixture
    def swap_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache traders and issuer; fund only counterparties and issuer as needed."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=4,
            fund_idx=[1, 2, 3],
            caching_key=helpers.get_current_line_str(),
            amount=20_000_000,
            min_amount=18_000_000,
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
            amount=15_000_000,
            min_amount=13_000_000,
        )

    @pytest.fixture
    def clearing_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Cache three traders, aggregator fee/liquidity addresses, and a funded issuer."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=6,
            fund_idx=[5],
            caching_key=helpers.get_current_line_str(),
            amount=15_000_000,
            min_amount=13_000_000,
        )

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
        output_lovelace = 2_000_000
        counterparty_lovelace = 2_000_000
        token_amount = 5
        fee = 500_000

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
                clusterlib.TxOut(address=alice.address, amount=swap_amount + output_lovelace),
                *[
                    txout
                    for addr in (bob, carol)
                    for txout in (
                        clusterlib.TxOut(address=addr.address, amount=counterparty_lovelace),
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
                clusterlib.TxOut(address=alice.address, amount=output_lovelace),
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
                            amount=counterparty_lovelace + swap_amount,
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
                parent_amount = counterparty_lovelace + swap_amount - fee

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
        user_lovelace = 4_000_000
        payment_amount = 3_000_000
        output_lovelace = 2_000_000
        provider_lovelace = 6_000_000
        compensation = 5
        fee = 500_000
        lovelace_deficit = payment_amount + 2 * output_lovelace - user_lovelace

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
                clusterlib.TxOut(address=user.address, amount=user_lovelace),
                clusterlib.TxOut(address=user.address, amount=2 * compensation, coin=token),
                clusterlib.TxOut(address=provider.address, amount=provider_lovelace),
                clusterlib.TxOut(address=poor_provider.address, amount=output_lovelace),
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
                clusterlib.TxOut(address=user.address, amount=output_lovelace),
                clusterlib.TxOut(address=user.address, amount=compensation, coin=token),
                clusterlib.TxOut(address=provider.address, amount=output_lovelace),
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
        change = provider_lovelace - lovelace_deficit - fee
        parent_output, parent_signed = _build_wrapper("valid", provider, provider_inputs, change)
        for name, src, txins, invalid_change, extra_tokens in (
            ("missing_fee_funding", provider, provider_inputs, change + fee, 0),
            ("insufficient_provider_ada", poor_provider, poor_inputs, output_lovelace, 0),
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
            user_lovelace + provider_lovelace
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
    @pytest.mark.parametrize("needs_liquidity", (False, True), ids=("cycle", "liquidity"))
    def test_multi_party_clearing(
        self,
        cluster: clusterlib.ClusterLib,
        clearing_addrs: list[clusterlib.AddressRecord],
        needs_liquidity: bool,
        subtests: pytest_subtests.SubTests,
    ):
        """Clear three independent offers atomically, optionally using aggregator liquidity.

        * Prepare offers exchanging A for B, B for C, and C for A. Each participant
          signs only its own child and publishes it for independent assembly.
        * In the liquidity variant, Alice offers one extra A and demands one extra B;
          the aggregator supplies that B and receives the extra A in parent change.
        * Build the complete valid batch, then omit each offer in turn and remove
          required liquidity. Check node rejection leaves every input unspent and
          creates no parent or child outputs.
        * Settle the complete batch and check all signed participant outputs,
          aggregator change and compensation, spent inputs, and unused liquidity.
        """
        temp_template = common.get_test_id(cluster)
        alice, bob, carol, aggregator, liquidity_addr, issuer = clearing_addrs
        participants = (alice, bob, carol)
        names = ("alice", "bob", "carol")
        participant_lovelace = 2_000_000
        aggregator_lovelace = 3_000_000
        liquidity_lovelace = 2_000_000
        trade_amount = 10
        alice_amount = trade_amount + int(needs_liquidity)
        fee = 500_000

        # Prepare all assets and spending inputs on the ledger before publishing offers.
        tokens = clusterlib_utils.new_tokens(
            *[clusterlib.get_rand_str(8).encode().hex() for _ in participants],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_assets",
            token_mint_addr=issuer,
            issuer_addr=issuer,
            amount=trade_amount + 1,
        )
        asset_a, asset_b, asset_c = [t.token for t in tokens]
        setup_txouts = [
            clusterlib.TxOut(address=aggregator.address, amount=aggregator_lovelace),
            clusterlib.TxOut(address=liquidity_addr.address, amount=liquidity_lovelace),
            clusterlib.TxOut(address=liquidity_addr.address, amount=1, coin=asset_b),
        ]
        for participant, asset, amount in zip(
            participants,
            (asset_a, asset_b, asset_c),
            (alice_amount, trade_amount, trade_amount),
            strict=True,
        ):
            # Join each ADA/token pair into a single participant UTxO.
            setup_txouts.extend(
                [
                    clusterlib.TxOut(address=participant.address, amount=participant_lovelace),
                    clusterlib.TxOut(address=participant.address, amount=amount, coin=asset),
                ]
            )
        setup_output = cluster.g_transaction.send_tx(
            src_address=issuer.address,
            tx_name=f"{temp_template}_setup",
            txouts=setup_txouts,
            tx_files=clusterlib.TxFiles(signing_key_files=[issuer.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup_output)
        fee_inputs = clusterlib.filter_utxos(utxos=setup_utxos, address=aggregator.address)
        liquidity_inputs = clusterlib.filter_utxos(
            utxos=setup_utxos, address=liquidity_addr.address
        )

        # Each trader fixes its own payout and signs without seeing the surrounding batch.
        offers: list[clusterlib.SubTxRawOutput] = []
        published_offers: list[pl.Path] = []
        published_bytes: list[bytes] = []
        offer_ids: list[str] = []
        for name, participant, wanted_asset, amount in zip(
            names,
            participants,
            (asset_b, asset_c, asset_a),
            (alice_amount, trade_amount, trade_amount),
            strict=True,
        ):
            offer = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{temp_template}_{name}_offer.body",
                txins=clusterlib.filter_utxos(utxos=setup_utxos, address=participant.address),
                txouts=[
                    clusterlib.TxOut(address=participant.address, amount=participant_lovelace),
                    clusterlib.TxOut(address=participant.address, amount=amount, coin=wanted_asset),
                ],
            )
            signed_offer = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=offer.out_file,
                tx_name=f"{temp_template}_{name}_published",
                signing_key_files=[participant.skey_file],
            )
            offers.append(offer)
            published_offers.append(signed_offer)
            published_bytes.append(signed_offer.read_bytes())
            offer_ids.append(cluster.g_sub_transaction.get_txid(signed_sub_tx_file=signed_offer))

        def _assemble(
            name: str, selected: list[int], include_liquidity: bool
        ) -> tuple[clusterlib.TxRawOutput, pl.Path]:
            """Assemble published offers using only aggregator-controlled signing keys."""
            txins = [*fee_inputs, *(liquidity_inputs if include_liquidity else [])]
            txouts = [
                clusterlib.TxOut(
                    address=aggregator.address,
                    amount=clusterlib.calculate_utxos_balance(txins) - fee,
                )
            ]
            if needs_liquidity:
                txouts.append(clusterlib.TxOut(address=aggregator.address, amount=1, coin=asset_a))
            parent_output = cluster.g_transaction.build_raw_tx(
                src_address=aggregator.address,
                tx_name=f"{temp_template}_{name}",
                txins=txins,
                txouts=txouts,
                fee=fee,
                sub_transaction_files=[published_offers[ix] for ix in selected],
            )
            parent_signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_output.out_file,
                signing_key_files=[
                    aggregator.skey_file,
                    *([liquidity_addr.skey_file] if include_liquidity else []),
                ],
                tx_name=f"{temp_template}_{name}",
            )
            parent_cbor = json.loads(parent_signed.read_text(encoding="utf-8"))["cborHex"]
            for ix in selected:
                assert json.loads(published_bytes[ix])["cborHex"] in parent_cbor, (
                    "Assembly changed a participant's signed offer"
                )
            return parent_output, parent_signed

        # Establish the full funded baseline before constructing incomplete batches.
        parent_output, parent_signed = _assemble("complete", [0, 1, 2], needs_liquidity)
        all_inputs = [u for offer in offers for u in offer.txins] + fee_inputs + liquidity_inputs
        inputs_before = cluster.g_query.get_utxo(utxo=all_inputs)
        child_refs = [
            f"{txid}#{ix}"
            for txid, offer in zip(offer_ids, offers, strict=True)
            for ix in range(offer.txouts_count)
        ]

        # Missing offers leave asset deficits that parent ADA change cannot cover.
        invalid_batches = [
            (f"omit_{name}", [ix for ix in range(3) if ix != omitted], needs_liquidity)
            for omitted, name in enumerate(names)
        ]
        if needs_liquidity:
            invalid_batches.append(("missing_liquidity", [0, 1, 2], False))
        for name, selected, include_liquidity in invalid_batches:
            with subtests.test(scenario=name):
                invalid_output, invalid_signed = _assemble(name, selected, include_liquidity)
                with pytest.raises(clusterlib.CLIError, match="ValueNotConservedUTxO"):
                    cluster.g_transaction.submit_tx_bare(tx_file=invalid_signed)

                assert cluster.g_query.get_utxo(utxo=all_inputs) == inputs_before
                assert not cluster.g_query.get_utxo(txin=child_refs)
                assert not cluster.g_query.get_utxo(tx_raw_output=invalid_output)

        # Settle all three offers atomically and verify every participant's fixed payout.
        cluster.g_transaction.submit_tx(tx_file=parent_signed, txins=parent_output.txins)
        assert not cluster.g_query.get_utxo(
            utxo=[u for offer in offers for u in offer.txins] + parent_output.txins
        ), "Some inputs of the complete clearing batch remain unspent"
        for txid, offer, published, original in zip(
            offer_ids, offers, published_offers, published_bytes, strict=True
        ):
            utxos = cluster.g_query.get_utxo(
                txin=[f"{txid}#{ix}" for ix in range(offer.txouts_count)]
            )
            assert sorted((u.address, u.amount, u.coin) for u in utxos) == sorted(
                (t.address, t.amount, t.coin) for t in offer.txouts
            ), "A participant did not receive its independently signed payout"
            assert published.read_bytes() == original

        # The aggregator pays the ADA fee and, when needed, exchanges one B for one A.
        parent_utxos = cluster.g_query.get_utxo(tx_raw_output=parent_output)
        assert sorted((u.address, u.amount, u.coin) for u in parent_utxos) == sorted(
            (t.address, t.amount, t.coin) for t in parent_output.txouts
        ), "Aggregator change or liquidity compensation is incorrect"
        if not needs_liquidity:
            assert cluster.g_query.get_utxo(utxo=liquidity_inputs) == liquidity_inputs

        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=parent_output)
        # Child payouts are checked through the node; only the parent has a db-sync helper.
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=parent_output)
