"""Non-Plutus required top-level guards and transaction-local guard scope."""

import dataclasses
import hashlib
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
def guard_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Cache two users, sponsor, key guard, and native signature guard owner."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=5,
        fund_idx=[0, 1, 2],
        amount=3_000_000,
        min_amount=2_500_000,
        caching_key=helpers.get_current_line_str(),
    )


@pytest.fixture
def native_guard(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    guard_addrs: list[clusterlib.AddressRecord],
) -> pl.Path:
    """Cache an independent native script guard, without a Plutus fixture."""
    cache: cluster_management.FixtureCache[pl.Path | None]
    with cluster_manager.cache_fixture() as cache:
        if cache.value is None:
            script = pl.Path(f"{common.get_test_id(cluster)}_native_guard.script")
            key_hash = cluster.g_address.get_payment_vkey_hash(
                payment_vkey_file=guard_addrs[4].vkey_file
            )
            script.write_text(json.dumps({"type": "sig", "keyHash": key_hash}), encoding="utf-8")
            cache.value = script
        return cache.value


@pytest.fixture
def require_guard_script(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    guard_addrs: list[clusterlib.AddressRecord],
    nested_timelock: bool,
) -> pl.Path:
    """Cache a native key-guard predicate, optionally conjoined with a lower bound."""
    cache: cluster_management.FixtureCache[pl.Path | None]
    with cluster_manager.cache_fixture(
        key=f"{helpers.get_current_line_str()}_{nested_timelock}"
    ) as cache:
        if cache.value is None:
            key_hash = cluster.g_address.get_payment_vkey_hash(
                payment_vkey_file=guard_addrs[3].vkey_file
            )
            predicate = {"type": "guard", "credential": {"keyHash": key_hash}}
            script_data = (
                {"type": "all", "scripts": [predicate, {"type": "after", "slot": 1}]}
                if nested_timelock
                else predicate
            )
            script = pl.Path(f"{common.get_test_id(cluster)}_require_guard.script")
            script.write_text(json.dumps(script_data), encoding="utf-8")
            cache.value = script
        return cache.value


class TestRequiredTopLevelGuards:
    """Require explicit enclosing guard entries and their enclosing authorization."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("guard_kind", ("key", "native"))
    def test_parent_required_guard_field(
        self,
        cluster: clusterlib.ClusterLib,
        guard_addrs: list[clusterlib.AddressRecord],
        native_guard: pl.Path,
        guard_kind: str,
        subtests: pytest_subtests.SubTests,
    ):
        """Require and authorize a guard declared in the parent's own requirement field.

        Put a key/native requirement with no argument on the parent, not its
        child. Reject a signed parent lacking the guard entry and a parent
        whose guard lacks its signature. Confirm the fully authorized parent
        with unchanged child bytes. This establishes current non-legacy field
        compatibility, not V4 datum support or normative CIP conformance.
        """
        temp_template = common.get_test_id(cluster)
        owner, _, sponsor, key_owner, native_owner = guard_addrs
        guard_owner = key_owner if guard_kind == "key" else native_owner
        owner_inputs = cluster.g_query.get_utxo(address=owner.address)
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_child",
            txins=owner_inputs,
            txouts=[
                clusterlib.TxOut(
                    address=owner.address, amount=clusterlib.calculate_utxos_balance(owner_inputs)
                )
            ],
            signing_keys=[owner.skey_file],
        )

        # Let the child CLI encode the parent-compatible guard fields and witness script.
        requirement = (
            clusterlib.RequiredTopLevelGuard(verification_key_file=key_owner.vkey_file)
            if guard_kind == "key"
            else clusterlib.RequiredTopLevelGuard(script_file=native_guard)
        )
        guard = (
            clusterlib.TxGuard(verification_key_file=key_owner.vkey_file)
            if guard_kind == "key"
            else clusterlib.TxGuard(script_file=native_guard)
        )
        seed = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_fields.body",
            txins=sponsor_inputs,
            txouts=[clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace)],
            guards=[guard],
            top_level_guards=[requirement],
        )
        seed_data = cbor2.loads(bytes.fromhex(json.loads(seed.out_file.read_text())["cborHex"]))
        assert len(seed_data[0][24]) == 1 and set(seed_data[0][24].values()) == {None}
        before = cluster.g_query.get_utxo(address=[a.address for a in guard_addrs])

        for scenario in ("missing_entry", "missing_signature", "authorized"):
            name = f"{temp_template}_{scenario}"
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{name}_seed",
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace - 500_000)
                ],
                sub_transaction_files=[child.signed_file],
                fee=500_000,
            )
            parent_file = sub_tx_utils.append_body_field(
                tx_file=output.out_file,
                out_file=pl.Path(f"{name}_required.body"),
                key=24,
                value=seed_data[0][24],
            )
            if scenario != "missing_entry":
                guarded = sub_tx_utils.append_body_field(
                    tx_file=parent_file,
                    out_file=pl.Path(f"{name}_guards.body"),
                    key=14,
                    value=seed_data[0][14],
                )
                parent_file = sub_tx_utils.replace_witnesses(
                    tx_file=guarded,
                    out_file=pl.Path(f"{name}_witnessed.body"),
                    witnesses=seed_data[1],
                )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_file,
                tx_name=f"{name}_parent",
                signing_key_files=[sponsor.skey_file]
                + ([] if scenario == "missing_signature" else [guard_owner.skey_file]),
            )
            output = dataclasses.replace(output, out_file=parent_file)
            assert (
                json.loads(child.signed_file.read_text())["cborHex"]
                in json.loads(signed.read_text())["cborHex"]
            )
            if scenario != "authorized":
                expected = (
                    "MissingRequiredGuards"
                    if scenario == "missing_entry"
                    else "MissingVKeyWitnessesUTXOW"
                    if guard_kind == "key"
                    else "ScriptWitnessNotValidatingUTXOW"
                )
                with (
                    subtests.test(scenario=scenario),
                    pytest.raises(clusterlib.CLIError, match=expected),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)
                assert cluster.g_query.get_utxo(address=[a.address for a in guard_addrs]) == before
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(txin=f"{child.txid}#0")
                continue

            # A real node accepts the parent field and enforces its declared requirement.
            cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
            assert not cluster.g_query.get_utxo(utxo=[*owner_inputs, *sponsor_inputs])
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
                sponsor_lovelace - output.fee
            ]
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_shared_key_and_native_guards(
        self,
        cluster: clusterlib.ClusterLib,
        guard_addrs: list[clusterlib.AddressRecord],
        native_guard: pl.Path,
        subtests: pytest_subtests.SubTests,
    ):
        """Satisfy two children's shared key/native requirements only at parent level.

        Independently require a key and native guard without arguments in both
        children. Reject each missing parent entry or signature, and reject
        satisfaction solely through a child-local guard. Explicit parent guards
        and their own authorization settle the unchanged payment terms.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, key_owner, native_owner = guard_addrs
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in guard_addrs[:3]]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        requirements = [
            clusterlib.RequiredTopLevelGuard(verification_key_file=key_owner.vkey_file),
            clusterlib.RequiredTopLevelGuard(script_file=native_guard),
        ]
        guards = [
            clusterlib.TxGuard(verification_key_file=key_owner.vkey_file),
            clusterlib.TxGuard(script_file=native_guard),
        ]

        # The CLI can build child guards; transplant its parent-compatible field and bytes.
        seed = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_guard_seed.body",
            txins=inputs[2],
            txouts=[clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2])],
            guards=guards,
        )
        seed_data = cbor2.loads(bytes.fromhex(json.loads(seed.out_file.read_text())["cborHex"]))
        assert {cred[0] for cred in seed_data[0][14]} == {0, 1}
        assert 1 in seed_data[1] and 11 not in seed_data[0]
        before = cluster.g_query.get_utxo(address=[a.address for a in guard_addrs])

        for scenario in (
            "missing_key_guard",
            "missing_native_guard",
            "missing_key_signature",
            "missing_native_signature",
            "key_child_only",
            "native_child_only",
            "authorized",
        ):
            name = f"{temp_template}_{scenario}"
            children = []
            for ix, owner in enumerate((first, second)):
                child_guards = []
                child_keys = [owner.skey_file]
                if ix == 0 and scenario in ("key_child_only", "native_child_only"):
                    is_key = scenario == "key_child_only"
                    child_guards = [guards[0 if is_key else 1]]
                    child_keys.append(key_owner.skey_file if is_key else native_owner.skey_file)
                child_output = cluster.g_sub_transaction.build_raw_tx_bare(
                    out_file=f"{name}_child_{ix}.body",
                    txins=inputs[ix],
                    txouts=[clusterlib.TxOut(address=owner.address, amount=balances_lovelace[ix])],
                    top_level_guards=requirements,
                    guards=child_guards,
                )
                signed = cluster.g_sub_transaction.sign_tx(
                    sub_tx_file=child_output.out_file,
                    tx_name=f"{name}_child_{ix}",
                    signing_key_files=child_keys,
                )
                children.append(
                    sub_tx_utils.SignedChild(
                        output=child_output,
                        signed_file=signed,
                        txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=signed),
                    )
                )

            omitted_guard_kind = {
                "missing_key_guard": 0,
                "key_child_only": 0,
                "missing_native_guard": 1,
                "native_child_only": 1,
            }.get(scenario, -1)
            parent_guards = [cred for cred in seed_data[0][14] if cred[0] != omitted_guard_kind]
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{name}_parent_seed",
                txins=inputs[2],
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
                ],
                sub_transaction_files=[c.signed_file for c in children],
                fee=500_000,
            )
            guarded = sub_tx_utils.append_body_field(
                tx_file=output.out_file,
                out_file=pl.Path(f"{name}_guarded.body"),
                key=14,
                value=parent_guards,
            )
            witnessed = sub_tx_utils.replace_witnesses(
                tx_file=guarded,
                out_file=pl.Path(f"{name}_witnessed.body"),
                witnesses=seed_data[1],
            )
            parent_keys = [sponsor.skey_file]
            if scenario not in ("missing_key_signature", "key_child_only"):
                parent_keys.append(key_owner.skey_file)
            if scenario not in ("missing_native_signature", "native_child_only"):
                parent_keys.append(native_owner.skey_file)
            signed_parent = cluster.g_transaction.sign_tx(
                tx_body_file=witnessed,
                tx_name=f"{name}_parent",
                signing_key_files=parent_keys,
            )
            parent_cbor = json.loads(signed_parent.read_text())["cborHex"]
            for child in children:
                assert json.loads(child.signed_file.read_text())["cborHex"] in parent_cbor
            output = dataclasses.replace(output, out_file=witnessed)

            if scenario != "authorized":
                expected = (
                    "MissingVKeyWitnessesUTXOW"
                    if scenario == "missing_key_signature"
                    else "ScriptWitnessNotValidatingUTXOW"
                    if scenario == "missing_native_signature"
                    else "MissingRequiredGuards"
                )
                with (
                    subtests.test(scenario=scenario),
                    pytest.raises(clusterlib.CLIError, match=expected),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed_parent)

                assert cluster.g_query.get_utxo(address=[a.address for a in guard_addrs]) == before
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(txin=[f"{c.txid}#0" for c in children])
                continue

            cluster.g_transaction.submit_tx(tx_file=signed_parent, txins=inputs[2])
            for child in children:
                sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert not cluster.g_query.get_utxo(utxo=[u for group in inputs for u in group])
            assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
                balances_lovelace[2] - 500_000
            ]
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)


@pytest.mark.usefixtures("require_sub_cli")
class TestNativeRequireGuard:
    """Evaluate native guard predicates in the executing child's own scope."""

    @pytest.fixture
    def guard_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Keep action funding separate from the top-level requirement tests."""
        return addrs_common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=5,
            fund_idx=[0, 1, 2],
            amount=5_000_000,
            min_amount=4_000_000,
            caching_key=helpers.get_current_line_str(),
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("purpose", ("spending", "minting"))
    @pytest.mark.parametrize("nested_timelock", (False, True), ids=("guard", "timelock"))
    def test_native_require_guard(
        self,
        cluster: clusterlib.ClusterLib,
        guard_addrs: list[clusterlib.AddressRecord],
        require_guard_script: pl.Path,
        purpose: str,
        nested_timelock: bool,
        subtests: pytest_subtests.SubTests,
    ):
        """Enforce child-local native guard entries, authorization and timelocks.

        Exercise spending and minting with a standalone key-guard predicate or
        its conjunction with a lower-bound timelock. Reject omitted/wrong guards,
        missing guard signatures, and guards supplied only by parent or sibling.
        For the conjunction, also violate only the interval. Re-sign every
        intentional body change. All failures preserve owned inputs and create
        no outputs/assets; the fully authorized control confirms exact outputs.
        """
        temp_template = common.get_test_id(cluster)
        owner, sibling_owner, sponsor, key_owner, wrong_owner = guard_addrs
        key_hash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=key_owner.vkey_file)
        predicate = (6, (0, bytes.fromhex(key_hash)))
        expected_script = (1, (predicate, (4, 1))) if nested_timelock else predicate
        policyid = cluster.g_transaction.get_policyid(script_file=require_guard_script)
        assert (
            hashlib.blake2b(b"\x00" + cbor2.dumps(expected_script), digest_size=28).hexdigest()
            == policyid
        )
        script_inputs = []
        asset_outputs = []
        mint = []
        if purpose == "spending":
            script_address = cluster.g_address.gen_payment_addr(
                addr_name=f"{temp_template}_script", payment_script_file=require_guard_script
            )
            funding = cluster.g_transaction.send_tx(
                src_address=owner.address,
                tx_name=f"{temp_template}_lock",
                txouts=[clusterlib.TxOut(address=script_address, amount=1_500_000)],
                tx_files=clusterlib.TxFiles(signing_key_files=[owner.skey_file]),
            )
            script_inputs = clusterlib.filter_utxos(
                cluster.g_query.get_utxo(tx_raw_output=funding), address=script_address
            )
            inputs = script_inputs
        else:
            inputs = cluster.g_query.get_utxo(address=owner.address)
            asset_outputs = [
                clusterlib.TxOut(address=owner.address, amount=1, coin=f"{policyid}.01")
            ]
            mint = [clusterlib.Mint(txouts=asset_outputs, script_file=require_guard_script)]
            # Cached owners can retain earlier minted assets; pass them through unchanged.
            asset_outputs = [
                clusterlib.TxOut(
                    address=owner.address,
                    amount=clusterlib.calculate_utxos_balance(inputs, coin=coin),
                    coin=coin,
                )
                for coin in sorted({u.coin for u in inputs if u.coin != clusterlib.DEFAULT_COIN})
            ] + asset_outputs
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        sibling_inputs = cluster.g_query.get_utxo(address=sibling_owner.address)
        payment_lovelace = clusterlib.calculate_utxos_balance(inputs)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        sibling_lovelace = clusterlib.calculate_utxos_balance(sibling_inputs)
        addresses = [a.address for a in guard_addrs]
        before = cluster.g_query.get_utxo(address=addresses)
        scenarios = [
            "missing_guard",
            "wrong_guard",
            "missing_signature",
            "parent_only",
            "sibling_only",
        ]
        if nested_timelock:
            scenarios.append("wrong_interval")
        scenarios.append("authorized")

        # Each mutation has fresh authorization, isolating native validation from stale signatures.
        for scenario in scenarios:
            name = f"{temp_template}_{scenario}"
            local_guard = scenario not in ("missing_guard", "parent_only", "sibling_only")
            guard_owner = wrong_owner if scenario == "wrong_guard" else key_owner
            child_keys = [owner.skey_file]
            if scenario != "missing_signature":
                child_keys.append(guard_owner.skey_file)
            child_output = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{name}_child.body",
                txins=[] if script_inputs else inputs,
                script_txins=[
                    clusterlib.ScriptTxIn(txins=script_inputs, script_file=require_guard_script)
                ]
                if script_inputs
                else [],
                txouts=[
                    clusterlib.TxOut(address=owner.address, amount=payment_lovelace),
                    *asset_outputs,
                ],
                mint=mint,
                guards=[clusterlib.TxGuard(verification_key_file=guard_owner.vkey_file)]
                if local_guard
                else [],
                invalid_before=0 if scenario == "wrong_interval" else 1,
            )
            seed = cbor2.loads(
                bytes.fromhex(json.loads(child_output.out_file.read_text())["cborHex"])
            )
            assert seed[1][1] == {expected_script}
            child_signed = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=child_output.out_file,
                tx_name=f"{name}_child",
                signing_key_files=child_keys,
            )
            child = sub_tx_utils.SignedChild(
                output=child_output,
                signed_file=child_signed,
                txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed),
            )
            sibling_output = cluster.g_sub_transaction.build_raw_tx_bare(
                out_file=f"{name}_sibling.body",
                txins=sibling_inputs,
                txouts=[clusterlib.TxOut(address=sibling_owner.address, amount=sibling_lovelace)],
                guards=[clusterlib.TxGuard(verification_key_file=key_owner.vkey_file)]
                if scenario == "sibling_only"
                else [],
            )
            sibling_signed = cluster.g_sub_transaction.sign_tx(
                sub_tx_file=sibling_output.out_file,
                tx_name=f"{name}_sibling",
                signing_key_files=[sibling_owner.skey_file, key_owner.skey_file],
            )
            sibling_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=sibling_signed)
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{name}_parent_seed",
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=sponsor_lovelace - 500_000)
                ],
                sub_transaction_files=[child_signed, sibling_signed],
                fee=500_000,
            )
            parent_file = output.out_file
            if scenario == "parent_only":
                key_hash = cluster.g_address.get_payment_vkey_hash(
                    payment_vkey_file=key_owner.vkey_file
                )
                parent_file = sub_tx_utils.append_body_field(
                    tx_file=parent_file,
                    out_file=pl.Path(f"{name}_guarded_parent.body"),
                    key=14,
                    value=cbor2.CBORTag(258, [[0, bytes.fromhex(key_hash)]]),
                )
                output = dataclasses.replace(output, out_file=parent_file)
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=parent_file,
                tx_name=f"{name}_parent",
                signing_key_files=[sponsor.skey_file, key_owner.skey_file],
            )
            parent_cbor = json.loads(signed.read_text())["cborHex"]
            assert json.loads(child_signed.read_text())["cborHex"] in parent_cbor
            assert json.loads(sibling_signed.read_text())["cborHex"] in parent_cbor

            if scenario != "authorized":
                error = (
                    "MissingVKeyWitnessesUTXOW"
                    if scenario == "missing_signature"
                    else "ScriptWitnessNotValidatingUTXOW"
                )
                with (
                    subtests.test(scenario=scenario),
                    pytest.raises(clusterlib.CLIError, match=error),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)
                assert cluster.g_query.get_utxo(address=addresses) == before
                assert cluster.g_query.get_utxo(utxo=inputs) == inputs
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(txin=[f"{child.txid}#0", f"{sibling_id}#0"])
                continue

            # The same predicate succeeds when the executing child satisfies every condition.
            cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
            assert not cluster.g_query.get_utxo(utxo=[*inputs, *sibling_inputs, *sponsor_inputs])
            sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert [
                (u.address, u.amount) for u in cluster.g_query.get_utxo(txin=f"{sibling_id}#0")
            ] == [(sibling_owner.address, sibling_lovelace)]
            assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
                sponsor_lovelace - output.fee
            ]
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)
