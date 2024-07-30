"""Tests for Conway governance constitution guardrail script."""
# ruff: noqa: E501

import dataclasses
import fractions
import logging
import pathlib as pl
import random
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def cluster_const_script(
    cluster_manager: cluster_management.ClusterManager,
) -> governance_utils.GovClusterT:
    """Mark governance as "locked" and return instance of `clusterlib.ClusterLib`."""
    cluster_obj = cluster_manager.get(
        mark="guardrails",
        use_resources=cluster_management.Resources.ALL_POOLS,
        lock_resources=[cluster_management.Resources.COMMITTEE, cluster_management.Resources.DREPS],
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    governance_utils.wait_delayed_ratification(cluster_obj=cluster_obj)
    return cluster_obj, governance_data


@pytest.fixture
def pool_user_const_script(
    cluster_manager: cluster_management.ClusterManager,
    cluster_const_script: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_const_script
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return conway_common.get_registered_pool_user(
        cluster_manager=cluster_manager,
        name_template=name_template,
        cluster_obj=cluster,
        caching_key=key,
    )


@pytest.fixture
def collaterals_const_script(
    cluster_manager: cluster_management.ClusterManager,
    cluster_const_script: governance_utils.GovClusterT,
) -> tp.Tuple[clusterlib.AddressRecord, tp.List[clusterlib.UTXOData]]:
    """Create collateral UTxO for constitution plutus script.

    It is ok to reuse the collateral as it will be never spent.
    """
    cluster, __ = cluster_const_script
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        temp_template = f"test_guardrails_ci{cluster_manager.cluster_instance_num}"

        addr = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_payment_addr_0",
            cluster_obj=cluster,
        )[0]

        # Fund source addresses
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        collateral_tx_outs = [
            clusterlib.TxOut(address=addr.address, amount=5_000_000),
        ]
        collaterals = clusterlib_utils.create_collaterals(
            cluster_obj=cluster,
            payment_addr=addr,
            name_template=temp_template,
            tx_outs=collateral_tx_outs,
        )

        fixture_cache.value = addr, collaterals

    return addr, collaterals


@dataclasses.dataclass(frozen=True)
class ConstScriptRecord:
    """Class to store the cluster with constitution record."""

    cluster: clusterlib.ClusterLib
    constitution_script_file: pl.Path
    constitution_script_hash: str
    pool_user: clusterlib.PoolUser
    collaterals_addr: clusterlib.AddressRecord
    collaterals: tp.List[clusterlib.UTXOData]


@pytest.fixture
def const_script_record(
    cluster_const_script: governance_utils.GovClusterT,
    pool_user_const_script: clusterlib.PoolUser,
    collaterals_const_script: tp.Tuple[clusterlib.AddressRecord, tp.List[clusterlib.UTXOData]],
) -> ConstScriptRecord:
    """Set up the constitution with a governance guardrails plutus script."""
    shared_tmp = temptools.get_pytest_shared_tmp()
    with locking.FileLockIfXdist(f"{shared_tmp}/constitution_script.lock"):
        cluster, governance_data = cluster_const_script
        temp_template = common.get_test_id(cluster)

        constitution_script_file = plutus_common.SCRIPTS_V3_DIR / "constitutionScriptV3.plutus"
        # Obtain script hash
        constitution_script_hash = cluster.g_conway_governance.get_script_hash(
            constitution_script_file
        )

        def _enact_script_constitution():
            """Enact a new constitution with a plutus script."""
            anchor_url = "http://www.const-action.com"
            anchor_data_hash = cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url)

            if conway_common.is_in_bootstrap(cluster_obj=cluster):
                pytest.skip("Cannot run update consitution during bootstrap period.")

            constitution_url = "http://www.const-with-plutus.com"
            constitution_hash = "0000000000000000000000000000000000000000000000000000000000000000"

            __, action_txid, action_ix = conway_common.propose_change_constitution(
                cluster_obj=cluster,
                name_template=temp_template,
                anchor_url=anchor_url,
                anchor_data_hash=anchor_data_hash,
                constitution_url=constitution_url,
                constitution_hash=constitution_hash,
                pool_user=pool_user_const_script,
                constitution_script_hash=constitution_script_hash,
            )

            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_yes",
                payment_addr=pool_user_const_script.payment,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_cc=True,
                approve_drep=True,
            )

            # Wait for the action to be ratified
            cluster.wait_for_new_epoch(padding_seconds=5)
            rat_gov_state = cluster.g_conway_governance.query.gov_state()
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )
            assert rat_action, "Action not found in ratified actions"

            # Wait for the action to be enacted
            cluster.wait_for_new_epoch(padding_seconds=5)
            new_constitution = cluster.g_conway_governance.query.constitution()
            assert new_constitution["script"] == constitution_script_hash

        cur_constitution = cluster.g_conway_governance.query.constitution()

        # Enact the new constitution if the current one is not the one we expect
        if cur_constitution.get("script") != constitution_script_hash:
            _enact_script_constitution()

        return ConstScriptRecord(
            cluster=cluster,
            constitution_script_file=constitution_script_file,
            constitution_script_hash=constitution_script_hash,
            pool_user=pool_user_const_script,
            collaterals_addr=collaterals_const_script[0],
            collaterals=collaterals_const_script[1],
        )


def propose_param_changes(
    const_script_record: ConstScriptRecord,
    proposals: tp.List[clusterlib_utils.UpdateProposal],
) -> str:
    """Build and submit update pparams action with specified proposals."""
    cluster = const_script_record.cluster
    pool_user = const_script_record.pool_user

    temp_template = common.get_test_id(cluster)
    anchor_url = f"http://www.pparam-action-{clusterlib.get_rand_str(4)}.com"
    anchor_data_hash = cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url)
    deposit_amt = cluster.conway_genesis["govActionDeposit"]
    prev_action_rec = governance_utils.get_prev_action(
        action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
        gov_state=cluster.g_conway_governance.query.gov_state(),
    )

    # Positive test: Update pparams action with specifying constitution script hash
    update_args = clusterlib_utils.get_pparams_update_args(update_proposals=proposals)
    update_args.extend(["--constitution-script-hash", const_script_record.constitution_script_hash])

    pparams_action = cluster.g_conway_governance.action.create_pparams_update(
        action_name=temp_template,
        deposit_amt=deposit_amt,
        anchor_url=anchor_url,
        anchor_data_hash=anchor_data_hash,
        cli_args=update_args,
        prev_action_txid=prev_action_rec.txid,
        prev_action_ix=prev_action_rec.ix,
        deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
    )

    execution_units = (740000000, 8000000)
    raw_fee = 900_000

    proposal_script = clusterlib.ComplexProposal(
        proposal_file=pparams_action.action_file,
        script_file=const_script_record.constitution_script_file,
        redeemer_file=plutus_common.REDEEMER_42,
        collaterals=const_script_record.collaterals,
        execution_units=execution_units,
    )

    tx_files = clusterlib.TxFiles(
        signing_key_files=[
            pool_user.payment.skey_file,
            const_script_record.collaterals_addr.skey_file,
        ],
    )

    # Make sure we have enough time to submit the proposal in one epoch
    clusterlib_utils.wait_for_epoch_interval(
        cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
    )

    tx_output_action = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster,
        name_template=f"{temp_template}_action",
        src_address=pool_user.payment.address,
        use_build_cmd=False,
        tx_files=tx_files,
        complex_proposals=[proposal_script],
        deposit=deposit_amt,
        raw_fee=raw_fee,
    )
    action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
    return action_txid


def check_invalid_proposals(
    const_script_record: ConstScriptRecord,
    proposals: tp.List[clusterlib_utils.UpdateProposal],
) -> None:
    """Check that the guardrails are enforced."""
    with pytest.raises(clusterlib.CLIError) as excinfo:
        propose_param_changes(const_script_record=const_script_record, proposals=proposals)
    err_msg = str(excinfo.value)

    if "expecting digit" in err_msg:
        # Incase cli throws error beforehand due to invalid input
        assert 'unexpected "-"' in err_msg, err_msg
    elif "toUnitIntervalOrError" in err_msg:
        # Incase of invalid unit interval
        assert "rational out of bounds" in err_msg, err_msg
    elif "Please enter a value" in err_msg:
        # Incase of invalid value
        assert "Please enter a value in the range [0,1]" in err_msg, err_msg
    elif "cannot parse value" in err_msg:
        # Incase of invalid value
        assert "cannot parse value" in err_msg, err_msg
    elif "MalformedProposal" in err_msg:
        # Incase of malformed proposal
        assert "ParameterChange" in err_msg, err_msg
    elif "EPOCH_INTERVAL" in err_msg:
        # Incase of invalid epoch interval
        assert "EPOCH_INTERVAL must not be less than 0" in err_msg, err_msg
    elif "Failed reading" in err_msg:
        # Incase of invalid value
        assert "Failed reading" in err_msg, err_msg
    else:
        assert "The machine terminated because of an error" in err_msg, err_msg


def check_valid_proposals(
    const_script_record: ConstScriptRecord,
    proposals: tp.List[clusterlib_utils.UpdateProposal],
) -> None:
    action_txid = propose_param_changes(
        const_script_record=const_script_record, proposals=proposals
    )
    action_gov_state = const_script_record.cluster.g_conway_governance.query.gov_state()
    prop_action = governance_utils.lookup_proposal(
        gov_state=action_gov_state, action_txid=action_txid
    )
    assert prop_action, "Update pparams action not found"


def _get_rational_str(value: float) -> str:
    return str(fractions.Fraction(value).limit_denominator())


@pytest.mark.testnets
class TestGovernanceGuardrails:
    """Test  governance guardrails using plutus script constitution.

    * Enact a new constitution with a plutus script
    * Propose parameter change for different guardrail checks
    * Check that the guardrails are enforced
    * Expecting plutus error incase of invalid proposals
    """

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_tx_fee_per_byte(self, const_script_record: ConstScriptRecord):
        """Test `txFeePerByte` guardrail using plutus script constitution.

        "0": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 30,
                "$comment": "txFeePerByte must not be lower than 30 (0.000030 ada)"
            },
            {
                "maxValue": 1000,
                "$comment": "txFeePerByte must not exceed 1,000 (0.001 ada)"
            },
            {
                "minValue": 0,
                "$comment": "txFeePerByte must not be negative"
            }
            ],
            "$comment": "txFeePerByte"
        }
        """
        tx_fee_per_byte_lower_bound = 30
        tx_fee_per_byte_upper_bound = 1000

        # "txFeePerByte must not be lower than 30 (0.000030 ada)"
        invalid_lower_bound_tx_fee_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=random.randint(0, tx_fee_per_byte_lower_bound - 1),
                name="txFeePerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_tx_fee_per_byte_proposal,
        )

        # "$comment": "txFeePerByte must not exceed 1,000 (0.001 ada)"
        invalid_upper_bound_tx_fee_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=random.randint(tx_fee_per_byte_upper_bound + 1, 2**32 - 1),
                name="txFeePerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_tx_fee_per_byte_proposal,
        )

        # "$comment": "txFeePerByte must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_tx_fee_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=random.randint(-(2**32), -1),
                name="txFeePerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_tx_fee_per_byte_proposal,
        )

        # Using valid min fee linear value
        valid_tx_fee_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=random.randint(tx_fee_per_byte_lower_bound, tx_fee_per_byte_upper_bound),
                name="txFeePerByte",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_tx_fee_per_byte_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_tx_fee_fixed(self, const_script_record: ConstScriptRecord):
        """Test `txFeeFixed` guardrail using plutus script constitution.

        "1": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 100000,
                "$comment": "txFeeFixed must not be lower than 100,000 (0.1 ada)"
            },
            {
                "maxValue": 10000000,
                "$comment": "txFeeFixed must not exceed 10,000,000 (10 ada)"
            },
            {
                "minValue": 0,
                "$comment": "txFeeFixed must not be negative"
            }
            ],
            "$comment": "txFeeFixed"
        }
        """
        tx_fee_fixed_lower_bound = 100000  # 0.1 ada
        tx_fee_fixed_upper_bound = 10000000  # 10 ada

        # "txFeeFixed must not be lower than 100,000 (0.1 ada)"
        invalid_lower_bound_tx_fee_fixed_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=random.randint(0, tx_fee_fixed_lower_bound - 1),
                name="txFeeFixed",
            ),
        ]

        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_tx_fee_fixed_proposal,
        )

        # "$comment": "txFeeFixed must not exceed 10,000,000 (10 ada)"
        invalid_upper_bound_tx_fee_fixed_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=random.randint(tx_fee_fixed_upper_bound + 1, 2**32 - 1),
                name="txFeeFixed",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_tx_fee_fixed_proposal,
        )

        # "$comment": "txFeeFixed must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_tx_fee_fixed_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=random.randint(-(2**32), -1),
                name="txFeeFixed",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_tx_fee_fixed_proposal,
        )

        # Using valid min fee constant value
        valid_tx_fee_fixed_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=random.randint(tx_fee_fixed_lower_bound, tx_fee_fixed_upper_bound),
                name="txFeeFixed",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_tx_fee_fixed_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_monetary_expansion(self, const_script_record: ConstScriptRecord):
        """Test `monetaryExpansion` guardrail using plutus script constitution.

        "10": {
            "type": "unit_interval",
            "predicates": [
            {
                "maxValue": { "numerator": 5, "denominator": 1000 },
                "$comment": "monetaryExpansion must not exceed 0.005"
            },
            {
                "minValue": { "numerator": 1, "denominator": 1000 },
                "$comment": "monetaryExpansion must not be lower than 0.001"
            },
            {
                "minValue": { "numerator": 0, "denominator": 1000 },
                "$comment": "monetaryExpansion must not be negative"
            }
            ],
            "$comment": "monetaryExpansion"
        }
        """
        monetary_expansion_lower_bound = 1 / 1000
        monetary_expansion_upper_bound = 5 / 1000

        # "monetaryExpansion must not be lower than 0.001"
        invalid_lower_bound_monetary_expansion_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--monetary-expansion",
                value=random.uniform(0, monetary_expansion_lower_bound),
                name="monetaryExpansion",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_monetary_expansion_proposal,
        )

        # "monetaryExpansion must not exceed 0.005"
        invalid_upper_bound_monetary_expansion_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--monetary-expansion",
                value=random.uniform(monetary_expansion_upper_bound, 1),
                name="monetaryExpansion",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_monetary_expansion_proposal,
        )

        # "monetaryExpansion must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_monetary_expansion_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--monetary-expansion",
                value=random.uniform(-1, 0),
                name="monetaryExpansion",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_monetary_expansion_proposal,
        )

        # Using valid monetary expansion value
        valid_monetary_expansion_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--monetary-expansion",
                value=random.uniform(
                    monetary_expansion_lower_bound, monetary_expansion_upper_bound
                ),
                name="monetaryExpansion",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_monetary_expansion_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_treasury_cut(self, const_script_record: ConstScriptRecord):
        """Test `treasuryCut` guardrail using plutus script constitution.

        "11": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 10, "denominator": 100 },
                "$comment": "treasuryCut must not be lower than 0.1 (10%)"
            },
            {
                "maxValue": { "numerator": 30, "denominator": 100 },
                "$comment": "treasuryCut must not exceed 0.3 (30%)"
            },
            {
                "minValue": { "numerator": 0, "denominator": 100 },
                "$comment": "treasuryCut must not be negative"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "treasuryCut must not exceed 1.0 (100%)"
            }
            ],
            "$comment": "treasuryCut"
        }
        """
        treasury_cut_lower_bound = 10 / 100
        treasury_cut_upper_bound = 30 / 100

        # "treasuryCut must not be lower than 0.1 (10%)"
        invalid_lower_bound_treasury_cut_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--treasury-expansion",
                value=random.uniform(0, treasury_cut_lower_bound),
                name="treasuryCut",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_treasury_cut_proposal,
        )

        # "treasuryCut must not exceed 0.3 (30%)"
        invalid_upper_bound_treasury_cut_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--treasury-expansion",
                value=random.uniform(treasury_cut_upper_bound, 1),
                name="treasuryCut",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_treasury_cut_proposal,
        )

        # "treasuryCut must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_treasury_cut_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--treasury-expansion",
                value=random.uniform(-1, 0),
                name="treasuryCut",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_treasury_cut_proposal,
        )

        # "treasuryCut must not exceed 1.0 (100%)"
        invalid_upper_bound_treasury_cut_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--treasury-expansion",
                value=random.uniform(1, 100),
                name="treasuryCut",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_treasury_cut_proposal,
        )

        # Using valid treasury cut value
        valid_treasury_cut_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--treasury-expansion",
                value=random.uniform(treasury_cut_lower_bound, treasury_cut_upper_bound),
                name="treasuryCut",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_treasury_cut_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_min_pool_cost(self, const_script_record: ConstScriptRecord):
        """Test `minPoolCost` guardrail using plutus script constitution.

        "16": {
        "type": "integer",
        "predicates": [
            {
            "minValue": 0,
            "$comment": "minPoolCost must not be negative" },
            {
            "maxValue": 500000000,
            "$comment": "minPoolCost must not be set above 500,000,000 (500 ada)"
            }
        ],
        "$comment": "minPoolCost"
        },
        """
        min_pool_cost_upper_bound = 500000

        # "minPoolCost must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_min_pool_cost_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=random.randint(-(2**32), -1),
                name="minPoolCost",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_min_pool_cost_proposal,
        )

        # "minPoolCost must not be set above 500,000,000 (500 ada)"
        invalid_upper_bound_min_pool_cost_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=random.randint(min_pool_cost_upper_bound + 1, 2**32 - 1),
                name="minPoolCost",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_min_pool_cost_proposal,
        )

        # Using valid min pool cost value
        valid_min_pool_cost_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=random.randint(0, min_pool_cost_upper_bound),
                name="minPoolCost",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_min_pool_cost_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_utxo_cost_per_byte(self, const_script_record: ConstScriptRecord):
        """Test `utxoCostPerByte` guardrail using plutus script constitution.

        "17": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 3000,
                "$comment": "utxoCostPerByte must not be lower than 3,000  (0.003 ada)"
            },
            {
                "maxValue": 6500,
                "$comment": "utxoCostPerByte must not exceed 6,500 (0.0065 ada)"
            },
            {
                "notEqual": 0,
                "$comment": "utxoCostPerByte must not be zero"
            },
            {
                "minValue": 0,
                "$comment": "utxoCostPerByte must not be negative"
            }
            ],
            "$comment": "utxoCostPerByte"
        }
        """
        utxo_cost_per_byte_lower_bound = 3000
        utxo_cost_per_byte_upper_bound = 6500

        # "utxoCostPerByte must not be lower than 3,000  (0.003 ada)"
        invalid_lower_bound_utxo_cost_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=random.randint(0, utxo_cost_per_byte_lower_bound - 1),
                name="utxoCostPerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_utxo_cost_per_byte_proposal,
        )

        # "utxoCostPerByte must not exceed 6,500 (0.0065 ada)"
        invalid_upper_bound_utxo_cost_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=random.randint(utxo_cost_per_byte_upper_bound + 1, 2**32 - 1),
                name="utxoCostPerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_utxo_cost_per_byte_proposal,
        )

        # "utxoCostPerByte must not be zero"
        invalid_zero_utxo_cost_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=0,
                name="utxoCostPerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_zero_utxo_cost_per_byte_proposal,
        )

        # "utxoCostPerByte must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_utxo_cost_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=random.randint(-(2**32), -1),
                name="utxoCostPerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_utxo_cost_per_byte_proposal,
        )

        # Using valid UTxO cost per byte value
        valid_utxo_cost_per_byte_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=random.randint(
                    utxo_cost_per_byte_lower_bound, utxo_cost_per_byte_upper_bound
                ),
                name="utxoCostPerByte",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_utxo_cost_per_byte_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_execution_unit_prices(self, const_script_record: ConstScriptRecord):
        """Test `executionUnitPrices` guardrail using plutus script constitution.

        "19[0]": {
            "type": "unit_interval",
            "predicates": [
            {
                "maxValue": { "numerator": 2000, "denominator": 10000 },
                "$comment": "executionUnitPrices[priceMemory] must not exceed 2,000 / 10,000"
            },
            {
                "minValue": { "numerator": 400, "denominator": 10000 },
                "$comment": "executionUnitPrices[priceMemory] must not be lower than 400 / 10,000"
            }
            ],
            "$comment": "executionUnitPrices[priceMemory]"
        }
        "19[1]": {
            "type": "unit_interval",
            "predicates": [
            {
                "maxValue": { "numerator": 2000, "denominator": 10000000 },
                "$comment": "executionUnitPrices[priceSteps] must not exceed 2,000 / 10,000,000"
            },
            {
                "minValue": { "numerator": 500, "denominator": 10000000 },
                "$comment": "executionUnitPrices[priceSteps] must not be lower than 500 / 10,000,000"
            }
            ],
            "$comment": "executionUnitPrices[priceSteps]"
        },
        """
        execution_unit_prices_price_memory_lower_bound = 400 / 10000
        execution_unit_prices_price_memory_upper_bound = 2000 / 10000

        execution_unit_prices_price_steps_lower_bound = 500 / 10000000
        execution_unit_prices_price_steps_upper_bound = 2000 / 10000000

        # "executionUnitPrices[priceMemory] must not be lower than 400 / 10,000"
        # "executionUnitPrices[priceSteps] must not be lower than 500 / 10,000,000"
        invalid_lower_bound_execution_unit_prices = [
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-memory",
                value=random.uniform(0, execution_unit_prices_price_memory_lower_bound),
                name="executionUnitPrices[priceMemory]",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-steps",
                value=round(random.uniform(0, execution_unit_prices_price_steps_lower_bound), 18),
                name="executionUnitPrices[priceSteps]",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_execution_unit_prices,
        )

        # "executionUnitPrices[priceMemory] must not exceed 2,000 / 10,000"
        # "executionUnitPrices[priceSteps] must not exceed 2,000 / 10,000,000"
        invalid_upper_bound_execution_unit_prices = [
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-memory",
                value=random.uniform(execution_unit_prices_price_memory_upper_bound, 1),
                name="executionUnitPrices[priceMemory]",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-steps",
                value=round(random.uniform(execution_unit_prices_price_steps_upper_bound, 1), 18),
                name="executionUnitPrices[priceSteps]",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_execution_unit_prices,
        )

        # Using valid execution unit prices price memory value
        # Using valid execution unit prices price steps value
        valid_execution_unit_prices = [
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-memory",
                value=random.uniform(
                    execution_unit_prices_price_memory_lower_bound,
                    execution_unit_prices_price_memory_upper_bound,
                ),
                name="executionUnitPrices[priceMemory]",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--price-execution-steps",
                value=round(
                    random.uniform(
                        execution_unit_prices_price_steps_lower_bound,
                        execution_unit_prices_price_steps_upper_bound,
                    ),
                    18,
                ),
                name="executionUnitPrices[priceSteps]",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_execution_unit_prices,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_max_block_body_size(self, const_script_record: ConstScriptRecord):
        """Test `maxBlockBodySize` guardrail using plutus script constitution.

        "2": {
            "type": "integer",
            "predicates": [
            {
                "maxValue": 122880,
                "$comment": "maxBlockBodySize must not exceed 122,880 Bytes (120KB)"
            },
            {
                "minValue": 24576,
                "$comment": "maxBlockBodySize must not be lower than 24,576 Bytes (24KB)"
            }
            ],
            "$comment": "maxBlockBodySize"
        },
        """
        max_block_body_size_lower_bound = 24576
        max_block_body_size_upper_bound = 122880

        # "maxBlockBodySize must not be lower than 24,576 Bytes (24KB)"
        invalid_lower_bound_max_block_body_size_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-body-size",
                value=random.randint(0, max_block_body_size_lower_bound - 1),
                name="maxBlockBodySize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_max_block_body_size_proposal,
        )

        # "maxBlockBodySize must not exceed 122,880 Bytes (120KB)"
        invalid_upper_bound_max_block_body_size_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-body-size",
                value=random.randint(max_block_body_size_upper_bound + 1, 2**32 - 1),
                name="maxBlockBodySize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_max_block_body_size_proposal,
        )

        # Using valid max block body size value
        valid_max_block_body_size_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-body-size",
                value=random.randint(
                    max_block_body_size_lower_bound, max_block_body_size_upper_bound
                ),
                name="maxBlockBodySize",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_max_block_body_size_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_max_tx_execution_units(self, const_script_record: ConstScriptRecord):
        """Test `maxTxExecutionUnits[memory]` guardrail using plutus script constitution.

        "20[0]": {
            "type": "integer",
            "predicates": [
            {
                "maxValue": 40000000,
                "$comment": "maxTxExecutionUnits[memory] must not exceed 40,000,000 units"
            },
            {
                "minValue": 0,
                "$comment": "maxTxExecutionUnits[memory] must not be negative"
            }
            ],
            "$comment": "maxTxExecutionUnits[memory]"
        },
        "20[1]": {
            "type": "integer",
            "predicates": [
              {
                "maxValue": 15000000000,
                "$comment": "maxTxExecutionUnits[steps] must not exceed 15,000,000,000 (15Bn) units"
              },
              {
                "minValue": 0,
                "$comment": "maxTxExecutionUnits[steps] must not be negative"
              }

            ],
            "$comment": "maxTxExecutionUnits[steps]"
        },
        """
        max_tx_execution_units_memory_upper_bound = 40000000  # 40_000_000
        max_tx_execution_units_steps_upper_bound = 15000000000  # 15_000_000_000

        # "maxTxExecutionUnits must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_max_tx_execution_units_memory_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({random.randint(-2**32-1, -1)}," f"{random.randint(-2**32-1, -1)})",
                name="maxTxExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_max_tx_execution_units_memory_proposal,
        )

        # "maxTxExecutionUnits must not exceed."
        invalid_upper_bound_max_tx_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({random.randint(max_tx_execution_units_steps_upper_bound+1, 2**63 - 1)},"
                f"{random.randint(max_tx_execution_units_memory_upper_bound+1, 2**63 - 1)})",
                name="maxTxExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_max_tx_execution_units_proposal,
        )

        # Using valid max tx execution units value
        valid_max_tx_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({random.randint(0, max_tx_execution_units_steps_upper_bound)},"
                f"{random.randint(0, max_tx_execution_units_memory_upper_bound)})",
                name="maxTxExecutionUnits",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_max_tx_execution_units_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_max_block_execution_units(self, const_script_record: ConstScriptRecord):
        """Test `maxBlockExecutionUnits` guardrail using plutus script constitution.

        "21[0]": {
            "type": "integer",
            "predicates": [
            {
                "maxValue": 120000000,
                "$comment": "maxBlockExecutionUnits[memory] must not exceed 120,000,000 units"
            },
            {
                "minValue": 0,
                "$comment": "maxBlockExecutionUnits[memory] must not be negative"
            }
            ],
            "$comment": "maxBlockExecutionUnits[memory]"
        },
        "21[1]": {
            "type": "integer",
            "predicates": [
            {
                "maxValue": 40000000000,
                "$comment": "maxBlockExecutionUnits[steps] must not exceed 40,000,000,000 (40Bn) units"
            },
            {
                "minValue": 0,
                "$comment": "maxBlockExecutionUnits[steps] must not be negative"
            }
            ],
            "$comment": "maxBlockExecutionUnits[steps]"
        },
        """
        max_block_execution_units_memory_upper_bound = 120_000_000
        max_block_execution_units_steps_upper_bound = 40_000_000_000

        # "maxBlockExecutionUnits must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_max_block_execution_units_memory_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(-2**32-1, -1)}," f"{random.randint(-2**32-1, -1)})",
                name="maxBlockExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_max_block_execution_units_memory_proposal,
        )

        # "maxBlockExecutionUnits must not exceed 120,000,000 units"
        # "maxBlockExecutionUnits must not exceed 40,000,000,000 (40Bn) units"
        invalid_upper_bound_max_block_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(max_block_execution_units_steps_upper_bound+1, 2**63 - 1)},"
                f"{random.randint(max_block_execution_units_memory_upper_bound+1, 2**63 - 1)})",
                name="maxBlockExecutionUnits",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_max_block_execution_units_proposal,
        )

        # Using valid max block execution units value
        valid_max_block_execution_units_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(0, max_block_execution_units_steps_upper_bound)},"
                f"{random.randint(0, max_block_execution_units_memory_upper_bound)})",
                name="maxBlockExecutionUnits",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_max_block_execution_units_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_max_value_size(self, const_script_record: ConstScriptRecord):
        """Test `maxValueSize guardrail` using plutus script constitution.

        "22": {
            "type": "integer",
            "predicates": [
            {
                "maxValue": 12288,
                "$comment": "maxValueSize must not exceed 12,288 Bytes (12KB)"
            },
            {
                "minValue": 0,
                "$comment": "maxValueSize must not be negative"
            }
            ],
            "$comment": "maxValueSize"
        },
        """
        max_value_size_upper_bound = 12288

        # "maxValueSize must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_max_value_size_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-value-size",
                value=random.randint(-(2**32), -1),
                name="maxValueSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_max_value_size_proposal,
        )

        # "maxValueSize must not exceed 12,288 Bytes (12KB)"
        invalid_upper_bound_max_value_size_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-value-size",
                value=random.randint(max_value_size_upper_bound + 1, 2**32 - 1),
                name="maxValueSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_max_value_size_proposal,
        )

        # Using valid max value size value
        valid_max_value_size_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-value-size",
                value=random.randint(0, max_value_size_upper_bound),
                name="maxValueSize",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_max_value_size_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_collateral_percentage(self, const_script_record: ConstScriptRecord):
        """Test `collateralPercentage` guardrail using plutus script constitution.

        "23": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 100,
                "$comment": "collateralPercentage must not be lower than 100"
            },
            {
                "maxValue": 200,
                "$comment": "collateralPercentage must not exceed 200"
            },
            {
                "minValue": 0,
                "$comment": "collateralPercentage must not be negative"
            },
            {
                "notEqual": 0,
                "$comment": "collateralPercentage must not be zero"
            }
            ],
            "$comment": "collateralPercentage"
        },
        """
        collateral_percentage_lower_bound = 100
        collateral_percentage_upper_bound = 200

        # "collateralPercentage must not be negative"
        # Note: Negative value is not allowed by the CLI before reaching the plutus script
        # so we can't test this case as guardrails from CLI it should be tested in the ledger level
        invalid_negative_collateral_percentage_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=random.randint(-(2**32), -1),
                name="collateralPercentage",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_negative_collateral_percentage_proposal,
        )

        # "collateralPercentage must not exceed 200"
        invalid_upper_bound_collateral_percentage_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=random.randint(collateral_percentage_upper_bound + 1, 65535),
                name="collateralPercentage",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_collateral_percentage_proposal,
        )

        # "collateralPercentage must not be zero"
        invalid_zero_collateral_percentage_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=0,
                name="collateralPercentage",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_zero_collateral_percentage_proposal,
        )

        # "collateralPercentage must not be lower than 100"
        invalid_lower_bound_collateral_percentage_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=random.randint(0, collateral_percentage_lower_bound - 1),
                name="collateralPercentage",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_collateral_percentage_proposal,
        )

        # Using valid collateral percentage value
        valid_collateral_percentage_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=random.randint(
                    collateral_percentage_lower_bound, collateral_percentage_upper_bound
                ),
                name="collateralPercentage",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_collateral_percentage_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_max_collateral_inputs(self, const_script_record: ConstScriptRecord):
        """Test `maxCollateralInputs` guardrail using plutus script constitution.

        "24": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 1,
                "$comment": "maxCollateralInputs must not be reduced below 1"
            }
            ],
            "$comment": "maxCollateralInputs"
        },
        """
        max_collateral_inputs_lower_bound = 1

        # "maxCollateralInputs must not be reduced below 1"
        invalid_lower_bound_max_collateral_inputs_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-collateral-inputs",
                value=random.randint(0, max_collateral_inputs_lower_bound - 1),
                name="maxCollateralInputs",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_max_collateral_inputs_proposal,
        )

        # Using valid max collateral inputs value
        valid_max_collateral_inputs_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--max-collateral-inputs",
                value=random.randint(max_collateral_inputs_lower_bound, 65535),
                name="maxCollateralInputs",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_max_collateral_inputs_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_pool_voting_thresholds(self, const_script_record: ConstScriptRecord):
        """Test `poolVotingThresholds` motionNoConfidence guardrail using plutus script constitution.

          "25[0]": {
              "type": "unit_interval",
              "predicates": [
              {
                  "minValue": { "numerator": 50, "denominator": 100 },
                  "$comment": "All thresholds must be in the range 50%-100%"
              },
              {
                  "maxValue": { "numerator": 100, "denominator": 100 },
                  "$comment": "All thresholds must be in the range 50%-100%"
              },
              {
                  "minValue": { "numerator": 51, "denominator": 100 },
                  "$comment": "No confidence action thresholds must be in the range 51%-75%"
              },
              {
                  "maxValue": { "numerator": 75, "denominator": 100 },
                  "$comment": "No confidence action thresholds must be in the range 51%-75%"
              }
              ],
              "$comment": "poolVotingThresholds[motionNoConfidence]"
          },

          "25[1]": {
              "type": "unit_interval",
              "predicates": [
              {
                  "minValue": { "numerator": 50, "denominator": 100 },
                  "$comment": "All thresholds must be in the range 50%-100%"
              },
              {
                  "maxValue": { "numerator": 100, "denominator": 100 },
                  "$comment": "All thresholds must be in the range 50%-100%"
              },
              {
                  "minValue": { "numerator": 65, "denominator": 100 },
                  "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
              },
              {
                  "maxValue": { "numerator": 90, "denominator": 100 },
                  "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
              }
              ],
              "$comment": "poolVotingThresholds[committeeNormal]"
          },


        "25[2]": {
          "type": "unit_interval",
          "predicates": [
            {
              "minValue": { "numerator": 50, "denominator": 100 },
              "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
              "maxValue": { "numerator": 100, "denominator": 100 },
              "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
              "minValue": { "numerator": 65, "denominator": 100 },
              "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
            },
            {
              "maxValue": { "numerator": 90, "denominator": 100 },
              "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
            }
          ],
          "$comment": "poolVotingThresholds[committeeNoConfidence]"
        },

        "25[3]": {
          "type": "unit_interval",
          "predicates": [
            {
              "minValue": { "numerator": 50, "denominator": 100 },
              "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
              "maxValue": { "numerator": 100, "denominator": 100 },
              "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
              "minValue": { "numerator": 51, "denominator": 100 },
              "$comment": "Hard fork action thresholds must be in the range 51%-80%"
            },
            {
              "maxValue": { "numerator": 80, "denominator": 100 },
              "$comment": "Hard fork action thresholds must be in the range 51%-80%"
            }
          ],
          "$comment": "poolVotingThresholds[hardForkInitiation]"
        },

        "25[4]": {
          "type": "unit_interval",
          "predicates": [
            {
              "minValue": { "numerator": 50, "denominator": 100 },
              "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
              "maxValue": { "numerator": 100, "denominator": 100 },
              "$comment": "All thresholds must be in the range 50%-100%"
            }
          ],
          "$comment": "poolVotingThresholds[ppSecurityGroup]"
        }
        """
        # "poolVotingThresholds[motionNoConfidence]"
        pool_voting_thresholds_motion_no_confidence_lower_bound = 0.51
        pool_voting_thresholds_motion_no_confidence_upper_bound = 0.75

        # "poolVotingThresholds[committeeNormal]"
        pool_voting_thresholds_committee_normal_lower_bound = 0.65
        pool_voting_thresholds_committee_normal_upper_bound = 0.90

        # "poolVotingThresholds[committeeNoConfidence]"
        pool_voting_thresholds_committee_no_confidence_lower_bound = 0.65
        pool_voting_thresholds_committee_no_confidence_upper_bound = 0.90

        # "poolVotingThresholds[hardForkInitiation]"
        pool_voting_thresholds_hard_fork_initiation_lower_bound = 0.51
        pool_voting_thresholds_hard_fork_initiation_upper_bound = 0.80

        # "poolVotingThresholds[ppSecurityGroup]"
        pool_voting_thresholds_pp_security_group_lower_bound = 0.50
        pool_voting_thresholds_pp_security_group_upper_bound = 1

        # "poolVotingThresholds[motionNoConfidence]" must not be lower than 51
        invalid_lower_bound_pool_voting_thresholds_motion_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(0, pool_voting_thresholds_motion_no_confidence_lower_bound)
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_voting_thresholds_motion_no_confidence,
        )

        # "poolVotingThresholds[motionNoConfidence]" must not exceed 75
        invalid_upper_bound_pool_voting_thresholds_motion_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(pool_voting_thresholds_motion_no_confidence_upper_bound, 1)
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_pool_voting_thresholds_motion_no_confidence,
        )

        # "poolVotingThresholds[committeeNormal]" must not be lower than 65
        invalid_lower_bound_pool_voting_thresholds_committee_normal = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(0, pool_voting_thresholds_committee_normal_lower_bound)
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_voting_thresholds_committee_normal,
        )

        # "poolVotingThresholds[committeeNormal]" must not exceed 90
        invalid_upper_bound_pool_voting_thresholds_committee_normal = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(pool_voting_thresholds_committee_normal_upper_bound, 1)
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_pool_voting_thresholds_committee_normal,
        )

        # "poolVotingThresholds[committeeNoConfidence]" must not be lower than 65
        invalid_lower_bound_pool_voting_thresholds_committee_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(0, pool_voting_thresholds_committee_no_confidence_lower_bound)
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_voting_thresholds_committee_no_confidence,
        )

        # "poolVotingThresholds[committeeNoConfidence]" must not exceed 90
        invalid_upper_bound_pool_voting_thresholds_committee_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(pool_voting_thresholds_committee_no_confidence_upper_bound, 1)
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_pool_voting_thresholds_committee_no_confidence,
        )

        # "poolVotingThresholds[hardForkInitiation]" must not be lower than 51
        invalid_lower_bound_pool_voting_thresholds_hard_fork_initiation = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(0, pool_voting_thresholds_hard_fork_initiation_lower_bound)
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_voting_thresholds_hard_fork_initiation,
        )

        # "poolVotingThresholds[hardForkInitiation]" must not exceed 80
        invalid_upper_bound_pool_voting_thresholds_hard_fork_initiation = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(pool_voting_thresholds_hard_fork_initiation_upper_bound, 1)
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_pool_voting_thresholds_hard_fork_initiation,
        )

        # "poolVotingThresholds[ppSecurityGroup]" must not be lower than 50
        invalid_lower_bound_pool_voting_thresholds_pp_security_group = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(0, pool_voting_thresholds_pp_security_group_lower_bound)
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_voting_thresholds_pp_security_group,
        )

        # "poolVotingThresholds[ppSecurityGroup]" must not exceed 100
        invalid_upper_bound_pool_voting_thresholds_pp_security_group = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(pool_voting_thresholds_pp_security_group_upper_bound + 1, 2**31)
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_pool_voting_thresholds_pp_security_group,
        )

        # Using valid pool voting thresholds value
        valid_pool_voting_thresholds_proposal = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_motion_no_confidence_lower_bound,
                        pool_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_normal_lower_bound,
                        pool_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_committee_no_confidence_lower_bound,
                        pool_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_hard_fork_initiation_lower_bound,
                        pool_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-voting-threshold-pp-security-group",
                value=_get_rational_str(
                    random.uniform(
                        pool_voting_thresholds_pp_security_group_lower_bound,
                        pool_voting_thresholds_pp_security_group_upper_bound,
                    )
                ),
                name="ppSecurityGroup",
            ),
        ]
        check_valid_proposals(
            const_script_record=const_script_record,
            proposals=valid_pool_voting_thresholds_proposal,
        )

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_drep_voting_thresholds(self, const_script_record: ConstScriptRecord):
        """Test `dRepVotingThresholds` guardrail using plutus script constitution.

        "26[0]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 51, "denominator": 100 },
                "$comment": "No confidence action thresholds must be in the range 51%-75%"
            },
            {
                "maxValue": { "numerator": 75, "denominator": 100 },
                "$comment": "No confidence action thresholds must be in the range 51%-75%"
            }
            ],
            "$comment": "dRepVotingThresholds[motionNoConfidence]"
        },

        "26[1]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 65, "denominator": 100 },
                "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
            },
            {
                "maxValue": { "numerator": 90, "denominator": 100 },
                "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
            }
            ],
            "$comment": "dRepVotingThresholds[committeeNormal]"
        },

        "26[2]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 65, "denominator": 100 },
                "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
            },
            {
                "maxValue": { "numerator": 90, "denominator": 100 },
                "$comment": "Update Constitutional committee action thresholds must be in the range 65%-90%"
            }
            ],
            "$comment": "dRepVotingThresholds[committeeNoConfidence]"
        },

        "26[3]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 65, "denominator": 100 },
                "$comment": "Update Constitution of proposal policy action thresholds must be in the range 65%-90%"
            },
            {
                "maxValue": { "numerator": 90, "denominator": 100 },
                "$comment": "Update Constitution of proposal policy action thresholds must be in the range 65%-90%"
            }
            ],
            "$comment": "dRepVotingThresholds[updateToConstitution]"
        },

        "26[4]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 51, "denominator": 100 },
                "$comment": "Hard fork action thresholds must be in the range 51%-80%"
            },
            {
                "maxValue": { "numerator": 80, "denominator": 100 },
                "$comment": "Hard fork action thresholds must be in the range 51%-80%"
            }
            ],
            "$comment": "dRepVotingThresholds[hardForkInitiation]"
        },

        "26[5]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 51, "denominator": 100 },
                "$comment": "Economic, network and technical parameter thresholds must be in the range 51%-75%"
            },
            {
                "maxValue": { "numerator": 75, "denominator": 100 },
                "$comment": "Economic, network and technical parameter thresholds must be in the range 51%-75%"
            }
            ],
            "$comment": "dRepVotingThresholds[ppNetworkGroup]"
        },

        "26[6]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 51, "denominator": 100 },
                "$comment": "Economic, network and technical parameter thresholds must be in the range 51%-75%"
            },
            {
                "maxValue": { "numerator": 75, "denominator": 100 },
                "$comment": "Economic, network and technical parameter thresholds must be in the range 51%-75%"
            }
            ],
            "$comment": "dRepVotingThresholds[ppEconomicGroup]"
        },

        "26[7]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 51, "denominator": 100 },
                "$comment": "Economic, network and technical parameter thresholds must be in the range 51%-75%"
            },
            {
                "maxValue": { "numerator": 75, "denominator": 100 },
                "$comment": "Economic, network and technical parameter thresholds must be in the range 51%-75%"
            }
            ],
            "$comment": "dRepVotingThresholds[ppTechnicalGroup]"
        },

        "26[8]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "minValue": { "numerator": 75, "denominator": 100 },
                "$comment": "Governance parameter thresholds must be in the range 75%-90%"
            },
            {
                "maxValue": { "numerator": 90, "denominator": 100 },
                "$comment": "Governance parameter thresholds must be in the range 75%-90%"
            }
            ],
            "$comment": "dRepVotingThresholds[ppGovGroup]"
        },

        "26[9]": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 50, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            },
            {
                "maxValue": { "numerator": 100, "denominator": 100 },
                "$comment": "All thresholds must be in the range 50%-100%"
            }
            ],
            "$comment": "dRepVotingThresholds[treasuryWithdrawal]"
        }
        """
        # "dRepVotingThresholds[motionNoConfidence]"
        drep_voting_thresholds_motion_no_confidence_lower_bound = 0.51
        drep_voting_thresholds_motion_no_confidence_upper_bound = 0.75

        # "dRepVotingThresholds[committeeNormal]"
        drep_voting_thresholds_committee_normal_lower_bound = 0.65
        drep_voting_thresholds_committee_normal_upper_bound = 0.90

        # "dRepVotingThresholds[committeeNoConfidence]"
        drep_voting_thresholds_committee_no_confidence_lower_bound = 0.65
        drep_voting_thresholds_committee_no_confidence_upper_bound = 0.90

        # "dRepVotingThresholds[updateToConstitution]"
        drep_voting_thresholds_update_to_constitution_lower_bound = 0.65
        drep_voting_thresholds_update_to_constitution_upper_bound = 0.90

        # "dRepVotingThresholds[hardForkInitiation]"
        drep_voting_thresholds_hard_fork_initiation_lower_bound = 0.51
        drep_voting_thresholds_hard_fork_initiation_upper_bound = 0.80

        # "dRepVotingThresholds[ppNetworkGroup]"
        drep_voting_thresholds_pp_network_group_lower_bound = 0.51
        drep_voting_thresholds_pp_network_group_upper_bound = 0.75

        # "dRepVotingThresholds[ppEconomicGroup]"
        drep_voting_thresholds_pp_economic_group_lower_bound = 0.51
        drep_voting_thresholds_pp_economic_group_upper_bound = 0.75

        # "dRepVotingThresholds[ppTechnicalGroup]"
        drep_voting_thresholds_pp_technical_group_lower_bound = 0.51
        drep_voting_thresholds_pp_technical_group_upper_bound = 0.75

        # "dRepVotingThresholds[ppGovGroup]"
        drep_voting_thresholds_pp_gov_group_lower_bound = 0.75
        drep_voting_thresholds_pp_gov_group_upper_bound = 0.90

        # "dRepVotingThresholds[treasuryWithdrawal]"
        drep_voting_thresholds_treasury_withdrawal_lower_bound = 0.50
        drep_voting_thresholds_treasury_withdrawal_upper_bound = 1

        # "dRepVotingThresholds[motionNoConfidence]" must not be lower than 51
        invalid_lower_bound_drep_voting_thresholds_motion_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_motion_no_confidence_lower_bound)
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_motion_no_confidence,
        )

        # "dRepVotingThresholds[motionNoConfidence]" must not exceed 75
        invalid_upper_bound_drep_voting_thresholds_motion_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_motion_no_confidence_upper_bound, 1)
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_motion_no_confidence,
        )

        # "dRepVotingThresholds[committeeNormal]" must not be lower than 65
        invalid_lower_bound_drep_voting_thresholds_committee_normal = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_committee_normal_lower_bound)
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_committee_normal,
        )

        # "dRepVotingThresholds[committeeNormal]" must not exceed 90
        invalid_upper_bound_drep_voting_thresholds_committee_normal = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_committee_normal_upper_bound, 1)
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_committee_normal,
        )

        # "dRepVotingThresholds[committeeNoConfidence]" must not be lower than 65
        invalid_lower_bound_drep_voting_thresholds_committee_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_committee_no_confidence_lower_bound)
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_committee_no_confidence,
        )

        # "dRepVotingThresholds[committeeNoConfidence]" must not exceed 90
        invalid_upper_bound_drep_voting_thresholds_committee_no_confidence = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_committee_no_confidence_upper_bound, 1)
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_committee_no_confidence,
        )

        # "dRepVotingThresholds[updateToConstitution]" must not be lower than 65
        invalid_lower_bound_drep_voting_thresholds_update_to_constitution = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_update_to_constitution_lower_bound)
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_update_to_constitution,
        )

        # "dRepVotingThresholds[updateToConstitution]" must not exceed 90
        invalid_upper_bound_drep_voting_thresholds_update_to_constitution = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_update_to_constitution_upper_bound, 1)
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_update_to_constitution,
        )

        # "dRepVotingThresholds[hardForkInitiation]" must not be lower than 51
        invalid_lower_bound_drep_voting_thresholds_hard_fork_initiation = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_hard_fork_initiation_lower_bound)
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_hard_fork_initiation,
        )

        # "dRepVotingThresholds[hardForkInitiation]" must not exceed 75
        invalid_upper_bound_drep_voting_thresholds_hard_fork_initiation = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_hard_fork_initiation_upper_bound, 1)
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_hard_fork_initiation,
        )

        # "dRepVotingThresholds[ppNetworkGroup]" must not be lower than 51
        invalid_lower_bound_drep_voting_thresholds_pp_network_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_pp_network_group_lower_bound)
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_pp_network_group,
        )

        # "dRepVotingThresholds[ppNetworkGroup]" must not exceed 75
        invalid_upper_bound_drep_voting_thresholds_pp_network_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_pp_network_group_upper_bound, 1)
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_pp_network_group,
        )

        # "dRepVotingThresholds[ppEconomicGroup]" must not be lower than 51
        invalid_lower_bound_drep_voting_thresholds_pp_economic_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_pp_economic_group_lower_bound)
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_pp_economic_group,
        )

        # "dRepVotingThresholds[ppEconomicGroup]" must not exceed 75
        invalid_upper_bound_drep_voting_thresholds_pp_economic_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_pp_economic_group_upper_bound, 1)
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_pp_economic_group,
        )

        # "dRepVotingThresholds[ppTechnicalGroup]" must not be lower than 51
        invalid_lower_bound_drep_voting_thresholds_pp_technical_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_pp_technical_group_lower_bound)
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_pp_technical_group,
        )

        # "dRepVotingThresholds[ppTechnicalGroup]" must not exceed 75
        invalid_upper_bound_drep_voting_thresholds_pp_technical_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_pp_technical_group_upper_bound, 1)
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_pp_technical_group,
        )

        # "dRepVotingThresholds[ppGovGroup]" must not be lower than 75
        invalid_lower_bound_drep_voting_thresholds_pp_gov_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_pp_gov_group_lower_bound)
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_pp_gov_group,
        )

        # "dRepVotingThresholds[ppGovGroup]" must not exceed 90
        invalid_upper_bound_drep_voting_thresholds_pp_gov_group = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(drep_voting_thresholds_pp_gov_group_upper_bound, 1)
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_pp_gov_group,
        )

        # "dRepVotingThresholds[treasuryWithdrawal]" must not be lower than 50
        invalid_lower_bound_drep_voting_thresholds_treasury_withdrawal = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(0, drep_voting_thresholds_treasury_withdrawal_lower_bound)
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_voting_thresholds_treasury_withdrawal,
        )

        # "dRepVotingThresholds[treasuryWithdrawal]" must not exceed 100
        invalid_upper_bound_drep_voting_thresholds_treasury_withdrawal = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound + 100,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_voting_thresholds_treasury_withdrawal,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-motion-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_motion_no_confidence_lower_bound,
                        drep_voting_thresholds_motion_no_confidence_upper_bound,
                    )
                ),
                name="motionNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-normal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_normal_lower_bound,
                        drep_voting_thresholds_committee_normal_upper_bound,
                    )
                ),
                name="committeeNormal",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-committee-no-confidence",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_committee_no_confidence_lower_bound,
                        drep_voting_thresholds_committee_no_confidence_upper_bound,
                    )
                ),
                name="committeeNoConfidence",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-update-to-constitution",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_update_to_constitution_lower_bound,
                        drep_voting_thresholds_update_to_constitution_upper_bound,
                    )
                ),
                name="updateToConstitution",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-hard-fork-initiation",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_hard_fork_initiation_lower_bound,
                        drep_voting_thresholds_hard_fork_initiation_upper_bound,
                    )
                ),
                name="hardForkInitiation",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-network-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_network_group_lower_bound,
                        drep_voting_thresholds_pp_network_group_upper_bound,
                    )
                ),
                name="ppNetworkGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-economic-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_economic_group_lower_bound,
                        drep_voting_thresholds_pp_economic_group_upper_bound,
                    )
                ),
                name="ppEconomicGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-technical-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_technical_group_lower_bound,
                        drep_voting_thresholds_pp_technical_group_upper_bound,
                    )
                ),
                name="ppTechnicalGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-pp-governance-group",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_pp_gov_group_lower_bound,
                        drep_voting_thresholds_pp_gov_group_upper_bound,
                    )
                ),
                name="ppGovGroup",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-voting-threshold-treasury-withdrawal",
                value=_get_rational_str(
                    random.uniform(
                        drep_voting_thresholds_treasury_withdrawal_lower_bound,
                        drep_voting_thresholds_treasury_withdrawal_upper_bound,
                    )
                ),
                name="treasuryWithdrawal",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_committee_min_size(
        self,
        const_script_record: ConstScriptRecord,
    ):
        """Test setting of `committeeMinSize`.

        "27": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 0,
                "$comment": "committeeMinSize must not be negative"
            },
            {
                "minValue": 3,
                "$comment": "committeeMinSize must not be lower than 3"
            },
            {
                "maxValue": 10,
                "$comment": "committeeMinSize must not exceed than 10"
            }
            ],
            "$comment": "committeeMinSize"
        },
        """
        committee_min_size_lower_bound = 3
        committee_min_size_upper_bound = 10

        # "committeeMinSize" must not be negative
        invalid_lower_bound_committee_min_size = [
            clusterlib_utils.UpdateProposal(
                arg="--min-committee-size",
                value=random.randint(-10, -1),
                name="committeeMinSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_committee_min_size,
        )

        # "committeeMinSize" must not be lower than 3
        invalid_lower_bound_committee_min_size = [
            clusterlib_utils.UpdateProposal(
                arg="--min-committee-size",
                value=random.randint(0, committee_min_size_lower_bound - 1),
                name="committeeMinSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_committee_min_size,
        )

        # "committeeMinSize" must not exceed than 10
        invalid_upper_bound_committee_min_size = [
            clusterlib_utils.UpdateProposal(
                arg="--min-committee-size",
                value=random.randint(committee_min_size_upper_bound, 65535),
                name="committeeMinSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_committee_min_size,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--min-committee-size",
                value=random.randint(
                    committee_min_size_lower_bound, committee_min_size_upper_bound
                ),
                name="committeeMinSize",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_committee_max_term_limit(self, const_script_record: ConstScriptRecord):
        """Test setting of `committeeMaxTermLimit`.

        "28": {
            "type": "integer",
            "predicates": [
            {
                "notEqual": 0,
                "$comment": "committeeMaxTermLimit must not be zero"
            },
            {
                "minValue": 0,
                "$comment": "committeeMaxTermLimit must not negative"
            },
            {
                "minValue": 18,
                "$comment": "committeeMaxTermLimit must not be less than 18 epochs (90 days, or approximately 3 months)"
            },
            {
                "maxValue": 293,
                "$comment": "committeeMaxTermLimit must not be more than 293 epochs (approximately 4 years)"
            }
            ],
            "$comment": "committeeMaxTermLimit"
        },
        """
        committee_max_term_limit_lower_bound = 18
        committee_max_term_limit_upper_bound = 293

        # "committeeMaxTermLimit" must not be zero
        invalid_zero_committee_max_term_limit = [
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value="0",
                name="committeeMaxTermLimit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_zero_committee_max_term_limit,
        )

        # "committeeMaxTermLimit" must not be negative
        invalid_lower_bound_committee_max_term_limit = [
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value=random.randint(-10, -1),
                name="committeeMaxTermLimit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_committee_max_term_limit,
        )

        # "committeeMaxTermLimit" must not be less than 18 epochs (90 days)
        invalid_lower_bound_committee_max_term_limit = [
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value=random.randint(0, committee_max_term_limit_lower_bound - 1),
                name="committeeMaxTermLimit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_committee_max_term_limit,
        )

        # "committeeMaxTermLimit" must not be more than 293 epochs (approximately 4 years)
        invalid_upper_bound_committee_max_term_limit = [
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value=random.randint(committee_max_term_limit_upper_bound, 65535),
                name="committeeMaxTermLimit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_committee_max_term_limit,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value=random.randint(
                    committee_max_term_limit_lower_bound, committee_max_term_limit_upper_bound
                ),
                name="committeeMaxTermLimit",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_gov_action_lifetime(self, const_script_record: ConstScriptRecord):
        """Test setting of `govActionLifetime`.

        "29": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 1,
                "$comment": "govActionLifetime must not be lower than 1 epoch (5 days)"
            },
            {
                "maxValue": 15,
                "$comment": "govActionLifetime must not exceed than 15 epochs (75 days)"
            }
            ],
            "$comment": "govActionLifetime"
        },
        """
        gov_action_lifetime_lower_bound = 1
        gov_action_lifetime_upper_bound = 15

        # "govActionLifetime" must not be negative
        # Note: For negative values CLI guards against it without reaching plutus script
        invalid_lower_bound_gov_action_lifetime = [
            clusterlib_utils.UpdateProposal(
                arg="--governance-action-lifetime",
                value=random.randint(-10, -1),
                name="govActionLifetime",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_gov_action_lifetime,
        )

        # "govActionLifetime" must not be lower than 1 epoch (5 days)
        invalid_lower_bound_gov_action_lifetime = [
            clusterlib_utils.UpdateProposal(
                arg="--governance-action-lifetime",
                value=random.randint(0, gov_action_lifetime_lower_bound - 1),
                name="govActionLifetime",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_gov_action_lifetime,
        )

        # "govActionLifetime" must not exceed than 15 epochs (75 days)
        invalid_upper_bound_gov_action_lifetime = [
            clusterlib_utils.UpdateProposal(
                arg="--governance-action-lifetime",
                value=random.randint(gov_action_lifetime_upper_bound, 65535),
                name="govActionLifetime",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_gov_action_lifetime,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--governance-action-lifetime",
                value=random.randint(
                    gov_action_lifetime_lower_bound, gov_action_lifetime_upper_bound
                ),
                name="govActionLifetime",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_max_tx_size(self, const_script_record: ConstScriptRecord):
        """Test setting of `maxTxSize`.

        "3": {
            "type": "integer",
            "predicates": [
            {
                "maxValue": 32768,
                "$comment": "maxTxSize must not exceed 32,768 Bytes (32KB)"
            },
            {
                "minValue": 0,
                "$comment": "maxTxSize must not be negative"
            }
            ],
            "$comment": "maxTxSize"
        },
        """
        max_tx_size_upper_bound = 32768

        # "maxTxSize" must not be negative
        invalid_lower_bound_max_tx_size = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=random.randint(-10, -1),
                name="maxTxSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_max_tx_size,
        )

        # "maxTxSize" must not exceed 32,768 Bytes (32KB)
        invalid_upper_bound_max_tx_size = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=random.randint(max_tx_size_upper_bound + 1, 2**32),
                name="maxTxSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_max_tx_size,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=random.randint(0, max_tx_size_upper_bound),
                name="maxTxSize",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_gov_deposit(self, const_script_record: ConstScriptRecord):
        """Test setting of `govDeposit`.

        "30": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 0,
                "$comment": "govDeposit must not be negative"
            },
            {
                "minValue": 1000000,
                "$comment": "govDeposit must not be lower than 1,000,000 (1 ada)"
            },
            {
                "maxValue": 10000000000000,
                "$comment": "govDeposit must not exceed 10,000,000,000,000 (10 Million ada)"
            }
            ],
            "$comment": "govDeposit"
        },
        """
        gov_deposit_lower_bound = 1000000
        gov_deposit_upper_bound = 10000000000000

        # "govDeposit" must not be negative
        invalid_lower_bound_gov_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--new-governance-action-deposit",
                value=random.randint(-10, -1),
                name="govDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_gov_deposit,
        )

        # "govDeposit" must not be lower than 1,000,000 (1 ada)
        invalid_lower_bound_gov_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--new-governance-action-deposit",
                value=random.randint(0, gov_deposit_lower_bound - 1),
                name="govDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_gov_deposit,
        )

        # "govDeposit" must not exceed 10,000,000,000,000 (10 Million ada)
        invalid_upper_bound_gov_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--new-governance-action-deposit",
                value=random.randint(gov_deposit_upper_bound + 1, 2**63),
                name="govDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_gov_deposit,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--new-governance-action-deposit",
                value=random.randint(gov_deposit_lower_bound, gov_deposit_upper_bound),
                name="govDeposit",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_drep_deposit(self, const_script_record: ConstScriptRecord):
        """Test setting of `dRepDeposit`.

        "31": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 0,
                "$comment": "dRepDeposit must not be negative"
            },
            {
                "minValue": 1000000,
                "$comment": "dRepDeposit must not be lower than 1,000,000 (1 ada)"
            },
            {
                "maxValue": 100000000000,
                "$comment": "dRepDeposit must not exceed 100,000,000,000 (100,000 ada)"
            }
            ],
            "$comment": "dRepDeposit"
        },
        """
        drep_deposit_lower_bound = 1000000
        drep_deposit_upper_bound = 100000000000

        # "dRepDeposit" must not be negative
        invalid_lower_bound_drep_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-deposit",
                value=random.randint(-10, -1),
                name="dRepDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_deposit,
        )

        # "dRepDeposit" must not be lower than 1,000,000 (1 ada)
        invalid_lower_bound_drep_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-deposit",
                value=random.randint(0, drep_deposit_lower_bound - 1),
                name="dRepDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_deposit,
        )

        # "dRepDeposit" must not exceed 100,000,000,000 (100,000 ada)
        invalid_upper_bound_drep_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-deposit",
                value=random.randint(drep_deposit_upper_bound + 1, 2**63),
                name="dRepDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_deposit,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-deposit",
                value=random.randint(drep_deposit_lower_bound, drep_deposit_upper_bound),
                name="dRepDeposit",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_drep_activity(self, const_script_record: ConstScriptRecord):
        """Test setting of `dRepActivity`.

        "32": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 13,
                "$comment": "dRepActivity must not be lower than 13 epochs (2 months)"
            },
            {
                "maxValue": 37,
                "$comment": "dRepActivity must not exceed 37 epochs (6 months)"
            },
            {
                "minValue": 0,
                "$comment": "dRepActivity must not be negative"
            }
            ],
            "$comment": "dRepActivity"
        },
        """
        drep_activity_lower_bound = 13
        drep_activity_upper_bound = 37

        # "dRepActivity" must not be negative
        invalid_lower_bound_drep_activity = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-activity",
                value=random.randint(-10, -1),
                name="dRepActivity",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_activity,
        )

        # "dRepActivity" must not be lower than 13 epochs (2 months)
        invalid_lower_bound_drep_activity = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-activity",
                value=random.randint(0, drep_activity_lower_bound - 1),
                name="dRepActivity",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_drep_activity,
        )

        # "dRepActivity" must not exceed 37 epochs (6 months)
        invalid_upper_bound_drep_activity = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-activity",
                value=random.randint(drep_activity_upper_bound + 1, 65535),
                name="dRepActivity",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_drep_activity,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--drep-activity",
                value=random.randint(drep_activity_lower_bound, drep_activity_upper_bound),
                name="dRepActivity",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_min_fee_ref_script_coins_per_byte(
        self, const_script_record: ConstScriptRecord
    ):
        """Test setting of `minFeeRefScriptCoinsPerByte`.

        "33": {
            "type": "unit_interval",
            "predicates": [
            {
                "maxValue": { "numerator": 1000, "denominator": 1 },
                "$comment": "minFeeRefScriptCoinsPerByte must not exceed 1,000 (0.001 ada)"
            },
            {
                "minValue": { "numerator": 0, "denominator": 1 },
                "$comment": "minFeeRefScriptCoinsPerByte must not be negative"
            }
            ],
            "$comment": "minFeeRefScriptCoinsPerByte"
        },
        """
        min_fee_ref_script_coins_per_byte_upper_bound = 1000

        # "minFeeRefScriptCoinsPerByte" must not be negative
        # Note: CLI guards against negative values without reaching plutus script
        invalid_lower_bound_min_fee_ref_script_coins_per_byte = [
            clusterlib_utils.UpdateProposal(
                arg="--ref-script-cost-per-byte",
                value=_get_rational_str(random.uniform(-10, -1)),
                name="minFeeRefScriptCoinsPerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_min_fee_ref_script_coins_per_byte,
        )

        # "minFeeRefScriptCoinsPerByte" must not exceed 1,000 (0.001 ada)
        invalid_upper_bound_min_fee_ref_script_coins_per_byte = [
            clusterlib_utils.UpdateProposal(
                arg="--ref-script-cost-per-byte",
                value=_get_rational_str(
                    random.uniform(min_fee_ref_script_coins_per_byte_upper_bound, 2**32)
                ),
                name="minFeeRefScriptCoinsPerByte",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_min_fee_ref_script_coins_per_byte,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--ref-script-cost-per-byte",
                value=_get_rational_str(
                    random.uniform(0, min_fee_ref_script_coins_per_byte_upper_bound)
                ),
                name="minFeeRefScriptCoinsPerByte",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_max_block_header_size(self, const_script_record: ConstScriptRecord):
        """Test setting of `maxBlockHeaderSize`.

        "4": {
            "type": "integer",
            "predicates": [
            {
                "maxValue": 5000,
                "$comment": "maxBlockHeaderSize must be set below 5000"
            },
            {
                "minValue": 0,
                "$comment": "maxBlockHeaderSize must not be negative"
            }
            ],
            "$comment": "maxBlockHeaderSize"
        },
        """
        max_block_header_size_upper_bound = 5000

        # "maxBlockHeaderSize" must not be negative
        invalid_lower_bound_max_block_header_size = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=random.randint(-10, -1),
                name="maxBlockHeaderSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_max_block_header_size,
        )

        # "maxBlockHeaderSize" must be set below 5000
        invalid_lower_bound_max_block_header_size = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=random.randint(max_block_header_size_upper_bound, 2**32),
                name="maxBlockHeaderSize",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_max_block_header_size,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=random.randint(0, max_block_header_size_upper_bound),
                name="maxBlockHeaderSize",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_stake_address_deposit(self, const_script_record: ConstScriptRecord):
        """Test setting of `stakeAddressDeposit`.

        "5": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 1000000,
                "$comment": "stakeAddressDeposit must not be lower than 1,000,000 (1 ada)"
            },
            {
                "maxValue": 5000000,
                "$comment": "stakeAddressDeposit must not exceed 5,000,000 (5 ada)"
            },
            {
                "minValue": 0,
                "$comment": "stakeAddressDeposit must not be negative"
            }
            ],
            "$comment": "stakeAddressDeposit"
        },
        """
        stake_address_deposit_lower_bound = 1000000
        stake_address_deposit_upper_bound = 5000000

        # "stakeAddressDeposit" must not be negative
        invalid_lower_bound_stake_address_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=random.randint(-10, -1),
                name="stakeAddressDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_stake_address_deposit,
        )

        # "stakeAddressDeposit" must not be lower than 1,000,000 (1 ada)
        invalid_lower_bound_stake_address_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=random.randint(0, stake_address_deposit_lower_bound - 1),
                name="stakeAddressDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_stake_address_deposit,
        )

        # "stakeAddressDeposit" must not exceed 5,000,000 (5 ada)
        invalid_upper_bound_stake_address_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=random.randint(stake_address_deposit_upper_bound + 1, 2**32),
                name="stakeAddressDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_stake_address_deposit,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=random.randint(
                    stake_address_deposit_lower_bound, stake_address_deposit_upper_bound
                ),
                name="stakeAddressDeposit",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_stake_pool_deposit(self, const_script_record: ConstScriptRecord):
        """Test setting of `stakePoolDeposit`.

        "6": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 250000000,
                "$comment": "stakePoolDeposit must not be lower than 250,000,000 (250 ada)"
            },
            {
                "maxValue": 500000000,
                "$comment": "stakePoolDeposit must not exceed 500,000,000 (500 ada)"
            },
            {
                "minValue": 0,
                "$comment": "stakePoolDeposit must not be negative"
            }
            ],
            "$comment": "stakePoolDeposit"
        },
        """
        stake_pool_deposit_lower_bound = 250000000
        stake_pool_deposit_upper_bound = 500000000

        # "stakePoolDeposit" must not be negative
        invalid_lower_bound_stake_pool_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=random.randint(-10, -1),
                name="stakePoolDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_stake_pool_deposit,
        )

        # "stakePoolDeposit" must not be lower than 250,000,000 (250 ada)
        invalid_lower_bound_stake_pool_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=random.randint(0, stake_pool_deposit_lower_bound - 1),
                name="stakePoolDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_stake_pool_deposit,
        )

        # "stakePoolDeposit" must not exceed 500,000,000 (500 ada)
        invalid_upper_bound_stake_pool_deposit = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=random.randint(stake_pool_deposit_upper_bound + 1, 2**63),
                name="stakePoolDeposit",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_stake_pool_deposit,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=random.randint(
                    stake_pool_deposit_lower_bound, stake_pool_deposit_upper_bound
                ),
                name="stakePoolDeposit",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_pool_retire_max_epoch(self, const_script_record: ConstScriptRecord):
        """Test setting of `poolRetireMaxEpoch`.

        "7": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 0,
                "$comment": "poolRetireMaxEpoch must not be negative"
            }
            ],
            "$comment": "poolRetireMaxEpoch"
        },
        """
        # "poolRetireMaxEpoch" must not be negative
        invalid_lower_bound_pool_retire_max_epoch = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-retirement-epoch-interval",
                value=random.randint(-10, -1),
                name="poolRetireMaxEpoch",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_retire_max_epoch,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-retirement-epoch-interval",
                value=random.randint(0, 2**32),
                name="poolRetireMaxEpoch",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_stake_pool_target_num(self, const_script_record: ConstScriptRecord):
        """Test setting of `stakePoolTargetNum`.

        "8": {
            "type": "integer",
            "predicates": [
            {
                "minValue": 250,
                "$comment": "stakePoolTargetNum must not be lower than 250"
            },
            {
                "maxValue": 2000,
                "$comment": "stakePoolTargetNum must not be set above 2,000"
            },
            {
                "minValue": 0,
                "$comment": "stakePoolTargetNum must not be negative"
            },
            {
                "notEqual": 0,
                "$comment": "stakePoolTargetNum must not be zero"
            }
            ],
            "$comment": "stakePoolTargetNum"
        },
        """
        stake_pool_target_num_lower_bound = 250
        stake_pool_target_num_upper_bound = 2000

        # "stakePoolTargetNum" must not be negative
        invalid_lower_bound_stake_pool_target_num = [
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=random.randint(-10, -1),
                name="stakePoolTargetNum",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_stake_pool_target_num,
        )

        # "stakePoolTargetNum" must not be lower than 250
        invalid_lower_bound_stake_pool_target_num = [
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=random.randint(0, stake_pool_target_num_lower_bound - 1),
                name="stakePoolTargetNum",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_stake_pool_target_num,
        )

        # "stakePoolTargetNum" must not be set above 2,000
        invalid_upper_bound_stake_pool_target_num = [
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=random.randint(stake_pool_target_num_upper_bound + 1, 65535),
                name="stakePoolTargetNum",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_stake_pool_target_num,
        )

        # "stakePoolTargetNum" must not be zero
        invalid_zero_stake_pool_target_num = [
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=0,
                name="stakePoolTargetNum",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_zero_stake_pool_target_num,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=random.randint(
                    stake_pool_target_num_lower_bound, stake_pool_target_num_upper_bound
                ),
                name="stakePoolTargetNum",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_pool_pledge_influence(self, const_script_record: ConstScriptRecord):
        """Test setting of `poolPledgeInfluence`.

        "9": {
            "type": "unit_interval",
            "predicates": [
            {
                "minValue": { "numerator": 1, "denominator": 10 },
                "$comment": "poolPledgeInfluence must not be set below 0.1"
            },
            {
                "maxValue": { "numerator": 10, "denominator": 10 },
                "$comment": "poolPledgeInfluence must not exceed 1.0"
            },
            {
                "minValue": { "numerator": 0, "denominator": 10 },
                "$comment": "poolPledgeInfluence must not be negative"
            }
            ],
            "$comment": "poolPledgeInfluence"
        },
        """
        pool_pledge_influence_lower_bound = 1 / 10
        pool_pledge_influence_upper_bound = 1

        # "poolPledgeInfluence" must not be negative
        invalid_lower_bound_pool_pledge_influence = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=_get_rational_str(random.uniform(-10, 0)),
                name="poolPledgeInfluence",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_pledge_influence,
        )

        # "poolPledgeInfluence" must not be set below 0.1
        invalid_lower_bound_pool_pledge_influence = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=_get_rational_str(random.uniform(0, pool_pledge_influence_lower_bound)),
                name="poolPledgeInfluence",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_lower_bound_pool_pledge_influence,
        )

        # "poolPledgeInfluence" must not exceed 1.0
        invalid_upper_bound_pool_pledge_influence = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=_get_rational_str(random.uniform(pool_pledge_influence_upper_bound, 10)),
                name="poolPledgeInfluence",
            ),
        ]
        check_invalid_proposals(
            const_script_record=const_script_record,
            proposals=invalid_upper_bound_pool_pledge_influence,
        )

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=_get_rational_str(
                    random.uniform(
                        pool_pledge_influence_lower_bound, pool_pledge_influence_upper_bound
                    )
                ),
                name="poolPledgeInfluence",
            ),
        ]

        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)

    @allure.link(helpers.get_vcs_link())
    def test_guardrail_cost_models(self, const_script_record: ConstScriptRecord):
        """Test setting of cost models.

        "18": {
            "type": "any",
            "$comment": "costmodels for all plutus versions"
            }
        }
        """
        cost_proposal_file = DATA_DIR / "cost_models_list_v3.json"

        # Valid proposals
        valid_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--cost-model-file",
                value=str(cost_proposal_file),
                name="costModels",
            ),
        ]
        check_valid_proposals(const_script_record=const_script_record, proposals=valid_proposals)
