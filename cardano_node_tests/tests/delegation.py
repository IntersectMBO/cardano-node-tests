"""Functionality for stake address delegation used in multiple tests modules."""

import dataclasses
import logging
import pathlib as pl
import typing as tp

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_types

LOGGER = logging.getLogger(__name__)

PREVIEW_POOL_IDS = (
    "pool1ynfnjspgckgxjf2zeye8s33jz3e3ndk9pcwp0qzaupzvvd8ukwt",  # GRADA
    "pool18pn6p9ef58u4ga3wagp44qhzm8f6zncl57g6qgh0pk3yytwz54h",  # ADACT
    "pool1vhdl0z496gxlkfpdzxhk80t4363puea6awp6hd0w0qwnw75auae",  # RABIT
    "pool1vzqtn3mtfvvuy8ghksy34gs9g97tszj5f8mr3sn7asy5vk577ec",  # PSBT
    "pool1y0uxkqyplyx6ld25e976t0s35va3ysqcscatwvy2sd2cwcareq7",  # HODLr
)


@dataclasses.dataclass(frozen=True, order=True)
class AddressRecordScript:
    address: str
    script_file: pl.Path


@dataclasses.dataclass(frozen=True, order=True)
class PoolUserScript:
    payment: clusterlib.AddressRecord
    stake: AddressRecordScript


@dataclasses.dataclass(frozen=True, order=True)
class DelegationOut:
    pool_user: clusterlib.PoolUser
    pool_id: str
    tx_raw_output: clusterlib.TxRawOutput


def get_pool_id(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    pool_name: str,
) -> str:
    """Return stake pool id."""
    node_cold = addrs_data[pool_name]["cold_key_pair"]
    return cluster_obj.g_stake_pool.get_stake_pool_id(node_cold.vkey_file)


def cluster_and_pool(
    cluster_manager: cluster_management.ClusterManager,
    use_resources: resources_management.ResourcesType = (),
) -> tp.Tuple[clusterlib.ClusterLib, str]:
    """Return instance of `clusterlib.ClusterLib`, and pool id to delegate to.

    We need to mark the pool as "in use" when requesting local cluster
    instance, that's why cluster instance and pool id are tied together in
    single fixture.
    """
    cluster_type = cluster_nodes.get_cluster_type()
    if cluster_type.type == cluster_nodes.ClusterType.TESTNET:
        cluster_obj: clusterlib.ClusterLib = cluster_manager.get(use_resources=use_resources)

        # Getting ledger state on official testnet is too expensive,
        # use one of hardcoded pool IDs if possible
        if cluster_type.testnet_type == cluster_nodes.Testnets.preview:
            stake_pools = cluster_obj.g_query.get_stake_pools()
            for pool_id in PREVIEW_POOL_IDS:
                if pool_id in stake_pools:
                    return cluster_obj, pool_id

        blocks_before = clusterlib_utils.get_blocks_before(cluster_obj)
        # sort pools by how many blocks they produce
        pool_ids_s = sorted(blocks_before, key=blocks_before.get, reverse=True)  # type: ignore
        # select a pool with reasonable margin
        for pool_id in pool_ids_s:
            pool_params = cluster_obj.g_query.get_pool_state(stake_pool_id=pool_id)
            if pool_params.pool_params["margin"] <= 0.5 and not pool_params.retiring:
                break
        else:
            pytest.skip("Cannot find any usable pool.")
    else:
        cluster_obj = cluster_manager.get(
            use_resources=[
                resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
                *use_resources,
            ]
        )
        pool_name = cluster_manager.get_used_resources(
            from_set=cluster_management.Resources.ALL_POOLS
        )[0]
        pool_id = get_pool_id(
            cluster_obj=cluster_obj,
            addrs_data=cluster_manager.cache.addrs_data,
            pool_name=pool_name,
        )
    return cluster_obj, pool_id


def db_check_delegation(
    pool_user: tp.Union[clusterlib.PoolUser, PoolUserScript],
    db_record: tp.Optional[dbsync_types.TxRecord],
    deleg_epoch: int,
    pool_id: str,
    check_registration: bool = True,
):
    """Check delegation in db-sync."""
    if not db_record:
        return

    assert pool_user.stake.address == db_record.stake_delegation[0].address
    assert db_record.stake_delegation[0].active_epoch_no == deleg_epoch + 2
    assert pool_id == db_record.stake_delegation[0].pool_id

    if check_registration:
        assert pool_user.stake.address in db_record.stake_registration


def delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    temp_template: str,
    pool_user: tp.Optional[clusterlib.PoolUser] = None,
    pool_id: str = "",
    cold_vkey: tp.Optional[pl.Path] = None,
    amount: int = 100_000_000,
    use_build_cmd: bool = False,
) -> DelegationOut:
    """Submit registration certificate and delegate to pool."""
    # create key pairs and addresses
    if not pool_user:
        stake_addr_rec = clusterlib_utils.create_stake_addr_records(
            f"{temp_template}_addr0", cluster_obj=cluster_obj
        )[0]
        payment_addr_rec = clusterlib_utils.create_payment_addr_records(
            f"{temp_template}_addr0",
            cluster_obj=cluster_obj,
            stake_vkey_file=stake_addr_rec.vkey_file,
        )[0]

        pool_user = clusterlib.PoolUser(payment=payment_addr_rec, stake=stake_addr_rec)

        # fund payment address
        clusterlib_utils.fund_from_faucet(
            pool_user.payment,
            cluster_obj=cluster_obj,
            faucet_data=addrs_data["user1"],
            amount=amount,
        )

    # create stake address registration cert if address is not already registered
    if cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        stake_addr_reg_cert_file = None
    else:
        stake_addr_reg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster_obj),
            stake_vkey_file=pool_user.stake.vkey_file,
        )

    # create stake address delegation cert
    deleg_kwargs: tp.Dict[str, tp.Any] = {
        "addr_name": f"{temp_template}_addr0",
        "stake_vkey_file": pool_user.stake.vkey_file,
    }
    if pool_id:
        deleg_kwargs["stake_pool_id"] = pool_id
    elif cold_vkey:
        deleg_kwargs["cold_vkey_file"] = cold_vkey
        pool_id = cluster_obj.g_stake_pool.get_stake_pool_id(cold_vkey)

    stake_addr_deleg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_delegation_cert(
        **deleg_kwargs
    )

    src_address = pool_user.payment.address
    src_init_balance = cluster_obj.g_query.get_address_balance(src_address)

    # register stake address and delegate it to pool
    certificate_files = [stake_addr_deleg_cert_file]
    if stake_addr_reg_cert_file:
        certificate_files.insert(0, stake_addr_reg_cert_file)
    tx_files = clusterlib.TxFiles(
        certificate_files=certificate_files,
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    if use_build_cmd:
        tx_raw_output = cluster_obj.g_transaction.build_tx(
            src_address=src_address,
            tx_name=f"{temp_template}_reg_deleg",
            tx_files=tx_files,
            fee_buffer=2_000_000,
            witness_override=len(tx_files.signing_key_files),
        )
        tx_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_reg_deleg",
        )
        cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
    else:
        tx_raw_output = cluster_obj.g_transaction.send_tx(
            src_address=src_address, tx_name=f"{temp_template}_reg_deleg", tx_files=tx_files
        )

    # check that the balance for source address was correctly updated
    deposit = cluster_obj.g_query.get_address_deposit() if stake_addr_reg_cert_file else 0
    assert (
        cluster_obj.g_query.get_address_balance(src_address)
        == src_init_balance - deposit - tx_raw_output.fee
    ), f"Incorrect balance for source address `{src_address}`"

    # check that the stake address was delegated
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
    assert pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return DelegationOut(pool_user=pool_user, pool_id=pool_id, tx_raw_output=tx_raw_output)
