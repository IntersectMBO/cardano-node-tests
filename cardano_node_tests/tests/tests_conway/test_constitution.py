"""Tests for Conway governance constitution."""

import dataclasses
import itertools
import logging
import pathlib as pl
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import web
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def cluster_lock_gov_script(
    cluster_manager: cluster_management.ClusterManager,
) -> governance_utils.GovClusterT:
    """Mark governance as "locked" and return instance of `clusterlib.ClusterLib`.

    Lock also the PlutusV3 script that is registered as DRep in the test.
    """
    cluster_obj = cluster_manager.get(
        use_resources=[
            *cluster_management.Resources.ALL_POOLS,
            cluster_management.Resources.REWARDS,
            cluster_management.Resources.PLUTUS,
        ],
        lock_resources=[
            cluster_management.Resources.COMMITTEE,
            cluster_management.Resources.DREPS,
            helpers.checksum(plutus_common.ALWAYS_SUCCEEDS["v3"].script_file),
        ],
    )
    governance_data = governance_setup.get_default_governance(
        cluster_manager=cluster_manager, cluster_obj=cluster_obj
    )
    governance_utils.wait_delayed_ratification(cluster_obj=cluster_obj)
    return cluster_obj, governance_data


@pytest.fixture
def pool_user_lg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_gov_script: governance_utils.GovClusterT,
) -> clusterlib.PoolUser:
    """Create a pool user for "lock governance"."""
    cluster, __ = cluster_lock_gov_script
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return common.get_registered_pool_user(
        name_template=name_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        caching_key=key,
    )


@pytest.fixture
def script_dreps_lg(
    cluster_manager: cluster_management.ClusterManager,
    cluster_lock_gov_script: governance_utils.GovClusterT,
    testfile_temp_dir: pl.Path,
) -> tp.Generator[
    tuple[list[governance_utils.DRepScriptRegistration], list[clusterlib.PoolUser]],
    None,
    None,
]:
    """Create script DReps for "lock governance"."""
    __: tp.Any  # mypy workaround
    cluster, __ = cluster_lock_gov_script
    temp_template = f"{common.get_test_id(cluster)}_script_dreps"
    pool_users = governance_setup.create_vote_stake(
        name_template=temp_template,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        no_of_addr=3,
    )

    def _simple_rec(
        name_template: str,
        slot: int,
    ) -> governance_utils.DRepScriptRegInputs:
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=name_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=[u.payment.vkey_file for u in pool_users],
            slot=slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )
        script_keys = [
            clusterlib.KeyPair(vkey_file=u.payment.vkey_file, skey_file=u.payment.skey_file)
            for u in pool_users
        ]

        reg_cert_script = clusterlib.ComplexCert(
            certificate_file="",
            script_file=multisig_script,
        )
        reg_cert_record = governance_utils.DRepScriptRegInputs(
            registration_cert=reg_cert_script,
            key_pairs=script_keys,
            script_type=governance_utils.ScriptTypes.SIMPLE,
        )

        return reg_cert_record

    def _plutus_cert_rec(
        name_template: str,
        script_data: plutus_common.PlutusScriptData,
        redeemer_file: pl.Path,
    ) -> governance_utils.DRepScriptRegInputs:
        collateral_fund = 1_500_000_000

        txouts = [
            clusterlib.TxOut(address=pool_users[1].payment.address, amount=collateral_fund),
        ]

        tx_files = clusterlib.TxFiles(
            signing_key_files=[pool_users[0].payment.skey_file],
        )
        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=name_template,
            src_address=pool_users[0].payment.address,
            use_build_cmd=True,
            txouts=txouts,
            tx_files=tx_files,
        )
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        collateral_utxos = clusterlib.filter_utxos(
            utxos=out_utxos,
            amount=txouts[0].amount,
            address=txouts[0].address,
        )
        reg_cert = clusterlib.ComplexCert(
            certificate_file="",
            script_file=script_data.script_file,
            collaterals=collateral_utxos,
            execution_units=(
                script_data.execution_cost.per_time,
                script_data.execution_cost.per_space,
            ),
            redeemer_file=redeemer_file,
        )
        reg_inputs = governance_utils.DRepScriptRegInputs(
            registration_cert=reg_cert,
            key_pairs=[
                clusterlib.KeyPair(
                    vkey_file=pool_users[1].payment.vkey_file,
                    skey_file=pool_users[1].payment.skey_file,
                )
            ],
            script_type=governance_utils.ScriptTypes.PLUTUS,
        )
        return reg_inputs

    _url = helpers.get_vcs_link()
    [r.start(url=_url) for r in (reqc.cip020_02, reqc.cip020_03)]
    reg_cert_script1 = _simple_rec(name_template=f"{temp_template}_simple1", slot=100)
    reg_cert_script2 = _simple_rec(name_template=f"{temp_template}_simple2", slot=101)
    reg_cert_script3 = _plutus_cert_rec(
        name_template=f"{temp_template}_pv3",
        script_data=plutus_common.ALWAYS_SUCCEEDS["v3"],
        redeemer_file=plutus_common.REDEEMER_42,
    )

    script_inputs = [reg_cert_script1, reg_cert_script2, reg_cert_script3]

    script_dreps = governance_utils.create_script_dreps(
        name_template=temp_template,
        script_inputs=script_inputs,
        cluster_obj=cluster,
        payment_addr=pool_users[0].payment,
        pool_users=pool_users,
    )
    [r.success() for r in (reqc.cip020_02, reqc.cip020_03)]

    yield script_dreps

    def _dereg_stake() -> None:
        """Deregister stake addresses and return funds."""
        _pool_users_info = [
            (p, cluster.g_query.get_stake_addr_info(p.stake.address)) for p in pool_users
        ]
        pool_users_info = [r for r in _pool_users_info if r[1]]

        stake_addr_dereg_certs = [
            cluster.g_stake_address.gen_stake_addr_deregistration_cert(
                addr_name=f"{temp_template}_addr{i}",
                deposit_amt=r[1].registration_deposit,
                stake_vkey_file=r[0].stake.vkey_file,
            )
            for i, r in enumerate(pool_users_info)
        ]

        tx_files = clusterlib.TxFiles(
            certificate_files=stake_addr_dereg_certs,
            signing_key_files=[
                pool_users[0].payment.skey_file,
                *[p.stake.skey_file for p, __ in pool_users_info],
            ],
        )

        _withdrawals = [
            (
                clusterlib.TxOut(
                    address=p.stake.address,
                    amount=s.reward_account_balance,
                )
                if s.reward_account_balance
                else None
            )
            for p, s in pool_users_info
        ]
        withdrawals = [w for w in _withdrawals if w is not None]

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_dereg",
            src_address=pool_users[0].payment.address,
            use_build_cmd=False,  # Workaround for CLI issue 942
            tx_files=tx_files,
            withdrawals=withdrawals,
            deposit=-sum(s.registration_deposit for __, s in pool_users_info),
        )

        dereg_stake_states = [
            cluster.g_query.get_stake_addr_info(p.stake.address) for p in pool_users
        ]
        assert not any(dereg_stake_states), "Some stake addresses were not deregistered"

    def _retire_dreps() -> None:
        drep_script_data, drep_users = script_dreps

        ret_cert_files = [
            cluster.g_governance.drep.gen_retirement_cert(
                cert_name=f"{temp_template}_sdrep_ret{i}",
                deposit_amt=d.deposit,
                drep_script_hash=d.script_hash,
            )
            for i, d in enumerate(drep_script_data)
        ]

        ret_certs = [
            dataclasses.replace(d.registration_cert, certificate_file=c)
            for c, d in zip(ret_cert_files, drep_script_data)
        ]

        witness_keys = itertools.chain.from_iterable(r.key_pairs for r in drep_script_data)
        tx_files = clusterlib.TxFiles(
            signing_key_files=[
                pool_users[0].payment.skey_file,
                *[r.stake.skey_file for r in drep_users],
                *[r.skey_file for r in witness_keys],
            ],
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_ret",
            src_address=pool_users[0].payment.address,
            use_build_cmd=True,
            tx_files=tx_files,
            complex_certs=ret_certs,
            deposit=-sum(d.deposit for d in drep_script_data),
        )

        ret_drep_states = [
            cluster.g_query.get_drep_state(drep_script_hash=d.script_hash) for d in drep_script_data
        ]
        assert not any(ret_drep_states), "Some DRep were not retired"

    with helpers.change_cwd(testfile_temp_dir):
        _dereg_stake()
        _retire_dreps()


@pytest.fixture
def governance_w_scripts_lg(
    cluster_lock_gov_script: governance_utils.GovClusterT,
    script_dreps_lg: tuple[
        list[governance_utils.DRepScriptRegistration], list[clusterlib.PoolUser]
    ],
) -> governance_utils.GovernanceRecords:
    """Create a governance records with script DReps."""
    cluster, default_governance = cluster_lock_gov_script
    script_dreps, script_delegators = script_dreps_lg
    return dataclasses.replace(
        default_governance, drep_scripts_reg=script_dreps, drep_scripts_delegators=script_delegators
    )


class TestConstitution:
    """Tests for constitution."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.long
    def test_change_constitution(
        self,
        cluster_lock_gov_script: governance_utils.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
        governance_w_scripts_lg: governance_utils.GovernanceRecords,
    ):
        """Test enactment of change of constitution.

        * submit a "create constitution" action
        * check that SPOs cannot vote on a "create constitution" action
        * vote to disapprove the action
        * vote to approve the action
        * check that the action is ratified
        * try to disapprove the ratified action, this shouldn't have any effect
        * try and fail to withdraw the deposit from stake address that is not delegated to a DRep
        * check that the action is enacted
        * check that it's not possible to vote on enacted action
        """
        __: tp.Any  # mypy workaround
        cluster, __ = cluster_lock_gov_script
        rand_str = clusterlib.get_rand_str(4)
        governance_data = governance_w_scripts_lg
        temp_template = f"{common.get_test_id(cluster)}_{rand_str}"
        deposit_amt = cluster.g_query.get_gov_action_deposit()

        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_lg.stake.address
        ).reward_account_balance

        # Create an action

        anchor_text = f"Change constitution action, temp ID {rand_str}"
        anchor_data = governance_utils.get_anchor_data(
            cluster_obj=cluster,
            name_template=temp_template,
            anchor_text=anchor_text,
        )

        constitution_text = (
            "Lorem ipsum dolor sit amet, consectetur adipiscing elit, "
            "sed do eiusmod tempor incididunt ut labore et dolore magna aliqua. "
            "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi "
            "ut aliquip ex ea commodo consequat. Duis aute irure dolor in reprehenderit "
            "in voluptate velit esse cillum dolore eu fugiat nulla pariatur. "
            "Excepteur sint occaecat cupidatat non proident, sunt in culpa qui officia "
            "deserunt mollit anim id est laborum."
        )
        constitution_file = pl.Path(f"{temp_template}_constitution.txt")
        constitution_file.write_text(data=constitution_text, encoding="utf-8")
        constitution_url = web.publish(file_path=constitution_file)

        reqc.cli002.start(url=helpers.get_vcs_link())
        constitution_hash = cluster.g_governance.get_anchor_data_hash(file_text=constitution_file)
        reqc.cli002.success()

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            reqc.cip026_02.start(url=helpers.get_vcs_link())
            with pytest.raises(clusterlib.CLIError) as excinfo:
                conway_common.propose_change_constitution(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_constitution_bootstrap",
                    anchor_url=anchor_data.url,
                    anchor_data_hash=anchor_data.hash,
                    constitution_url=constitution_url,
                    constitution_hash=constitution_hash,
                    pool_user=pool_user_lg,
                )
            err_str = str(excinfo.value)
            assert "(DisallowedProposalDuringBootstrap" in err_str, err_str
            reqc.cip026_02.success()
            return

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli013, reqc.cip031a_02, reqc.cip031c_01, reqc.cip054_03)]
        (
            constitution_action,
            action_txid,
            action_ix,
        ) = conway_common.propose_change_constitution(
            cluster_obj=cluster,
            name_template=f"{temp_template}_constitution",
            anchor_url=anchor_data.url,
            anchor_data_hash=anchor_data.hash,
            constitution_url=constitution_url,
            constitution_hash=constitution_hash,
            pool_user=pool_user_lg,
        )
        [r.success() for r in (reqc.cli013, reqc.cip031a_02, reqc.cip031c_01, reqc.cip054_03)]

        # Make sure we have enough time to submit the votes in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=5, stop=common.EPOCH_STOP_SEC_BUFFER - 20
        )
        init_epoch = cluster.g_query.get_epoch()

        # Check that SPOs cannot vote on change of constitution action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_with_spos",
                payment_addr=pool_user_lg.payment,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_cc=False,
                approve_drep=False,
                approve_spo=False,
                use_build_cmd=False,  # cardano-cli issue #650
            )
        err_str = str(excinfo.value)
        assert "StakePoolVoter" in err_str, err_str

        # Vote & disapprove the action
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_no",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=False,
            approve_drep=False,
            use_build_cmd=False,  # cardano-cli issue #650
        )

        # Vote & approve the action
        reqc.cip042.start(url=helpers.get_vcs_link())
        voted_votes = conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=True,
            approve_drep=True,
            use_build_cmd=False,  # cardano-cli issue #650
        )

        assert cluster.g_query.get_epoch() == init_epoch, (
            "Epoch changed and it would affect other checks"
        )

        def _assert_anchor(anchor: dict):
            assert (
                anchor["dataHash"]
                == constitution_hash
                == "d6d9034f61e2f7ada6e58c252e15684c8df7f0b197a95d80f42ca0a3685de26e"
            ), "Incorrect constitution data hash"
            assert anchor["url"] == constitution_url, "Incorrect constitution data URL"

        def _check_state(state: dict):
            anchor = state["constitution"]["anchor"]
            _assert_anchor(anchor)

        def _check_cli_query():
            anchor = cluster.g_query.get_constitution()["anchor"]
            _assert_anchor(anchor)

        # Check ratification
        rat_epoch = cluster.wait_for_epoch(epoch_no=init_epoch + 1, padding_seconds=5)
        rat_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=rat_gov_state, name_template=f"{temp_template}_rat_{rat_epoch}"
        )
        rat_action = governance_utils.lookup_ratified_actions(
            gov_state=rat_gov_state, action_txid=action_txid
        )
        assert rat_action, "Action not found in ratified actions"

        # Disapprove ratified action, the voting shouldn't have any effect
        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_after_ratification",
            payment_addr=pool_user_lg.payment,
            action_txid=action_txid,
            action_ix=action_ix,
            approve_cc=False,
            approve_drep=False,
            use_build_cmd=False,  # cardano-cli issue #650
        )

        next_rat_state = rat_gov_state["nextRatifyState"]
        _url = helpers.get_vcs_link()
        [
            r.start(url=_url)
            for r in (
                reqc.cli001,
                reqc.cip001a,
                reqc.cip001b,
                reqc.cip072,
                reqc.cip073_01,
                reqc.cip073_04,
            )
        ]
        _check_state(next_rat_state["nextEnactState"])
        [r.success() for r in (reqc.cli001, reqc.cip001a, reqc.cip001b, reqc.cip073_01)]
        reqc.cip038_02.start(url=_url)
        assert next_rat_state["ratificationDelayed"], "Ratification not delayed"
        reqc.cip038_02.success()

        # Check enactment
        enact_epoch = cluster.wait_for_epoch(epoch_no=init_epoch + 2, padding_seconds=5)
        enact_gov_state = cluster.g_query.get_gov_state()
        conway_common.save_gov_state(
            gov_state=enact_gov_state, name_template=f"{temp_template}_enact_{enact_epoch}"
        )
        _check_state(enact_gov_state)
        [r.success() for r in (reqc.cip042, reqc.cip072, reqc.cip073_04)]

        reqc.cli036.start(url=helpers.get_vcs_link())
        _check_cli_query()
        reqc.cli036.success()

        # Check that deposit was returned immediately after enactment
        enact_deposit_returned = cluster.g_query.get_stake_addr_info(
            pool_user_lg.stake.address
        ).reward_account_balance

        assert enact_deposit_returned == init_return_account_balance + deposit_amt, (
            "Incorrect return account balance"
        )

        reqc.cip027.start(url=helpers.get_vcs_link())
        # Try to withdraw the deposit from stake address that is not delegated to a DRep
        with pytest.raises(clusterlib.CLIError) as excinfo:
            clusterlib_utils.withdraw_reward_w_build(
                cluster_obj=cluster,
                stake_addr_record=pool_user_lg.stake,
                dst_addr_record=pool_user_lg.payment,
                tx_name=temp_template,
            )
        err_str = str(excinfo.value)
        assert "(ConwayWdrlNotDelegatedToDRep" in err_str, err_str
        reqc.cip027.success()

        # Try to vote on enacted action
        with pytest.raises(clusterlib.CLIError) as excinfo:
            conway_common.cast_vote(
                cluster_obj=cluster,
                governance_data=governance_data,
                name_template=f"{temp_template}_enacted",
                payment_addr=pool_user_lg.payment,
                action_txid=action_txid,
                action_ix=action_ix,
                approve_cc=False,
                approve_drep=False,
                use_build_cmd=False,  # cardano-cli issue #650
            )
        err_str = str(excinfo.value)
        assert "(GovActionsDoNotExist" in err_str, err_str

        # Check action view
        reqc.cli020.start(url=helpers.get_vcs_link())
        governance_utils.check_action_view(cluster_obj=cluster, action_data=constitution_action)
        reqc.cli020.success()

        # Check vote view
        if voted_votes.cc:
            governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.cc[0])
        governance_utils.check_vote_view(cluster_obj=cluster, vote_data=voted_votes.drep[0])

        # Check new constitution proposal in dbsync
        if configuration.HAS_DBSYNC:
            reqc.db012.start(url=helpers.get_vcs_link())
            constitution_db = list(dbsync_queries.query_new_constitution(txhash=action_txid))
            assert constitution_db, "No new constitution proposal found in dbsync"
            assert constitution_db[0].gov_action_type == "NewConstitution"
            reqc.db012.success()

        # Check epoch state in dbsync
        reqc.db025_02.start(url=helpers.get_vcs_link())
        dbsync_utils.check_epoch_state(
            epoch_no=cluster.g_query.get_epoch(), txid=action_txid, change_type="constitution"
        )
        reqc.db025_02.success()
