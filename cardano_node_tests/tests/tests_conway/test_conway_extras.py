"""Contains Extra Conway Tests that are not already covered on other topics"""

import binascii
import hashlib
import json
import logging

import cbor2
import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_setup
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS
from cardano_node_tests.tests.tests_conway.test_drep import (
    get_custom_drep,
    get_payment_addr,
    get_pool_user,
)
from cardano_node_tests.utils import submit_api


LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@pytest.fixture
def pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.PoolUser:
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_pool_user(
        name_template=test_id, cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


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
    )


@pytest.fixture
def payment_addr(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Create a payment address"""
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_payment_addr(
        name_template=test_id, cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


@pytest.fixture
def custom_drep(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
) -> governance_utils.DRepRegistration:
    """Get a new custom drep"""
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_custom_drep(
        name_template=f"custom_drep_{test_id}",
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        payment_addr=payment_addr,
        caching_key=key,
    )

@pytest.fixture
def registered_pool_user(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.PoolUser:
    """Create a pool user for "use governance"."""
    key = helpers.get_current_line_str()
    name_template = common.get_test_id(cluster)
    return conway_common.get_registered_pool_user(
        cluster_manager=cluster_manager,
        name_template=name_template,
        cluster_obj=cluster,
        caching_key=key,
    )

class TestConwayExtras:
    """
    Starting Class for Extra Conway Tests that are not already covered on other topics.
    """

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_drep_id_is_blake2b_224_of_drep_vkey(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test proper drep id is being generated
        * Register a drep
        * Hash drep vkey using blake2b_224
        * Check drep ID generated from cli is same as blake2b_224 hash of drep vkey
        """
        temp_template = common.get_test_id(cluster)
        drep_metadata_url = "https://www.the-drep.com"
        drep_metadata_file = f"{temp_template}_drep_metadata.json"
        drep_metadata_content = {"name": "The DRep", "ranking": "uno"}
        helpers.write_json(out_file=drep_metadata_file, content=drep_metadata_content)
        drep_metadata_hash = cluster.g_conway_governance.drep.get_metadata_hash(
            drep_metadata_file=drep_metadata_file
        )
        reg_drep = governance_utils.get_drep_reg_record(
            cluster_obj=cluster,
            name_template=temp_template,
            drep_metadata_url=drep_metadata_url,
            drep_metadata_hash=drep_metadata_hash,
        )
        vkey_file_path = reg_drep.key_pair.vkey_file
        vkey_file = open(vkey_file_path, "r")
        vkey_file_json = json.loads(vkey_file.read())
        cbor_hex = vkey_file_json["cborHex"]
        cbor_binary = binascii.unhexlify(cbor_hex)
        decoded_data = cbor2.loads(cbor_binary)
        blake2b_224 = hashlib.blake2b(digest_size=28)
        blake2b_224.update(decoded_data)
        hash_digest = blake2b_224.hexdigest()
        assert reg_drep.drep_id == hash_digest, f"Drep ID hash is not blake2b_224."

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_change_delegation(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addr: clusterlib.AddressRecord,
        pool_user: clusterlib.PoolUser,
    ):
        """Test Change delegation to different dreps
        * Create 2 Dreps
        * Create vote delegation certifcate for first drep
        * Submit certificate
        * check that the delegation is of correct drep id
        * Change delegation to drep2 and submit certificate
        * Check vote delegation is updated to second drep
        """
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        key1 = helpers.get_current_line_str()
        drep1 = get_custom_drep(
            name_template=f"custom_drep_1_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            caching_key=key1,
        )

        key2 = helpers.get_current_line_str()
        drep2 = get_custom_drep(
            name_template=f"custom_drep_2_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            caching_key=key2,
        )

        # Create stake address registration cert
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user.stake.vkey_file,
        )

        # Create vote delegation cert
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr1",
            stake_vkey_file=pool_user.stake.vkey_file,
            drep_key_hash=drep1.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert],
            signing_key_files=[payment_addr.skey_file, pool_user.stake.skey_file],
        )

        # Make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr.address,
            use_build_cmd=True,
            tx_files=tx_files,
            deposit=deposit_amt,
        )
        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user.stake.address)
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep1.drep_id
        ), "Votes are NOT delegated to the correct DRep 1"

        # Change delegation to drep2
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr2",
            stake_vkey_file=pool_user.stake.vkey_file,
            drep_key_hash=drep2.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[deleg_cert],
            signing_key_files=[payment_addr.skey_file, pool_user.stake.skey_file],
        )

        # Make sure we have enough time to finish the delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr.address,
            use_build_cmd=True,
            tx_files=tx_files,
            deposit=deposit_amt,
        )
        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user.stake.address)
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep2.drep_id
        ), "Votes are NOT changed to the correct DRep 2"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_no_multiple_delegation(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addr: clusterlib.AddressRecord,
        pool_user: clusterlib.PoolUser,
    ):
        """Test No multiple delegation to different dreps
        * Create 2 Dreps
        * Create vote delegation certifcate to both dreps
        * Submit both certificates
        * check that the Drep certificate placed at last of the certificates is delegated to
        """
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        key1 = helpers.get_current_line_str()
        drep1 = get_custom_drep(
            name_template=f"custom_drep_1_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            caching_key=key1,
        )

        key2 = helpers.get_current_line_str()
        drep2 = get_custom_drep(
            name_template=f"custom_drep_2_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            caching_key=key2,
        )

        # Create stake address registration cert
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user.stake.vkey_file,
        )

        # Create vote delegation cert for drep 1
        deleg_cert_1 = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr1",
            stake_vkey_file=pool_user.stake.vkey_file,
            drep_key_hash=drep1.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        # Create vote delegation cert for drep 2
        deleg_cert_2 = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr2",
            stake_vkey_file=pool_user.stake.vkey_file,
            drep_key_hash=drep2.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        # Submit two vote delegation certificate at once
        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert_2, deleg_cert_1],
            signing_key_files=[payment_addr.skey_file, pool_user.stake.skey_file],
        )

        # Make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr.address,
            use_build_cmd=True,
            tx_files=tx_files,
            deposit=deposit_amt,
        )
        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user.stake.address)

        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep1.drep_id
        ), "Votes are NOT delegated to the correct DRep 1 placed at last of certificates list."

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("drep", ("always_abstain", "always_no_confidence", "custom"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_no_delegation_without_stake_registration(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        pool_user: clusterlib.PoolUser,
        custom_drep: governance_utils.DRepRegistration,
        drep: str,
    ):
        """Test Change delegation to different dreps
        * Create 2 Dreps
        * Create vote delegation certifcate for first drep
        * Submit certificate
        * check that the delegation is of correct drep id
        * Change delegation to drep2 and submit certificate
        * Check vote delegation is updated to second drep
        """
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()

        # Create vote delegation cert
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr1",
            stake_vkey_file=pool_user.stake.vkey_file,
            drep_key_hash=custom_drep.drep_id if drep == "custom" else "",
            always_abstain=drep == "always_abstain",
            always_no_confidence=drep == "always_no_confidence",
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[deleg_cert],
            signing_key_files=[payment_addr.skey_file, pool_user.stake.skey_file],
        )

        # Make sure we have enough time to finish the delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=temp_template,
                src_address=payment_addr.address,
                use_build_cmd=True,
                tx_files=tx_files,
                deposit=deposit_amt,
            )

        err_msg = str(excinfo.value)
        assert "StakeKeyNotRegisteredDELEG" in err_msg, err_msg

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_drep_no_retirement_before_register(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test No Drep retirement before register
        * Create a retirement certificate without registering
        * Submit certificate
        * check it is not possible to retire before register
        """
        temp_template = common.get_test_id(cluster)
        drep_keys = cluster.g_conway_governance.drep.gen_key_pair(
            key_name=temp_template, destination_dir="."
        )

        deposit = cluster.conway_genesis["dRepDeposit"]
        ret_cert = cluster.g_conway_governance.drep.gen_retirement_cert(
            cert_name=temp_template,
            deposit_amt=deposit,
            drep_vkey_file=drep_keys.vkey_file,
        )
        tx_files_ret = clusterlib.TxFiles(
            certificate_files=[ret_cert],
            signing_key_files=[payment_addr.skey_file, drep_keys.skey_file],
        )

        # For handling case where str method of submit api error returns extra \\
        error_string_content = ""

        if submit_method == "api":
            with pytest.raises(submit_api.SubmitApiError) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_ret",
                    src_address=payment_addr.address,
                    submit_method=submit_method,
                    use_build_cmd=use_build_cmd,
                    tx_files=tx_files_ret,
                    deposit=-deposit,
                )
            error_string_content = "\\"
        else:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_ret",
                    src_address=payment_addr.address,
                    submit_method=submit_method,
                    use_build_cmd=use_build_cmd,
                    tx_files=tx_files_ret,
                    deposit=-deposit,
                )

        drep_id = cluster.g_conway_governance.drep.get_id(
            drep_vkey_file=drep_keys.vkey_file,
            out_format="hex",
        )
        err_msg = str(excinfo.value)
        assert (
            f'ConwayDRepNotRegistered (KeyHashObj (KeyHash {error_string_content}"{drep_id}'
            in err_msg
        ), err_msg

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_drep_no_multiple_registration(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test Drep cannot be registered multiple time
        * Create a drep registration certificate
        * Submit certificate
        * check it is not possible to register
        """
        temp_template = common.get_test_id(cluster)
        drep_metadata_url = "https://www.the-drep.com"
        drep_metadata_file = f"{temp_template}_drep_metadata.json"
        drep_metadata_content = {"name": "The DRep", "ranking": "uno"}
        helpers.write_json(out_file=drep_metadata_file, content=drep_metadata_content)
        drep_metadata_hash = cluster.g_conway_governance.drep.get_metadata_hash(
            drep_metadata_file=drep_metadata_file
        )
        deposit_amt = cluster.conway_genesis["dRepDeposit"]
        drep_keys = cluster.g_conway_governance.drep.gen_key_pair(
            key_name=temp_template, destination_dir="."
        )
        drep_id = cluster.g_conway_governance.drep.get_id(
            drep_vkey_file=drep_keys.vkey_file,
            out_format="hex",
        )
        reg_cert = cluster.g_conway_governance.drep.gen_registration_cert(
            cert_name=temp_template,
            deposit_amt=deposit_amt,
            drep_vkey_file=drep_keys.vkey_file,
            drep_metadata_url=drep_metadata_url,
            drep_metadata_hash=drep_metadata_hash,
            destination_dir=".",
        )
        tx_files_reg = clusterlib.TxFiles(
            certificate_files=[reg_cert],
            signing_key_files=[payment_addr.skey_file, drep_keys.skey_file],
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_reg",
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files_reg,
            deposit=deposit_amt,
        )

        # Wait for some blocks and again submit drep registration certificate
        cluster.wait_for_new_block(new_blocks=2)

        # For handling case where str method of submit api error returns extra \\
        error_string_content = ""
        if submit_method == "api":
            with pytest.raises(submit_api.SubmitApiError) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_ret",
                    src_address=payment_addr.address,
                    submit_method=submit_method,
                    use_build_cmd=use_build_cmd,
                    tx_files=tx_files_reg,
                    deposit=deposit_amt,
                )
            error_string_content = "\\"
        else:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_ret",
                    src_address=payment_addr.address,
                    submit_method=submit_method,
                    use_build_cmd=use_build_cmd,
                    tx_files=tx_files_reg,
                    deposit=-deposit_amt,
                )

        drep_id = cluster.g_conway_governance.drep.get_id(
            drep_vkey_file=drep_keys.vkey_file,
            out_format="hex",
        )
        err_msg = str(excinfo.value)
        assert (
            f'ConwayDRepAlreadyRegistered (KeyHashObj (KeyHash {error_string_content}"{drep_id}'
            in err_msg
        ), err_msg

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_deposit_return_committee(
        self,
        cluster_lock_governance: governance_setup.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test Deposit is returned for update committee action

        * Submit a update committee action
        * Check for enactment
        * Check deposit is returned to user reward account after enactment
        """
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            pytest.skip("Cannot run during bootstrap period.")

        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            pool_user_lg.stake.address
        ).reward_account_balance

        deposit_amt = cluster.conway_genesis["govActionDeposit"]
        anchor_url_add = "http://www.cc-add.com"
        anchor_data_hash_add = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"
        prev_action_rec = governance_utils.get_prev_action(
            action_type=governance_utils.PrevGovActionIds.COMMITTEE,
            gov_state=cluster.g_conway_governance.query.gov_state(),
        )
        # Auth keys for CC members
        cc_auth_record1 = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=f"{temp_template}_member1",
        )
        cc_auth_record2 = governance_utils.get_cc_member_auth_record(
            cluster_obj=cluster,
            name_template=f"{temp_template}_member2",
        )

        # New CC members to be added
        cc_member1_expire = cluster.g_query.get_epoch() + 3
        cc_members = [
            clusterlib.CCMember(
                epoch=cc_member1_expire,
                cold_vkey_file=cc_auth_record1.cold_key_pair.vkey_file,
                cold_skey_file=cc_auth_record1.cold_key_pair.skey_file,
                hot_vkey_file=cc_auth_record1.hot_key_pair.vkey_file,
                hot_skey_file=cc_auth_record1.hot_key_pair.skey_file,
            ),
            clusterlib.CCMember(
                epoch=cluster.g_query.get_epoch() + 5,
                cold_vkey_file=cc_auth_record2.cold_key_pair.vkey_file,
                cold_skey_file=cc_auth_record2.cold_key_pair.skey_file,
                hot_vkey_file=cc_auth_record2.hot_key_pair.vkey_file,
                hot_skey_file=cc_auth_record2.hot_key_pair.skey_file,
            ),
        ]

        add_cc_action = cluster.g_conway_governance.action.update_committee(
            action_name=f"{temp_template}_add",
            deposit_amt=deposit_amt,
            anchor_url=anchor_url_add,
            anchor_data_hash=anchor_data_hash_add,
            threshold=str(cluster.conway_genesis["committee"]["threshold"]),
            add_cc_members=[*cc_members],
            prev_action_txid=prev_action_rec.txid,
            prev_action_ix=prev_action_rec.ix,
            deposit_return_stake_vkey_file=pool_user_lg.stake.vkey_file,
        )

        tx_files_action_add = clusterlib.TxFiles(
            proposal_files=[add_cc_action.action_file],
            signing_key_files=[
                pool_user_lg.payment.skey_file,
            ],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )
        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action_add",
            src_address=pool_user_lg.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_action_add,
        )

        action_add_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_add_gov_state = cluster.g_conway_governance.query.gov_state()

        prop_action_add = governance_utils.lookup_proposal(
            gov_state=action_add_gov_state, action_txid=action_add_txid
        )
        action_add_ix = prop_action_add["actionId"]["govActionIx"]

        conway_common.cast_vote(
            cluster_obj=cluster,
            governance_data=governance_data,
            name_template=f"{temp_template}_add_yes",
            payment_addr=pool_user_lg.payment,
            action_txid=action_add_txid,
            action_ix=action_add_ix,
            approve_drep=True,
            approve_spo=True,
            drep_skip_votes=True,
        )

        # Check ratification of add action
        cluster.wait_for_new_epoch(padding_seconds=5)
        rat_add_gov_state = cluster.g_conway_governance.query.gov_state()

        rat_action = governance_utils.lookup_ratified_actions(
            gov_state=rat_add_gov_state, action_txid=action_add_txid
        )
        assert rat_action, "Action not found in ratified actions"

        # Wait for enactment of add action
        cluster.wait_for_new_epoch(padding_seconds=5)

        enact_deposit_returned = cluster.g_query.get_stake_addr_info(
            pool_user_lg.stake.address
        ).reward_account_balance

        assert (
            enact_deposit_returned == init_return_account_balance + deposit_amt
        ), "Incorrect return account balance"


    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_info_return(
        self,
        cluster: clusterlib.ClusterLib,
        registered_pool_user: clusterlib.PoolUser,
    ):
        """Test deposit return on info action.
        * submit an "info" action
        * Wait for govActionLifetime
        * Check deposit is returned
        """
        temp_template = common.get_test_id(cluster)
        action_deposit_amt = cluster.conway_genesis["govActionDeposit"]

        init_return_account_balance = cluster.g_query.get_stake_addr_info(
            registered_pool_user.stake.address
        ).reward_account_balance

        # Create an action
        rand_str = helpers.get_rand_str(4)
        anchor_url = f"http://www.info-action-{rand_str}.com"
        anchor_data_hash = "5d372dca1a4cc90d7d16d966c48270e33e3aa0abcb0e78f0d5ca7ff330d2245d"

        info_action = cluster.g_conway_governance.action.create_info(
            action_name=temp_template,
            deposit_amt=action_deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_return_stake_vkey_file=registered_pool_user.stake.vkey_file,
        )

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[info_action.action_file],
            signing_key_files=[registered_pool_user.payment.skey_file],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_action",
            src_address=registered_pool_user.payment.address,
            use_build_cmd=True,
            tx_files=tx_files_action,
        )

        action_txid = cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)
        action_gov_state = cluster.g_conway_governance.query.gov_state()
        prop_action = governance_utils.lookup_proposal(
                    gov_state=action_gov_state, action_txid=action_txid
                )
        assert prop_action, "Info action not found"
        
        gov_action_lifetime = cluster.conway_genesis["govActionLifetime"]
        # Wait for gov action lifetime
        for _ in range(0, gov_action_lifetime):
            cluster.wait_for_new_epoch(padding_seconds=5)

        # Check deposit is returned
        deposit_returned = cluster.g_query.get_stake_addr_info(
            registered_pool_user.stake.address
        ).reward_account_balance
        assert (
            deposit_returned == init_return_account_balance + action_deposit_amt
        ), "Incorrect return account balance"
