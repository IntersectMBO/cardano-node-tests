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

PREPROD_POOL_IDS = (
    "pool1nmfr5j5rnqndprtazre802glpc3h865sy50mxdny65kfgf3e5eh",  # GRADA
    "pool132jxjzyw4awr3s75ltcdx5tv5ecv6m042306l630wqjckhfm32r",  # ADACT
    "pool10dtwvn64akqjdtn9d4pd2mnhpxfgp76hvsfkgmfwugrsxef3y2p",  # RABIT
    "pool1tjc56tq7adk64nnq2ldu3f4nh6sxkphhmnejlu67ux7acq8y7rx",  # PSBT
    "pool1z05xqzuxnpl8kg8u2wwg8ftng0fwtdluv3h20ruryfqc5gc3efl",  # HODLr
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


@dataclasses.dataclass(frozen=True, order=True)
class DelegationScriptOut:
    pool_user: PoolUserScript
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
) -> tuple[clusterlib.ClusterLib, str]:
    """Return instance of `clusterlib.ClusterLib`, and pool id to delegate to.

    We need to mark the pool as "in use" when requesting local cluster
    instance, that's why cluster instance and pool id are tied together in
    single fixture.
    """
    cluster_type = cluster_nodes.get_cluster_type()
    if cluster_type.type == cluster_nodes.ClusterType.TESTNET:
        cluster_obj: clusterlib.ClusterLib = cluster_manager.get(use_resources=use_resources)

        # Getting ledger state on official testnet is too expensive,
        # use one of hardcoded pool IDs if possible.
        testnet_pools = None
        if cluster_type.testnet_type == cluster_nodes.Testnets.preview:
            testnet_pools = PREVIEW_POOL_IDS
        elif cluster_type.testnet_type == cluster_nodes.Testnets.preprod:
            testnet_pools = PREPROD_POOL_IDS
        if testnet_pools:
            stake_pools = cluster_obj.g_query.get_stake_pools()
            for pool_id in testnet_pools:
                if pool_id in stake_pools:
                    return cluster_obj, pool_id

        blocks_before = clusterlib_utils.get_blocks_before(cluster_obj=cluster_obj)
        # Sort pools by how many blocks they produce
        pool_ids_s = sorted(blocks_before, key=blocks_before.get, reverse=True)  # type: ignore
        # Select a pool with reasonable margin
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
    pool_user: clusterlib.PoolUser | PoolUserScript,
    db_record: dbsync_types.TxRecord | None,
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
    pool_user: clusterlib.PoolUser | None = None,
    pool_id: str = "",
    cold_vkey: pl.Path | None = None,
    amount: int = 100_000_000,
    build_method: str = clusterlib_utils.BuildMethods.BUILD_RAW,
) -> DelegationOut:
    """Submit registration certificate and delegate a stake address to a pool."""
    # Create key pairs and addresses
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

        # Fund payment address
        clusterlib_utils.fund_from_faucet(
            pool_user.payment,
            cluster_obj=cluster_obj,
            all_faucets=addrs_data,
            amount=amount,
        )

    # Create stake address registration cert if address is not already registered
    if cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        stake_addr_reg_cert_file = None
    else:
        stake_addr_reg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster_obj),
            stake_vkey_file=pool_user.stake.vkey_file,
        )

    # Create stake address delegation cert
    deleg_kwargs: dict[str, tp.Any] = {
        "addr_name": f"{temp_template}_addr0",
        "stake_vkey_file": pool_user.stake.vkey_file,
        "always_abstain": True,
    }
    if pool_id:
        deleg_kwargs["stake_pool_id"] = pool_id
    elif cold_vkey:
        deleg_kwargs["cold_vkey_file"] = cold_vkey
        pool_id = cluster_obj.g_stake_pool.get_stake_pool_id(cold_vkey)

    stake_addr_deleg_cert_file = cluster_obj.g_stake_address.gen_stake_and_vote_delegation_cert(
        **deleg_kwargs
    )

    src_address = pool_user.payment.address
    src_init_balance = cluster_obj.g_query.get_address_balance(src_address)

    # Register stake address and delegate it to pool
    certificate_files = [stake_addr_deleg_cert_file]
    if stake_addr_reg_cert_file:
        certificate_files.insert(0, stake_addr_reg_cert_file)
    tx_files = clusterlib.TxFiles(
        certificate_files=certificate_files,
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    tx_output = clusterlib_utils.build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{temp_template}_reg_deleg",
        src_address=src_address,
        tx_files=tx_files,
        build_method=build_method,
        witness_override=len(tx_files.signing_key_files),
    )

    # Check that the balance for source address was correctly updated
    deposit = cluster_obj.g_query.get_address_deposit() if stake_addr_reg_cert_file else 0
    assert (
        cluster_obj.g_query.get_address_balance(src_address)
        == src_init_balance - deposit - tx_output.fee
    ), f"Incorrect balance for source address `{src_address}`"

    # Check that the stake address was delegated
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
    assert stake_addr_info.delegation == pool_id, "Stake address delegated to wrong pool"
    assert stake_addr_info.vote_delegation == "alwaysAbstain"

    return DelegationOut(pool_user=pool_user, pool_id=pool_id, tx_raw_output=tx_output)


def delegate_multisig_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    pool_user: PoolUserScript,
    skey_files: tp.Iterable[clusterlib.FileType],
    pool_id: str = "",
    cold_vkey: pl.Path | None = None,
    build_method=clusterlib_utils.BuildMethods.BUILD_RAW,
) -> DelegationScriptOut:
    """Submit registration certificate and delegate a multisig stake address to a pool."""
    # Create stake address registration cert if address is not already registered
    if cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        stake_addr_reg_cert_file = None
    else:
        stake_addr_reg_cert_file = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
            addr_name=f"{temp_template}_addr0",
            deposit_amt=common.get_conway_address_deposit(cluster_obj=cluster_obj),
            stake_script_file=pool_user.stake.script_file,
        )

    # Create stake address delegation cert
    deleg_kwargs: dict[str, tp.Any] = {
        "addr_name": f"{temp_template}_addr0",
        "stake_script_file": pool_user.stake.script_file,
        "always_abstain": True,
    }
    if pool_id:
        deleg_kwargs["stake_pool_id"] = pool_id
    elif cold_vkey:
        deleg_kwargs["cold_vkey_file"] = cold_vkey
        pool_id = cluster_obj.g_stake_pool.get_stake_pool_id(cold_vkey)

    stake_addr_deleg_cert_file = cluster_obj.g_stake_address.gen_stake_and_vote_delegation_cert(
        **deleg_kwargs
    )

    src_address = pool_user.payment.address
    src_init_balance = cluster_obj.g_query.get_address_balance(src_address)

    # Register stake address and delegate it to pool
    deleg_cert_script = clusterlib.ComplexCert(
        certificate_file=stake_addr_deleg_cert_file,
        script_file=pool_user.stake.script_file,
    )

    complex_certs = [deleg_cert_script]

    if stake_addr_reg_cert_file:
        reg_cert_script = clusterlib.ComplexCert(
            certificate_file=stake_addr_reg_cert_file,
            script_file=pool_user.stake.script_file,
        )
        complex_certs.insert(0, reg_cert_script)

    signing_key_files = [pool_user.payment.skey_file, *skey_files]
    witness_len = len(signing_key_files)

    if build_method == clusterlib_utils.BuildMethods.BUILD:
        tx_output = cluster_obj.g_transaction.build_tx(
            src_address=src_address,
            tx_name=temp_template,
            complex_certs=complex_certs,
            fee_buffer=2_000_000,
            witness_override=witness_len,
        )
    elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
        tx_output = cluster_obj.g_transaction.build_estimate_tx(
            src_address=src_address,
            tx_name=temp_template,
            complex_certs=complex_certs,
            fee_buffer=2_000_000,
            witness_count_add=witness_len,
        )
    elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
        fee = cluster_obj.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            complex_certs=complex_certs,
            witness_count_add=witness_len,
        )
        tx_output = cluster_obj.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            complex_certs=complex_certs,
            fee=fee,
        )
    else:
        msg = f"Unsupported build method: {build_method}"
        raise ValueError(msg)

    # Create witness file for each key
    witness_files = [
        cluster_obj.g_transaction.witness_tx(
            tx_body_file=tx_output.out_file,
            witness_name=f"{temp_template}_skey{idx}",
            signing_key_files=[skey],
        )
        for idx, skey in enumerate(signing_key_files, start=1)
    ]

    # Sign TX using witness files
    tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
        tx_body_file=tx_output.out_file,
        witness_files=witness_files,
        tx_name=temp_template,
    )

    # Submit signed TX
    cluster_obj.g_transaction.submit_tx(tx_file=tx_witnessed_file, txins=tx_output.txins)

    # Check that the balance for source address was correctly updated
    deposit = cluster_obj.g_query.get_address_deposit() if stake_addr_reg_cert_file else 0
    assert (
        cluster_obj.g_query.get_address_balance(src_address)
        == src_init_balance - deposit - tx_output.fee
    ), f"Incorrect balance for source address `{src_address}`"

    # Check that the stake address was delegated
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"
    assert stake_addr_info.delegation == pool_id, "Stake address delegated to wrong pool"
    assert stake_addr_info.vote_delegation == "alwaysAbstain"

    return DelegationScriptOut(pool_user=pool_user, pool_id=pool_id, tx_raw_output=tx_output)
