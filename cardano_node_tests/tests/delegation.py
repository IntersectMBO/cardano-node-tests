"""Functionality for stake address delegation used in multiple tests modules."""
import logging
from pathlib import Path
from typing import Any
from typing import Dict
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Union

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.cluster_management import resources_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils

LOGGER = logging.getLogger(__name__)


class AddressRecordScript(NamedTuple):
    address: str
    script_file: Path


class PoolUserScript(NamedTuple):
    payment: clusterlib.AddressRecord
    stake: AddressRecordScript


class DelegationOut(NamedTuple):
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
) -> Tuple[clusterlib.ClusterLib, str]:
    """Return instance of `clusterlib.ClusterLib`, and pool id to delegate to.

    We need to mark the pool as "in use" when requesting local cluster
    instance, that's why cluster instance and pool id are tied together in
    single fixture.
    """
    cluster_type = cluster_nodes.get_cluster_type()
    if cluster_type.type == cluster_nodes.ClusterType.TESTNET_NOPOOLS:
        cluster_obj: clusterlib.ClusterLib = cluster_manager.get()

        # getting ledger state on official testnet is too expensive,
        # use one of hardcoded pool IDs if possible
        if cluster_type.testnet_type == cluster_nodes.Testnets.testnet:  # type: ignore
            stake_pools = cluster_obj.g_query.get_stake_pools()
            for pool_id in configuration.TESTNET_POOL_IDS:
                if pool_id in stake_pools:
                    return cluster_obj, pool_id

        blocks_before = clusterlib_utils.get_blocks_before(cluster_obj)
        # sort pools by how many blocks they produce
        pool_ids_s = sorted(blocks_before, key=blocks_before.get, reverse=True)  # type: ignore
        # select a pool with reasonable margin
        for pool_id in pool_ids_s:
            pool_params = clusterlib_utils.get_pool_state(cluster_obj=cluster_obj, pool_id=pool_id)
            if pool_params.pool_params["margin"] <= 0.5 and not pool_params.retiring:
                break
        else:
            pytest.skip("Cannot find any usable pool.")
    elif cluster_type.type == cluster_nodes.ClusterType.TESTNET:
        # the "testnet" cluster has just single pool, "node-pool1"
        cluster_obj = cluster_manager.get(use_resources=[cluster_management.Resources.POOL1])
        pool_id = get_pool_id(
            cluster_obj=cluster_obj,
            addrs_data=cluster_manager.cache.addrs_data,
            pool_name=cluster_management.Resources.POOL1,
        )
    else:
        cluster_obj = cluster_manager.get(
            use_resources=[
                resources_management.OneOf(resources=cluster_management.Resources.ALL_POOLS),
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
    pool_user: Union[clusterlib.PoolUser, PoolUserScript],
    db_record: Optional[dbsync_utils.TxRecord],
    deleg_epoch: int,
    pool_id: str,
):
    """Check delegation in db-sync."""
    if not db_record:
        return

    assert pool_user.stake.address in db_record.stake_registration
    assert pool_user.stake.address == db_record.stake_delegation[0].address
    assert db_record.stake_delegation[0].active_epoch_no == deleg_epoch + 2
    assert pool_id == db_record.stake_delegation[0].pool_id


def delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    temp_template: str,
    pool_user: Optional[clusterlib.PoolUser] = None,
    pool_id: str = "",
    cold_vkey: Optional[Path] = None,
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
            addr_name=f"{temp_template}_addr0", stake_vkey_file=pool_user.stake.vkey_file
        )

    # create stake address delegation cert
    deleg_kwargs: Dict[str, Any] = {
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
