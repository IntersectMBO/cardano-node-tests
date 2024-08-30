"""Tests for Conway governance DRep functionality."""

import binascii
import dataclasses
import hashlib
import json
import logging
import pathlib as pl
import pickle
import typing as tp

import allure
import cbor2
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import delegation
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.tests.tests_conway import conway_common
from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.CONWAY,
    reason="runs only with Tx era >= Conway",
)


@dataclasses.dataclass(frozen=True, order=True)
class DRepStateRecord:
    epoch_no: int
    id: str
    drep_state: governance_utils.DRepStateT


@dataclasses.dataclass(frozen=True, order=True)
class DRepRatRecord:
    id: str
    ratified: bool


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
    )
    return pool_user


def create_drep(
    name_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
) -> governance_utils.DRepRegistration:
    """Create a DRep."""
    reg_drep = governance_utils.get_drep_reg_record(
        cluster_obj=cluster_obj,
        name_template=name_template,
    )

    tx_files_reg = clusterlib.TxFiles(
        certificate_files=[reg_drep.registration_cert],
        signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
    )

    clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_drep_reg",
        src_address=payment_addr.address,
        submit_method=submit_utils.SubmitMethods.CLI,
        use_build_cmd=True,
        tx_files=tx_files_reg,
        deposit=reg_drep.deposit,
    )

    return reg_drep


def get_custom_drep(
    name_template: str,
    cluster_manager: cluster_management.ClusterManager,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    caching_key: str,
) -> governance_utils.DRepRegistration:
    """Create a custom DRep and cache it."""
    if cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.LOCAL:
        pytest.skip("runs only on local cluster")

    with cluster_manager.cache_fixture(key=caching_key) as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        reg_drep = create_drep(
            name_template=name_template,
            cluster_obj=cluster_obj,
            payment_addr=payment_addr,
        )
        fixture_cache.value = reg_drep

    return reg_drep


@pytest.fixture
def cluster_and_pool_and_rewards(
    cluster_manager: cluster_management.ClusterManager,
) -> tp.Tuple[clusterlib.ClusterLib, str]:
    return delegation.cluster_and_pool(
        cluster_manager=cluster_manager, use_resources=[cluster_management.Resources.REWARDS]
    )


@pytest.fixture
def cluster_rewards(
    cluster_manager: cluster_management.ClusterManager,
) -> clusterlib.ClusterLib:
    return cluster_manager.get(lock_resources=[cluster_management.Resources.REWARDS])


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
        name_template=f"custom_drep_{test_id}",
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        payment_addr=payment_addr,
        caching_key=key,
    )


@pytest.fixture
def payment_addr_wpr(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool_and_rewards: tp.Tuple[clusterlib.ClusterLib, str],
) -> clusterlib.AddressRecord:
    cluster, __ = cluster_and_pool_and_rewards
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_payment_addr(
        name_template=test_id, cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


@pytest.fixture
def pool_user_wpr(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool_and_rewards: tp.Tuple[clusterlib.ClusterLib, str],
) -> clusterlib.PoolUser:
    cluster, __ = cluster_and_pool_and_rewards
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_pool_user(
        name_template=test_id, cluster_manager=cluster_manager, cluster_obj=cluster, caching_key=key
    )


@pytest.fixture
def custom_drep_wpr(
    cluster_manager: cluster_management.ClusterManager,
    cluster_and_pool_and_rewards: tp.Tuple[clusterlib.ClusterLib, str],
    payment_addr_wpr: clusterlib.AddressRecord,
) -> governance_utils.DRepRegistration:
    cluster, __ = cluster_and_pool_and_rewards
    test_id = common.get_test_id(cluster)
    key = helpers.get_current_line_str()
    return get_custom_drep(
        name_template=f"custom_drep_{test_id}",
        cluster_manager=cluster_manager,
        cluster_obj=cluster,
        payment_addr=payment_addr_wpr,
        caching_key=key,
    )


@pytest.fixture
def payment_addr_rewards(
    cluster_manager: cluster_management.ClusterManager,
    cluster_rewards: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    test_id = common.get_test_id(cluster_rewards)
    key = helpers.get_current_line_str()
    return get_payment_addr(
        name_template=test_id,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_rewards,
        caching_key=key,
    )


@pytest.fixture
def pool_user_rewards(
    cluster_manager: cluster_management.ClusterManager,
    cluster_rewards: clusterlib.ClusterLib,
) -> clusterlib.PoolUser:
    test_id = common.get_test_id(cluster_rewards)
    key = helpers.get_current_line_str()
    return get_pool_user(
        name_template=test_id,
        cluster_manager=cluster_manager,
        cluster_obj=cluster_rewards,
        caching_key=key,
    )


@pytest.fixture
def custom_drep_rewards(
    cluster_manager: cluster_management.ClusterManager,
    cluster_rewards: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
) -> governance_utils.DRepRegistration:
    test_id = common.get_test_id(cluster_rewards)
    key = helpers.get_current_line_str()
    return get_custom_drep(
        name_template=f"custom_drep_{test_id}",
        cluster_manager=cluster_manager,
        cluster_obj=cluster_rewards,
        payment_addr=payment_addr,
        caching_key=key,
    )


class TestDReps:
    """Tests for DReps."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_drep_id_is_blake2b_224_of_drep_vkey(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Test proper drep id is being generated.

        * Register a drep
        * Hash drep vkey using blake2b_224
        * Check drep ID generated from cli is same as blake2b_224 hash of drep vkey
        """
        reqc.cip085.start(url=helpers.get_vcs_link())
        temp_template = common.get_test_id(cluster)
        drep_metadata_url = "https://www.the-drep.com"
        drep_metadata_file = f"{temp_template}_drep_metadata.json"
        drep_metadata_content = {"name": "The DRep", "ranking": "uno"}
        helpers.write_json(out_file=drep_metadata_file, content=drep_metadata_content)
        drep_metadata_hash = cluster.g_conway_governance.drep.get_metadata_hash(
            drep_metadata_file=drep_metadata_file
        )
        # Get a drep registration record
        reg_drep = governance_utils.get_drep_reg_record(
            cluster_obj=cluster,
            name_template=temp_template,
            drep_metadata_url=drep_metadata_url,
            drep_metadata_hash=drep_metadata_hash,
        )
        vkey_file_path = reg_drep.key_pair.vkey_file
        # Get drep vkey from vkey file
        with open(vkey_file_path) as vkey_file:
            vkey_file_json = json.loads(vkey_file.read())
            cbor_hex = vkey_file_json["cborHex"]
            cbor_binary = binascii.unhexlify(cbor_hex)
            decoded_data = cbor2.loads(cbor_binary)
            blake2b_224 = hashlib.blake2b(digest_size=28)
            blake2b_224.update(decoded_data)
            # Obtain blake2b_224 hash of drep vkey
            hash_digest = blake2b_224.hexdigest()
            assert reg_drep.drep_id == hash_digest, "Drep ID hash is not blake2b_224."
            reqc.cip085.success()

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

        drep_metadata_file = DATA_DIR / "drep_metadata.json"

        # Register DRep
        drep_metadata_url = "https://tinyurl.com/w7vd3ek6"
        reqc.cli012.start(url=helpers.get_vcs_link())
        drep_metadata_hash = cluster.g_conway_governance.drep.get_metadata_hash(
            drep_metadata_file=drep_metadata_file
        )
        with open(drep_metadata_file, encoding="utf-8") as anchor_fp:
            drep_metadata_content = json.load(anchor_fp)

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
            == "18b4b10150eab04ba66c8f9cb497ff05c6c31b9c9825388481c1790ce76b6b90"
        ), "Unexpected metadata hash"
        assert metadata_anchor["url"] == drep_metadata_url, "Unexpected metadata url"
        try:
            _url = helpers.get_vcs_link()
            [r.start(url=_url) for r in (reqc.db001, reqc.db006)]
            drep_data = dbsync_utils.check_drep_registration(
                drep=reg_drep, drep_state=reg_drep_state
            )
            [r.success() for r in (reqc.db001, reqc.db006)]

            def _query_func():
                dbsync_utils.check_off_chain_drep_registration(
                    drep_data=drep_data, metadata=drep_metadata_content
                )

            dbsync_utils.retry_query(query_func=_query_func, timeout=300)

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
            issues.ledger_3890.finish_test()

        if errors_final:
            raise AssertionError("\n".join(errors_final))

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_no_multiple_delegation(
        self,
        cluster_rewards: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addr_rewards: clusterlib.AddressRecord,
        pool_user_rewards: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Test No multiple delegation to different dreps.

        * Create 2 Dreps
        * Create vote delegation certifcate to both dreps
        * Submit both certificates
        * check that the Drep certificate placed at last of the certificates is delegated to
        """
        cluster = cluster_rewards
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        key1 = helpers.get_current_line_str()
        drep1 = get_custom_drep(
            name_template=f"custom_drep_1_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr_rewards,
            caching_key=key1,
        )

        key2 = helpers.get_current_line_str()
        drep2 = get_custom_drep(
            name_template=f"custom_drep_2_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr_rewards,
            caching_key=key2,
        )

        # Create stake address registration cert
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
        )

        reqc.cip087.start(url=helpers.get_vcs_link())
        # Create vote delegation cert for drep 1
        deleg_cert_1 = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr1",
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
            drep_key_hash=drep1.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        # Create vote delegation cert for drep 2
        deleg_cert_2 = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr2",
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
            drep_key_hash=drep2.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        # Submit two vote delegation certificate at once
        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert_2, deleg_cert_1],
            signing_key_files=[payment_addr_rewards.skey_file, pool_user_rewards.stake.skey_file],
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_rewards.address,
            use_build_cmd=True,
            tx_files=tx_files,
            deposit=deposit_amt,
        )

        # Deregister stake address so it doesn't affect stake distribution
        def _deregister():
            with helpers.change_cwd(testfile_temp_dir):
                clusterlib_utils.deregister_stake_address(
                    cluster_obj=cluster,
                    pool_user=pool_user_rewards,
                    name_template=temp_template,
                    deposit_amt=deposit_amt,
                )

        request.addfinalizer(_deregister)

        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user_rewards.stake.address)

        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep1.drep_id
        ), "Votes are NOT delegated to the correct DRep 1 placed at last of certificates list."
        reqc.cip087.success()

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
        """Test No voting delegation without registering stake address first.

        * Use a wallet without registered stake address
        * Create vote delegation certifcate using unregistered wallet stake key
        * Submit the certificate
        * Expect error StakeKeyNotRegisteredDELEG
        """
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()

        reqc.cip088.start(url=helpers.get_vcs_link())
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

        # Expecting error as stake address is not registered
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
        reqc.cip088.success()

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_drep_no_retirement_before_register(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test No Drep retirement before register.

        * Create a retirement certificate without registering
        * Submit certificate
        * check it is not possible to retire before register
        """
        temp_template = common.get_test_id(cluster)
        drep_keys = cluster.g_conway_governance.drep.gen_key_pair(
            key_name=temp_template, destination_dir="."
        )
        deposit = cluster.conway_genesis["dRepDeposit"]

        reqc.cip089.start(url=helpers.get_vcs_link())
        ret_cert = cluster.g_conway_governance.drep.gen_retirement_cert(
            cert_name=temp_template,
            deposit_amt=deposit,
            drep_vkey_file=drep_keys.vkey_file,
        )
        tx_files_ret = clusterlib.TxFiles(
            certificate_files=[ret_cert],
            signing_key_files=[payment_addr.skey_file, drep_keys.skey_file],
        )

        # Expecting error for both cases as drep is not registered
        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_reg2",
                src_address=payment_addr.address,
                submit_method=submit_method,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files_ret,
                deposit=deposit,
            )

        err_msg = str(excinfo.value)
        assert "ConwayDRepNotRegistered" in err_msg, err_msg
        reqc.cip089.success()

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
        """Test Drep cannot be registered multiple time.

        * Generate drep keys
        * Create a drep registration certificate
        * Submit the registration certificate twice
        * Expect ConwayDRepAlreadyRegistered on the second time
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
        reqc.cip090.start(url=helpers.get_vcs_link())
        # Obtain drep registration certificate
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

        # Submit drep registration certificate
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

        # Expecting error as drep is already registered
        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{temp_template}_reg2",
                src_address=payment_addr.address,
                submit_method=submit_method,
                use_build_cmd=use_build_cmd,
                tx_files=tx_files_reg,
                deposit=deposit_amt,
            )

        err_msg = str(excinfo.value)
        assert "ConwayDRepAlreadyRegistered" in err_msg, err_msg
        reqc.cip090.success()


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
        cluster_rewards: clusterlib.ClusterLib,
        payment_addr_rewards: clusterlib.AddressRecord,
        pool_user_rewards: clusterlib.PoolUser,
        custom_drep_rewards: governance_utils.DRepRegistration,
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
        cluster = cluster_rewards
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        drep_id = custom_drep_rewards.drep_id if drep == "custom" else drep

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
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
        )
        reqc.cli027.success()

        # Create vote delegation cert
        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli029, reqc.cip022)]
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
            drep_key_hash=custom_drep_rewards.drep_id if drep == "custom" else "",
            always_abstain=drep == "always_abstain",
            always_no_confidence=drep == "always_no_confidence",
        )
        [r.success() for r in (reqc.cli029, reqc.cip022)]

        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert],
            signing_key_files=[payment_addr_rewards.skey_file, pool_user_rewards.stake.skey_file],
        )

        # Make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_rewards.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
            deposit=deposit_amt,
        )

        # Deregister stake address so it doesn't affect stake distribution
        def _deregister():
            with helpers.change_cwd(testfile_temp_dir):
                reqc.cli028.start(url=helpers.get_vcs_link())
                clusterlib_utils.deregister_stake_address(
                    cluster_obj=cluster,
                    pool_user=pool_user_rewards,
                    name_template=temp_template,
                    deposit_amt=deposit_amt,
                )
                reqc.cli028.success()

        request.addfinalizer(_deregister)

        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user_rewards.stake.address)
        assert (
            stake_addr_info.address
        ), f"Stake address is NOT registered: {pool_user_rewards.stake.address}"
        reqc.cli035.start(url=helpers.get_vcs_link())
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep_id
        ), "Votes are NOT delegated to the correct DRep"
        reqc.cli035.success()

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr_rewards.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{payment_addr_rewards.address}`"

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
                stake_vkey_file=pool_user_rewards.stake.vkey_file
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
                    drep_key_hash=custom_drep_rewards.drep_id
                )
                stake_distrib_vkey = cluster.g_conway_governance.query.drep_stake_distribution(
                    drep_vkey_file=custom_drep_rewards.key_pair.vkey_file
                )
                assert (
                    stake_distrib == stake_distrib_vkey
                ), "DRep stake distribution output mismatch"
                assert (
                    len(stake_distrib_vkey) == 1
                ), "Unexpected number of DRep stake distribution records"

                assert stake_distrib_vkey[0][0] == f"drep-keyHash-{custom_drep_rewards.drep_id}", (
                    "The DRep distribution record doesn't match the DRep ID "
                    f"'{custom_drep_rewards.drep_id}'"
                )
            else:
                stake_distrib = cluster.g_conway_governance.query.drep_stake_distribution()

            deleg_amount = cluster.g_query.get_address_balance(pool_user_rewards.payment.address)
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
        cluster_and_pool_and_rewards: tp.Tuple[clusterlib.ClusterLib, str],
        payment_addr_wpr: clusterlib.AddressRecord,
        pool_user_wpr: clusterlib.PoolUser,
        custom_drep_wpr: governance_utils.DRepRegistration,
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
        cluster, pool_id = cluster_and_pool_and_rewards
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        drep_id = custom_drep_wpr.drep_id if drep == "custom" else drep

        # Create stake address registration cert
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user_wpr.stake.vkey_file,
        )

        # Create stake and vote delegation cert
        reqc.cli030.start(url=helpers.get_vcs_link())
        deleg_cert = cluster.g_stake_address.gen_stake_and_vote_delegation_cert(
            addr_name=f"{temp_template}_addr0",
            stake_vkey_file=pool_user_wpr.stake.vkey_file,
            stake_pool_id=pool_id,
            drep_key_hash=custom_drep_wpr.drep_id if drep == "custom" else "",
            always_abstain=drep == "always_abstain",
            always_no_confidence=drep == "always_no_confidence",
        )
        reqc.cli030.success()

        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert],
            signing_key_files=[payment_addr_wpr.skey_file, pool_user_wpr.stake.skey_file],
        )

        # Make sure we have enough time to finish the registration/delegation in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
        )

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_wpr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
            deposit=deposit_amt,
        )

        # Deregister stake address so it doesn't affect stake distribution
        def _deregister():
            with helpers.change_cwd(testfile_temp_dir):
                clusterlib_utils.deregister_stake_address(
                    cluster_obj=cluster,
                    pool_user=pool_user_wpr,
                    name_template=temp_template,
                    deposit_amt=deposit_amt,
                )

        request.addfinalizer(_deregister)

        # Check that the stake address was registered and delegated
        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user_wpr.stake.address)
        assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
        assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep_id
        ), "Votes are NOT delegated to the correct DRep"

        # Check the expected balance
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=payment_addr_wpr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
        ), f"Incorrect balance for source address `{payment_addr_wpr.address}`"

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
                stake_vkey_file=pool_user_wpr.stake.vkey_file
            )
            governance_utils.check_drep_delegation(
                deleg_state=deleg_state, drep_id=drep_id, stake_addr_hash=stake_addr_hash
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    def test_cli_drep_status_consistency(
        self,
        cluster_use_dreps: governance_utils.GovClusterT,
    ):
        """Test consistency of `cardano-cli conway query drep-state` output.

        * List status of all DReps
        * List status of selected DReps
        * Compare the output to check that it is consistent
        """
        cluster, governance_data = cluster_use_dreps
        common.get_test_id(cluster)

        def _get_drep_rec(
            drep_state: governance_utils.DRepStateT,
        ) -> tp.Dict[str, tp.Dict[str, tp.Any]]:
            return {drep[0]["keyHash"]: drep[1] for drep in drep_state}

        drep_states_all = _get_drep_rec(drep_state=cluster.g_conway_governance.query.drep_state())
        drep_states_gov_data = _get_drep_rec(
            drep_state=[
                cluster.g_conway_governance.query.drep_state(drep_key_hash=drep.drep_id)[0]
                for drep in governance_data.dreps_reg
            ]
        )

        first_key = next(iter(drep_states_gov_data))
        if drep_states_all[first_key]["expiry"] != drep_states_gov_data[first_key]["expiry"]:
            issues.ledger_4349.finish_test()

        for key, rec in drep_states_gov_data.items():
            assert key in drep_states_all, f"DRep '{key}' not found in DRep state"
            assert rec == drep_states_all[key], f"DRep '{key}' state mismatch"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_change_delegation(
        self,
        cluster_rewards: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addr_rewards: clusterlib.AddressRecord,
        pool_user_rewards: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Test Change delegation to different dreps.

        * Create 2 Dreps
        * Create vote delegation certifcate for first drep
        * Submit certificate
        * check that the delegation is of correct drep id
        * Change delegation to drep2 and submit certificate
        * Check vote delegation is updated to second drep
        """
        cluster = cluster_rewards
        temp_template = common.get_test_id(cluster)
        deposit_amt = cluster.g_query.get_address_deposit()
        key1 = helpers.get_current_line_str()
        # Get first drep
        drep1 = get_custom_drep(
            name_template=f"custom_drep_1_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr_rewards,
            caching_key=key1,
        )

        key2 = helpers.get_current_line_str()
        # Get second drep
        drep2 = get_custom_drep(
            name_template=f"custom_drep_2_{temp_template}",
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            payment_addr=payment_addr_rewards,
            caching_key=key2,
        )

        # Create stake address registration cert
        reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=deposit_amt,
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
        )

        # Create vote delegation cert
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr1",
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
            drep_key_hash=drep1.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[reg_cert, deleg_cert],
            signing_key_files=[payment_addr_rewards.skey_file, pool_user_rewards.stake.skey_file],
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_rewards.address,
            use_build_cmd=True,
            tx_files=tx_files,
            deposit=deposit_amt,
        )

        # Deregister stake address so it doesn't affect stake distribution
        def _deregister():
            with helpers.change_cwd(testfile_temp_dir):
                clusterlib_utils.deregister_stake_address(
                    cluster_obj=cluster,
                    pool_user=pool_user_rewards,
                    name_template=temp_template,
                    deposit_amt=deposit_amt,
                )

        request.addfinalizer(_deregister)

        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user_rewards.stake.address)
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep1.drep_id
        ), "Votes are NOT delegated to the correct DRep 1"

        reqc.cip086.start(url=helpers.get_vcs_link())
        # Change delegation to drep2
        deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
            addr_name=f"{temp_template}_addr2",
            stake_vkey_file=pool_user_rewards.stake.vkey_file,
            drep_key_hash=drep2.drep_id,
            always_abstain=False,
            always_no_confidence=False,
        )

        tx_files = clusterlib.TxFiles(
            certificate_files=[deleg_cert],
            signing_key_files=[payment_addr_rewards.skey_file, pool_user_rewards.stake.skey_file],
        )

        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addr_rewards.address,
            use_build_cmd=True,
            tx_files=tx_files,
            deposit=deposit_amt,
        )
        stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user_rewards.stake.address)
        assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
            drep_id=drep2.drep_id
        ), "Votes are NOT changed to the correct DRep 2"
        reqc.cip086.success()


class TestDRepActivity:
    """Tests for DReps activity."""

    @pytest.fixture
    def pool_user_lg(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_governance: governance_utils.GovClusterT,
    ) -> clusterlib.PoolUser:
        """Create a pool user for "lock governance".

        This fixture is NOT cached, as it is used only in one test.
        """
        cluster, __ = cluster_lock_governance
        name_template = common.get_test_id(cluster)
        return conway_common.get_registered_pool_user(
            cluster_manager=cluster_manager,
            name_template=name_template,
            cluster_obj=cluster,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.order(5)
    @pytest.mark.long
    def test_drep_inactivity(  # noqa: C901
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_lock_governance: governance_utils.GovClusterT,
        pool_user_lg: clusterlib.PoolUser,
    ):
        """Test DRep inactivity.

        * Create the first DRep and delegate to it.
        * Update the `dRepActivity` parameter to `1`.
        * Create the second DRep and delegate to it.
        * Update DRep activity again so there is a proposal to vote for. The newly created DReps
          will not vote. The action will not be ratified, because the newly created DReps didn't
          vote and their delegated stake is > 51% (threshold).
        * Update DRep activity again so there is a proposal to vote for. The newly created DRep1
          will vote. The action will be ratified, because the newly created DRep1 voted and
          together with the original DReps their delegated stake is > 51% (threshold).
        * Update DRep activity again so there is a proposal to vote for. The newly created DReps
          will not vote. The action will be ratified, because the newly created DReps are
          inactive and so their delegated stake does not count towards the active voting stake.
        * Wait for another epoch without submitting any proposal, to see if "expire" counters
          are incremented.
        * Update DRep activity again so there is a proposal to vote for. The newly created DRep2
          will vote. The action will be ratified, because the newly created DRep2 voted and
          together with the original DReps their delegated stake is > 51% (threshold).
        * Check DRep activity records using saved DRep status data.
        """
        cluster, governance_data = cluster_lock_governance
        temp_template = common.get_test_id(cluster)

        if conway_common.is_in_bootstrap(cluster_obj=cluster):
            pytest.skip("Cannot run in bootstrap period.")

        deposit_amt = cluster.g_query.get_address_deposit()

        # Saved DRep records
        drep1_state: tp.Dict[str, DRepStateRecord] = {}
        drep2_state: tp.Dict[str, DRepStateRecord] = {}
        rat_records: tp.Dict[str, DRepRatRecord] = {}

        # Register and delegate stake address
        def _delegate_addr(
            name_template: str,
            drep_reg: governance_utils.DRepRegistration,
            pool_user: clusterlib.PoolUser,
        ) -> None:
            # Create stake address registration cert
            reg_cert = cluster.g_stake_address.gen_stake_addr_registration_cert(
                addr_name=name_template,
                deposit_amt=deposit_amt,
                stake_vkey_file=pool_user.stake.vkey_file,
            )

            # Create vote delegation cert
            deleg_cert = cluster.g_stake_address.gen_vote_delegation_cert(
                addr_name=name_template,
                stake_vkey_file=pool_user.stake.vkey_file,
                drep_key_hash=drep_reg.drep_id,
            )

            tx_files = clusterlib.TxFiles(
                certificate_files=[reg_cert, deleg_cert],
                signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
            )

            # Make sure we have enough time to finish the registration/delegation in one epoch
            clusterlib_utils.wait_for_epoch_interval(
                cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_LEDGER_STATE
            )

            tx_output = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=name_template,
                src_address=pool_user.payment.address,
                use_build_cmd=True,
                tx_files=tx_files,
                deposit=deposit_amt,
            )

            stake_addr_info = cluster.g_query.get_stake_addr_info(pool_user.stake.address)
            assert (
                stake_addr_info.address
            ), f"Stake address is NOT registered: {pool_user.stake.address}"
            assert stake_addr_info.vote_delegation == governance_utils.get_drep_cred_name(
                drep_id=drep_reg.drep_id
            ), "Votes are NOT delegated to the correct DRep"

            out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
            assert (
                clusterlib.filter_utxos(utxos=out_utxos, address=pool_user.payment.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - deposit_amt
            ), f"Incorrect balance for source address `{pool_user.payment.address}`"

            # Check that stake address is delegated to the correct DRep.
            cluster.wait_for_new_epoch(padding_seconds=5)
            deleg_state = clusterlib_utils.get_delegation_state(cluster_obj=cluster)
            stake_addr_hash = cluster.g_stake_address.get_stake_vkey_hash(
                stake_vkey_file=pool_user.stake.vkey_file
            )
            governance_utils.check_drep_delegation(
                deleg_state=deleg_state,
                drep_id=drep_reg.drep_id,
                stake_addr_hash=stake_addr_hash,
            )

        def _update_drep_activity(
            governance_data: governance_utils.GovernanceRecords,
            action_id: str,
        ) -> str:
            anchor_url = f"http://www.drep-activity-{action_id}.com"
            anchor_data_hash = cluster.g_conway_governance.get_anchor_data_hash(text=anchor_url)
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )

            proposals = [
                clusterlib_utils.UpdateProposal(
                    arg="--drep-activity",
                    value=1,
                    name="dRepActivity",
                ),
            ]

            prop_rec = conway_common.propose_pparams_update(
                cluster_obj=cluster,
                name_template=f"{temp_template}_{action_id}_drep_activity",
                anchor_url=anchor_url,
                anchor_data_hash=anchor_data_hash,
                pool_user=pool_user_lg,
                proposals=proposals,
                prev_action_rec=prev_action_rec,
            )

            votes_cc = [
                cluster.g_conway_governance.vote.create_committee(
                    vote_name=f"{temp_template}_{action_id}_drep_activity_cc{i}",
                    action_txid=prop_rec.action_txid,
                    action_ix=prop_rec.action_ix,
                    vote=clusterlib.Votes.YES,
                    cc_hot_vkey_file=m.hot_keys.hot_vkey_file,
                )
                for i, m in enumerate(governance_data.cc_key_members, start=1)
            ]
            votes_drep = [
                cluster.g_conway_governance.vote.create_drep(
                    vote_name=f"{temp_template}_{action_id}_drep_activity_drep{i}",
                    action_txid=prop_rec.action_txid,
                    action_ix=prop_rec.action_ix,
                    vote=clusterlib.Votes.YES,
                    drep_vkey_file=d.key_pair.vkey_file,
                )
                for i, d in enumerate(governance_data.dreps_reg, start=1)
            ]

            votes: tp.List[governance_utils.VotesAllT] = [*votes_cc, *votes_drep]
            vote_keys = [
                *[r.hot_keys.hot_skey_file for r in governance_data.cc_key_members],
                *[r.key_pair.skey_file for r in governance_data.dreps_reg],
            ]

            conway_common.submit_vote(
                cluster_obj=cluster,
                name_template=f"{temp_template}_{action_id}_drep_activity",
                payment_addr=pool_user_lg.payment,
                votes=votes,
                keys=vote_keys,
                use_build_cmd=True,
            )

            return prop_rec.action_txid

        def _check_ratification(
            action_txid: str,
            action_id: str,
        ):
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            rat_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=rat_gov_state,
                name_template=f"{temp_template}_{action_id}_drep_activity_rat_{_cur_epoch}",
            )
            rat_action = governance_utils.lookup_ratified_actions(
                gov_state=rat_gov_state, action_txid=action_txid
            )

            rat_records[action_id] = DRepRatRecord(id=action_id, ratified=bool(rat_action))

        def _check_enactment(
            action_txid: str,
            action_id: str,
        ):
            _cur_epoch = cluster.wait_for_new_epoch(padding_seconds=5)
            enact_gov_state = cluster.g_conway_governance.query.gov_state()
            conway_common.save_gov_state(
                gov_state=enact_gov_state,
                name_template=f"{temp_template}_{action_id}_drep_activity_enact_{_cur_epoch}",
            )
            prev_action_rec = governance_utils.get_prev_action(
                action_type=governance_utils.PrevGovActionIds.PPARAM_UPDATE,
                gov_state=cluster.g_conway_governance.query.gov_state(),
            )
            assert (
                action_txid == prev_action_rec.txid
            ), f"Unexpected action txid: {prev_action_rec.txid}"

        def _save_drep_states(
            id: str,
            drep1: tp.Optional[governance_utils.DRepRegistration],
            drep2: tp.Optional[governance_utils.DRepRegistration],
        ) -> None:
            _cur_epoch = cluster.g_query.get_epoch()
            if drep1 is not None:
                _drep_state = cluster.g_conway_governance.query.drep_state(
                    drep_vkey_file=drep1.key_pair.vkey_file
                )
                assert id not in drep1_state
                drep1_state[id] = DRepStateRecord(
                    epoch_no=_cur_epoch,
                    id=id,
                    drep_state=_drep_state,
                )
                conway_common.save_drep_state(
                    drep_state=_drep_state,
                    name_template=f"{temp_template}_drep1_{id}_{_cur_epoch}",
                )
            if drep2 is not None:
                _drep_state = cluster.g_conway_governance.query.drep_state(
                    drep_vkey_file=drep2.key_pair.vkey_file
                )
                assert id not in drep2_state
                drep2_state[id] = DRepStateRecord(
                    epoch_no=_cur_epoch,
                    id=id,
                    drep_state=_drep_state,
                )
                conway_common.save_drep_state(
                    drep_state=_drep_state,
                    name_template=f"{temp_template}_drep2_{id}_{_cur_epoch}",
                )

        def _dump_records() -> None:
            """Save debugging data in case of test failure."""
            with open(f"{temp_template}_drep_records.pickle", "wb") as out_data:
                _state = {"drep1": drep1_state, "drep2": drep2_state, "rat_records": rat_records}
                pickle.dump(_state, out_data)

        def _check_records() -> tp.List[blockers.GH]:
            found_issues = []

            assert drep1_state, "No DRep1 states"
            assert drep2_state, "No DRep2 states"

            drep1_init_expiry = drep1_state["created_drep1"].drep_state[0][1]["expiry"]
            assert (
                drep1_init_expiry > drep1_state["created_drep1"].epoch_no + 5
            ), "Unexpected DRep1 init expiry"
            assert (
                drep1_state["delegated_drep1"].drep_state[0][1]["expiry"] > drep1_init_expiry
            ), "DRep1 expiry was not updated"

            assert governance_utils.is_drep_active(
                cluster_obj=cluster,
                drep_state=drep1_state["voted_drep1_voted"].drep_state,
                epoch=drep1_state["voted_drep1_voted"].epoch_no,
            ), "DRep1 is not active"
            assert not governance_utils.is_drep_active(
                cluster_obj=cluster,
                drep_state=drep1_state["voted_orig2_voted"].drep_state,
                epoch=drep1_state["voted_orig2_voted"].epoch_no,
            ), "DRep1 is still active"
            assert not governance_utils.is_drep_active(
                cluster_obj=cluster,
                drep_state=drep1_state["voted_orig2_ratified"].drep_state,
                epoch=drep1_state["voted_orig2_ratified"].epoch_no,
            ), "DRep1 is still active"
            assert not governance_utils.is_drep_active(
                cluster_obj=cluster,
                drep_state=drep1_state["voted_drep2_voted"].drep_state,
                epoch=drep1_state["voted_drep2_voted"].epoch_no,
            ), "DRep1 is still active"

            drep2_init_expiry = drep2_state["created_drep2"].drep_state[0][1]["expiry"]
            assert (
                drep2_init_expiry < drep2_state["created_drep2"].epoch_no + 3
            ), "Unexpected DRep2 init expiry"
            assert (
                drep2_state["delegated_drep2"].drep_state[0][1]["expiry"] > drep2_init_expiry
            ), "DRep2 expiry was not updated"

            assert not governance_utils.is_drep_active(
                cluster_obj=cluster,
                drep_state=drep2_state["voted_orig2_voted"].drep_state,
                epoch=drep2_state["voted_orig2_voted"].epoch_no,
            ), "DRep2 is still active"
            assert not governance_utils.is_drep_active(
                cluster_obj=cluster,
                drep_state=drep2_state["no_proposal"].drep_state,
                epoch=drep2_state["no_proposal"].epoch_no,
            ), "DRep2 is still active"
            assert governance_utils.is_drep_active(
                cluster_obj=cluster,
                drep_state=drep2_state["voted_drep2_voted"].drep_state,
                epoch=drep2_state["voted_drep2_voted"].epoch_no,
            ), "DRep2 is not active"

            assert not rat_records["voted_orig1_ratification"].ratified, "Action was not ratified"
            assert rat_records["voted_drep1_ratification"].ratified, "Action was not ratified"
            assert rat_records["voted_orig2_ratification"].ratified, "Action was not ratified"
            assert rat_records["voted_drep2_ratification"].ratified, "Action was not ratified"

            if (
                drep1_state["voted_orig2_ratified"].drep_state[0][1]["expiry"]
                > drep1_state["voted_orig2_voted"].drep_state[0][1]["expiry"]
            ):
                found_issues.append(issues.ledger_4346)

            return found_issues

        # Create stake addresses for votes delegation and fund them
        drep_users = clusterlib_utils.create_pool_users(
            cluster_obj=cluster,
            name_template=f"{temp_template}_pool_user",
            no_of_addr=2,
        )
        clusterlib_utils.fund_from_faucet(
            *drep_users,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            # Add a lot of funds so no action can be ratified without the new DReps
            amount=10_000_000_000_000,
        )

        # Testnet respin is needed after this point
        cluster_manager.set_needs_respin()

        # Create the first DRep
        custom_drep1 = create_drep(
            name_template=f"{temp_template}_drep1",
            cluster_obj=cluster,
            payment_addr=pool_user_lg.payment,
        )
        _save_drep_states(drep1=custom_drep1, drep2=None, id="created_drep1")
        _delegate_addr(
            name_template=f"{temp_template}_pool_user1_deleg",
            drep_reg=custom_drep1,
            pool_user=drep_users[0],
        )
        _save_drep_states(drep1=custom_drep1, drep2=None, id="delegated_drep1")

        # Add the first DRep to the governance data
        governance_data_drep1 = dataclasses.replace(
            governance_data, dreps_reg=[*governance_data.dreps_reg, custom_drep1]
        )

        # Update DRep activity
        _action_txid = _update_drep_activity(
            governance_data=governance_data_drep1, action_id="update_activity_vote"
        )
        _save_drep_states(drep1=custom_drep1, drep2=None, id="updated_activity_voted")
        _check_ratification(action_txid=_action_txid, action_id="updated_activity_ratification")
        _save_drep_states(drep1=custom_drep1, drep2=None, id="updated_activity_ratified")
        _check_enactment(action_txid=_action_txid, action_id="updated_activity_enacted")
        _save_drep_states(drep1=custom_drep1, drep2=None, id="updated_activity_enacted")

        # Create the second DRep
        custom_drep2 = create_drep(
            name_template=f"{temp_template}_drep2",
            cluster_obj=cluster,
            payment_addr=pool_user_lg.payment,
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="created_drep2")
        _delegate_addr(
            name_template=f"{temp_template}_pool_user2_deleg",
            drep_reg=custom_drep2,
            pool_user=drep_users[1],
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="delegated_drep2")

        # Add the second DRep to the governance data
        governance_data_drep2 = dataclasses.replace(
            governance_data, dreps_reg=[*governance_data.dreps_reg, custom_drep2]
        )

        # Update DRep activity again so there is a proposal to vote for. The newly created DReps
        # will not vote. The action will not be ratified, because the newly created DReps didn't
        # vote and their delegated stake is > 51%.
        _action_txid = _update_drep_activity(
            governance_data=governance_data, action_id="vote_orig1_vote"
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_orig1_voted")
        _check_ratification(action_txid=_action_txid, action_id="voted_orig1_ratification")
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_orig1_ratified")

        # Update DRep activity again so there is a proposal to vote for. The newly created DRep1
        # will vote. The action will be ratified, because the newly created DRep1 voted and
        # together with the original DReps their delegated stake is > 51%.
        _action_txid = _update_drep_activity(
            governance_data=governance_data_drep1,
            action_id="vote_drep1_vote",
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_drep1_voted")
        _check_ratification(
            action_txid=_action_txid,
            action_id="voted_drep1_ratification",
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_drep1_ratified")
        _check_enactment(action_txid=_action_txid, action_id="voted_drep1_enacted")
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_drep1_enacted")

        # Update DRep activity again so there is a proposal to vote for. The newly created DReps
        # will not vote. The action will be ratified, because the newly created DReps are
        # inactive and so their delegated stake does not count towards the active voting stake.
        _action_txid = _update_drep_activity(
            governance_data=governance_data, action_id="vote_orig2_vote"
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_orig2_voted")
        _check_ratification(action_txid=_action_txid, action_id="voted_orig2_ratification")
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_orig2_ratified")

        # Wait for another epoch without submitting any proposal, to see if "expire" counters
        # are incremented.
        cluster.wait_for_new_epoch(padding_seconds=5)
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="no_proposal")

        # Update DRep activity again so there is a proposal to vote for. The newly created DRep2
        # will vote. The action will be ratified, because the newly created DRep2 voted and
        # together with the original DReps their delegated stake is > 51%.
        _action_txid = _update_drep_activity(
            governance_data=governance_data_drep2,
            action_id="vote_drep2_vote",
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_drep2_voted")
        _check_ratification(
            action_txid=_action_txid,
            action_id="voted_drep2_ratification",
        )
        _save_drep_states(drep1=custom_drep1, drep2=custom_drep2, id="voted_drep2_ratified")
        # We'll not check the enactment here, as we don't want to wait for another epoch

        # Check DRep records
        reqc.cip019.start(url=helpers.get_vcs_link())
        found_issues = []
        try:
            found_issues = _check_records()
        except Exception:
            _dump_records()
            raise
        reqc.cip019.success()

        if found_issues:
            blockers.finish_test(issues=found_issues)
