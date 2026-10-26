"""Plutus execution and sponsorship in independently signed children."""

import json

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import node_consistency


@pytest.fixture
def cluster_plutus(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    """Protect script execution parameters and require child construction support."""
    if not clusterlib_utils.cli_has("dijkstra sub-transaction"):
        pytest.skip("The CLI does not support sub-transactions")
    return cluster_manager.get(use_resources=[cluster_management.Resources.PLUTUS])


@pytest.fixture
def payment_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster_plutus: clusterlib.ClusterLib,
) -> list[clusterlib.AddressRecord]:
    """Cache user, sponsor, collateral owner, and recipient with modest funding."""
    return addrs_common.get_payment_addrs(
        name_template=common.get_test_id(cluster_plutus),
        cluster_manager=cluster_manager,
        cluster_obj=cluster_plutus,
        num=4,
        fund_idx=[0, 1, 2],
        amount=5_000_000,
        min_amount=4_000_000,
        caching_key=helpers.get_current_line_str(),
    )


class TestSubTransactionSponsorship:
    """Check fee and collateral sponsorship for child-only Plutus execution."""

    @allure.link(helpers.get_vcs_link())
    @markers.SKIPIF_PLUTUSV4_UNUSABLE
    @pytest.mark.plutus
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    @pytest.mark.parametrize("script_fails", (False, True), ids=("passing", "failing"))
    def test_script_sponsorship(
        self,
        cluster_plutus: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        subtests: pytest_subtests.SubTests,
        script_fails: bool,
    ):
        """Sponsor a child contract action without parent redeemers or user collateral.

        Lock a user-funded output at a V4 script, then independently sign its
        child spend. Reject missing and insufficient sponsor collateral without
        changing owned state. Settle a passing action with collateral untouched,
        or reject a failing action without ordinary or collateral effects.
        Dijkstra tooling currently cannot submit a phase-2-invalid declaration.
        """
        cluster = cluster_plutus
        temp_template = common.get_test_id(cluster)
        user, sponsor, collateral_owner, recipient = payment_addrs
        script_file = (
            plutus_common.SCRIPTS_V4_DIR / "always-fails.plutus"
            if script_fails
            else plutus_common.ALWAYS_SUCCEEDS_PLUTUS_V4
        )
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=f"{temp_template}_script", payment_script_file=script_file
        )
        fee_lovelace = 500_000
        action_lovelace = 2_000_000
        collateral_lovelace = 1_000_000

        # Lock only the user's action value; the sponsor owns all fee/collateral inputs.
        funding = cluster.g_transaction.send_tx(
            src_address=user.address,
            tx_name=f"{temp_template}_lock",
            txouts=[
                clusterlib.TxOut(
                    address=script_address,
                    amount=action_lovelace,
                    inline_datum_value="42",
                )
            ],
            tx_files=clusterlib.TxFiles(signing_key_files=[user.skey_file]),
        )
        script_inputs = clusterlib.filter_utxos(
            cluster.g_query.get_utxo(tx_raw_output=funding), address=script_address
        )
        assert len(script_inputs) == 1
        sponsor_inputs = cluster.g_query.get_utxo(address=sponsor.address)
        collateral_inputs = cluster.g_query.get_utxo(address=collateral_owner.address)
        sponsor_lovelace = clusterlib.calculate_utxos_balance(sponsor_inputs)
        collateral_balance_lovelace = clusterlib.calculate_utxos_balance(collateral_inputs)

        # The child contains the only execution and no collateral fields.
        child = cluster.g_sub_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_child.body",
            txouts=[clusterlib.TxOut(address=recipient.address, amount=action_lovelace)],
            script_txins=[
                clusterlib.ScriptTxIn(
                    txins=script_inputs,
                    script_file=script_file,
                    inline_datum_present=True,
                    redeemer_value="0",
                    execution_units=(1_000_000, 10_000),
                )
            ],
        )
        child_signed = cluster.g_sub_transaction.sign_tx(
            sub_tx_file=child.out_file,
            tx_name=f"{temp_template}_child",
            signing_key_files=[user.skey_file],
        )
        child_bytes = child_signed.read_bytes()
        child_id = cluster.g_sub_transaction.get_txid(signed_sub_tx_file=child_signed)
        addresses = [a.address for a in payment_addrs]
        state_before = cluster.g_query.get_utxo(address=addresses)

        # Explicit parent outputs prevent balancing from hiding intentional invalidity.
        for name, total_lovelace in (
            ("missing", 0),
            ("insufficient", 1),
            ("valid", collateral_lovelace),
        ):
            output = cluster.g_transaction.build_raw_tx(
                src_address=sponsor.address,
                tx_name=f"{temp_template}_{name}_parent",
                txins=sponsor_inputs,
                txouts=[
                    clusterlib.TxOut(
                        address=sponsor.address, amount=sponsor_lovelace - fee_lovelace
                    )
                ],
                fee=fee_lovelace,
                collateral_txins=collateral_inputs if total_lovelace else [],
                total_collateral_amount=total_lovelace or None,
                return_collateral_txouts=[
                    clusterlib.TxOut(
                        address=collateral_owner.address,
                        amount=collateral_balance_lovelace - total_lovelace,
                    )
                ]
                if total_lovelace
                else [],
                sub_transaction_files=[child_signed],
            )
            signed = cluster.g_transaction.sign_tx(
                tx_body_file=output.out_file,
                signing_key_files=[sponsor.skey_file, collateral_owner.skey_file],
                tx_name=f"{temp_template}_{name}_parent",
            )
            assert json.loads(child_bytes)["cborHex"] in json.loads(signed.read_text())["cborHex"]

            if name != "valid":
                with (
                    subtests.test(collateral=name),
                    pytest.raises(
                        clusterlib.CLIError,
                        match=r"NoCollateralInputs|InsufficientCollateral",
                    ),
                ):
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)

                assert cluster.g_query.get_utxo(address=addresses) == state_before
                assert cluster.g_query.get_utxo(utxo=script_inputs) == script_inputs
                assert not cluster.g_query.get_utxo(txin=f"{child_id}#0")
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
                continue

            if script_fails:
                # Local submission protects the sponsor from an unexpectedly failing action.
                with pytest.raises(clusterlib.CLIError, match="ValidationTagMismatch") as excinfo:
                    cluster.g_transaction.submit_tx_bare(tx_file=signed)
                assert "The PlutusV4 script failed" in str(excinfo.value)
                assert cluster.g_query.get_utxo(address=addresses) == state_before
                assert cluster.g_query.get_utxo(utxo=script_inputs) == script_inputs
                assert not cluster.g_query.get_utxo(txin=f"{child_id}#0")
                assert not cluster.g_query.get_utxo(tx_raw_output=output)
            else:
                # Only the sponsor pays the fee; successful execution leaves collateral alone.
                cluster.g_transaction.submit_tx(tx_file=signed, txins=sponsor_inputs)
                assert not cluster.g_query.get_utxo(utxo=[*script_inputs, *sponsor_inputs])
                assert cluster.g_query.get_utxo(utxo=collateral_inputs) == collateral_inputs
                assert [
                    (u.address, u.amount) for u in cluster.g_query.get_utxo(txin=f"{child_id}#0")
                ] == [(recipient.address, action_lovelace)]
                assert [
                    (u.address, u.amount) for u in cluster.g_query.get_utxo(tx_raw_output=output)
                ] == [(sponsor.address, sponsor_lovelace - fee_lovelace)]
                node_consistency.check_tx_on_all_nodes(cluster_obj=cluster, tx_raw_output=output)
                dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=output)

        assert child_signed.read_bytes() == child_bytes
