"""Tests for node upgrade."""

import logging
import os
import shutil
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)

UPGRADE_TESTS_STEP = int(os.environ.get("UPGRADE_TESTS_STEP") or 0)
BASE_REVISION = version.parse(os.environ.get("BASE_REVISION") or "0.0.0")
UPGRADE_REVISION = version.parse(os.environ.get("UPGRADE_REVISION") or "0.0.0")
GOV_DATA_DIR = "governance_data"

pytestmark = [
    pytest.mark.skipif(not UPGRADE_TESTS_STEP, reason="not upgrade testing"),
]


@pytest.fixture
def payment_addr_locked(
    cluster_manager: cluster_management.ClusterManager,
    cluster_singleton: clusterlib.ClusterLib,
) -> clusterlib.AddressRecord:
    """Create new payment addresses."""
    cluster = cluster_singleton
    temp_template = common.get_test_id(cluster)

    addr = clusterlib_utils.create_payment_addr_records(
        f"{temp_template}_payment_addr_0",
        cluster_obj=cluster,
    )[0]

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addr,
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return addr


@pytest.fixture
def payment_addrs_disposable(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> tp.List[clusterlib.AddressRecord]:
    """Create new disposable payment addresses."""
    temp_template = common.get_test_id(cluster)

    addrs = clusterlib_utils.create_payment_addr_records(
        f"{temp_template}_payment_addr_disposable_0",
        f"{temp_template}_payment_addr_disposable_1",
        cluster_obj=cluster,
    )

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
    )

    return addrs


class TestSetup:
    """Tests for setting up cardano network before and during upgrade testing.

    Special tests that run outside of normal test run.
    """

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP < 2, reason="runs only on step >= 2 of upgrade testing")
    def test_ignore_log_errors(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        worker_id: str,
    ):
        """Ignore selected errors in log right after node upgrade."""
        cluster = cluster_singleton
        common.get_test_id(cluster)

        if UPGRADE_REVISION >= version.parse("9.0.0") > BASE_REVISION:
            logfiles.add_ignore_rule(
                files_glob="*.stdout",
                regex="ChainDB:Error:.* Invalid snapshot DiskSnapshot .*DeserialiseFailure "
                ".*expected list len or indef",
                ignore_file_id=worker_id,
            )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing")
    def test_update_to_conway_pv9(
        self,
        cluster_singleton: clusterlib.ClusterLib,
        payment_addr_locked: clusterlib.AddressRecord,
    ):
        """Update cluster to Conway PV9."""
        cluster = cluster_singleton
        temp_template = common.get_test_id(cluster)

        def _update_to_pv9() -> None:
            cluster.wait_for_new_epoch()

            update_proposal_pv9 = [
                clusterlib_utils.UpdateProposal(
                    arg="--protocol-major-version",
                    value=9,
                    name="",  # needs custom check
                ),
                clusterlib_utils.UpdateProposal(
                    arg="--protocol-minor-version",
                    value=0,
                    name="",  # needs custom check
                ),
            ]

            clusterlib_utils.update_params(
                cluster_obj=cluster,
                src_addr_record=payment_addr_locked,
                update_proposals=update_proposal_pv9,
            )

            cluster.wait_for_new_epoch(padding_seconds=3)

            prot_ver = cluster.g_query.get_protocol_params()["protocolVersion"]
            assert prot_ver["major"] == 9
            assert prot_ver["minor"] == 0

        def _load_cc_members(
            cluster_obj: clusterlib.ClusterLib,
        ) -> tp.List[governance_utils.CCMemberAuth]:
            data_dir = cluster_obj.state_dir / GOV_DATA_DIR

            cc_members = []
            for vkey_file in sorted(data_dir.glob("cc_member*_committee_cold.vkey")):
                fpath = vkey_file.parent
                fbase = vkey_file.name.replace("cold.vkey", "")
                hot_vkey_file = fpath / f"{fbase}hot.vkey"
                cold_vkey_hash = cluster_obj.g_conway_governance.committee.get_key_hash(
                    vkey_file=vkey_file
                )
                auth_cert = fpath / f"{fbase}hot_auth.cert"
                cold_key_pair = clusterlib.KeyPair(
                    vkey_file=vkey_file, skey_file=fpath / f"{fbase}cold.skey"
                )
                hot_key_pair = clusterlib.KeyPair(
                    vkey_file=hot_vkey_file, skey_file=fpath / f"{fbase}hot.skey"
                )
                cc_members.append(
                    governance_utils.CCMemberAuth(
                        auth_cert=auth_cert,
                        cold_key_pair=cold_key_pair,
                        hot_key_pair=hot_key_pair,
                        key_hash=cold_vkey_hash,
                    )
                )

            return cc_members

        def _reg_cc_members(
            cluster_obj: clusterlib.ClusterLib, cc_members: tp.List[governance_utils.CCMemberAuth]
        ) -> None:
            tx_files = clusterlib.TxFiles(
                certificate_files=[c.auth_cert for c in cc_members],
                signing_key_files=[
                    payment_addr_locked.skey_file,
                    *[c.cold_key_pair.skey_file for c in cc_members],
                ],
            )

            tx_output_auth = clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster_obj,
                name_template=f"{temp_template}_auth",
                src_address=payment_addr_locked.address,
                tx_files=tx_files,
            )

            auth_out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output_auth)
            assert (
                clusterlib.filter_utxos(utxos=auth_out_utxos, address=payment_addr_locked.address)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(tx_output_auth.txins) - tx_output_auth.fee
            ), f"Incorrect balance for source address `{payment_addr_locked.address}`"

            auth_committee_state = cluster_obj.g_conway_governance.query.committee_state()
            for cm in cc_members:
                member_key = f"keyHash-{cm.key_hash}"
                member_rec = auth_committee_state["committee"][member_key]
                assert (
                    member_rec["hotCredsAuthStatus"]["tag"] == "MemberAuthorized"
                ), "CC Member was NOT authorized"

        _update_to_pv9()
        cluster_conway = cluster_nodes.get_cluster_type().get_cluster_obj(command_era="conway")
        cc_members = _load_cc_members(cluster_obj=cluster_conway)
        _reg_cc_members(cluster_obj=cluster_conway, cc_members=cc_members)


@pytest.mark.upgrade
class TestUpgrade:
    """Tests for node upgrade testing."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP > 2, reason="doesn't run on step > 2 of upgrade testing")
    @pytest.mark.order(-1)
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "for_step",
        (
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 2, reason="doesn't run on step 2 of upgrade testing"
                ),
            ),
            pytest.param(
                3,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 3, reason="doesn't run on step 3 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize("file_type", ("tx", "tx_body"))
    def test_prepare_tx(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs_disposable: tp.List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        for_step: int,
        file_type: str,
    ):
        """Prepare transactions that will be submitted in next steps of upgrade testing.

        For testing that transaction created by previous node version and/or in previous era can
        be submitted in next node version and/or next era.
        """
        temp_template = common.get_test_id(cluster)
        build_str = "build" if use_build_cmd else "build_raw"

        src_address = payment_addrs_disposable[0].address
        dst_address = payment_addrs_disposable[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs_disposable[0].skey_file])

        if use_build_cmd:
            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                tx_files=tx_files,
                txouts=destinations,
                fee_buffer=1_000_000,
            )
            out_file_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
        else:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
            tx_raw_output = cluster.g_transaction.build_raw_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
                fee=fee,
            )
            out_file_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )

        copy_files = [
            payment_addrs_disposable[0].skey_file,
            tx_raw_output.out_file,
            out_file_signed,
        ]

        tx_dir = (
            temptools.get_basetemp()
            / cluster_manager.cache.last_checksum
            / f"{UPGRADE_TESTS_STEP}for{for_step}"
            / file_type
            / build_str
        ).resolve()

        if tx_dir.exists():
            shutil.rmtree(tx_dir)
        tx_dir.mkdir(parents=True)

        for f in copy_files:
            shutil.copy(f, tx_dir)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(UPGRADE_TESTS_STEP < 2, reason="runs only on step >= 2 of upgrade testing")
    @pytest.mark.order(5)
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "for_step",
        (
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP != 2, reason="runs only on step 2 of upgrade testing"
                ),
            ),
            pytest.param(
                3,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP != 3, reason="runs only on step 3 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize(
        "from_step",
        (
            1,
            pytest.param(
                2,
                marks=pytest.mark.skipif(
                    UPGRADE_TESTS_STEP == 2, reason="doesn't run on step 2 of upgrade testing"
                ),
            ),
        ),
    )
    @pytest.mark.parametrize("file_type", ("tx", "tx_body"))
    def test_submit_tx(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        use_build_cmd: bool,
        for_step: int,
        from_step: int,
        file_type: str,
    ):
        """Submit transaction that was created by previous node version and/or in previous era."""
        temp_template = common.get_test_id(cluster)
        build_str = "build" if use_build_cmd else "build_raw"

        tx_dir = (
            temptools.get_basetemp()
            / cluster_manager.cache.last_checksum
            / f"{from_step}for{for_step}"
            / file_type
            / build_str
        ).resolve()

        if not tx_dir.exists():
            pytest.skip("No tx files found")

        tx_file = next(iter(tx_dir.glob("*.signed")))
        tx_body_file = next(iter(tx_dir.glob("*.body")))
        skey_file = next(iter(tx_dir.glob("*.skey")))

        if file_type == "tx_body":
            tx_file = cluster.g_transaction.sign_tx(
                tx_body_file=tx_body_file,
                tx_name=temp_template,
                signing_key_files=[skey_file],
            )

        cluster.g_transaction.submit_tx_bare(tx_file=tx_file)
        cluster.wait_for_new_block(2)
