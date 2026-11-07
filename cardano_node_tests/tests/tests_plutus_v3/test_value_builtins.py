"""CIP-0168 Value builtins in PlutusV3/V4 transactions at protocol version 12."""

import json
import logging
import pathlib as pl
import typing as tp

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import common
from cardano_node_tests.tests import markers
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.tests.tests_plutus_v3 import builtins_cost_wiring
from cardano_node_tests.tests.tests_plutus_v3 import builtins_mint_build as mint_build
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

# V3.ParamName order: the 355 existing parameters followed by the fourteen
# assetCount, policies, keepPolicies and dropPolicies parameters, also used by V4.
VALUE_COST_MODEL_LEN = 369
SCRIPTS = plutus_common.VALUE_BUILTIN_SCRIPTS

pytestmark = [
    pytest.mark.skipif(
        VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST,
        reason="CIP-0168 Value builtins require protocol version 12 or later",
    ),
    markers.SKIPIF_PLUTUSV3_UNUSABLE,
    markers.SKIPIF_PLUTUSV4_UNUSABLE,
    markers.SKIPIF_BUILD_UNUSABLE,
    pytest.mark.plutus,
    pytest.mark.team_plutus,
]


class _StateBuildArgs(tp.TypedDict):
    """Transaction arguments shared by state evaluation and construction."""

    src_address: str
    tx_name: str
    txins: list[clusterlib.UTXOData]
    txouts: list[clusterlib.TxOut]
    script_txins: list[clusterlib.ScriptTxIn]
    tx_files: clusterlib.TxFiles
    mint: list[clusterlib.Mint]


@pytest.fixture
def value_builtins_setup(
    cluster_manager: cluster_management.ClusterManager,
    cluster_plutus: clusterlib.ClusterLib,
) -> clusterlib.ClusterLib:
    """Enable CIP-0168 costing once for both languages under governance/Plutus locks.

    Require PV12 and CLI hashing for every ledger/Core combination before changing
    governance state. Install the 369-parameter models only when a tested language
    lacks the expected parameters or has different values. Mark the cluster for respin
    before submitting the update. Skip known CLI activation gaps before governance.
    """
    cluster = cluster_plutus
    pparams = cluster.g_query.get_protocol_params()
    if pparams["protocolVersion"]["major"] < 12:
        pytest.skip("CIP-0168 Value builtins require protocol version 12 or later")

    for scripts in SCRIPTS.values():
        # Check the full matrix before governance; known activation gaps skip the test.
        for script in (*scripts.succeeding, scripts.state):
            try:
                cluster.g_transaction.get_policyid(script.script_file)
            except clusterlib.CLIError as exc:
                if "is not available" in str(exc) or "Failed to decode builtin tag" in str(exc):
                    pytest.skip(
                        f"CLI lacks batch7/PV12 support for {script.script_file.name}: {exc}"
                    )
                raise

    expected_models = json.loads(
        (plutus_common.DATA_DIR / "cost_models_list_369_v3_v4.json").read_text(encoding="utf-8")
    )

    def _is_costing_enabled(cost_models: dict) -> bool:
        """Require the tested parameter values, including the fourteen new coefficients."""
        return all(
            cost_models.get(language, [])[:VALUE_COST_MODEL_LEN] == expected_models[language]
            for language in ("PlutusV3", "PlutusV4")
        )

    if _is_costing_enabled(pparams["costModels"]):
        return cluster

    # One epoch for initial governance, up to two delayed-ratification waits,
    # then two for ratification/enactment. A third delayed wait raises instead.
    common.skip_on_long_epochs(cluster_obj=cluster, epochs=5)
    name_template = f"{common.get_test_id(cluster)}_cost_model"
    pool_user = addrs_common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster
    )

    cluster_manager.set_needs_respin()
    conway_common.update_cost_model(
        cluster_obj=cluster,
        name_template=name_template,
        governance_data=governance_data,
        cost_proposal_file=plutus_common.DATA_DIR / "cost_models_list_369_v3_v4.json",
        pool_user=pool_user,
    )
    assert _is_costing_enabled(cluster.g_query.get_protocol_params()["costModels"]), (
        "CIP-0168 cost models were not enacted"
    )
    return cluster


class TestValueBuiltins:
    """CIP-0168 semantics, state transitions and budgets across four ledger/Core combinations."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(5)
    @pytest.mark.xdist_split(markers.XdSplits.governance, markers.XdSplits.heavy)
    @pytest.mark.long
    @pytest.mark.dbsync
    def test_value_builtins(
        self,
        cluster_manager: cluster_management.ClusterManager,
        value_builtins_setup: clusterlib.ClusterLib,
        subtests: pytest_subtests.SubTests,
    ):
        """Mint with Value policies and spend state UTxOs using keep/drop restrictions.

        Exercise policies, keepPolicies, dropPolicies and assetCount with PlutusV3
        and PlutusV4, each compiled for Plutus Core 1.1.0 and 1.2.0. Cover empty/ADA
        values, ordering, filtering, duplicates and absent/overlong ids, and the
        inclusive 256-entry input-list bound. Check whole-policy budgets at 1, 16
        and 64 assets, both under one policy and under distinct policies; these
        measurements include construction and assertions, not just builtin costs.

        Reject one-unit CPU and memory shortages before a sufficient-budget mint.
        Verify actual state inputs and continuing outputs preserve protocol assets
        while changing lovelace, and reject removed/changed assets and wrong counts.
        Perturb all fourteen new cost parameters offline and require the exact
        resource changes, with no effect on the other builtin families.
        Check successful transactions and execution units in db-sync when available.
        All scenarios share one governance setup and locks.
        """
        cluster = value_builtins_setup
        temp_template = common.get_test_id(cluster)
        max_units = cluster.g_query.get_protocol_params()["maxTxExecutionUnits"]

        for variant, scripts in SCRIPTS.items():
            variant_template = f"{temp_template}_{variant}"
            mints = mint_build.run_plutusv3_builtins_test(
                cluster_manager=cluster_manager,
                cluster_obj=cluster,
                temp_template=variant_template,
                variant=f"value_{variant}",
                success_scripts=scripts.succeeding,
                fail_scripts=scripts.failing,
                overspend_scripts=(),
                is_cost_model_ok=True,
                is_prot_version_ok=True,
                subtests=subtests,
                expected_errors={
                    s.script_file.stem: (
                        ("keepPolicies" if "ValueKeep" in s.script_file.stem else "dropPolicies")
                        + ": "
                        "maximum number of policy ids (256) exceeded"
                    )
                    for s in scripts.failing
                },
            )

            # Record actual mint budgets, with optional independent db-sync records.
            for minted in mints:
                assert minted.tx_output.mint, "Expected a minting transaction"
                script_name = pl.Path(minted.tx_output.mint[0].script_file).stem
                with subtests.test(variant=variant, script=script_name, check="budget"):
                    assert len(minted.plutus_costs) == 1, "Expected one script cost record"
                    units = minted.plutus_costs[0]["executionUnits"]
                    assert 0 < units["steps"] <= max_units["steps"]
                    assert 0 < units["memory"] <= max_units["memory"]
                    LOGGER.info("Value policy execution budget: %s: %s", script_name, units)
                    db_record = dbsync_utils.check_tx(
                        cluster_obj=cluster, tx_raw_output=minted.tx_output
                    )
                    if db_record:
                        dbsync_utils.check_plutus_costs(
                            redeemer_records=db_record.redeemers, cost_records=minted.plutus_costs
                        )

            with subtests.test(variant=variant, check="execution_budget"):
                mint_build.check_execution_budget(
                    cluster_manager=cluster_manager,
                    cluster_obj=cluster,
                    temp_template=f"{variant_template}_budget",
                    plutus_version=variant,
                    script=next(
                        s for s in scripts.succeeding if "SizeMultiPolicy64" in s.script_file.stem
                    ),
                    subtests=subtests,
                )

            for other_assets in (1, 16, 64):
                with subtests.test(
                    variant=variant, check="state_transitions", assets=other_assets + 4
                ):
                    _check_state_transitions(
                        cluster_manager=cluster_manager,
                        cluster_obj=cluster,
                        temp_template=f"{variant_template}_state{other_assets}",
                        variant=variant,
                        script=scripts.state,
                        other_assets=other_assets,
                        subtests=subtests,
                    )

            with subtests.test(variant=variant, check="cost_parameters"):
                builtins_cost_wiring.check_cost_wiring(
                    cluster_manager=cluster_manager,
                    cluster_obj=cluster,
                    temp_template=f"{variant_template}_wiring",
                    variant=variant,
                    scripts=scripts.succeeding,
                    subtests=subtests,
                )


def _state_outputs(
    quantities: dict[str, int],
    protocol_tokens: list[str],
    scenario: str,
    state_address: str,
    wallet_address: str,
) -> tuple[list[clusterlib.TxOut], dict[str, int]]:
    """Build a continuing state, returning removed assets to the wallet without burning them."""
    next_quantities = dict(quantities)
    if scenario == "quantity":
        next_quantities[protocol_tokens[1]] -= 1
    elif scenario == "missing":
        next_quantities[protocol_tokens[0]] = 0
    elif scenario == "redirected":
        for coin in protocol_tokens:
            next_quantities[coin] = 0
    elif scenario == "other_changed":
        other_token = next(coin for coin in quantities if coin not in protocol_tokens)
        next_quantities[other_token] -= 1
    elif scenario == "substitution":
        next_quantities[protocol_tokens[0]] = 0
        policyid = protocol_tokens[0].split(".", maxsplit=1)[0]
        next_quantities[f"{policyid}.{b'replacement'.hex()}"] = 1

    outputs = [
        clusterlib.TxOut(
            address=state_address,
            amount=8_000_000 if scenario == "ada_decrease" else 12_000_000,
            inline_datum_value="42",
        ),
        *[
            clusterlib.TxOut(
                address=state_address, amount=amount, coin=coin, inline_datum_value="42"
            )
            for coin, amount in next_quantities.items()
            if amount
        ],
    ]
    redirected = [
        clusterlib.TxOut(address=wallet_address, amount=amount - next_quantities[coin], coin=coin)
        for coin, amount in quantities.items()
        if amount != next_quantities[coin]
    ]
    if redirected:
        outputs.extend([clusterlib.TxOut(address=wallet_address, amount=2_000_000), *redirected])
    return outputs, next_quantities


def _write_state_redeemer(
    temp_template: str, scenario: str, mode: int, protocol_policy: str
) -> pl.Path:
    """Write one state redeemer, changing only the field targeted by a negative case."""
    redeemer = pl.Path(f"{temp_template}.redeemer")
    redeemer.write_text(
        json.dumps(
            {
                "constructor": 1 if scenario == "malformed" else 0,
                "fields": [
                    {"int": 2 if scenario == "invalid_mode" else mode},
                    {"bytes": "ff" * 28 if scenario == "absent_policy" else protocol_policy},
                    {"int": {"count": 2, "zero_count": 0}.get(scenario, 3)},
                ],
            }
        ),
        encoding="utf-8",
    )
    return redeemer


def _state_script_address(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    script_file: pl.Path,
    payment_address: str,
) -> str:
    """Encode an enterprise script address using the payment address's network tag.

    The generic CLI `address build --payment-script-file` can decode at a fixed
    older protocol version even when the transaction commands support PV12.
    A script credential is its 28-byte hash, also returned by `policyid`.
    """
    script_hash = cluster_obj.g_transaction.get_policyid(script_file)
    network_tag = int(helpers.decode_bech32(payment_address)[:2], 16) & 0x0F
    address = helpers.encode_bech32(
        prefix=payment_address.split("1", maxsplit=1)[0],
        data=f"{0x70 | network_tag:02x}{script_hash}",
    )
    pl.Path(f"{temp_template}.addr").write_text(address, encoding="utf-8")
    return address


def _check_state_transitions(
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    variant: str,
    script: plutus_common.PlutusScriptData,
    other_assets: int,
    subtests: pytest_subtests.SubTests,
) -> None:
    """Preserve state tokens in continuing outputs while permitting lovelace changes.

    Mint three protocol assets and `other_assets` unrelated assets with two native policies.
    Lock them at the tested validator, then try invalid transitions before a valid
    transition for each restriction mode. Invalid builds must fail with the validator
    trace. Distinguish keep/drop with an unrelated-asset change, and allow both ADA
    increases and decreases. Check successful outputs by transaction id, including
    their datum and quantities. Use a fresh state for each restriction mode.
    """
    cluster = cluster_obj
    payment_addr, protocol_issuer, other_issuer = addrs_common.get_payment_addrs(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        num=3,
        fund_idx=[0],
        caching_key=f"value_state_{variant}",
        amount=1_000_000_000,
        min_amount=200_000_000,
    )

    # Separate native policies make the protocol partition different from all non-ADA.
    native_scripts = []
    policyids = []
    for idx, issuer in enumerate((protocol_issuer, other_issuer)):
        native_script = pl.Path(f"{temp_template}_native_{idx}.script")
        keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer.vkey_file)
        native_script.write_text(json.dumps({"type": "sig", "keyHash": keyhash}), encoding="utf-8")
        native_scripts.append(native_script)
        policyids.append(cluster.g_transaction.get_policyid(native_script))

    protocol_tokens = [f"{policyids[0]}.{name.encode().hex()}" for name in ("pair", "offer", "ask")]
    initial_quantities = dict(zip(protocol_tokens, (1, 2, 7), strict=True))
    initial_quantities.update(
        {f"{policyids[1]}.{f'other{idx}'.encode().hex()}": 9 for idx in range(other_assets)}
    )
    state_address = _state_script_address(
        cluster_obj=cluster,
        temp_template=f"{temp_template}_state",
        script_file=script.script_file,
        payment_address=payment_addr.address,
    )
    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file, protocol_issuer.skey_file]
    )

    for mode in (0, 1):
        mode_template = f"{temp_template}_mode{mode}"
        quantities = dict(initial_quantities)

        # Lock a fresh state for each mode, with separate collateral owned by this test.
        state_txouts = [
            clusterlib.TxOut(address=state_address, amount=10_000_000, inline_datum_value="42"),
            *[
                clusterlib.TxOut(
                    address=state_address, amount=amount, coin=coin, inline_datum_value="42"
                )
                for coin, amount in quantities.items()
            ],
        ]
        funding = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{mode_template}_lock",
            src_address=payment_addr.address,
            build_method=clusterlib_utils.BuildMethods.BUILD,
            txouts=[
                *state_txouts,
                clusterlib.TxOut(address=protocol_issuer.address, amount=20_000_000),
            ],
            mint=[
                clusterlib.Mint(
                    txouts=[t for t in state_txouts if t.coin.startswith(policyid)],
                    script_file=native_script,
                )
                for policyid, native_script in zip(policyids, native_scripts, strict=True)
            ],
            tx_files=clusterlib.TxFiles(
                signing_key_files=[
                    payment_addr.skey_file,
                    protocol_issuer.skey_file,
                    other_issuer.skey_file,
                ]
            ),
        )
        funded_utxos = cluster.g_query.get_utxo(tx_raw_output=funding)
        state_utxos = [u for u in funded_utxos if u.address == state_address]
        collateral = [u for u in funded_utxos if u.address == protocol_issuer.address]
        assert len({(u.utxo_hash, u.utxo_ix) for u in state_utxos}) == 1
        assert len(collateral) == 1

        scenarios = (
            (
                "quantity",
                "missing",
                "redirected",
                "count",
                "substitution",
                "absent_policy",
                "zero_count",
                "invalid_mode",
                "malformed",
                "valid",
                "other_changed",
                "ada_decrease",
            )
            if other_assets == 1
            else ("valid",)
        )
        for scenario in scenarios:
            with subtests.test(variant=variant, mode=mode, transition=scenario):
                scenario_template = f"{mode_template}_{scenario}"
                redeemer = _write_state_redeemer(
                    temp_template=scenario_template,
                    scenario=scenario,
                    mode=mode,
                    protocol_policy=policyids[0],
                )

                # Redirect removed assets to a wallet so rejection cannot be caused
                # by an unbalanced transaction or an attempted implicit burn.
                outputs, next_quantities = _state_outputs(
                    quantities=quantities,
                    protocol_tokens=protocol_tokens,
                    scenario=scenario,
                    state_address=state_address,
                    wallet_address=payment_addr.address,
                )
                script_txins = [
                    clusterlib.ScriptTxIn(
                        txins=state_utxos,
                        script_file=script.script_file,
                        collaterals=collateral,
                        inline_datum_present=True,
                        redeemer_file=redeemer,
                    )
                ]
                build_args: _StateBuildArgs = {
                    "src_address": payment_addr.address,
                    "tx_name": scenario_template,
                    # Script inputs disable automatic key-input selection. Add a
                    # wallet input to cover the fee and any increase in state ADA.
                    "txins": [
                        next(
                            u
                            for u in clusterlib_utils.get_just_lovelace_utxos(
                                address_utxos=cluster.g_query.get_utxo(address=payment_addr.address)
                            )
                            if u.amount >= 20_000_000
                        )
                    ],
                    "txouts": outputs,
                    "script_txins": script_txins,
                    "tx_files": tx_files,
                    "mint": [
                        clusterlib.Mint(txouts=[t], script_file=native_scripts[0])
                        for t in outputs
                        if t.coin != clusterlib.DEFAULT_COIN and t.coin not in quantities
                    ],
                }
                succeeds = scenario in ("valid", "ada_decrease") or (
                    scenario == "other_changed" and mode == 0
                )
                if not succeeds:
                    with pytest.raises(clusterlib.CLIError) as excinfo:
                        cluster.g_transaction.build_tx(**build_args)
                    exc_value = str(excinfo.value)
                    with common.allow_unstable_error_messages():
                        assert any(
                            text in exc_value
                            for text in (
                                "The Plutus script evaluation failed",
                                "Script evaluation error",
                                "The machine terminated because of an error",
                            )
                        ), exc_value
                        expected_error = (
                            "value state redeemer"
                            if scenario == "malformed"
                            else "value state transition"
                        )
                        assert expected_error in exc_value, exc_value
                    continue

                # Evaluate and submit a transition accepted by the selected restriction.
                cost_args = build_args.copy()
                cost_args["tx_name"] = f"{scenario_template}_cost"
                costs = cluster.g_transaction.calculate_plutus_script_cost(**cost_args)
                tx_output = cluster.g_transaction.build_tx(**build_args)
                signed = cluster.g_transaction.sign_tx(
                    tx_body_file=tx_output.out_file,
                    signing_key_files=tx_files.signing_key_files,
                    tx_name=scenario_template,
                )
                cluster.g_transaction.submit_tx(tx_file=signed, txins=[state_utxos[0]])
                out_utxos = [
                    u
                    for u in cluster.g_query.get_utxo(tx_raw_output=tx_output)
                    if u.address == state_address
                ]
                assert len({(u.utxo_hash, u.utxo_ix) for u in out_utxos}) == 1
                assert clusterlib.calculate_utxos_balance(out_utxos) == outputs[0].amount
                for coin, amount in next_quantities.items():
                    assert clusterlib.calculate_utxos_balance(out_utxos, coin=coin) == amount
                assert {u.coin for u in out_utxos} == {clusterlib.DEFAULT_COIN, *quantities}
                assert any(u.inline_datum == {"int": 42} for u in out_utxos)
                assert not cluster.g_query.get_utxo(utxo=state_utxos[0])
                assert cluster.g_query.get_utxo(utxo=collateral[0])
                assert len(costs) == 1, "Expected one state-validator cost record"
                max_units = cluster.g_query.get_protocol_params()["maxTxExecutionUnits"]
                units = costs[0]["executionUnits"]
                assert 0 < units["steps"] <= max_units["steps"]
                assert 0 < units["memory"] <= max_units["memory"]
                LOGGER.info(
                    "Value state execution budget: %s mode%s assets%s: %s",
                    variant,
                    mode,
                    len(quantities) + 1,
                    costs,
                )
                db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
                if db_record:
                    dbsync_utils.check_plutus_costs(
                        redeemer_records=db_record.redeemers, cost_records=costs
                    )
                state_utxos = out_utxos
                quantities = next_quantities
