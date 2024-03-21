"""Tests for Conway governance DRep functionality."""

# pylint: disable=expression-not-assigned
import logging
import pathlib as pl
import typing as tp

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


def get_payment_addr(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str,
) -> clusterlib.AddressRecord:
    """Create new payment address."""
    with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addr = clusterlib_utils.create_payment_addr_records(
            f"drep_addr_{name_template}",
            cluster_obj=cluster_obj,
        )[0]
        fixture_cache.value = addr

    # Fund source address
    clusterlib_utils.fund_from_faucet(
        addr,
        cluster_obj=cluster_obj,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return addr


def get_pool_user(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    caching_key: str,
) -> clusterlib.PoolUser:
    """Create a pool user."""
    with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        pool_user = clusterlib_utils.create_pool_users(
            cluster_obj=cluster_obj,
            name_template=f"{name_template}_pool_user",
            no_of_addr=1,
        )[0]
        fixture_cache.value = pool_user

    # Fund the payment address with some ADA
    clusterlib_utils.fund_from_faucet(
        pool_user.payment,
        cluster_obj=cluster_obj,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=1_500_000,
    )
    return pool_user


def get_custom_drep(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    caching_key: str,
) -> governance_utils.DRepRegistration:
    """Create a custom DRep."""
    if cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.LOCAL:
        pytest.skip("runs only on local cluster")

    with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        reg_drep = governance_utils.get_drep_reg_record(
            cluster_obj=cluster_obj,
            name_template=f"drep_custom_{name_template}",
        )

        tx_files_reg = clusterlib.TxFiles(
            certificate_files=[reg_drep.registration_cert],
            signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster_obj,
            name_template=f"drep_custom_reg{name_template}",
            src_address=payment_addr.address,
            submit_method=submit_utils.SubmitMethods.CLI,
            use_build_cmd=True,
            tx_files=tx_files_reg,
            deposit=reg_drep.deposit,
        )
        fixture_cache.value = reg_drep

    return reg_drep


@pytest.fixture
def cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
) -> tp.Tuple[clusterlib.ClusterLib, str]:
    return delegation.cluster_and_pool(cluster_manager=cluster_manager)


@pytest.fixture
def payment_addr(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_payment_addr(
        name_template=test_id, cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
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
def custom_drep(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
) -> governance_utils.DRepRegistration:
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_custom_drep(
        name_template=test_id,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        payment_addr=payment_addr,
        caching_key=key,
    )


@pytest.fixture
def payment_addr_wp(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool: tp.Tuple[clusterlib.ClusterLib, str],
) -> clusterlib.AddressRecord:
    cluster, __ = cluster_and_pool
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_payment_addr(
        name_template=test_id, cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


@pytest.fixture
def pool_user_wp(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool: tp.Tuple[clusterlib.ClusterLib, str],
) -> clusterlib.PoolUser:
    cluster, __ = cluster_and_pool
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_pool_user(
        name_template=test_id, cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


@pytest.fixture
def custom_drep_wp(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool: tp.Tuple[clusterlib.ClusterLib, str],
    payment_addr_wp: clusterlib.AddressRecord,
) -> governance_utils.DRepRegistration:
    cluster, __ = cluster_and_pool
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_custom_drep(
        name_template=test_id,
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        payment_addr=payment_addr_wp,
        caching_key=key,
    )


class TestDReps:
    """Tests for DReps."""

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_register_and_retire_drep(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test DRep registration and retirement.

        * register DRep
        * check that DRep was registered
        * retire DRep
        * check that DRep was retired
        * check that deposit was returned to source address
        """
        # pylint: disable=too-many-locals
        temp_template = common.get_test_id(cluster)
        errors_final = []

        # Register DRep

        drep_metadata_url = "https://www.the-drep.com"
        drep_metadata_file = f"{temp_template}_drep_metadata.json"
        drep_metadata_content = {"name": "The DRep", "ranking": "uno"}
        helpers.write_json(out_file=drep_metadata_file, content=drep_metadata_content)
        reqc.cli012.start(url=helpers.get_vcs_link())
        drep_metadata_hash = cluster.g_conway_governance.drep.get_metadata_hash(
            drep_metadata_file=drep_metadata_file
        )

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli008, reqc.cli009, reqc.cli010, reqc.cip021)]
        reg_drep = governance_utils.get_drep_reg_record(
            cluster_obj=cluster,
            name_template=temp_template,
            drep_metadata_url=drep_metadata_url,
            drep_metadata_hash=drep_metadata_hash,
        )
        [r.success() for r in (reqc.cli008, reqc.cli009, reqc.cli010, reqc.cip021)]

        tx_files_reg = clusterlib.TxFiles(
            certificate_files=[reg_drep.registration_cert],
            signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
        )

        tx_output_reg = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_reg",
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files_reg,
            deposit=reg_drep.deposit,
        )

        reg_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_reg)
        assert (
            clusterlib.filter_utxos(utxos=reg_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_reg.txins)
            - tx_output_reg.fee
            - reg_drep.deposit
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        reqc.cli033.start(url=helpers.get_vcs_link())
        reg_drep_state = cluster.g_conway_governance.query.drep_state(
            drep_vkey_file=reg_drep.key_pair.vkey_file
        )
        assert reg_drep_state[0][0]["keyHash"] == reg_drep.drep_id, "DRep was not registered"
        reqc.cli033.success()

        metadata_anchor = reg_drep_state[0][1]["anchor"]
        assert (
            metadata_anchor["dataHash"]
            == drep_metadata_hash
            == "592e53f74765c8c6c97dfda2fd6038236ffc7ad55800592118d9e36ad1c8140d"
        ), "Unexpected metadata hash"
        assert metadata_anchor["url"] == drep_metadata_url, "Unexpected metadata url"
        try:
            dbsync_utils.check_drep_registration(drep=reg_drep, drep_state=reg_drep_state)
        except AssertionError as exc:
            str_exc = str(exc)
            errors_final.append(f"DB-Sync unexpected DRep registration error: {str_exc}")
        reqc.cli012.success()

        # Retire DRep

        reqc.cli011.start(url=helpers.get_vcs_link())
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli011, reqc.cip023)]
        ret_cert = cluster.g_conway_governance.drep.gen_retirement_cert(
            cert_name=temp_template,
            deposit_amt=reg_drep.deposit,
            drep_vkey_file=reg_drep.key_pair.vkey_file,
        )
        [r.success() for r in (reqc.cli011, reqc.cip023)]

        tx_files_ret = clusterlib.TxFiles(
            certificate_files=[ret_cert],
            signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
        )

        tx_output_ret = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_ret",
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files_ret,
            deposit=-reg_drep.deposit,
        )

        reqc.cip024.start(url=helpers.get_vcs_link())
        ret_drep_state = cluster.g_conway_governance.query.drep_state(
            drep_vkey_file=reg_drep.key_pair.vkey_file
        )
        assert not ret_drep_state, "DRep was not retired"
        reqc.cip024.success()

        ret_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_ret)
        assert (
            clusterlib.filter_utxos(utxos=ret_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_ret.txins)
            - tx_output_ret.fee
            + reg_drep.deposit
        ), f"Incorrect balance for source address `{payment_addr.address}`"
        try:
            dbsync_utils.check_drep_deregistration(drep=reg_drep)
        except AssertionError as exc:
            str_exc = str(exc)
            errors_final.append(f"DB-Sync unexpected DRep deregistration error: {str_exc}")
        if errors_final:
            raise AssertionError("\n".join(errors_final))


class TestNegativeDReps:
    """Tests for DReps where we test failing condition."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_no_witness_register_and_retire(  # noqa: C901
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
    ):
        """Test DRep registration and retirement without needing an skey as witness.

        There was a ledger issue that allowed a DRep to be registered without needing
        the corresponding skey witness.

        * try to register DRep without skey, expect failure
        * register DRep
        * check that DRep was registered
        * try to retire DRep without skey, expect failure
        * retire DRep
        * check that DRep was retired
        """
        temp_template = common.get_test_id(cluster)
        errors_final = []

        # Register DRep

        reg_drep = governance_utils.get_drep_reg_record(
            cluster_obj=cluster,
            name_template=temp_template,
        )

        tx_files_reg_missing = clusterlib.TxFiles(
            certificate_files=[reg_drep.registration_cert],
            signing_key_files=[payment_addr.skey_file],
        )

        reg_missing_success = False
        try:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_reg",
                src_address=payment_addr.address,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files_reg_missing,
                deposit=reg_drep.deposit,
            )
        except clusterlib.CLIError as exc:
            str_exc = str(exc)
            if "(MissingVKeyWitnessesUTXOW" not in str_exc:
                errors_final.append(f"Unexpected DRep registration error: {str_exc}")
        else:
            reg_missing_success = True
            errors_final.append("DRep registered without needing an skey")

        if not reg_missing_success:
            tx_files_reg = clusterlib.TxFiles(
                certificate_files=[reg_drep.registration_cert],
                signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
            )

            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_reg",
                src_address=payment_addr.address,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files_reg,
                deposit=reg_drep.deposit,
            )

        reg_drep_state = cluster.g_conway_governance.query.drep_state(
            drep_vkey_file=reg_drep.key_pair.vkey_file
        )
        assert reg_drep_state[0][0]["keyHash"] == reg_drep.drep_id, "DRep was not registered"

        # Retire DRep

        ret_cert = cluster.g_conway_governance.drep.gen_retirement_cert(
            cert_name=temp_template,
            deposit_amt=reg_drep.deposit,
            drep_vkey_file=reg_drep.key_pair.vkey_file,
        )

        tx_files_ret_missing = clusterlib.TxFiles(
            certificate_files=[ret_cert],
            signing_key_files=[payment_addr.skey_file],
        )

        ret_missing_success = False
        try:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_ret",
                src_address=payment_addr.address,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files_ret_missing,
                deposit=-reg_drep.deposit,
            )
        except clusterlib.CLIError as exc:
            str_exc = str(exc)
            if "(MissingVKeyWitnessesUTXOW" not in str_exc:
                errors_final.append(f"Unexpected DRep retirement error: {str_exc}")
        else:
            ret_missing_success = True
            errors_final.append("DRep retired without needing an skey")

        if not ret_missing_success:
            tx_files_ret = clusterlib.TxFiles(
                certificate_files=[ret_cert],
                signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
            )

            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_ret",
                src_address=payment_addr.address,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files_ret,
                deposit=-reg_drep.deposit,
            )

        ret_drep_state = cluster.g_conway_governance.query.drep_state(
            drep_vkey_file=reg_drep.key_pair.vkey_file
        )
        assert not ret_drep_state, "DRep was not retired"

        # Known ledger issue: https://github.com/IntersectMBO/cardano-ledger/issues/3890
        if len(errors_final) == 1 and reg_missing_success:
            blockers.GH(
                issue=3890,
                repo="IntersectMBO/cardano-ledger",
                message=errors_final[0],
                check_on_devel=False,
            ).finish_test()

        if errors_final:
            raise AssertionError("\n".join(errors_final))


class TestDelegDReps:
    """Tests for votes delegation to DReps."""

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("drep", ("always_abstain", "always_no_confidence", "custom"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_dreps_delegation(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        pool_user: clusterlib.PoolUser,
        custom_drep: governance_utils.DRepRegistration,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
        use_build_cmd: bool,
        submit_method: str,
        drep: str,
    ):
        """Test delegating to DReps.

        * register stake address
        * delegate stake to following DReps:

            - always-abstain
            - always-no-confidence
            - custom DRep

        * check that the stake address is registered
        """
        # pylint: disable=too-many-statements,too-many-locals
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        drep_id = custom_drep.drep_id if drep == "custom" else drep

        if drep == "custom":
            reqc_deleg = reqc.cip016
        elif drep == "always_abstain":
            reqc_deleg = reqc.cip017
        elif drep == "always_no_confidence":
            reqc_deleg = reqc.cip018
        else:
            msg = f"Unexpected DRep: {drep}"
            raise ValueError(msg)

        reqc_deleg.start(url=helpers.get_vcs_link())

        # Create stake address registration cert
        reqc.cli027.start(url=helpers.get_vcs_link())
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user.stake.vkey_file,
        )
        reqc.cli027.success()

        # Create vote delegation cert
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli029, reqc.cip022)]
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=pool_user.stake.vkey_file,
            drep_key_hash=custom_drep.drep_id if drep == "custom" else "",
            always_abstain=drep == "always_abstain",
            always_no_confidence=drep == "always_no_confidence",
        )
        [r.success() for r in (reqc.cli029, reqc.cip022)]

        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert],
            signing_key_files=[payment_addr.skey_file, pool_user.stake.skey_file],
        )

        # Make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
            deposit=deposit_amt,
        )

        # Deregister stake address so it doesn't affect stake distribution
        def _deregister():
            with helpers.change_cwd(testfile_temp_dir):
                stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user.stake.address)
                if not stake_addr_info:
                    return

                # Deregister stake address
                reqc.cli028.start(url=helpers.get_vcs_link())
                stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
                    addr_name=f"{temp_template}_addr0",
                    deposit_amt=deposit_amt,
                    stake_vkey_file=pool_user.stake.vkey_file,
                )
                tx_files_dereg = clusterlib.TxFiles(
                    certificate_files=[stake_addr_dereg_cert],
                    signing_key_files=[
                        payment_addr.skey_file,
                        pool_user.stake.skey_file,
                    ],
                )
                withdrawals = (
                    [
                        clusterlib.TxOut(
                            address=pool_user.stake.address,
                            amount=stake_addr_info.reward_account_balance,
                        )
                    ]
                    if stake_addr_info.reward_account_balance
                    else []
                )
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_dereg",
                    src_address=payment_addr.address,
                    use_build_cmd=use_build_cmd,
                    tx_files=tx_files_dereg,
                    withdrawals=withdrawals,
                    deposit=-deposit_amt,
                )
                reqc.cli028.success()

        request.addfinalizer(_deregister)

        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user.stake.address)
        assert (
            stake_addr_info.address
        ), f"Stake address is NOT registered: {pool_user.stake.address}"
        reqc.cli035.start(url=helpers.get_vcs_link())
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep_id
        ), "Votes are NOT delegated to the correct DRep"
        reqc.cli035.success()

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        # Check that stake address is delegated to the correct DRep.
        # This takes one epoch, so test this only for selected combinations of build command
        # and submit method, only when we are running on local testnet, and only if we are not
        # running smoke tests.
        if (
            use_build_cmd
            and submit_method == submit_utils.SubmitMethods.CLI
            and cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL
            and "smoke" not in request.config.getoption("-m")
        ):
            cluster.wait_for_new_epoch(padding_seconds=5)
            deleg_state = clusterlib_utils.get_delegation_state(cluster_obj=cluster)
            stake_addr_hash = cluster.g_stake_address.get_stake_vkey_hash(
                stake_vkey_file=pool_user.stake.vkey_file
            )
            reqc.cip020_01.start(url=helpers.get_vcs_link())
            governance_utils.check_drep_delegation(
                deleg_state=deleg_state, drep_id=drep_id, stake_addr_hash=stake_addr_hash
            )
            reqc.cip020_01.success()

            _url = helpers.get_vcs_link()
            [r.start(url=_url) for r in (reqc.cli034, reqc.cip025)]
            if drep == "custom":
                stake_distrib = cluster.g_conway_governance.query.drep_stake_distribution(
                    drep_key_hash=custom_drep.drep_id
                )
                stake_distrib_vkey = cluster.g_conway_governance.query.drep_stake_distribution(
                    drep_vkey_file=custom_drep.key_pair.vkey_file
                )
                assert (
                    stake_distrib == stake_distrib_vkey
                ), "DRep stake distribution output mismatch"
                assert (
                    len(stake_distrib_vkey) == 1
                ), "Unexpected number of DRep stake distribution records"

                assert (
                    stake_distrib_vkey[0][0] == f"drep-keyHash-{custom_drep.drep_id}"
                ), f"The DRep distribution record doesn't match the DRep ID '{custom_drep.drep_id}'"
            else:
                stake_distrib = cluster.g_conway_governance.query.drep_stake_distribution()

            deleg_amount = cluster.g_query.get_address_balance(pool_user.payment.address)
            governance_utils.check_drep_stake_distribution(
                distrib_state=stake_distrib,
                drep_id=drep_id,
                min_amount=deleg_amount,
            )
            [r.success() for r in (reqc.cli034, reqc.cip025)]

        reqc_deleg.success()

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("drep", ("always_abstain", "always_no_confidence", "custom"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_dreps_and_spo_delegation(
        self,
        cluster_and_pool: tp.Tuple[clusterlib.ClusterLib, str],
        payment_addr_wp: clusterlib.AddressRecord,
        pool_user_wp: clusterlib.PoolUser,
        custom_drep_wp: governance_utils.DRepRegistration,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
        use_build_cmd: bool,
        submit_method: str,
        drep: str,
    ):
        """Test delegating to DRep and SPO using single certificate.

        * register stake address
        * delegate stake to a stake pool and to following DReps:

            - always-abstain
            - always-no-confidence
            - custom DRep

        * check that the stake address is registered and delegated
        """
        cluster, pool_id = cluster_and_pool
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        drep_id = custom_drep_wp.drep_id if drep == "custom" else drep

        # Create stake address registration cert
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user_wp.stake.vkey_file,
        )

        # Create stake and vote delegation cert
        reqc.cli030.start(url=helpers.get_vcs_link())
        deleg_cert = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=pool_user_wp.stake.vkey_file,
            stake_pool_id=pool_id,
            drep_key_hash=custom_drep_wp.drep_id if drep == "custom" else "",
            always_abstain=drep == "always_abstain",
            always_no_confidence=drep == "always_no_confidence",
        )
        reqc.cli030.success()

        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert],
            signing_key_files=[payment_addr_wp.skey_file, pool_user_wp.stake.skey_file],
        )

        # Make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_wp.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
            deposit=deposit_amt,
        )

        # Deregister stake address so it doesn't affect stake distribution
        def _deregister():
            with helpers.change_cwd(testfile_temp_dir):
                stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user_wp.stake.address)
                if not stake_addr_info:
                    return

                # Deregister stake address
                stake_addr_dereg_cert = cluster.g_stake_address.gen_stake_addr_deregistration_cert(
                    addr_name=f"{temp_template}_addr0",
                    deposit_amt=deposit_amt,
                    stake_vkey_file=pool_user_wp.stake.vkey_file,
                )
                tx_files_dereg = clusterlib.TxFiles(
                    certificate_files=[stake_addr_dereg_cert],
                    signing_key_files=[
                        payment_addr_wp.skey_file,
                        pool_user_wp.stake.skey_file,
                    ],
                )
                withdrawals = (
                    [
                        clusterlib.TxOut(
                            address=pool_user_wp.stake.address,
                            amount=stake_addr_info.reward_account_balance,
                        )
                    ]
                    if stake_addr_info.reward_account_balance
                    else []
                )
                clusterlib_utils.build_and_submit_tx(
                    cluster_obj=cluster,
                    name_template=f"{temp_template}_dereg",
                    src_address=payment_addr_wp.address,
                    use_build_cmd=use_build_cmd,
                    tx_files=tx_files_dereg,
                    withdrawals=withdrawals,
                    deposit=-deposit_amt,
                )

        request.addfinalizer(_deregister)

        # Check that the stake address was registered and delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user_wp.stake.address)
        assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
        assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep_id
        ), "Votes are NOT delegated to the correct DRep"

        # Check the expected balance
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr_wp.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{payment_addr_wp.address}`"

        # Check that stake address is delegated to the correct DRep.
        # This takes one epoch, so test this only for selected combinations of build command
        # and submit method, only when we are running on local testnet, and only if we are not
        # running smoke tests.
        if (
            use_build_cmd
            and submit_method == submit_utils.SubmitMethods.CLI
            and cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.LOCAL
            and "smoke" not in request.config.getoption("-m")
        ):
            cluster.wait_for_new_epoch(padding_seconds=5)
            deleg_state = clusterlib_utils.get_delegation_state(cluster_obj=cluster)
            stake_addr_hash = cluster.g_stake_address.get_stake_vkey_hash(
                stake_vkey_file=pool_user_wp.stake.vkey_file
            )
            governance_utils.check_drep_delegation(
                deleg_state=deleg_state, drep_id=drep_id, stake_addr_hash=stake_addr_hash
            )
