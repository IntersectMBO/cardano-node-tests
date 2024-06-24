"""Tests for Conway governance protocol parameters update."""

# pylint: disable=expression-not-assigned
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
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


NETWORK_GROUP_PPARAMS = {
    "maxBlockBodySize",
    "maxTxSize",
    "maxBlockHeaderSize",
    "maxValueSize",
    "maxTxExecutionUnits",
    "maxBlockExecutionUnits",
    "maxCollateralInputs",
}

ECONOMIC_GROUP_PPARAMS = {
    "txFeePerByte",
    "txFeeFixed",
    "stakeAddressDeposit",
    "stakePoolDeposit",
    "monetaryExpansion",
    "treasuryCut",
    "minPoolCost",
    "utxoCostPerByte",
    "executionUnitPrices",
}

TECHNICAL_GROUP_PPARAMS = {
    "poolPledgeInfluence",
    "poolRetireMaxEpoch",
    "stakePoolTargetNum",
    "costModels",
    "collateralPercentage",
}

GOVERNANCE_GROUP_PPARAMS = {
    "govActionLifetime",
    "govActionDeposit",
    "dRepDeposit",
    "dRepActivity",
    "committeeMinSize",
    "committeeMaxTermLength",
}

GOVERNANCE_GROUP_PPARAMS_DREP_THRESHOLDS = {
    "committeeNoConfidence",
    "committeeNormal",
    "hardForkInitiation",
    "motionNoConfidence",
    "ppEconomicGroup",
    "ppGovGroup",
    "ppNetworkGroup",
    "ppTechnicalGroup",
    "treasuryWithdrawal",
    "updateToConstitution",
}

GOVERNANCE_GROUP_PPARAMS_POOL_THRESHOLDS = {
    "committeeNoConfidence",
    "committeeNormal",
    "hardForkInitiation",
    "motionNoConfidence",
    "ppSecurityGroup",
}

# Security related pparams that require also SPO approval
SECURITY_PPARAMS = {
    "maxBlockBodySize",
    "maxTxSize",
    "maxBlockHeaderSize",
    "maxValueSize",
    "maxBlockExecutionUnits",
    "txFeePerByte",
    "txFeeFixed",
    "utxoCostPerByte",
    "govActionDeposit",
    "minFeeRefScriptsCoinsPerByte",  # not in 8.8 release yet
}


@pytest.fixture
def pool_user_lg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_governance: governance_setup.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_governance
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return conway_common.get_registered_pool_user(
        cluster_manager=cluster_manager,
        name_template=name_template,
        cluster_obj=cluster,
        caching_key=key,
        fund_amount=2000_000_000,
    )


def _get_rational_str(value: float) -> str:
    return str(fractions.Fraction(value).limit_denominator())


def _check_w_denominator(
    update_proposal: clusterlib_utils.UpdateProposal, pparam: tp.Union[float, dict]
) -> bool:
    exp_val: tp.Union[float, dict, str] = pparam
    if isinstance(pparam, dict):
        exp_val = f"{pparam['numerator']}/{pparam['denominator']}"
    return bool(update_proposal.value == exp_val)


def _check_max_tx_execution_units(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    pparam = protocol_params["maxTxExecutionUnits"]
    exp_val = f"({pparam['steps']},{pparam['memory']})"
    return bool(update_proposal.value == exp_val)


def _check_max_block_execution_units(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    pparam = protocol_params["maxBlockExecutionUnits"]
    exp_val = f"({pparam['steps']},{pparam['memory']})"
    return bool(update_proposal.value == exp_val)


def _check_execution_unit_prices_mem(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    return _check_w_denominator(
        update_proposal=update_proposal,
        pparam=protocol_params["executionUnitPrices"]["priceMemory"],
    )


def _check_execution_unit_prices_steps(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    return _check_w_denominator(
        update_proposal=update_proposal, pparam=protocol_params["executionUnitPrices"]["priceSteps"]
    )


def _check_monetary_expansion(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    return _check_w_denominator(
        update_proposal=update_proposal, pparam=protocol_params["monetaryExpansion"]
    )


def _check_treasury_expansion(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    return _check_w_denominator(
        update_proposal=update_proposal, pparam=protocol_params["treasuryCut"]
    )


def _check_pool_pledge_influence(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    return _check_w_denominator(
        update_proposal=update_proposal, pparam=protocol_params["poolPledgeInfluence"]
    )


def _check_pool_thresholds(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    return _check_w_denominator(
        update_proposal=update_proposal,
        pparam=protocol_params["poolVotingThresholds"][update_proposal.name],
    )


def _check_drep_thresholds(
    update_proposal: clusterlib_utils.UpdateProposal, protocol_params: dict
) -> bool:
    return _check_w_denominator(
        update_proposal=update_proposal,
        pparam=protocol_params["dRepVotingThresholds"][update_proposal.name],
    )


class TestPParamUpdate:
    """Tests for protocol parameters update."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_pparam_update(  # noqa: C901
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test enactment of protocol parameter update.

        * submit multiple "protocol parameters update" action

            - one action for each parameter group
            - one action with multiple proposals from different groups

        * vote to disapprove the actions
        * submit a "protocol parameters update" action that will be enacted
        * check that SPOs cannot vote on a "protocol parameters update" action that doesn't
          change security parameters
        * vote to approve the action
        * check that the action is ratified
        * try to disapprove the ratified action, this shouldn't have any effect
        * check that the action is enacted
        * check that only the ratified action that got accepted first to the chain gets enacted
        * check that it's not possible to vote on enacted action
        """
        # pylint: disable=too-many-locals,too-many-statements
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)
        cost_proposal_file = DATA_DIR / "cost_models_list.json"
        is_in_bootstrap = conway_common.is_in_bootstrap(cluster_obj=cluster)

        if is_in_bootstrap and not configuration.HAS_CC:
            pytest.skip("The test doesn't work in bootstrap period without CC.")

        # Check if total delegated stake is below the threshold. This can be used to check that
        # undelegated stake is treated as Abstain. If undelegated stake was treated as Yes, than
        # missing votes would approve the action.
        delegated_stake = governance_utils.get_delegated_stake(cluster_obj=cluster)
        cur_pparams = cluster.g_conway_governance.query.gov_state()["currentPParams"]
        drep_constitution_threshold = cur_pparams["dRepVotingThresholds"]["ppGovGroup"]
        spo_constitution_threshold = cur_pparams["poolVotingThresholds"]["ppSecurityGroup"]
        is_drep_total_below_threshold = (
            delegated_stake.drep / delegated_stake.total_lovelace
        ) < drep_constitution_threshold
        is_spo_total_below_threshold = (
            delegated_stake.spo / delegated_stake.total_lovelace
        ) < spo_constitution_threshold

        # PParam groups

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cip049, reqc.cip050, reqc.cip051, reqc.cip052)]

        network_g_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-body-size",
                value=65544,
                name="maxBlockBodySize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=16392,
                name="maxTxSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=random.randint(1101, 1200),
                name="maxBlockHeaderSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-value-size",
                value=random.randint(5001, 5100),
                name="maxValueSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-execution-units",
                value=f"({random.randint(14000001, 14000100)},"
                f"{random.randint(10000000001, 10000000100)})",
                name="maxTxExecutionUnits",
                check_func=_check_max_tx_execution_units,
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(62000001, 62000100)},"
                f"{random.randint(40000000001, 40000000100)})",
                name="maxBlockExecutionUnits",
                check_func=_check_max_block_execution_units,
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-collateral-inputs",
                value=random.randint(3, 10),
                name="maxCollateralInputs",
            ),
        ]

        economic_g_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=44,
                name="txFeePerByte",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=155381,
                name="txFeeFixed",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--key-reg-deposit-amt",
                value=random.randint(400001, 400100),
                name="stakeAddressDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-reg-deposit",
                value=random.randint(500000001, 500000100),
                name="stakePoolDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--monetary-expansion",
                value=_get_rational_str(random.uniform(0.0023, 0.0122)),
                name="monetaryExpansion",
                check_func=_check_monetary_expansion,
            ),
            clusterlib_utils.UpdateProposal(
                arg="--treasury-expansion",
                value=_get_rational_str(random.uniform(0.051, 0.1)),
                name="treasuryCut",
                check_func=_check_treasury_expansion,
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=random.randint(0, 10),
                name="minPoolCost",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=random.randint(4311, 4400),
                name="utxoCostPerByte",
            ),
            # These must be passed together
            [
                clusterlib_utils.UpdateProposal(
                    arg="--price-execution-steps",
                    value=_get_rational_str(random.uniform(0.0578, 0.0677)),
                    name="executionUnitPrices (steps)",
                    check_func=_check_execution_unit_prices_steps,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--price-execution-memory",
                    value=_get_rational_str(random.uniform(0.00008, 0.00009)),
                    name="executionUnitPrices (memory)",
                    check_func=_check_execution_unit_prices_mem,
                ),
            ],
        ]

        technical_g_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--pool-influence",
                value=_get_rational_str(random.uniform(0.1, 0.5)),
                name="poolPledgeInfluence",
                check_func=_check_pool_pledge_influence,
            ),
            clusterlib_utils.UpdateProposal(
                arg="--pool-retirement-epoch-interval",
                value=random.randint(19, 30),
                name="poolRetireMaxEpoch",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--number-of-pools",
                value=random.randint(500, 65535),
                name="stakePoolTargetNum",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--cost-model-file",
                value=str(cost_proposal_file),
                name="",  # costModels
            ),
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=random.randint(151, 160),
                name="collateralPercentage",
            ),
        ]

        governance_g_proposals = [
            # These must be passed together
            [
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-committee-no-confidence",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="committeeNoConfidence",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-committee-normal",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="committeeNormal",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-hard-fork-initiation",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="hardForkInitiation",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-motion-no-confidence",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="motionNoConfidence",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-pp-economic-group",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="ppEconomicGroup",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-pp-governance-group",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="ppGovGroup",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-pp-network-group",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="ppNetworkGroup",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-pp-technical-group",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="ppTechnicalGroup",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-treasury-withdrawal",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="treasuryWithdrawal",
                    check_func=_check_drep_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--drep-voting-threshold-update-to-constitution",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="updateToConstitution",
                    check_func=_check_drep_thresholds,
                ),
            ],
            # These must be passed together
            [
                clusterlib_utils.UpdateProposal(
                    arg="--pool-voting-threshold-committee-no-confidence",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="committeeNoConfidence",
                    check_func=_check_pool_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--pool-voting-threshold-committee-normal",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="committeeNormal",
                    check_func=_check_pool_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--pool-voting-threshold-hard-fork-initiation",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="hardForkInitiation",
                    check_func=_check_pool_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--pool-voting-threshold-motion-no-confidence",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="motionNoConfidence",
                    check_func=_check_pool_thresholds,
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--pool-voting-threshold-pp-security-group",
                    value=_get_rational_str(random.uniform(0.52, 0.60)),
                    name="ppSecurityGroup",
                    check_func=_check_pool_thresholds,
                ),
            ],
            clusterlib_utils.UpdateProposal(
                arg="--governance-action-lifetime",
                value=random.randint(3, 10),
                name="govActionLifetime",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--new-governance-action-deposit",
                value=random.randint(100000001, 100000100),
                name="govActionDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-deposit",
                value=random.randint(2000001, 2000100),
                name="dRepDeposit",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-activity",
                value=random.randint(101, 200),
                name="dRepActivity",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-committee-size",
                value=1,
                name="committeeMinSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value=random.randint(11001, 12000),
                name="committeeMaxTermLength",
            ),
        ]

        security_proposals = [
            clusterlib_utils.UpdateProposal(
                arg="--max-block-body-size",
                value=65544,
                name="maxBlockBodySize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=16392,
                name="maxTxSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-header-size",
                value=random.randint(1101, 1200),
                name="maxBlockHeaderSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-value-size",
                value=random.randint(5001, 5100),
                name="maxValueSize",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--max-block-execution-units",
                value=f"({random.randint(62000001, 62000100)},"
                f"{random.randint(40000000001, 40000000100)})",
                name="maxBlockExecutionUnits",
                check_func=_check_max_block_execution_units,
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-linear",
                value=44,
                name="txFeePerByte",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--min-fee-constant",
                value=155381,
                name="txFeeFixed",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--utxo-cost-per-byte",
                value=random.randint(4311, 4400),
                name="utxoCostPerByte",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--new-governance-action-deposit",
                value=random.randint(100000001, 100000100),
                name="govActionDeposit",
            ),
        ]

        # Hand-picked parameters and values that can stay changed even for other tests
        cur_pparams = cluster.g_conway_governance.query.gov_state()["currentPParams"]
        fin_update_proposals = [
            # From network group
            clusterlib_utils.UpdateProposal(
                arg="--max-collateral-inputs",
                value=cur_pparams["maxCollateralInputs"],
                name="maxCollateralInputs",
            ),
            # From economic group
            clusterlib_utils.UpdateProposal(
                arg="--min-pool-cost",
                value=cur_pparams["minPoolCost"],
                name="minPoolCost",
            ),
            # From technical group
            clusterlib_utils.UpdateProposal(
                arg="--collateral-percent",
                value=cur_pparams["collateralPercentage"],
                name="collateralPercentage",
            ),
            # From governance group
            clusterlib_utils.UpdateProposal(
                arg="--committee-term-length",
                value=random.randint(11000, 12000),
                name="committeeMaxTermLength",
            ),
            clusterlib_utils.UpdateProposal(
                arg="--drep-activity",
                value=random.randint(101, 120),
                name="dRepActivity",
            ),
            # From security pparams
            clusterlib_utils.UpdateProposal(
                arg="--max-tx-size",
                value=cur_pparams["maxTxSize"],
                name="maxTxSize",
            ),
        ]
        if configuration.HAS_CC:
            fin_update_proposals.append(
                clusterlib_utils.UpdateProposal(
                    arg="--min-committee-size",
                    value=random.randint(3, 5),
                    name="committeeMinSize",
                )
            )

        # Intentionally use the same previous action for all proposals
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )

        def _propose_pparams_update(
            name_template: str,
            proposals: tp.List[clusterlib_utils.UpdateProposal],
        ) -> conway_common.PParamPropRec:
            anchor_url = f"http://www.pparam-action-{clusterlib.get_rand_str(4)}.com"
            anchor_data_hash = cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url)

            return conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=name_template,
                anchor_url=anchor_url,
                anchor_data_hash=anchor_data_hash,
                pool_user=pool_user_lg,
                proposals=proposals,
                prev_action_rec=prev_action_rec,
            )

        proposed_pparams_errors = []

        def _check_proposed_pparams(
            update_proposals: tp.List[clusterlib_utils.UpdateProposal], protocol_params: dict
        ) -> None:
            try:
                clusterlib_utils.check_updated_params(
                    update_proposals=update_proposals,
                    protocol_params=protocol_params,
                )
            except AssertionError as err:
                proposed_pparams_errors.append(str(err))

        _url = helpers.get_vcs_link()
        [
            r.start(url=_url)
            for r in (reqc.cip044, reqc.cip045, reqc.cip046, reqc.cip047, reqc.cip060)
        ]

        # Vote on update proposals from network group that will NOT get approved by DReps
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli017, reqc.cip031a_05, reqc.cip031e, reqc.cip054_01)]
        if configuration.HAS_CC:
            reqc.cip006.start(url=_url)
        net_nodrep_prop_rec = _propose_pparams_update(
            name_template=f"{temp_template}_net_nodrep", proposals=network_g_proposals
        )
        [r.success() for r in (reqc.cli017, reqc.cip031a_05, reqc.cip031e, reqc.cip054_01)]

        _check_proposed_pparams(
            update_proposals=net_nodrep_prop_rec.proposals,
            protocol_params=net_nodrep_prop_rec.future_pparams,
        )

        reqc.cip061_04.start(url=_url)

        if is_in_bootstrap:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                conway_common.cast_vote(
                    cluster_obj=cluster,
                    governance_data=governance_data,
                    name_template=f"{temp_template}_net_nodrep_bootstrap",
                    payment_addr=pool_user_lg.payment,
                    action_txid=net_nodrep_prop_rec.action_txid,
                    action_ix=net_nodrep_prop_rec.action_ix,
                    approve_cc=True,
                    approve_drep=False,
                    approve_spo=None
                    if net_nodrep_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                    else True,
                )
            err_str = str(excinfo.value)
            assert "(DisallowedVotesDuringBootstrap" in err_str, err_str
        else:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_net_nodrep",
                payment_addr=pool_user_lg.payment,
                action_txid=net_nodrep_prop_rec.action_txid,
                action_ix=net_nodrep_prop_rec.action_ix,
                approve_cc=True,
                approve_drep=False,
                approve_spo=None
                if net_nodrep_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from network group that will NOT get approved by CC
        if configuration.HAS_CC:
            reqc.cip062_02.start(url=helpers.get_vcs_link())
            net_nocc_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_net_nocc", proposals=network_g_proposals
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_net_nocc",
                payment_addr=pool_user_lg.payment,
                action_txid=net_nocc_prop_rec.action_txid,
                action_ix=net_nocc_prop_rec.action_ix,
                approve_cc=False,
                approve_drep=None if is_in_bootstrap else True,
                approve_spo=None
                if net_nocc_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from economic group that will NOT get approved by DReps
        if not is_in_bootstrap:
            eco_nodrep_update_proposals = list(helpers.flatten(economic_g_proposals))
            eco_nodrep_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_eco_nodrep", proposals=eco_nodrep_update_proposals
            )
            _check_proposed_pparams(
                update_proposals=eco_nodrep_prop_rec.proposals,
                protocol_params=eco_nodrep_prop_rec.future_pparams,
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_eco_nodrep",
                payment_addr=pool_user_lg.payment,
                action_txid=eco_nodrep_prop_rec.action_txid,
                action_ix=eco_nodrep_prop_rec.action_ix,
                approve_cc=True,
                approve_drep=False,
                approve_spo=None
                if eco_nodrep_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from economic group that will NOT get approved by CC
        if configuration.HAS_CC:
            eco_nocc_update_proposals = list(helpers.flatten(economic_g_proposals))
            eco_nocc_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_eco_nocc", proposals=eco_nocc_update_proposals
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_eco_nocc",
                payment_addr=pool_user_lg.payment,
                action_txid=eco_nocc_prop_rec.action_txid,
                action_ix=eco_nocc_prop_rec.action_ix,
                approve_cc=False,
                approve_drep=None if is_in_bootstrap else True,
                approve_spo=None
                if eco_nocc_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from technical group that will NOT get approved by DReps
        tech_nodrep_prop_rec = _propose_pparams_update(
            name_template=f"{temp_template}_fin_with_spos", proposals=technical_g_proposals
        )
        _check_proposed_pparams(
            update_proposals=tech_nodrep_prop_rec.proposals,
            protocol_params=tech_nodrep_prop_rec.future_pparams,
        )

        assert tech_nodrep_prop_rec.proposal_names.isdisjoint(
            SECURITY_PPARAMS
        ), "There are security pparams being changed"

        # Check that SPOs cannot vote on change of constitution action if no security params
        # are being changed.
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_fin_with_spos",
                payment_addr=pool_user_lg.payment,
                action_txid=tech_nodrep_prop_rec.action_txid,
                action_ix=tech_nodrep_prop_rec.action_ix,
                approve_cc=False,
                approve_drep=None if is_in_bootstrap else False,
                approve_spo=True,
            )
        err_str = str(excinfo.value)
        assert "StakePoolVoter" in err_str, err_str

        _url = helpers.get_vcs_link()
        reqc.cip065.start(url=_url)
        if is_drep_total_below_threshold:
            reqc.cip064_03.start(url=_url)

        if not is_in_bootstrap:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_tech_nodrep",
                payment_addr=pool_user_lg.payment,
                action_txid=tech_nodrep_prop_rec.action_txid,
                action_ix=tech_nodrep_prop_rec.action_ix,
                approve_cc=True,
                approve_drep=None,
            )

        # Vote on update proposals from technical group that will NOT get approved by CC
        if configuration.HAS_CC:
            tech_nocc_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_tech_nocc", proposals=technical_g_proposals
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_tech_nocc",
                payment_addr=pool_user_lg.payment,
                action_txid=tech_nocc_prop_rec.action_txid,
                action_ix=tech_nocc_prop_rec.action_ix,
                approve_cc=None,
                approve_drep=None if is_in_bootstrap else True,
                approve_spo=None
                if tech_nocc_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from security params that will NOT get votes from SPOs
        _url = helpers.get_vcs_link()
        reqc.cip074.start(url=_url)
        if is_spo_total_below_threshold:
            reqc.cip064_04.start(url=_url)
        sec_nonespo_prop_rec = _propose_pparams_update(
            name_template=f"{temp_template}_sec_nonespo", proposals=security_proposals
        )
        _check_proposed_pparams(
            update_proposals=sec_nonespo_prop_rec.proposals,
            protocol_params=sec_nonespo_prop_rec.future_pparams,
        )
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_sec_nonespo",
            payment_addr=pool_user_lg.payment,
            action_txid=sec_nonespo_prop_rec.action_txid,
            action_ix=sec_nonespo_prop_rec.action_ix,
            approve_cc=True,
            approve_drep=None if is_in_bootstrap else True,
            approve_spo=None,
        )

        # Vote on update proposals from security params that will NOT get approved by SPOs
        reqc.cip061_02.start(url=helpers.get_vcs_link())
        sec_nospo_prop_rec = _propose_pparams_update(
            name_template=f"{temp_template}_sec_nospo", proposals=security_proposals
        )
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_sec_nospo",
            payment_addr=pool_user_lg.payment,
            action_txid=sec_nospo_prop_rec.action_txid,
            action_ix=sec_nospo_prop_rec.action_ix,
            approve_cc=True,
            approve_drep=None if is_in_bootstrap else True,
            approve_spo=False,
        )

        # Vote on update proposals from governance group that will NOT get approved by DReps
        if not is_in_bootstrap:
            gov_nodrep_update_proposals = list(helpers.flatten(governance_g_proposals))
            gov_nodrep_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_gov_nodrep", proposals=gov_nodrep_update_proposals
            )
            _check_proposed_pparams(
                update_proposals=gov_nodrep_prop_rec.proposals,
                protocol_params=gov_nodrep_prop_rec.future_pparams,
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_gov_nodrep",
                payment_addr=pool_user_lg.payment,
                action_txid=gov_nodrep_prop_rec.action_txid,
                action_ix=gov_nodrep_prop_rec.action_ix,
                approve_cc=True,
                approve_drep=False,
                approve_spo=None
                if gov_nodrep_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from governance group that will NOT get approved by CC
        if configuration.HAS_CC:
            gov_nocc_update_proposals = list(helpers.flatten(governance_g_proposals))
            gov_nocc_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_gov_nocc", proposals=gov_nocc_update_proposals
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_gov_nocc",
                payment_addr=pool_user_lg.payment,
                action_txid=gov_nocc_prop_rec.action_txid,
                action_ix=gov_nocc_prop_rec.action_ix,
                approve_cc=False,
                approve_drep=None if is_in_bootstrap else True,
                approve_spo=None
                if gov_nocc_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from mix of groups that will NOT get approved by DReps
        if not is_in_bootstrap:
            mix_nodrep_update_proposals = list(
                helpers.flatten(
                    [
                        *random.sample(network_g_proposals, 2),
                        *random.sample(economic_g_proposals, 2),
                        *random.sample(technical_g_proposals, 2),
                        *random.sample(governance_g_proposals, 2),
                    ]
                )
            )
            mix_nodrep_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_mix_nodrep", proposals=mix_nodrep_update_proposals
            )
            _check_proposed_pparams(
                update_proposals=mix_nodrep_prop_rec.proposals,
                protocol_params=mix_nodrep_prop_rec.future_pparams,
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_mix_nodrep",
                payment_addr=pool_user_lg.payment,
                action_txid=mix_nodrep_prop_rec.action_txid,
                action_ix=mix_nodrep_prop_rec.action_ix,
                approve_cc=True,
                approve_drep=False,
                approve_spo=None
                if mix_nodrep_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on update proposals from mix of groups that will NOT get approved by CC
        if configuration.HAS_CC:
            mix_nocc_update_proposals = list(
                helpers.flatten(
                    [
                        *random.sample(network_g_proposals, 2),
                        *random.sample(economic_g_proposals, 2),
                        *random.sample(technical_g_proposals, 2),
                        *random.sample(governance_g_proposals, 2),
                    ]
                )
            )
            mix_nocc_prop_rec = _propose_pparams_update(
                name_template=f"{temp_template}_mix_nocc", proposals=mix_nocc_update_proposals
            )
            _check_proposed_pparams(
                update_proposals=mix_nocc_prop_rec.proposals,
                protocol_params=mix_nocc_prop_rec.future_pparams,
            )
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_mix_nocc",
                payment_addr=pool_user_lg.payment,
                action_txid=mix_nocc_prop_rec.action_txid,
                action_ix=mix_nocc_prop_rec.action_ix,
                approve_cc=False,
                approve_drep=None if is_in_bootstrap else True,
                approve_spo=None
                if mix_nocc_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
                else True,
            )

        # Vote on the "final" action that will be enacted
        reqc.cip037.start(url=helpers.get_vcs_link())
        fin_prop_rec = _propose_pparams_update(
            name_template=f"{temp_template}_fin_no", proposals=fin_update_proposals
        )
        _check_proposed_pparams(
            update_proposals=fin_prop_rec.proposals,
            protocol_params=fin_prop_rec.future_pparams,
        )

        # Vote & disapprove the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_fin_no",
            payment_addr=pool_user_lg.payment,
            action_txid=fin_prop_rec.action_txid,
            action_ix=fin_prop_rec.action_ix,
            approve_cc=False,
            approve_drep=None if is_in_bootstrap else False,
            approve_spo=False,
        )

        # Vote & approve the action
        if configuration.HAS_CC:
            reqc.cip062_01.start(url=helpers.get_vcs_link())
        fin_voted_votes = conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_fin_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=fin_prop_rec.action_txid,
            action_ix=fin_prop_rec.action_ix,
            approve_cc=True,
            approve_drep=None if is_in_bootstrap else True,
            approve_spo=True,
        )
        fin_approve_epoch = cluster.g_query.get_epoch()

        # Vote on another update proposals from mix of groups. The proposal will get approved,
        # but not enacted, because it comes after the "final" action that was accepted to the chain
        # first.
        reqc.cip056.start(url=helpers.get_vcs_link())
        mix_approved_update_proposals = list(
            helpers.flatten(
                [
                    *random.sample(network_g_proposals, 2),
                    *random.sample(governance_g_proposals, 2),
                ]
            )
        )
        mix_approved_prop_rec = _propose_pparams_update(
            name_template=f"{temp_template}_mix_approved", proposals=mix_approved_update_proposals
        )
        _check_proposed_pparams(
            update_proposals=mix_approved_prop_rec.proposals,
            protocol_params=mix_approved_prop_rec.future_pparams,
        )
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_mix_approved",
            payment_addr=pool_user_lg.payment,
            action_txid=mix_approved_prop_rec.action_txid,
            action_ix=mix_approved_prop_rec.action_ix,
            approve_cc=True,
            approve_drep=None if is_in_bootstrap else True,
            approve_spo=None
            if mix_approved_prop_rec.proposal_names.isdisjoint(SECURITY_PPARAMS)
            else True,
        )

        def _check_state(state: dict):
            pparams = state.get("curPParams") or state.get("currentPParams") or {}
            clusterlib_utils.check_updated_params(
                update_proposals=fin_update_proposals, protocol_params=pparams
            )

        # Check ratification
        reqc.cip068.start(url=helpers.get_vcs_link())
        _cur_epoch = cluster.g_query.get_epoch()
        if _cur_epoch == fin_approve_epoch:
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)

        if _cur_epoch == fin_approve_epoch + 1:
            rat_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{_cur_epoch}"
            )

            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=fin_prop_rec.action_txid
            )
            assert rat_action, "Action not found in ratified actions"

            # Disapprove ratified action, the voting shouldn't have any effect
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_after_ratification",
                payment_addr=pool_user_lg.payment,
                action_txid=fin_prop_rec.action_txid,
                action_ix=fin_prop_rec.action_ix,
                approve_cc=False,
                approve_drep=None if is_in_bootstrap else False,
            )

            next_rat_state = rat_gov_state["nextRatifyState"]
            _check_state(next_rat_state["nextEnactState"])
            reqc.cip038_04.start(url=helpers.get_vcs_link())
            assert not next_rat_state["ratificationDelayed"], "Ratification is delayed unexpectedly"
            reqc.cip038_04.success()

            # Wait for enactment
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)

        # Check enactment
        assert _cur_epoch == fin_approve_epoch + 2, f"Unexpected epoch {_cur_epoch}"
        enact_gov_state = cluster.g_conway_governance.query.gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{_cur_epoch}"
        )
        _check_state(enact_gov_state)
        [
            r.success()
            for r in (
                reqc.cip037,
                reqc.cip044,
                reqc.cip045,
                reqc.cip046,
                reqc.cip047,
                reqc.cip049,
                reqc.cip050,
                reqc.cip051,
                reqc.cip052,
                reqc.cip056,
                reqc.cip060,
                reqc.cip061_02,
                reqc.cip061_04,
                reqc.cip065,
                reqc.cip068,
                reqc.cip074,
            )
        ]
        if configuration.HAS_CC:
            reqc.cip006.success()
            reqc.cip062_01.success()
            reqc.cip062_02.success()
        if is_drep_total_below_threshold:
            reqc.cip064_03.success()
        if is_spo_total_below_threshold:
            reqc.cip064_04.success()

        if proposed_pparams_errors:
            proposed_pparams_errors_str = "\n".join(proposed_pparams_errors)
            raise AssertionError(proposed_pparams_errors_str)

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_enacted",
                payment_addr=pool_user_lg.payment,
                action_txid=fin_prop_rec.action_txid,
                action_ix=fin_prop_rec.action_ix,
                approve_cc=False,
                approve_drep=None if is_in_bootstrap else False,
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Check vote view
        if fin_voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=fin_voted_votes.cc[0])
        if fin_voted_votes.drep:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=fin_voted_votes.drep[0])


class TestPParamData:
    """Tests for checking protocol parameters keys and values."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_pparam_keys(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test presence of expected protocol parameters keys."""
        common.get_test_id(cluster)

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cip075, reqc.cip076, reqc.cip077, reqc.cip078)]

        cur_pparam = cluster.g_conway_governance.query.gov_state()["currentPParams"]
        cur_pparam_keys = set(cur_pparam.keys())
        known_pparam_keys = set().union(
            NETWORK_GROUP_PPARAMS,
            ECONOMIC_GROUP_PPARAMS,
            TECHNICAL_GROUP_PPARAMS,
            GOVERNANCE_GROUP_PPARAMS,
        )
        missing_pparams = known_pparam_keys - cur_pparam_keys
        assert not missing_pparams, f"Missing pparams: {missing_pparams}"

        drep_thresholds = set(cur_pparam["dRepVotingThresholds"].keys())
        missing_drep_thresholds = GOVERNANCE_GROUP_PPARAMS_DREP_THRESHOLDS - drep_thresholds
        assert not missing_drep_thresholds, f"Missing DRep thresholds: {missing_drep_thresholds}"

        pool_thresholds = set(cur_pparam["poolVotingThresholds"].keys())
        missing_pool_thresholds = GOVERNANCE_GROUP_PPARAMS_POOL_THRESHOLDS - pool_thresholds
        assert not missing_pool_thresholds, f"Missing pool thresholds: {missing_pool_thresholds}"

        [r.success() for r in (reqc.cip075, reqc.cip076, reqc.cip077, reqc.cip078)]
