"""Complete-batch value conservation and atomic state transitions."""

import allure
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
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Cache two participants and a sponsor/issuer; fund only the sponsor."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=3,
        fund_idx=[2],
        amount=12_000_000,
        min_amount=10_000_000,
        caching_key=helpers.get_current_line_str(),
    )


@pytest.fixture
def cluster_treasury(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Serialize treasury donations while exercising child state changes."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return cluster_manager.get(lock_resources=[cluster_management.Resources.TREASURY])


@pytest.fixture
def atomicity_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster_treasury: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Cache two participants, sponsor, collateral owner, and recipient."""
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster_treasury),
        cluster_manager=cluster_manager,
        cluster_obj=cluster_treasury,
        num=5,
        fund_idx=[0, 1, 2, 3],
        amount=3_000_000,
        min_amount=2_500_000,
        caching_key=helpers.get_current_line_str(),
    )


@pytest.fixture
def stake_address(
    cluster_manager: cluster_management.ClusterManager,
    cluster_treasury: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Cache an independent stake credential used by the early child."""
    cache: cluster_management.FixtureCache[clusterlib.AddressRecord | None]
    with cluster_manager.cache_fixture() as cache:
        if cache.value is None:
            cache.value = cluster_treasury.g_stake_address.gen_stake_addr_and_keys(
                name=f"{common.get_test_id(cluster_treasury)}_stake"
            )
        return cache.value


class TestSubTransactionConservation:
    """Enforce ADA and native-asset conservation over the complete batch."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_global_value_conservation(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
    ):
        """Balance complementary children collectively, rejecting one-unit mismatches.

        Move ADA and a fresh native asset between individually unbalanced,
        independently signed children. Re-sign each one-lovelace or one-token
        mismatch to isolate conservation from authorization. Check unchanged
        owned state on rejection, then confirm the balanced batch and outputs.
        """
        temp_template = common.get_test_id(cluster)
        first, second, sponsor = payment_addrs
        fee_lovelace = 500_000

        # Mint only the five tokens needed by the offers and isolate pre-batch inputs.
        token = clusterlib_utils.new_tokens(
            clusterlib.get_rand_str(8).encode().hex(),
            cluster_obj=cluster,
            temp_template=f"{temp_template}_asset",
            token_mint_addr=sponsor,
            issuer_addr=sponsor,
            amount=5,
        )[0].token
        setup = cluster.g_transaction.send_tx(
            src_address=sponsor.address,
            tx_name=f"{temp_template}_setup",
            txouts=[
                clusterlib.TxOut(address=first.address, amount=3_000_000),
                clusterlib.TxOut(address=first.address, amount=5, coin=token),
                clusterlib.TxOut(address=second.address, amount=3_000_000),
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[sponsor.skey_file]),
        )
        setup_utxos = cluster.g_query.get_utxo(tx_raw_output=setup)
        first_inputs = clusterlib.filter_utxos(setup_utxos, address=first.address)
        second_inputs = clusterlib.filter_utxos(setup_utxos, address=second.address)
        sponsor_inputs = clusterlib.filter_utxos(setup_utxos, address=sponsor.address)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        before = cluster.g_query.get_utxo(address=[a.address for a in payment_addrs])
        first_child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_first",
            txins=first_inputs,
            txouts=[clusterlib.TxOut(address=first.address, amount=2_000_000)],
            signing_keys=[first.skey_file],
        )

        # File-only parent assembly cannot repair a deliberately unbalanced child.
        for name, delta_lovelace, delta_token in (
            ("extra_lovelace", 1, 0),
            ("missing_lovelace", -1, 0),
            ("extra_token", 0, 1),
            ("missing_token", 0, -1),
            ("balanced", 0, 0),
        ):
            second_child = sub_tx_utils.build_child(
                cluster=cluster,
                name=f"{temp_template}_{name}_second",
                txins=second_inputs,
                txouts=[
                    clusterlib.TxOut(address=second.address, amount=4_000_000 + delta_lovelace),
                    clusterlib.TxOut(address=second.address, amount=5 + delta_token, coin=token),
                ],
                signing_keys=[second.skey_file],
            )
            output, signed = sub_tx_utils.build_parent(
                cluster=cluster,
                name=f"{temp_template}_{name}",
                sponsor=sponsor,
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(
                        address=sponsor.address, amount=sponsor_lovelace - fee_lovelace
                    )
                ],
                children=[first_child, second_child],
                fee_lovelace=fee_lovelace,
            )
            if name != "balanced":
                with (
                    subtests.test(mismatch=name),
                    pytest.raises(clusterlib.CLIError, match="ValueNotConservedUTxO"),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert (
                    cluster.g_query.get_utxo(address=[a.address for a in payment_addrs]) == before
                )
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(
                    txin=[f"{first_child.txid}#0", f"{second_child.txid}#0"]
                )
                continue

            # Each child is unbalanced alone, but their aggregate ADA/assets balance exactly.
            cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
            assert not cluster.g_query.get_utxo(
                utxo=[*first_inputs, *second_inputs, *sponsor_inputs]
            )
            for child in (first_child, second_child):
                sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
            assert [
                (u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=output)
            ] == [(sponsor.address, sponsor_lovelace - fee_lovelace)]
            node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)


class TestSubTransactionAtomicity:
    """Reject a complete batch without committing earlier component effects."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_phase_one_atomic_rejection(
        self,
        cluster_treasury: clusterlib.ClusterLib,
        atomicity_addrs: list[clusterlib.AddressRecord],
        stake_address: clusterlib.AddressRecord,
        subtests: pytest_subtests.SubTests,
    ):
        """Discard a child payment, registration and donation on phase-one failure.

        Omit a required key witness in either child position or the parent.
        Check owned inputs (including separate collateral), absent outputs and
        an unregistered stake credential after each rejection. Then confirm
        the fully authorized batch and deregister to refund its actual deposit.
        """
        cluster = cluster_treasury
        temp_template = common.get_test_id(cluster)
        first, second, sponsor, collateral, recipient = atomicity_addrs
        inputs = [cluster.g_query.get_utxo(address=a.address) for a in atomicity_addrs[:4]]
        balances_lovelace = [clusterlib.calculate_utxos_balance(ins) for ins in inputs]
        deposit_lovelace = cluster.g_query.get_address_deposit()
        donation_lovelace = 100_000
        assert not cluster.g_query.get_stake_addr_info(stake_address.address)
        cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_register",
            stake_vkey_file=stake_address.vkey_file,
            deposit_amt=deposit_lovelace,
        )
        before = cluster.g_query.get_utxo(address=[a.address for a in atomicity_addrs])

        # A valid early child requests several effects, not just a UTxO transfer.
        state_child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_state_child",
            txins=inputs[0],
            txouts=[
                clusterlib.TxOut(
                    address=recipient.address,
                    amount=balances_lovelace[0] - deposit_lovelace - donation_lovelace,
                )
            ],
            signing_keys=[first.skey_file, stake_address.skey_file],
            tx_files=clusterlib.TxFiles(certificate_files=[cert]),
            treasury_donation=donation_lovelace,
        )
        valid_child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_valid_child",
            txins=inputs[1],
            txouts=[clusterlib.TxOut(address=second.address, amount=balances_lovelace[1])],
            signing_keys=[second.skey_file],
        )
        missing_child = sub_tx_utils.build_child(
            cluster=cluster,
            name=f"{temp_template}_missing_child",
            txins=inputs[1],
            txouts=[clusterlib.TxOut(address=second.address, amount=balances_lovelace[1])],
            signing_keys=[],
        )

        for name in ("first_child", "last_child", "parent", "authorized"):
            children = [state_child, valid_child]
            if name in ("first_child", "last_child"):
                children = [state_child, missing_child]
                if name == "first_child":
                    children.reverse()

            # Separate collateral proves a phase-one failure cannot charge it either.
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{temp_template}_{name}",
                txins=inputs[2],
                txouts=[
                    clusterlib.TxOut(address=sponsor.address, amount=balances_lovelace[2] - 500_000)
                ],
                collateral_txins=inputs[3],
                sub_transaction_files=[c.signed_file for c in children],
                fee=500_000,
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=output.out_file,
                tx_name=f"{temp_template}_{name}",
                signing_key_files=[collateral.skey_file]
                if name == "parent"
                else [sponsor.skey_file, collateral.skey_file],
            )
            if name != "authorized":
                with (
                    subtests.test(failure=name),
                    pytest.raises(clusterlib.CLIError, match="MissingVKeyWitnessesUTXOW"),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert (
                    cluster.g_query.get_utxo(address=[a.address for a in atomicity_addrs]) == before
                )
                assert not cluster.g_query.get_stake_addr_info(stake_address.address)
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                assert not cluster.g_query.get_utxo(txin=[f"{c.txid}#0" for c in children])
                continue

            # Successful control establishes that deposit and donation are correctly funded.
            cluster.g_transaction.submit_tx(tx_file=signed, txins=inputs[2])
            try:
                registered = cluster.g_query.get_stake_addr_info(stake_address.address)
                assert registered.registration_deposit == deposit_lovelace
                for child in children:
                    sub_tx_utils.check_child_outputs(cluster=cluster, child=child)
                assert not cluster.g_query.get_utxo(utxo=[*inputs[0], *inputs[1], *inputs[2]])
                assert cluster.g_query.get_utxo(utxo=inputs[3]) == inputs[3]
                assert [u.amount for u in cluster.g_query.get_utxo(tx_raw_output=output)] == [
                    balances_lovelace[2] - 500_000
                ]
                node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
                dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)
            finally:
                # Refund the recorded deposit rather than assume the current parameter value.
                registered = cluster.g_query.get_stake_addr_info(stake_address.address)
                if registered:
                    dereg = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
                        addr_name=f"{temp_template}_cleanup",
                        stake_vkey_file=stake_address.vkey_file,
                        deposit_amt=registered.registration_deposit,
                    )
                    cluster.g_transaction.send_tx(
                        src_address=sponsor.address,
                        tx_name=f"{temp_template}_cleanup",
                        tx_files=clusterlib.TxFiles(
                            certificate_files=[dereg],
                            signing_key_files=[sponsor.skey_file, stake_address.skey_file],
                        ),
                        deposit=-registered.registration_deposit,
                    )
                    assert not cluster.g_query.get_stake_addr_info(stake_address.address)
