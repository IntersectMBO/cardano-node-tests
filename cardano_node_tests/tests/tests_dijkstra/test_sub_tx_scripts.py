"""Batch-wide native/V4 script supply with transaction-local authorization."""

import dataclasses
import json
import pathlib as pl
import typing as tp

import allure
import cbor2
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_dijkstra import sub_tx_utils
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency
from cardano_node_tests.utils.versions import VERSIONS

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST,
    reason="Sub-transactions are available only in Dijkstra+ eras",
)


@pytest.fixture
def cluster(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Protect V4 execution parameters for the shared script-supply matrix."""
    return cluster_manager.get(use_resources=[cluster_management.Resources.PLUTUS])


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Cache script owner, sponsor, sibling owner, recipient and collateral owner."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=5,
        fund_idx=[0, 1, 2, 4],
        amount=5_000_000,
        min_amount=4_000_000,
        caching_key=helpers.get_current_line_str(),
    )


@pytest.fixture
def native_script(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    payment_addrs: list[clusterlib.AddressRecord],
) -> pl.Path:
    """Cache a native signature script controlled by the first participant."""
    cache: cluster_management.FixtureCache[pl.Path | None]
    with cluster_manager.cache_fixture() as cache:
        if cache.value is None:
            script_file = pl.Path(f"{common.get_test_id(cluster)}_native.script")
            key_hash = cluster.g_address.get_payment_vkey_hash(
                payment_vkey_file=payment_addrs[0].vkey_file
            )
            script_file.write_text(
                json.dumps({"type": "sig", "keyHash": key_hash}), encoding="utf-8"
            )
            cache.value = script_file
        return cache.value


class TestSubTransactionScriptSupply:
    """Share script bytes without sharing native key authorization."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize(
        "script_kind", ("native", pytest.param("v4", marks=pytest.mark.plutus))
    )
    @pytest.mark.parametrize(
        "supply", ("parent", "sibling", "reference", "missing", "unused", "parent_key")
    )
    def test_script_supply(  # noqa: C901
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        native_script: pl.Path,
        supply: str,
        script_kind: str,
    ):
        """Resolve child native/V4 script bytes batch-wide while keeping keys local.

        Supply bytes in the parent, sibling, or a parent reference input. Reject
        missing script bytes, an extraneous script, or native authorization
        supplied only by the parent. Check rejection has no effects and settle
        a correctly witnessed control. Confirm exact child and parent outputs.
        """
        is_plutus = script_kind == "v4"
        if is_plutus and supply == "parent_key":
            pytest.skip("The context-independent V4 script does not require a key witness")
        temp_template = common.get_test_id(cluster)
        owner, sponsor, sibling_owner, recipient, collateral = payment_addrs
        script_file = plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V4 if is_plutus else native_script
        script_witness_key = 8 if is_plutus else 1
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_script", payment_script_file=script_file
        )
        funding_txouts = [
            clusterlib.TxOut(
                address=script_address,
                amount=2_000_000,
                inline_datum_value="42" if is_plutus else "",
            )
        ]
        if supply == "reference":
            funding_txouts.append(
                clusterlib.TxOut(
                    address=recipient.address, amount=1_500_000, reference_script_file=script_file
                )
            )
        funding = cluster.g_transaction.send_tx(
            src_address=owner.address,
            tx_name=f"{temp_template}_lock",
            txins=cluster.g_query.get_utxo(address=owner.address),
            txouts=funding_txouts,
            tx_files=clusterlib.TxFiles(signing_key_files=[owner.skey_file]),
        )
        funded = cluster.g_query.get_utxo(tx_raw_output=funding)
        script_inputs = clusterlib.filter_utxos(funded, address=script_address)
        reference_inputs = [u for u in funded if u.reference_script]
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        sibling_inputs = cluster.g_query.get_utxo(address=sibling_owner.address)
        collateral_inputs = (
            cluster.g_query.get_utxo(address=collateral.address) if is_plutus else []
        )
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        sibling_lovelace = clusterlib.calculate_utxos_balance(sibling_inputs)
        addresses = [a.address for a in payment_addrs]
        before = cluster.g_query.get_utxo(address=addresses)

        # Build the spending body once; moving script bytes preserves its integrity data.
        script_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_spender_seed.body",
            txouts=[clusterlib.TxOut(address=recipient.address, amount=2_000_000)],
            script_txins=[
                clusterlib.ScriptTxIn(
                    txins=script_inputs,
                    script_file=script_file,
                    inline_datum_present=is_plutus,
                    redeemer_value="0" if is_plutus else "",
                    execution_units=(1_000_000, 10_000) if is_plutus else None,
                )
            ],
        )
        seed = cbor2.loads(bytes.fromhex(json.loads(script_output.out_file.read_text())["cborHex"]))
        assert script_witness_key in seed[1]
        script_witnesses = {script_witness_key: seed[1][script_witness_key]}
        stripped = sub_tx_utils.replace_witnesses(
            tx_file=script_output.out_file,
            out_file=pl.Path(f"{temp_template}_spender.body"),
            witnesses={k: v for k, v in seed[1].items() if k != script_witness_key},
        )
        spender_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=stripped,
            tx_name=f"{temp_template}_spender",
            signing_key_files=[] if supply == "parent_key" else [owner.skey_file],
        )
        spender = sub_tx_utils.SignedChild(
            output=script_output,
            signed_file=spender_signed,
            txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=spender_signed),
        )
        sibling_output = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_sibling_seed.body",
            txins=sibling_inputs,
            txouts=[clusterlib.TxOut(address=sibling_owner.address, amount=sibling_lovelace)],
        )
        sibling_file = sibling_output.out_file
        if supply == "sibling":
            sibling_file = sub_tx_utils.replace_witnesses(
                tx_file=sibling_file,
                out_file=pl.Path(f"{temp_template}_sibling.body"),
                witnesses=script_witnesses,
            )
        sibling_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=sibling_file,
            tx_name=f"{temp_template}_sibling",
            signing_key_files=[sibling_owner.skey_file],
        )
        sibling = sub_tx_utils.SignedChild(
            output=sibling_output,
            signed_file=sibling_signed,
            txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=sibling_signed),
        )

        output = cluster.g_transaction.build_raw_tx(
            src_address=sponsor.address,
            tx_name=f"{temp_template}_parent_seed",
            txins=sponsor_inputs,
            txouts=[clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace - 500_000)],
            readonly_reference_txins=reference_inputs,
            collateral_txins=collateral_inputs,
            sub_transaction_files=[spender_signed, sibling_signed],
            fee=500_000,
        )
        parent_file = output.out_file
        if supply in ("parent", "unused", "parent_key"):
            witnesses = dict(script_witnesses)
            if supply == "unused":
                # This second signature script is not required by any component.
                extra_hash = cluster.g_address.get_payment_vkey_hash(
                    payment_vkey_file=sponsor.vkey_file
                )
                witnesses[1] = [*witnesses.get(1, []), [0, bytes.fromhex(extra_hash)]]
            parent_file = sub_tx_utils.replace_witnesses(
                tx_file=parent_file,
                out_file=pl.Path(f"{temp_template}_parent.body"),
                witnesses=witnesses,
            )
            output = dataclasses.replace(output, out_file=parent_file)
        parent_keys = (
            [sponsor.skey_file, collateral.skey_file] if is_plutus else [sponsor.skey_file]
        )
        if supply == "parent_key":
            parent_keys.append(owner.skey_file)
        signed_parent = cluster.g_transaction.sign_tx(
            tx_body_file=parent_file,
            tx_name=f"{temp_template}_parent",
            signing_key_files=parent_keys,
        )

        if supply in ("missing", "unused", "parent_key"):
            expected = {
                "missing": "MissingScriptWitnessesUTXOW",
                "unused": "ExtraneousScriptWitnessesUTXOW",
                "parent_key": "ScriptWitnessNotValidatingUTXOW",
            }[supply]
            with pytest.raises(clusterlib.CLIError, match=expected):
                cluster.g_transaction.submit_tx_bare(tx_file=signed_parent)

            assert cluster.g_query.get_utxo(address=addresses) == before
            assert cluster.g_query.get_utxo(utxo=script_inputs) == script_inputs
            assert not cluster.g_query.get_utxo(tx_raw_output=output)
            assert not cluster.g_query.get_utxo(txin=[f"{spender.txid}#0", f"{sibling.txid}#0"])

            # Restore local script bytes and key authorization without changing spending terms.
            restored = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=script_output.out_file,
                tx_name=f"{temp_template}_restored",
                signing_key_files=[owner.skey_file],
            )
            spender = dataclasses.replace(spender, signed_file=restored)
            output, signed_parent = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{temp_template}_control",
                sponsor=sponsor,
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace - 500_000)
                ],
                children=[spender, sibling],
                collateral_inputs=collateral_inputs,
                signing_keys=parent_keys,
            )

        cluster.g_transaction.submit_tx(tx_file=signed_parent, txins=sponsor_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*script_inputs, *sponsor_inputs, *sibling_inputs])
        if reference_inputs:
            assert cluster.g_query.get_utxo(utxo=reference_inputs) == reference_inputs
        if collateral_inputs:
            assert cluster.g_query.get_utxo(utxo=collateral_inputs) == collateral_inputs
        for child in (spender, sibling):
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
        assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
            sponsor_lovelace - 500_000
        ]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.plutus
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_shared_script_separate_executions(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Execute one shared V4 program separately for each child's local budget.

        Spend two distinct script inputs with distinct local redeemers, supplying
        the script bytes once in the parent. Give either child a zero budget;
        the other child's successful execution cannot substitute for it. Reject
        each case without effects, then settle with both local budgets funded.
        The context-independent program does not verify business predicates.
        """
        temp_template = common.get_test_id(cluster)
        owner, sponsor, _, recipient, collateral = payment_addrs
        script_file = plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V4
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_script", payment_script_file=script_file
        )

        # Distinct original outputs are essential; joining would create one double-spend.
        funding = cluster.g_transaction.send_tx(
            src_address=owner.address,
            tx_name=f"{temp_template}_lock",
            txins=cluster.g_query.get_utxo(address=owner.address),
            txouts=[
                clusterlib.TxOut(
                    address=script_address, amount=1_500_000, inline_datum_value=str(ix)
                )
                for ix in range(2)
            ],
            join_txouts=False,
            tx_files=clusterlib.TxFiles(signing_key_files=[owner.skey_file]),
        )
        script_inputs = clusterlib.filter_utxos(
            cluster.g_query.get_utxo(tx_raw_output=funding), address=script_address
        )
        assert len(script_inputs) == 2
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        collateral_inputs = cluster.g_query.get_utxo(address=collateral.address)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        addresses = [a.address for a in payment_addrs]
        before = cluster.g_query.get_utxo(address=addresses)

        for failing_index in (0, 1, -1):
            name = f"{temp_template}_budget_{failing_index}"
            children = []
            shared_witnesses: dict[int, tp.Any] = {}
            for ix, txin in enumerate(script_inputs):
                # Each child retains its own redeemer and integrity hash after script removal.
                child_output = cluster.g_sub_transaction.build_raw_tx_bare(
                    out_file=f"{name}_child_{ix}_seed.body",
                    txouts=[clusterlib.TxOut(address=recipient.address, amount=1_500_000)],
                    script_txins=[
                        clusterlib.ScriptTxIn(
                            txins=[txin],
                            script_file=script_file,
                            inline_datum_present=True,
                            redeemer_value=str(ix + 10),
                            execution_units=(0, 0) if ix == failing_index else (1_000_000, 10_000),
                        )
                    ],
                )
                encoded = bytes.fromhex(json.loads(child_output.out_file.read_text())["cborHex"])
                witnesses = cbor2.loads(encoded)[1]
                if shared_witnesses:
                    assert shared_witnesses[8] == witnesses[8]
                shared_witnesses = {8: witnesses.pop(8)}
                stripped = sub_tx_utils.replace_witnesses(
                    tx_file=child_output.out_file,
                    out_file=pl.Path(f"{name}_child_{ix}.body"),
                    witnesses=witnesses,
                )
                signed = cluster.g_sub_transaction.sign_tx(
                    sub_tx_file=stripped, tx_name=f"{name}_child_{ix}"
                )
                children.append(
                    sub_tx_utils.SignedChild(
                        output=child_output,
                        signed_file=signed,
                        txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=signed),
                    )
                )

            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{name}_parent_seed",
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace - 500_000)
                ],
                collateral_txins=collateral_inputs,
                sub_transaction_files=[c.signed_file for c in children],
                fee=500_000,
            )
            parent_file = sub_tx_utils.replace_witnesses(
                tx_file=output.out_file,
                out_file=pl.Path(f"{name}_parent.body"),
                witnesses=shared_witnesses,
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_file,
                tx_name=f"{name}_parent",
                signing_key_files=[sponsor.skey_file, collateral.skey_file],
            )
            encoded_parent = bytes.fromhex(json.loads(signed.read_text())["cborHex"])
            assert len(cbor2.loads(encoded_parent)[1][8]) == 1
            for child in children:
                assert (
                    bytes.fromhex(json.loads(child.signed_file.read_text())["cborHex"])
                    in encoded_parent
                )
            output = dataclasses.replace(output, out_file=parent_file)

            if failing_index >= 0:
                with (
                    subtests.test(failing_child=failing_index),
                    pytest.raises(clusterlib.CLIError, match="ValidationTagMismatch") as excinfo,
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert "The PlutusV4 script failed" in str(excinfo.value)
                assert "budget" in str(excinfo.value).lower()
                assert cluster.g_query.get_utxo(address=addresses) == before
                assert cluster.g_query.get_utxo(utxo=script_inputs) == script_inputs
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(txin=[f"{c.txid}#0" for c in children])
                continue

            # Funding both local budgets settles both actions with one copy of the program.
            cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
            assert not cluster.g_query.get_utxo(utxo=[*script_inputs, *sponsor_inputs])
            assert cluster.g_query.get_utxo(utxo=collateral_inputs) == collateral_inputs
            for child in children:
                sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
                sponsor_lovelace - 500_000
            ]
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)


class TestSubTransactionLegacyMode:
    """Keep legacy parent balance and context restrictions separate from child features."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.plutus
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("plutus_version", ("v1", "v2", "v3"))
    def test_legacy_parent_balance(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        plutus_version: str,
    ):
        """Enforce legacy parent balance even when the complete batch balances.

        Execute a passing older script in the parent. Reject a balanced batch
        that transfers parent ADA into a child's imbalance. Then confirm two
        complementary children whose net imbalance is zero and whose inline
        datum output remains isolated from the parent's legacy script context.
        For V1, reject that same newer output feature directly in the parent.
        """
        temp_template = common.get_test_id(cluster)
        owner, sponsor, sibling_owner, recipient, collateral = payment_addrs
        script_file = plutus_common.ALWAYS_SUCCEEDS[plutus_version].script_file
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_script", payment_script_file=script_file
        )

        # A hashed datum works with all three older context versions.
        funding = cluster.g_transaction.send_tx(
            src_address=owner.address,
            tx_name=f"{temp_template}_lock",
            txins=cluster.g_query.get_utxo(address=owner.address),
            txouts=[
                clusterlib.TxOut(address=script_address, amount=1_500_000, datum_hash_value="42")
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[owner.skey_file]),
        )
        script_inputs = clusterlib.filter_utxos(
            cluster.g_query.get_utxo(tx_raw_output=funding), address=script_address
        )
        owner_inputs = cluster.g_query.get_utxo(address=owner.address)
        sibling_inputs = cluster.g_query.get_utxo(address=sibling_owner.address)
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        collateral_inputs = cluster.g_query.get_utxo(address=collateral.address)
        owner_lovelace = clusterlib.calculate_utxos_balance(owner_inputs)
        sibling_lovelace = clusterlib.calculate_utxos_balance(sibling_inputs)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        script_input = clusterlib.ScriptTxIn(
            txins=script_inputs,
            script_file=script_file,
            datum_value="42",
            redeemer_value="0",
            execution_units=(1_000_000, 10_000),
        )
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])
        first_child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_first",
            txins=owner_inputs,
            txouts=[
                clusterlib.TxOut(
                    address=owner.address, amount=owner_lovelace + 1_000_000, inline_datum_value="7"
                )
            ],
            signing_keys=[owner.skey_file],
        )

        for balanced_children in (False, True):
            name = f"{temp_template}_{'balanced' if balanced_children else 'parent_imbalance'}"
            second_child = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{name}_second",
                txins=sibling_inputs,
                txouts=[
                    clusterlib.TxOut(
                        address=sibling_owner.address,
                        amount=sibling_lovelace - (1_000_000 if balanced_children else 0),
                    )
                ],
                signing_keys=[sibling_owner.skey_file],
            )
            # Both batches balance globally. Only the control balances its parent independently.
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{name}_parent",
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(address=recipient.address, amount=1_500_000),
                    clusterlib.TxOut(
                        address=sponsor.address,
                        amount=sponsor_lovelace - 500_000 - (0 if balanced_children else 1_000_000),
                    ),
                ],
                script_txins=[script_input],
                collateral_txins=collateral_inputs,
                sub_transaction_files=[first_child.signed_file, second_child.signed_file],
                fee=500_000,
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=output.out_file,
                tx_name=f"{name}_parent",
                signing_key_files=[sponsor.skey_file, collateral.skey_file],
            )
            if not balanced_children:
                with pytest.raises(clusterlib.CLIError, match="ValueNotConservedInLegacyMode"):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert (
                    cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
                )
                assert cluster.g_query.get_utxo(utxo=script_inputs) == script_inputs
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(
                    txin=[f"{first_child.txid}#0", f"{second_child.txid}#0"]
                )
                continue

            if plutus_version == "v1":
                # V1 cannot translate a parent inline datum, unlike a child-only inline datum.
                unsupported = cluster.g_transaction.build_raw_tx(
                    src_address=sponsor.address,
                    tx_name=f"{name}_parent_inline",
                    txins=sponsor_inputs,
                    txouts=[
                        clusterlib.TxOut(address=recipient.address, amount=1_500_000),
                        clusterlib.TxOut(
                            address=sponsor.address,
                            amount=sponsor_lovelace - 500_000,
                            inline_datum_value="7",
                        ),
                    ],
                    script_txins=[script_input],
                    collateral_txins=collateral_inputs,
                    sub_transaction_files=[first_child.signed_file, second_child.signed_file],
                    fee=500_000,
                )
                unsupported_signed = cluster.g_transaction.sign_tx(
                    tx_body_file=unsupported.out_file,
                    tx_name=f"{name}_parent_inline",
                    signing_key_files=[sponsor.skey_file, collateral.skey_file],
                )
                with pytest.raises(clusterlib.CLIError, match="InlineDatumsNotSupported"):
                    cluster.g_transaction.submit_tx_bare(tx_file=unsupported_signed)

                assert (
                    cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
                )
                assert cluster.g_query.get_utxo(utxo=script_inputs) == script_inputs
                assert not cluster.g_query.get_utxo(tx_raw_output=unsupported)

            cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
            assert not cluster.g_query.get_utxo(
                utxo=[*script_inputs, *owner_inputs, *sibling_inputs, *sponsor_inputs]
            )
            assert cluster.g_query.get_utxo(utxo=collateral_inputs) == collateral_inputs
            for child in (first_child, second_child):
                sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert cluster.g_query.get_utxo(txin=f"{first_child.txid}#0")[0].inline_datum
            assert sorted(
                (u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=output)
            ) == sorted((o.address, o.amount) for o in output.txouts)
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)


class TestSubTransactionConfirmedOutputs:
    """Use confirmed parent/child outputs and stored older scripts normally."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.plutus
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_confirmed_output_usability(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
    ):
        """Spend parent/child outputs and execute a V3 script stored by a child.

        Confirm a normal-mode batch whose child creates a key output carrying
        an inline datum and V3 reference script. A parent-to-child imbalance
        proves storing old script bytes does not activate legacy balance rules.
        In a later ordinary transaction, use the parent's output for fees and
        the child's predicted output as the reference source for a V3 spend.
        Finally spend the child key output itself. Check exact original output
        references, stored datum/script, consumed inputs, and untouched collateral.
        """
        temp_template = common.get_test_id(cluster)
        owner, sponsor, child_owner, recipient, collateral = payment_addrs
        script_file = plutus_common.ALWAYS_SUCCEEDS["v3"].script_file
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_script", payment_script_file=script_file
        )

        # Prepare a script input before the batch, but do not supply a reference script yet.
        funding = cluster.g_transaction.send_tx(
            src_address=owner.address,
            tx_name=f"{temp_template}_lock",
            txouts=[
                clusterlib.TxOut(address=script_address, amount=1_500_000, inline_datum_value="42")
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[owner.skey_file]),
        )
        script_inputs = clusterlib.filter_utxos(
            cluster.g_query.get_utxo(tx_raw_output=funding), address=script_address
        )
        child_inputs = cluster.g_query.get_utxo(address=child_owner.address)
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        collateral_inputs = cluster.g_query.get_utxo(address=collateral.address)
        child_lovelace = clusterlib.calculate_utxos_balance(child_inputs)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        contribution_lovelace = 500_000

        # Store V3 bytes without executing any old script in the normal-mode batch.
        child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_child",
            txins=child_inputs,
            txouts=[
                clusterlib.TxOut(
                    address=recipient.address,
                    amount=child_lovelace + contribution_lovelace,
                    inline_datum_value="7",
                    reference_script_file=script_file,
                )
            ],
            signing_keys=[child_owner.skey_file],
        )
        output, signed = sub_tx_utils.build_parent(
            cluster=cluster,
            name=f"{temp_template}_batch",
            sponsor=sponsor,
            txins=sponsor_inputs,
            txouts=[
                clusterlib.TxOut(
                    address=sponsor.address,
                    amount=sponsor_lovelace - contribution_lovelace - 500_000,
                )
            ],
            children=[child],
        )
        cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
        sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
        reference_inputs = cluster.g_query.get_utxo(txin=f"{child.txid}#0")
        assert len(reference_inputs) == 1
        reference = reference_inputs[0]
        assert reference.inline_datum == {"int": 7}
        assert reference.reference_script
        clusterlib_utils.check_reference_script_policyid(
            name_template=f"{temp_template}_stored_script",
            cluster_obj=cluster,
            script_file=script_file,
            script_data=reference.reference_script["script"],
        )
        parent_inputs = cluster.g_query.get_utxo(tx_raw_output=output)
        assert [(u.address, u.amount) for u in parent_inputs] == [
            (sponsor.address, sponsor_lovelace - contribution_lovelace - output.fee)
        ]
        assert not cluster.g_query.get_utxo(utxo=[*child_inputs, *sponsor_inputs])
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)

        # The confirmed child reference now supplies V3 execution in an ordinary parent.
        spend = cluster.g_transaction.build_raw_tx(
            src_address=sponsor.address,
            tx_name=f"{temp_template}_reference_spend",
            txins=parent_inputs,
            txouts=[
                clusterlib.TxOut(
                    address=sponsor.address,
                    amount=clusterlib.calculate_utxos_balance(parent_inputs) + 1_500_000 - 500_000,
                )
            ],
            script_txins=[
                clusterlib.ScriptTxIn(
                    txins=script_inputs,
                    reference_txin=reference,
                    reference_type=clusterlib.ScriptTypes.PLUTUS_V3,
                    inline_datum_present=True,
                    redeemer_value="0",
                    execution_units=(1_000_000, 10_000),
                )
            ],
            collateral_txins=collateral_inputs,
            fee=500_000,
        )
        spend_signed = cluster.g_transaction.sign_tx(
            tx_body_file=spend.out_file,
            tx_name=f"{temp_template}_reference_spend",
            signing_key_files=[sponsor.skey_file, collateral.skey_file],
        )
        cluster.g_transaction.submit_tx(tx_file=spend_signed, txins=parent_inputs)
        assert not cluster.g_query.get_utxo(utxo=[*parent_inputs, *script_inputs])
        assert cluster.g_query.get_utxo(utxo=reference_inputs) == reference_inputs
        assert cluster.g_query.get_utxo(utxo=collateral_inputs) == collateral_inputs
        assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=spend)] == [
            clusterlib.calculate_utxos_balance(parent_inputs) + 1_500_000 - spend.fee
        ]
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=spend)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=spend)

        # Output attachments do not stop an ordinary key spend of the child output itself.
        child_spend = cluster.g_transaction.send_tx(
            src_address=recipient.address,
            tx_name=f"{temp_template}_child_spend",
            txins=reference_inputs,
            txouts=[clusterlib.TxOut(address=recipient.address, amount=1_500_000)],
            tx_files=clusterlib.TxFiles(signing_key_files=[recipient.skey_file]),
        )
        assert not cluster.g_query.get_utxo(utxo=reference_inputs)
        assert (
            clusterlib.calculate_utxos_balance(cluster.g_query.get_utxo(tx_raw_output=child_spend))
            == child_lovelace + contribution_lovelace - child_spend.fee
        )
        node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=child_spend)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=child_spend)
