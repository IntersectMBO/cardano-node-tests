"""Non-Plutus required top-level guards and transaction-local guard scope."""

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


class TestRequiredTopLevelGuards:
    """Require explicit enclosing guard entries and their enclosing authorization."""

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
