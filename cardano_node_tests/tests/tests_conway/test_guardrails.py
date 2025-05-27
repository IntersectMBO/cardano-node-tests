"""Tests for Conway governance guardrails."""

import dataclasses
import fractions
import json
import logging
import pathlib as pl
import random
import typing as tp

import allure
import pytest
import pytest_subtests
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils import web
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def cluster_guardrails(
    cluster_manager: cluster_management.ClusterManager,
) -> governance_utils.GovClusterT:
    """Mark governance as "locked" and return instance of `clusterlib.ClusterLib`.

    Also mark guardrails tests with "guardrails" marker. As such, all the tests will run
    on the same cluster instance where the initial setup was already done (by the first test).

    Cleanup (== respin the cluster instance) after the tests are finished.
    """
    cluster_obj = cluster_manager.get(
        use_resources=[
            *cluster_management.Resources.ALL_POOLS,
            cluster_management.Resources.PLUTUS,
        ],
        lock_resources=[cluster_management.Resources.COMMITTEE, cluster_management.Resources.DREPS],
        cleanup=True,
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    return cluster_obj, governance_data


@pytest.fixture
def pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster_guardrails: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_guardrails
    name_template = common.get_test_id(cluster)
    return common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
    )


@pytest.fixture
def payment_addr(
    cluster_manager: cluster_management.ClusterManager,
    cluster_guardrails: governance_utils.GovClusterT,
) -> clusterlib.AddressRecord:
    """Create new payment address."""
    cluster, __ = cluster_guardrails
    addr = common.get_payment_addr(
        name_template=common.get_test_id(cluster),
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        amount=10_000_000_000,
    )
    return addr


@dataclasses.dataclass(frozen=True)
class ClusterWithConstitutionRecord:
    """Class to store the cluster with constitution record."""

    cluster: clusterlib.ClusterLib
    constitution_script_file: pl.Path
    constitution_script_hash: str
    default_constitution: dict[str, tp.Any]
    pool_user: clusterlib.PoolUser
    payment_addr: clusterlib.AddressRecord
    collaterals: list[clusterlib.UTXOData]


@pytest.fixture
def cluster_with_constitution(
    cluster_guardrails: governance_utils.GovClusterT,
    pool_user: clusterlib.PoolUser,
    payment_addr: clusterlib.AddressRecord,
) -> ClusterWithConstitutionRecord:
    """Enact the constitution with guardrails plutus script and return constitution data."""
    # Make sure the setup is executed only by the first worker when running in parallel
    shared_tmp = temptools.get_pytest_shared_tmp()
    with locking.FileLockIfXdist(f"{shared_tmp}/constitution_script.lock"):
        cluster, governance_data = cluster_guardrails
        temp_template = common.get_test_id(cluster)

        constitution_script_file = plutus_common.SCRIPTS_V3_DIR / "constitutionScriptV3.plutus"
        # Obtain script hash
        constitution_script_hash = cluster.g_governance.get_script_hash(constitution_script_file)

        data_dir = pl.Path(__file__).parent.parent / "data"
        default_constitution_file = data_dir / "defaultConstitution.json"
        default_constitution = json.loads(default_constitution_file.read_text())

        def _enact_script_constitution():
            """Enact a new constitution with a plutus script."""
            anchor_data = governance_utils.get_default_anchor_data()

            constitution_file = pl.Path(f"{temp_template}_constitution.txt")
            constitution_file.write_text(data="Constitution is here", encoding="utf-8")
            constitution_url = web.publish(file_path=constitution_file)
            constitution_hash = cluster.g_governance.get_anchor_data_hash(
                file_text=constitution_file
            )

            governance_utils.wait_delayed_ratification(cluster_obj=cluster)

            _, action_txid, action_ix = conway_common.propose_change_constitution(
                cluster_obj=cluster,
                name_template=temp_template,
                anchor_url=anchor_data.url,
                anchor_data_hash=anchor_data.hash,
                constitution_url=constitution_url,
                constitution_hash=constitution_hash,
                pool_user=pool_user,
                constitution_script_hash=constitution_script_hash,
            )

            # Make sure we have enough time to submit the votes in one epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER
            )

            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_yes",
                payment_addr=payment_addr,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_cc=True,
                approve_drep=True,
            )
            approve_epoch = cluster.g_query.get_epoch()

            # Wait for the action to be ratified
            cluster.wait_for_epoch(epoch_no=approve_epoch + 1, padding_seconds=5)
            rat_gov_state = cluster.g_query.gov_state()
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )
            assert rat_action, "Action not found in ratified actions"

            # Wait for the action to be enacted
            cluster.wait_for_epoch(epoch_no=approve_epoch + 2, padding_seconds=5)
            new_constitution = cluster.g_query.constitution()
            assert new_constitution["script"] == constitution_script_hash

        cur_constitution = cluster.g_query.constitution()

        # Enact the new constitution if the current one is not the one we expect
        if cur_constitution.get("script") != constitution_script_hash:
            if conway_common.is_in_bootstrap(cluster_obj=cluster):
                pytest.skip("Cannot run update constitution during bootstrap period.")
            if cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.LOCAL:
                pytest.skip("Cannot run update constitution on non-local testnet.")

            _url = helpers.get_vcs_link()
            [r.start(url=_url) for r in (reqc.cip015, reqc.cip031c_02)]
            _enact_script_constitution()
            [r.success() for r in (reqc.cip015, reqc.cip031c_02)]

        # Create collateral UTxO for plutus script
        collateral_tx_outs = [
            clusterlib.TxOut(address=pool_user.payment.address, amount=5_000_000),
        ]
        collaterals = clusterlib_utils.create_collaterals(
            cluster=cluster,
            payment_addr=payment_addr,
            temp_template=f"{temp_template}_collateral",
            tx_outs=collateral_tx_outs,
        )

        return ClusterWithConstitutionRecord(
            cluster=cluster,
            constitution_script_file=constitution_script_file,
            constitution_script_hash=constitution_script_hash,
            default_constitution=default_constitution,
            pool_user=pool_user,
            payment_addr=payment_addr,
            collaterals=collaterals,
        )


def propose_param_changes(
    cluster_with_constitution: ClusterWithConstitutionRecord,
    proposals: list[clusterlib_utils.UpdateProposal],
) -> str:
    """Build and submit update pparams action with specified proposals."""
    cluster = cluster_with_constitution.cluster
    pool_user = cluster_with_constitution.pool_user
    payment_addr = cluster_with_constitution.payment_addr

    temp_template = common.get_test_id(cluster)
    anchor_data = governance_utils.get_default_anchor_data()
    deposit_amt = cluster.g_query.get_gov_action_deposit()
    prev_action_rec = governance_utils.get_prev_action(
        action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
        gov_state=cluster.g_query.gov_state(),
    )

    # Update pparams action with specifying constitution script hash
    update_args = clusterlib_utils.get_pparams_update_args(update_proposals=proposals)
    update_args.extend(
        ["--constitution-script-hash", cluster_with_constitution.constitution_script_hash]
    )

    pparams_action = cluster.g_governance.action.create_pparams_update(
        action_name=temp_template,
        deposit_amt=deposit_amt,
        anchor_url=anchor_data.url,
        anchor_data_hash=anchor_data.hash,
        cli_args=update_args,
        prev_action_txid=prev_action_rec.txid,
        prev_action_ix=prev_action_rec.ix,
        deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
    )

    execution_units = (740000000, 8000000)
    raw_fee = 900_000

    proposal_script = clusterlib.ComplexProposal(
        proposal_file=pparams_action.action_file,
        script_file=cluster_with_constitution.constitution_script_file,
        redeemer_file=plutus_common.REDEEMER_42,
        collaterals=cluster_with_constitution.collaterals,
        execution_units=execution_units,
    )

    tx_files = clusterlib.TxFiles(
        signing_key_files=[
            pool_user.payment.skey_file,  # For collaterals
            payment_addr.skey_file,
        ],
    )

    # Make sure we have enough time to submit the proposal in one epoch
    clusterlib_utils.wait_for_epoch_interval(
        cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
    )

    tx_output_action = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster,
        name_template=f"{temp_template}_action",
        src_address=cluster_with_constitution.payment_addr.address,
        use_build_cmd=False,
        tx_files=tx_files,
        complex_proposals=[proposal_script],
        deposit=deposit_amt,
        raw_fee=raw_fee,
    )
    action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
    return action_txid


def check_invalid_proposals(  # noqa: C901
    cluster_with_constitution: ClusterWithConstitutionRecord,
    proposals: list[clusterlib_utils.UpdateProposal],
):
    """Check that the guardrails are enforced."""
    action_txid = ""
    err_msg = ""
    try:
        action_txid = propose_param_changes(
            cluster_with_constitution=cluster_with_constitution,
            proposals=proposals,
        )
    except Exception as excp:
        err_msg = str(excp)

    if (
        "expecting digit" in err_msg
        or "expecting white space or digit" in err_msg  # In node 9.2.0+
    ):
        # In case cli throws error beforehand due to invalid input
        assert 'unexpected "-"' in err_msg, err_msg
    elif "toUnitIntervalOrError" in err_msg or "toNonNegativeIntervalOrErr" in err_msg:
        # In case of invalid unit interval
        assert "rational out of bounds" in err_msg, err_msg
    elif "Please enter a value" in err_msg:
        # In case of invalid value
        assert "Please enter a value in the range [0,1]" in err_msg, err_msg
    elif "cannot parse value" in err_msg:
        # In case of invalid value
        assert "cannot parse value" in err_msg, err_msg
    elif "MalformedProposal" in err_msg:
        # In case of malformed proposal
        assert "ParameterChange" in err_msg, err_msg
    elif "EPOCH_INTERVAL" in err_msg:
        # In case of invalid epoch interval
        assert "EPOCH_INTERVAL must not be less than 0" in err_msg, err_msg
    elif "Failed reading" in err_msg:
        # In case of invalid value
        assert "Failed reading" in err_msg, err_msg
    elif err_msg == "":
        action_gov_state = cluster_with_constitution.cluster.g_query.gov_state()
        prop_action = governance_utils.lookup_proposal(
            gov_state=action_gov_state, action_txid=action_txid
        )
        if prop_action:
            issues.cli_860.finish_test()
        assert err_msg, "Expected an error from cardano-cli"
    else:
        assert "The machine terminated because of an error" in err_msg, err_msg


def check_valid_proposals(
    cluster_with_constitution: ClusterWithConstitutionRecord,
    proposals: list[clusterlib_utils.UpdateProposal],
):
    action_txid = propose_param_changes(
        cluster_with_constitution=cluster_with_constitution, proposals=proposals
    )
    action_gov_state = cluster_with_constitution.cluster.g_query.gov_state()
    prop_action = governance_utils.lookup_proposal(
        gov_state=action_gov_state, action_txid=action_txid
    )
    assert prop_action, "Update pparams action not found"


def _get_rational_str(value: float) -> str:
    return str(fractions.Fraction(value).limit_denominator())


def _get_param_min_value(
    cluster_with_constitution: ClusterWithConstitutionRecord, key: str
) -> float | int:
    """Get the min value from the default constitution for a param."""
    param_predicates = cluster_with_constitution.default_constitution[key]
    min_val_dicts = list(filter(lambda x: "minValue" in x, param_predicates["predicates"]))

    if len(min_val_dicts) == 1:
        min_val = min_val_dicts[0]["minValue"]
        if isinstance(min_val, int):
            return min_val
        if "numerator" in min_val:
            return float(min_val["numerator"] / min_val["denominator"])
        raise PredicateNotSupportedError(min_val_dicts[0])

    # Find maximum value from the list of min values
    max_of_min_values = -float("inf")
    for min_val_dict in min_val_dicts:
        min_val = min_val_dict["minValue"]
        if not isinstance(min_val, int) and "numerator" in min_val:
            min_val = min_val["numerator"] / min_val["denominator"]
        max_of_min_values = max(min_val, max_of_min_values)
    return max_of_min_values


def _get_param_max_value(
    cluster_with_constitution: ClusterWithConstitutionRecord, key: str
) -> float | int:
    """Get the max value from the default constitution for a param."""
    param_predicates = cluster_with_constitution.default_constitution[key]
    max_value_dicts = list(filter(lambda x: "maxValue" in x, param_predicates["predicates"]))

    if len(max_value_dicts) == 1:
        max_val = max_value_dicts[0]["maxValue"]
        if isinstance(max_val, int):
            return max_val
        if "numerator" in max_val:
            return float(max_val["numerator"] / max_val["denominator"])
        raise PredicateNotSupportedError(max_value_dicts[0])

    # Find minimum value from the list of max values
    min_of_max_values = float("inf")
    for max_val_dict in max_value_dicts:
        max_val = max_val_dict["maxValue"]
        if not isinstance(max_val, int) and "numerator" in max_val:
            max_val = max_val["numerator"] / max_val["denominator"]
        min_of_max_values = min(max_val, min_of_max_values)
    return min_of_max_values


@dataclasses.dataclass(frozen=True)
class GuardrailTestParam:
    """Class to store parameter information for the guardrail test."""

    param_key: str  # key in the default constitution json file
    param_cli_arg: str  # CLI argument for the parameter
    param_name: str  # name of the protocol parameter
    param_lower_limit: int | None = None  # optional lower limit of the parameter
    param_upper_limit: int | None = None  # optional upper limit of the parameter


def check_min_value_proposals(
    cluster_with_constitution: ClusterWithConstitutionRecord,
    param: GuardrailTestParam,
    min_value: int | float,
    dependent_proposals: list[clusterlib_utils.UpdateProposal] | tuple,
):
    """Check invalid proposals for min value predicate (must not be lower than)."""
    if min_value == 0:
        # Handle the case for must not be negative

        lower_limit = param.param_lower_limit if param.param_lower_limit else -(2**32)
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_proposal = [
            clusterlib_utils.UpdateProposal(
                arg=param.param_cli_arg,
                value=random.randint(lower_limit, -1),
                name=param.param_name,
            ),
            *dependent_proposals,
        ]
        check_invalid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=invalid_negative_proposal,
        )
    else:
        # Handle the case for must not be lower than
        lower_bound_proposal = [
            clusterlib_utils.UpdateProposal(
                arg=param.param_cli_arg,
                value=(
                    random.randint(0, min_value - 1)
                    if isinstance(min_value, int)
                    else _get_rational_str(random.uniform(0, min_value - 0.1))
                ),
                name=param.param_name,
            ),
            *dependent_proposals,
        ]
        check_invalid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=lower_bound_proposal,
        )


def check_max_value_proposals(
    cluster_with_constitution: ClusterWithConstitutionRecord,
    param: GuardrailTestParam,
    max_value: int | float,
    dependent_proposals: list[clusterlib_utils.UpdateProposal] | tuple,
    type_upper_limit: int,
):
    """Check invalid proposals for max value predicate (must not exceed)."""
    upper_bound_proposal = [
        clusterlib_utils.UpdateProposal(
            arg=param.param_cli_arg,
            value=(
                random.randint(max_value + 1, type_upper_limit)
                if isinstance(max_value, int)
                else _get_rational_str(random.uniform(max_value + 0.1, type_upper_limit))
            ),
            name=param.param_name,
        ),
        *dependent_proposals,
    ]

    check_invalid_proposals(
        cluster_with_constitution=cluster_with_constitution,
        proposals=upper_bound_proposal,
    )


def perform_predicates_check_with_dependent_params(
    cluster_with_constitution: ClusterWithConstitutionRecord,
    param: GuardrailTestParam,
    dependent_params: list[GuardrailTestParam],
):
    """
    Check for predicates defined in the constitution with dependent parameters.

    Eg: executionUnitPrices[priceMemory] and executionUnitPrices[priceSteps] are
    dependent parameters.
    """
    dependent_proposals = []
    for dependent_param in dependent_params:
        dependent_param_key = dependent_param.param_key
        dependent_param_cli_arg = dependent_param.param_cli_arg
        dependent_param_name = dependent_param.param_name

        dependent_param_min_value = _get_param_min_value(
            cluster_with_constitution=cluster_with_constitution, key=dependent_param_key
        )
        dependent_param_max_value = _get_param_max_value(
            cluster_with_constitution=cluster_with_constitution, key=dependent_param_key
        )

        dependent_proposals.append(
            clusterlib_utils.UpdateProposal(
                arg=dependent_param_cli_arg,
                value=(
                    random.randint(dependent_param_min_value, dependent_param_max_value)
                    if isinstance(dependent_param_min_value, int)
                    and isinstance(dependent_param_max_value, int)
                    else _get_rational_str(
                        random.uniform(dependent_param_min_value, dependent_param_max_value)
                    )
                ),
                name=dependent_param_name,
            ),
        )

    perform_predicates_check(
        cluster_with_constitution=cluster_with_constitution,
        param=param,
        dependent_proposals=dependent_proposals,
    )


class PredicateNotSupportedError(Exception):
    def __init__(self, predicate: dict):
        super().__init__(
            f"{predicate} predicate is not supported."
            "Supported predicates are minValue, maxValue and notEqual."
        )


def get_upper_limit_according_to_type(type: str) -> int:
    """Check the value of type and return upper limit accordingly."""
    if type == "uint.size2":
        return 65535  # Obtained from CLI Error message
    if type in ("uint.size4", "epoch_interval"):
        return 4294967295  # Obtained from CLI Error message
    return 2**63


def perform_predicates_check(
    cluster_with_constitution: ClusterWithConstitutionRecord,
    param: GuardrailTestParam,
    dependent_proposals: list[clusterlib_utils.UpdateProposal] | tuple = (),
):
    """Check for predicates defined in the constitution.

    * Check invalid proposals from the predicates
    * Check valid proposals using the valid range of values
    """
    param_predicates = cluster_with_constitution.default_constitution[param.param_key]

    type_upper_limit = (
        param.param_upper_limit
        if param.param_upper_limit
        else get_upper_limit_according_to_type(param_predicates["type"])
    )

    # For finding out valid range of values
    max_of_min_values = -type_upper_limit
    min_of_max_values = type_upper_limit
    for predicate in param_predicates["predicates"]:
        if "minValue" in predicate:
            min_value = predicate["minValue"]

            if not isinstance(min_value, int) and "numerator" in min_value:
                min_value = min_value["numerator"] / min_value["denominator"]

            max_of_min_values = max(min_value, max_of_min_values)

            check_min_value_proposals(
                cluster_with_constitution=cluster_with_constitution,
                param=param,
                min_value=min_value,
                dependent_proposals=dependent_proposals,
            )

        elif "maxValue" in predicate:
            max_value = predicate["maxValue"]

            if not isinstance(max_value, int) and "numerator" in max_value:
                max_value = max_value["numerator"] / max_value["denominator"]

            min_of_max_values = min(max_value, min_of_max_values)

            check_max_value_proposals(
                cluster_with_constitution=cluster_with_constitution,
                param=param,
                max_value=max_value,
                dependent_proposals=dependent_proposals,
                type_upper_limit=type_upper_limit,
            )

        elif "notEqual" in predicate:
            not_equal = predicate["notEqual"]
            # Handle the case for must not be
            not_equal_proposal = [
                clusterlib_utils.UpdateProposal(
                    arg=param.param_cli_arg,
                    value=not_equal,
                    name=param.param_name,
                ),
                *dependent_proposals,
            ]
            check_invalid_proposals(
                cluster_with_constitution=cluster_with_constitution,
                proposals=not_equal_proposal,
            )

        else:
            raise PredicateNotSupportedError(predicate)

    # Using valid proposal
    valid_proposals = [
        clusterlib_utils.UpdateProposal(
            arg=param.param_cli_arg,
            value=(
                random.randint(max_of_min_values, min_of_max_values)
                if isinstance(max_of_min_values, int) and isinstance(min_of_max_values, int)
                else _get_rational_str(random.uniform(max_of_min_values, min_of_max_values))
            ),
            name=param.param_name,
        ),
        *dependent_proposals,
    ]
    check_valid_proposals(
        cluster_with_constitution=cluster_with_constitution,
        proposals=valid_proposals,
    )


def get_subtests() -> tp.Generator[tp.Callable, None, None]:  # noqa: C901
    """Get the guardrails scenarios.

    The scenarios are executed as subtests in the `test_guardrails` test.
    """

    def tx_fee_per_byte(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test txFeePerByte guardrails defined in the key "0" of default constitution."""
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.gr001, reqc.cip066)]
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="0",
                param_cli_arg="--min-fee-linear",
                param_name="txFeePerByte",
            ),
        )
        [r.success() for r in (reqc.gr001, reqc.cip066)]

    yield tx_fee_per_byte

    def tx_fee_fixed(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test txFeeFixed guardrails defined in the key "1" of default constitution."""
        reqc.gr002.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="1",
                param_cli_arg="--min-fee-constant",
                param_name="txFeeFixed",
            ),
        )
        reqc.gr002.success()

    yield tx_fee_fixed

    def monetary_expansion(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test monetaryExpansion guardrails defined in the key "10" of default constitution."""
        reqc.gr003.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="10",
                param_cli_arg="--monetary-expansion",
                param_name="monetaryExpansion",
            ),
        )
        reqc.gr003.success()

    yield monetary_expansion

    def treasury_cut(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test treasuryCut guardrails defined in the key "11" of default constitution."""
        reqc.gr004.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="11",
                param_cli_arg="--treasury-expansion",
                param_name="treasuryCut",
            ),
        )
        reqc.gr004.success()

    yield treasury_cut

    def min_pool_cost(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test minPoolCost guardrails defined in the key "16" of default constitution."""
        reqc.gr005.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="16",
                param_cli_arg="--min-pool-cost",
                param_name="minPoolCost",
            ),
        )
        reqc.gr005.success()

    yield min_pool_cost

    def utxo_cost_per_byte(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test utxoCostPerByte guardrails defined in the key "17" of default constitution."""
        reqc.gr006.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="17",
                param_cli_arg="--utxo-cost-per-byte",
                param_name="utxoCostPerByte",
            ),
        )
        reqc.gr006.success()

    yield utxo_cost_per_byte

    def execution_unit_prices(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test executionUnitPrices guardrails defined in the key "19" of default constitution."""
        ex_units_prices_memory_param = GuardrailTestParam(
            param_key="19[0]",
            param_cli_arg="--price-execution-memory",
            param_name="executionUnitPrices[priceMemory]",
        )

        ex_units_prices_steps_param = GuardrailTestParam(
            param_key="19[1]",
            param_cli_arg="--price-execution-steps",
            param_name="executionUnitPrices[priceSteps]",
        )

        # For executionUnitPrices[priceMemory]
        reqc.gr007a.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=ex_units_prices_memory_param,
            dependent_params=[ex_units_prices_steps_param],
        )
        reqc.gr007a.success()

        # For executionUnitPrices[priceSteps]
        reqc.gr007b.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=ex_units_prices_steps_param,
            dependent_params=[ex_units_prices_memory_param],
        )
        reqc.gr007b.success()

    yield execution_unit_prices

    def max_block_body_size(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test maxBlockBodySize guardrails defined in the key "2" of default constitution."""
        reqc.gr008.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="2",
                param_cli_arg="--max-block-body-size",
                param_name="maxBlockBodySize",
            ),
        )
        reqc.gr008.success()

    yield max_block_body_size

    def max_tx_execution_units(cluster_with_constitution: ClusterWithConstitutionRecord):
        """
        Test maxTxExecutionUnits guardrail defined in the key "20" of default constitution.

        Note: Special case for Tx Execution units where parameter value is of format
        (steps, memory) which is incompatible with perform_predicates_check function.
        """
        # Find the upper bound for the maxTxExecutionUnits[memory] from the default constitution
        max_tx_ex_units_mem_upper_bound = int(
            _get_param_max_value(cluster_with_constitution, "20[0]")
        )

        # Find the upper bound for the maxTxExecutionUnits[steps] from the default constitution
        max_tx_ex_units_steps_upper_bound = int(
            _get_param_max_value(cluster_with_constitution, "20[1]")
        )

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.gr009a, reqc.gr009b)]

        # "maxTxExecutionUnits must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_max_tx_execution_units_memory_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({random.randint(-(2**32) - 1, -1)},{random.randint(-(2**32) - 1, -1)})",
                name="maxTxExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=invalid_negative_max_tx_execution_units_memory_proposal,
        )

        # "maxTxExecutionUnits must not exceed."
        invalid_upper_bound_max_tx_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({random.randint(max_tx_ex_units_steps_upper_bound + 1, 2**63 - 1)},"
                f"{random.randint(max_tx_ex_units_mem_upper_bound + 1, 2**63 - 1)})",
                name="maxTxExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=invalid_upper_bound_max_tx_execution_units_proposal,
        )

        # Using valid max tx execution units value
        valid_max_tx_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({random.randint(0, max_tx_ex_units_steps_upper_bound)},"
                f"{random.randint(0, max_tx_ex_units_mem_upper_bound)})",
                name="maxTxExecutionUnits",
            ),
        ]
        check_valid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=valid_max_tx_execution_units_proposal,
        )
        [r.success() for r in (reqc.gr009a, reqc.gr009b)]

    yield max_tx_execution_units

    def max_block_execution_units(cluster_with_constitution: ClusterWithConstitutionRecord):
        """
        Test maxBlockExecutionUnits guardrails defined in the key "21" of default constitution.

        Note: Special case for Block Execution units where parameter value is of format
        (steps, memory) which is incompatible with perform_predicates_check function.
        """
        # Find the upper bound for the maxBlockExecutionUnits[memory] from the default constitution
        max_block_ex_units_mem_upper_bound = int(
            _get_param_max_value(cluster_with_constitution, "21[0]")
        )

        # Find the upper bound for the maxBlockExecutionUnits[steps] from the default constitution
        max_block_ex_units_steps_upper_bound = int(
            _get_param_max_value(cluster_with_constitution, "21[1]")
        )

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.gr010a, reqc.gr010b)]
        # "maxBlockExecutionUnits must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_max_block_execution_units_memory_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(-(2**32) - 1, -1)},{random.randint(-(2**32) - 1, -1)})",
                name="maxBlockExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=invalid_negative_max_block_execution_units_memory_proposal,
        )

        # "maxBlockExecutionUnits must not exceed 120,000,000 units"
        # "maxBlockExecutionUnits must not exceed 40,000,000,000 (40Bn) units"
        invalid_upper_bound_max_block_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(max_block_ex_units_steps_upper_bound + 1, 2**63 - 1)},"
                f"{random.randint(max_block_ex_units_mem_upper_bound + 1, 2**63 - 1)})",
                name="maxBlockExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=invalid_upper_bound_max_block_execution_units_proposal,
        )

        # Using valid max block execution units value
        valid_max_block_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(0, max_block_ex_units_steps_upper_bound)},"
                f"{random.randint(0, max_block_ex_units_mem_upper_bound)})",
                name="maxBlockExecutionUnits",
            ),
        ]
        check_valid_proposals(
            cluster_with_constitution=cluster_with_constitution,
            proposals=valid_max_block_execution_units_proposal,
        )
        [r.success() for r in (reqc.gr010a, reqc.gr010b)]

    yield max_block_execution_units

    def max_value_size(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test maxValueSize guardrails defined in the key "22" of default constitution."""
        reqc.gr011.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="22",
                param_cli_arg="--max-value-size",
                param_name="maxValueSize",
            ),
        )
        reqc.gr011.success()

    yield max_value_size

    def collateral_percentage(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test collateralPercentage guardrails defined in the key "23" of default constitution."""
        reqc.gr012.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="23",
                param_cli_arg="--collateral-percent",
                param_name="collateralPercentage",
            ),
        )
        reqc.gr012.success()

    yield collateral_percentage

    def max_collateral_inputs(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test maxCollateralInputs guardrails defined in the key "24" of default constitution."""
        reqc.gr013.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="24",
                param_cli_arg="--max-collateral-inputs",
                param_name="maxCollateralInputs",
            ),
        )
        reqc.gr013.success()

    yield max_collateral_inputs

    def pool_voting_thresholds(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test for poolVotingThresholds defined in the key "25" of default constitution."""
        pool_motion_no_confidence_param = GuardrailTestParam(
            param_key="25[0]",
            param_cli_arg="--pool-voting-threshold-motion-no-confidence",
            param_name="poolVotingThresholds[motionNoConfidence]",
        )

        pool_committee_normal_param = GuardrailTestParam(
            param_key="25[1]",
            param_cli_arg="--pool-voting-threshold-committee-normal",
            param_name="poolVotingThresholds[committeeNormal]",
        )

        pool_committee_no_confidence_param = GuardrailTestParam(
            param_key="25[2]",
            param_cli_arg="--pool-voting-threshold-committee-no-confidence",
            param_name="poolVotingThresholds[committeeNoConfidence]",
        )

        pool_hard_fork_initiation_param = GuardrailTestParam(
            param_key="25[3]",
            param_cli_arg="--pool-voting-threshold-hard-fork-initiation",
            param_name="poolVotingThresholds[hardForkInitiation]",
        )

        pool_pp_security_group_param = GuardrailTestParam(
            param_key="25[4]",
            param_cli_arg="--pool-voting-threshold-pp-security-group",
            param_name="poolVotingThresholds[ppSecurityGroup]",
        )

        # For poolVotingThresholds[motionNoConfidence]
        reqc.gr014a.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=pool_motion_no_confidence_param,
            dependent_params=[
                pool_committee_normal_param,
                pool_committee_no_confidence_param,
                pool_hard_fork_initiation_param,
                pool_pp_security_group_param,
            ],
        )
        reqc.gr014a.success()

        # For poolVotingThresholds[committeeNormal]
        reqc.gr014b.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=pool_committee_normal_param,
            dependent_params=[
                pool_motion_no_confidence_param,
                pool_committee_no_confidence_param,
                pool_hard_fork_initiation_param,
                pool_pp_security_group_param,
            ],
        )
        reqc.gr014b.success()

        # For poolVotingThresholds[committeeNoConfidence]
        reqc.gr014c.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=pool_committee_no_confidence_param,
            dependent_params=[
                pool_motion_no_confidence_param,
                pool_committee_normal_param,
                pool_hard_fork_initiation_param,
                pool_pp_security_group_param,
            ],
        )
        reqc.gr014c.success()

        # For poolVotingThresholds[hardForkInitiation]
        reqc.gr014d.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=pool_hard_fork_initiation_param,
            dependent_params=[
                pool_motion_no_confidence_param,
                pool_committee_normal_param,
                pool_committee_no_confidence_param,
                pool_pp_security_group_param,
            ],
        )
        reqc.gr014d.success()

        # For poolVotingThresholds[ppSecurityGroup]
        reqc.gr014e.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=pool_pp_security_group_param,
            dependent_params=[
                pool_motion_no_confidence_param,
                pool_committee_normal_param,
                pool_committee_no_confidence_param,
                pool_hard_fork_initiation_param,
            ],
        )
        reqc.gr014e.success()

    yield pool_voting_thresholds

    def drep_voting_thresholds(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test for dRepVotingThresholds defined in the key "26" of default constitution."""
        drep_motion_no_confidence_param = GuardrailTestParam(
            param_key="26[0]",
            param_cli_arg="--drep-voting-threshold-motion-no-confidence",
            param_name="dRepVotingThresholds[motionNoConfidence]",
        )

        drep_committee_normal_param = GuardrailTestParam(
            param_key="26[1]",
            param_cli_arg="--drep-voting-threshold-committee-normal",
            param_name="dRepVotingThresholds[committeeNormal]",
        )

        drep_committee_no_confidence_param = GuardrailTestParam(
            param_key="26[2]",
            param_cli_arg="--drep-voting-threshold-committee-no-confidence",
            param_name="dRepVotingThresholds[committeeNoConfidence]",
        )

        drep_update_to_constitution_param = GuardrailTestParam(
            param_key="26[3]",
            param_cli_arg="--drep-voting-threshold-update-to-constitution",
            param_name="dRepVotingThresholds[updateToConstitution]",
        )

        drep_hard_fork_initiation_param = GuardrailTestParam(
            param_key="26[4]",
            param_cli_arg="--drep-voting-threshold-hard-fork-initiation",
            param_name="dRepVotingThresholds[hardForkInitiation]",
        )

        drep_pp_network_group_param = GuardrailTestParam(
            param_key="26[5]",
            param_cli_arg="--drep-voting-threshold-pp-network-group",
            param_name="dRepVotingThresholds[ppNetworkGroup]",
        )

        drep_pp_economic_group_param = GuardrailTestParam(
            param_key="26[6]",
            param_cli_arg="--drep-voting-threshold-pp-economic-group",
            param_name="dRepVotingThresholds[ppEconomicGroup]",
        )

        drep_pp_technical_group_param = GuardrailTestParam(
            param_key="26[7]",
            param_cli_arg="--drep-voting-threshold-pp-technical-group",
            param_name="dRepVotingThresholds[ppTechnicalGroup]",
        )

        drep_pp_gov_group_param = GuardrailTestParam(
            param_key="26[8]",
            param_cli_arg="--drep-voting-threshold-pp-governance-group",
            param_name="dRepVotingThresholds[ppGovGroup]",
        )

        drep_treasury_withdrawal_param = GuardrailTestParam(
            param_key="26[9]",
            param_cli_arg="--drep-voting-threshold-treasury-withdrawal",
            param_name="dRepVotingThresholds[treasuryWithdrawal]",
        )

        # For dRepVotingThresholds[motionNoConfidence]
        reqc.gr015a.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_motion_no_confidence_param,
            dependent_params=[
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015a.success()

        # For dRepVotingThresholds[committeeNormal]
        reqc.gr015b.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_committee_normal_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015b.success()

        # For dRepVotingThresholds[committeeNoConfidence]
        reqc.gr015c.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_committee_no_confidence_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015c.success()

        # For dRepVotingThresholds[updateToConstitution]
        reqc.gr015d.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_update_to_constitution_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015d.success()

        # For dRepVotingThresholds[hardForkInitiation]
        reqc.gr015e.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_hard_fork_initiation_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015e.success()

        # For dRepVotingThresholds[ppNetworkGroup]
        reqc.gr015f.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_pp_network_group_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015f.success()

        # For dRepVotingThresholds[ppEconomicGroup]
        reqc.gr015g.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_pp_economic_group_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015g.success()

        # For dRepVotingThresholds[ppTechnicalGroup]
        reqc.gr015h.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_pp_technical_group_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_gov_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015h.success()

        # For dRepVotingThresholds[ppGovGroup]
        reqc.gr015i.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_pp_gov_group_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_treasury_withdrawal_param,
            ],
        )
        reqc.gr015i.success()

        # For dRepVotingThresholds[treasuryWithdrawal]
        reqc.gr015j.start(url=helpers.get_vcs_link())
        perform_predicates_check_with_dependent_params(
            cluster_with_constitution=cluster_with_constitution,
            param=drep_treasury_withdrawal_param,
            dependent_params=[
                drep_motion_no_confidence_param,
                drep_committee_normal_param,
                drep_committee_no_confidence_param,
                drep_update_to_constitution_param,
                drep_hard_fork_initiation_param,
                drep_pp_network_group_param,
                drep_pp_economic_group_param,
                drep_pp_technical_group_param,
                drep_pp_gov_group_param,
            ],
        )
        reqc.gr015j.success()

    yield drep_voting_thresholds

    def committee_min_size(
        cluster_with_constitution: ClusterWithConstitutionRecord,
    ):
        """Test committeeMinSize guardrails defined in the key "27" of default constitution."""
        reqc.gr016.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="27",
                param_cli_arg="--min-committee-size",
                param_name="committeeMinSize",
            ),
        )
        reqc.gr016.success()

    yield committee_min_size

    def committee_max_term_limit(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test committeeMaxTermLimit guardrails defined in the key "28" of default constitution."""
        reqc.gr017.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="28",
                param_cli_arg="--committee-term-length",
                param_name="committeeMaxTermLimit",
            ),
        )
        reqc.gr017.success()

    yield committee_max_term_limit

    def gov_action_lifetime(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test govActionLifetime guardrails defined in the key "29" of default constitution."""
        reqc.gr018.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="29",
                param_cli_arg="--governance-action-lifetime",
                param_name="govActionLifetime",
            ),
        )
        reqc.gr018.success()

    yield gov_action_lifetime

    def max_tx_size(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test maxTxSize guardrails defined in the key "3" of default constitution."""
        reqc.gr019.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="3",
                param_cli_arg="--max-tx-size",
                param_name="maxTxSize",
            ),
        )
        reqc.gr019.success()

    yield max_tx_size

    def gov_deposit(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test govDeposit guardrails defined in the key "30" of default constitution."""
        reqc.gr020.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="30",
                param_cli_arg="--new-governance-action-deposit",
                param_name="govDeposit",
            ),
        )
        reqc.gr020.success()

    yield gov_deposit

    def drep_deposit(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test dRepDeposit guardrails defined in the key "31" of default constitution."""
        reqc.gr021.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="31",
                param_cli_arg="--drep-deposit",
                param_name="dRepDeposit",
            ),
        )
        reqc.gr021.success()

    yield drep_deposit

    def drep_activity(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test dRepActivity guardrails defined in the key "32" of default constitution."""
        reqc.gr022.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="32",
                param_cli_arg="--drep-activity",
                param_name="dRepActivity",
            ),
        )
        reqc.gr022.success()

    yield drep_activity

    def min_fee_ref_script_coins_per_byte(
        cluster_with_constitution: ClusterWithConstitutionRecord,
    ):
        """Test minFeeRefScriptCoinsPerByte defined in the key "33" of default constitution."""
        reqc.gr023.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="33",
                param_cli_arg="--ref-script-cost-per-byte",
                param_name="minFeeRefScriptCoinsPerByte",
            ),
        )
        reqc.gr023.success()

    yield min_fee_ref_script_coins_per_byte

    def max_block_header_size(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test maxBlockHeaderSize guardrails defined in the key "4" of default constitution."""
        reqc.gr024.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="4",
                param_cli_arg="--max-block-header-size",
                param_name="maxBlockHeaderSize",
            ),
        )
        reqc.gr024.success()

    yield max_block_header_size

    def stake_address_deposit(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test stakeAddressDeposit guardrails defined in the key "5" of default constitution."""
        reqc.gr025.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="5",
                param_cli_arg="--key-reg-deposit-amt",
                param_name="stakeAddressDeposit",
            ),
        )
        reqc.gr025.success()

    yield stake_address_deposit

    def stake_pool_deposit(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test stakePoolDeposit guardrails defined in the key "6" of default constitution."""
        reqc.gr026.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="6",
                param_cli_arg="--pool-reg-deposit",
                param_name="stakePoolDeposit",
            ),
        )
        reqc.gr026.success()

    yield stake_pool_deposit

    def pool_retire_max_epoch(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test poolRetireMaxEpoch guardrails defined in the key "7" of default constitution."""
        reqc.gr027.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="7",
                param_cli_arg="--pool-retirement-epoch-interval",
                param_name="poolRetireMaxEpoch",
            ),
        )
        reqc.gr027.success()

    yield pool_retire_max_epoch

    def stake_pool_target_num(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test stakePoolTargetNum guardrails defined in the key "8" of default constitution."""
        reqc.gr028.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="8",
                param_cli_arg="--number-of-pools",
                param_name="stakePoolTargetNum",
            ),
        )
        reqc.gr028.success()

    yield stake_pool_target_num

    def pool_pledge_influence(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test poolPledgeInfluence guardrails defined in the key "9" of default constitution."""
        reqc.gr029.start(url=helpers.get_vcs_link())
        perform_predicates_check(
            cluster_with_constitution=cluster_with_constitution,
            param=GuardrailTestParam(
                param_key="9",
                param_cli_arg="--pool-influence",
                param_name="poolPledgeInfluence",
                param_lower_limit=-100,
                param_upper_limit=100,
            ),
        )
        reqc.gr029.success()

    yield pool_pledge_influence

    def cost_models(cluster_with_constitution: ClusterWithConstitutionRecord):
        """Test costModels guardrails defined in the key "18" of default constitution."""
        # Sample cost model data file
        data_dir = pl.Path(__file__).parent.parent / "data"
        cost_proposal_file = data_dir / "cost_models_list_v3.json"

        # Check that guardrails script accepts cost models
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cip028, reqc.cip036)]
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--cost-model-file",
                value=str(cost_proposal_file),
                name="costModels",
            ),
        ]
        check_valid_proposals(
            cluster_with_constitution=cluster_with_constitution, proposals=valid_proposals
        )
        [r.success() for r in (reqc.cip028, reqc.cip036)]

    yield cost_models


class TestGovernanceGuardrails:
    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_guardrails(
        self,
        cluster_with_constitution: ClusterWithConstitutionRecord,
        subtests: pytest_subtests.SubTests,
    ):
        """Test governance guardrails using plutus script constitution.

        * Enact a new constitution with a plutus script
        * Propose parameter change for different guardrail checks
        * Check that the guardrails are enforced
        * Expecting plutus error in case of invalid proposals
        * Expecting valid proposals to be accepted
        * Data file used : data/defaultConstitution.json
        """
        common.get_test_id(cluster_with_constitution.cluster)

        for subt in get_subtests():
            with subtests.test(scenario=subt.__name__):
                subt(cluster_with_constitution)
