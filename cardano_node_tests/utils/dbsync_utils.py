"""Functionality for interacting with db-sync."""

import functools
import logging
import time
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_check_tx
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import governance_utils

LOGGER = logging.getLogger(__name__)

NO_RESPONSE_STR = "No response returned from db-sync:"


def get_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> dbsync_types.RewardRecord:
    """Get reward data for stake address from db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward can be spent.
    """
    rewards = []
    for db_row in dbsync_queries.query_address_reward(
        address=address, epoch_from=epoch_from, epoch_to=epoch_to
    ):
        rewards.append(
            dbsync_types.RewardEpochRecord(
                amount=int(db_row.amount),
                earned_epoch=db_row.earned_epoch,
                spendable_epoch=db_row.spendable_epoch,
                type=db_row.type,
                pool_id=db_row.pool_id or "",
            )
        )

    for db_row in dbsync_queries.query_address_reward_rest(
        address=address, epoch_from=epoch_from, epoch_to=epoch_to
    ):
        rewards.append(
            dbsync_types.RewardEpochRecord(
                amount=int(db_row.amount),
                earned_epoch=db_row.earned_epoch,
                spendable_epoch=db_row.spendable_epoch,
                type=db_row.type,
                pool_id=db_row.pool_id or "",
            )
        )
    if not rewards:
        return dbsync_types.RewardRecord(address=address, reward_sum=0, rewards=[])

    reward_sum = functools.reduce(lambda x, y: x + y.amount, rewards, 0)
    # pylint: disable=undefined-loop-variable
    return dbsync_types.RewardRecord(address=db_row.address, reward_sum=reward_sum, rewards=rewards)


def check_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> dbsync_types.RewardRecord:
    """Check reward data for stake address in db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward can be spent.
    """
    reward = get_address_reward(address=address, epoch_from=epoch_from, epoch_to=epoch_to)
    if not reward:
        return reward

    errors = []
    for r in reward.rewards:
        if r.type in ("member", "leader"):
            if r.spendable_epoch != r.earned_epoch + 2:
                errors.append(f"type == {r.type} and {r.spendable_epoch} != {r.earned_epoch} + 2")
        # Transfer from reserves or treasury
        elif r.spendable_epoch != r.earned_epoch + 1:
            errors.append(f"type == {r.type} and {r.spendable_epoch} != {r.earned_epoch} + 1")

    if errors:
        err_str = ", ".join(errors)
        msg = f"The 'earned epoch' and 'spendable epoch' don't match: {err_str}."
        raise AssertionError(msg)

    return reward


def get_utxo(address: str) -> dbsync_types.PaymentAddrRecord:
    """Return UTxO info for payment address from db-sync."""
    utxos = []
    for db_row in dbsync_queries.query_utxo(address=address):
        utxos.append(
            dbsync_types.GetUTxORecord(
                utxo_hash=db_row.tx_hash.hex(),
                utxo_ix=db_row.utxo_ix,
                has_script=db_row.has_script,
                amount=int(db_row.value),
                data_hash=db_row.data_hash.hex() if db_row.data_hash else "",
            )
        )
    if not utxos:
        return dbsync_types.PaymentAddrRecord(
            payment_address=address,
            stake_address=None,
            amount_sum=0,
            utxos=[],
        )

    amount_sum = functools.reduce(lambda x, y: x + y.amount, utxos, 0)
    # pylint: disable=undefined-loop-variable
    return dbsync_types.PaymentAddrRecord(
        payment_address=db_row.payment_address,
        stake_address=db_row.stake_address,
        amount_sum=amount_sum,
        utxos=utxos,
    )


def get_pool_data(pool_id_bech32: str) -> tp.Optional[dbsync_types.PoolDataRecord]:
    """Get pool data from db-sync."""
    pools = list(dbsync_queries.query_pool_data(pool_id_bech32))
    if not pools:
        return None

    known_owners = set()
    single_host_names = []
    single_host_addresses = []

    latest_registered_tx_id = pools[-1].registered_tx_id
    latest_pools = [pool for pool in pools if pool.registered_tx_id == latest_registered_tx_id]

    for pool in latest_pools:
        if pool.owner:
            owner = pool.owner.hex()[2:]
            known_owners.add(owner)
        if pool.dns_name:
            host_name = {"single host name": {"dnsName": pool.dns_name, "port": pool.port}}
            if host_name not in single_host_names:
                single_host_names.append(host_name)
        if pool.ipv4:
            host_address = {
                "single host address": {"IPv4": pool.ipv4, "IPv6": pool.ipv6, "port": pool.port}
            }
            if host_address not in single_host_addresses:
                single_host_addresses.append(host_address)

    # pylint: disable=undefined-loop-variable
    pool_data = dbsync_types.PoolDataRecord(
        id=pool.id,
        hash=pool.hash.hex(),
        view=pool.view,
        cert_index=pool.cert_index,
        vrf_key_hash=pool.vrf_key_hash.hex(),
        pledge=int(pool.pledge),
        reward_addr=pool.reward_addr.hex()[2:],
        active_epoch_no=pool.active_epoch_no,
        meta_id=pool.meta_id,
        margin=float(pool.margin),
        fixed_cost=int(pool.fixed_cost),
        registered_tx_id=pool.registered_tx_id,
        metadata_url=str(pool.metadata_url or ""),
        metadata_hash=pool.metadata_hash.hex() if pool.metadata_hash else "",
        owners=list(known_owners),
        relays=[*single_host_names, *single_host_addresses],
        retire_cert_index=pool.retire_cert_index,
        retire_announced_tx_id=pool.retire_announced_tx_id,
        retiring_epoch=pool.retiring_epoch,
    )

    return pool_data


def get_prelim_tx_record(txhash: str) -> dbsync_types.TxPrelimRecord:
    """Get first batch of transaction data from db-sync."""
    utxo_out: tp.List[dbsync_types.UTxORecord] = []
    seen_tx_out_ids = set()
    ma_utxo_out: tp.List[dbsync_types.UTxORecord] = []
    seen_ma_tx_out_ids = set()
    mint_utxo_out: tp.List[dbsync_types.UTxORecord] = []
    seen_ma_tx_mint_ids = set()
    tx_id = -1

    for query_row in dbsync_queries.query_tx(txhash=txhash):
        if tx_id == -1:
            tx_id = query_row.tx_id
        if tx_id != query_row.tx_id:
            msg = "Transaction ID differs from the expected ID."
            raise AssertionError(msg)

        # Lovelace outputs
        if query_row.tx_out_id and query_row.tx_out_id not in seen_tx_out_ids:
            seen_tx_out_ids.add(query_row.tx_out_id)
            out_rec = dbsync_types.UTxORecord(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.tx_out_value),
                address=str(query_row.tx_out_addr),
                datum_hash=query_row.tx_out_data_hash.hex() if query_row.tx_out_data_hash else "",
                inline_datum_hash=query_row.tx_out_inline_datum_hash.hex()
                if query_row.tx_out_inline_datum_hash
                else "",
                reference_script_hash=query_row.tx_out_reference_script_hash.hex()
                if query_row.tx_out_reference_script_hash
                else "",
            )
            utxo_out.append(out_rec)

        # MA outputs
        if query_row.ma_tx_out_id and query_row.ma_tx_out_id not in seen_ma_tx_out_ids:
            seen_ma_tx_out_ids.add(query_row.ma_tx_out_id)
            asset_name = query_row.ma_tx_out_name.hex() if query_row.ma_tx_out_name else None
            policyid = query_row.ma_tx_out_policy.hex() if query_row.ma_tx_out_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            ma_rec = dbsync_types.UTxORecord(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.ma_tx_out_quantity or 0),
                address=str(query_row.tx_out_addr),
                coin=coin,
                datum_hash=query_row.tx_out_data_hash.hex() if query_row.tx_out_data_hash else "",
            )
            ma_utxo_out.append(ma_rec)

        # MA minting
        if query_row.ma_tx_mint_id and query_row.ma_tx_mint_id not in seen_ma_tx_mint_ids:
            seen_ma_tx_mint_ids.add(query_row.ma_tx_mint_id)
            asset_name = query_row.ma_tx_mint_name.hex() if query_row.ma_tx_mint_name else None
            policyid = query_row.ma_tx_mint_policy.hex() if query_row.ma_tx_mint_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            mint_rec = dbsync_types.UTxORecord(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.ma_tx_mint_quantity or 0),
                address="",  # This is available only for MA outputs
                coin=coin,
            )
            mint_utxo_out.append(mint_rec)

    if tx_id == -1:
        msg = "No results were returned by the TX SQL query."
        raise RuntimeError(msg)

    # pylint: disable=undefined-loop-variable
    txdata = dbsync_types.TxPrelimRecord(
        utxo_out=utxo_out,
        ma_utxo_out=ma_utxo_out,
        mint_utxo_out=mint_utxo_out,
        last_row=query_row,
    )

    return txdata


def get_txins(txhash: str) -> tp.List[dbsync_types.UTxORecord]:
    """Get txins of a transaction from db-sync."""
    txins: tp.List[dbsync_types.UTxORecord] = []
    seen_txins_out_ids = set()
    seen_txins_ma_ids = set()

    for txins_row in dbsync_queries.query_tx_ins(txhash=txhash):
        # Lovelace inputs
        if txins_row.tx_out_id and txins_row.tx_out_id not in seen_txins_out_ids:
            seen_txins_out_ids.add(txins_row.tx_out_id)
            txins.append(
                dbsync_types.UTxORecord(
                    utxo_hash=txins_row.tx_hash.hex(),
                    utxo_ix=int(txins_row.utxo_ix),
                    amount=int(txins_row.value),
                    address=str(txins_row.address),
                    reference_script_hash=txins_row.reference_script_hash.hex()
                    if txins_row.reference_script_hash
                    else "",
                )
            )

        # MA inputs
        if txins_row.ma_tx_out_id and txins_row.ma_tx_out_id not in seen_txins_ma_ids:
            seen_txins_ma_ids.add(txins_row.ma_tx_out_id)
            asset_name = txins_row.ma_tx_out_name.hex() if txins_row.ma_tx_out_name else None
            policyid = txins_row.ma_tx_out_policy.hex() if txins_row.ma_tx_out_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            txins.append(
                dbsync_types.UTxORecord(
                    utxo_hash=txins_row.tx_hash.hex(),
                    utxo_ix=int(txins_row.utxo_ix),
                    amount=int(txins_row.ma_tx_out_quantity or 0),
                    address=str(txins_row.address),
                    coin=coin,
                    reference_script_hash=txins_row.reference_script_hash.hex()
                    if txins_row.reference_script_hash
                    else "",
                )
            )

    return txins


def get_tx_record(txhash: str) -> dbsync_types.TxRecord:  # noqa: C901
    """Get transaction data from db-sync.

    Compile data from multiple SQL queries to get as much information about the TX as possible.
    """
    # pylint: disable=too-many-branches
    txdata = get_prelim_tx_record(txhash)
    txins = get_txins(txhash)

    metadata = []
    if txdata.last_row.metadata_count:
        metadata = [
            dbsync_types.MetadataRecord(key=int(r.key), json=r.json, bytes=r.bytes)
            for r in dbsync_queries.query_tx_metadata(txhash=txhash)
        ]

    reserve = []
    if txdata.last_row.reserve_count:
        reserve = [
            dbsync_types.ADAStashRecord(
                address=str(r.addr_view), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in dbsync_queries.query_tx_reserve(txhash=txhash)
        ]

    treasury = []
    if txdata.last_row.treasury_count:
        treasury = [
            dbsync_types.ADAStashRecord(
                address=str(r.addr_view), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in dbsync_queries.query_tx_treasury(txhash=txhash)
        ]

    pot_transfers = []
    if txdata.last_row.pot_transfer_count:
        pot_transfers = [
            dbsync_types.PotTransferRecord(treasury=int(r.treasury), reserves=int(r.reserves))
            for r in dbsync_queries.query_tx_pot_transfers(txhash=txhash)
        ]

    stake_registration = []
    if txdata.last_row.stake_reg_count:
        stake_registration = [r.view for r in dbsync_queries.query_tx_stake_reg(txhash=txhash)]

    stake_deregistration = []
    if txdata.last_row.stake_dereg_count:
        stake_deregistration = [r.view for r in dbsync_queries.query_tx_stake_dereg(txhash=txhash)]

    stake_delegation = []
    if txdata.last_row.stake_deleg_count:
        stake_delegation = [
            dbsync_types.DelegationRecord(
                address=r.address, pool_id=r.pool_id, active_epoch_no=r.active_epoch_no
            )
            for r in dbsync_queries.query_tx_stake_deleg(txhash=txhash)
            if (r.address and r.pool_id and r.active_epoch_no)
        ]

    withdrawals = []
    if txdata.last_row.withdrawal_count:
        withdrawals = [
            clusterlib.TxOut(address=r.address, amount=int(r.amount))
            for r in dbsync_queries.query_tx_withdrawal(txhash=txhash)
        ]

    collaterals = []
    if txdata.last_row.collateral_count:
        collaterals = [
            dbsync_types.UTxORecord(
                utxo_hash=r.tx_hash.hex(),
                utxo_ix=int(r.utxo_ix),
                amount=int(r.value),
                address=str(r.address),
                reference_script_hash=r.reference_script_hash.hex()
                if r.reference_script_hash
                else "",
            )
            for r in dbsync_queries.query_collateral_tx_ins(txhash=txhash)
        ]

    collateral_outputs = []
    if txdata.last_row.collateral_out_count:
        collateral_outputs = [
            clusterlib.UTXOData(
                utxo_hash=r.tx_hash.hex(),
                utxo_ix=int(r.utxo_ix),
                amount=int(r.value),
                address=str(r.address),
            )
            for r in dbsync_queries.query_collateral_tx_outs(txhash=txhash)
        ]

    reference_inputs = []
    if txdata.last_row.reference_input_count:
        reference_inputs = [
            dbsync_types.UTxORecord(
                utxo_hash=r.tx_hash.hex(),
                utxo_ix=int(r.utxo_ix),
                amount=int(r.value),
                address=str(r.address),
                reference_script_hash=r.reference_script_hash.hex()
                if r.reference_script_hash
                else "",
            )
            for r in dbsync_queries.query_reference_tx_ins(txhash=txhash)
        ]

    scripts = []
    if txdata.last_row.script_count:
        scripts = [
            dbsync_types.ScriptRecord(
                hash=r.hash.hex(),
                type=str(r.type),
                serialised_size=int(r.serialised_size) if r.serialised_size else 0,
            )
            for r in dbsync_queries.query_scripts(txhash=txhash)
        ]

    redeemers = []
    if txdata.last_row.redeemer_count:
        redeemers = [
            dbsync_types.RedeemerRecord(
                unit_mem=int(r.unit_mem),
                unit_steps=int(r.unit_steps),
                fee=int(r.fee),
                purpose=str(r.purpose),
                script_hash=r.script_hash.hex(),
                value=r.value,
            )
            for r in dbsync_queries.query_redeemers(txhash=txhash)
        ]

    extra_key_witness = []
    if txdata.last_row.extra_key_witness_count:
        extra_key_witness = [r.hex() for r in dbsync_queries.query_extra_key_witness(txhash=txhash)]

    record = dbsync_types.TxRecord(
        tx_id=int(txdata.last_row.tx_id),
        tx_hash=txdata.last_row.tx_hash.hex(),
        block_id=int(txdata.last_row.block_id),
        block_index=int(txdata.last_row.block_index),
        out_sum=int(txdata.last_row.out_sum),
        fee=int(txdata.last_row.fee),
        deposit=int(txdata.last_row.deposit),
        size=int(txdata.last_row.size),
        invalid_before=int(txdata.last_row.invalid_before)
        if txdata.last_row.invalid_before
        else None,
        invalid_hereafter=int(txdata.last_row.invalid_hereafter)
        if txdata.last_row.invalid_hereafter
        else None,
        txins=txins,
        txouts=[*txdata.utxo_out, *txdata.ma_utxo_out],
        mint=txdata.mint_utxo_out,
        collaterals=collaterals,
        collateral_outputs=collateral_outputs,
        reference_inputs=reference_inputs,
        scripts=scripts,
        redeemers=redeemers,
        metadata=metadata,
        reserve=reserve,
        treasury=treasury,
        pot_transfers=pot_transfers,
        stake_registration=stake_registration,
        stake_deregistration=stake_deregistration,
        stake_delegation=stake_delegation,
        withdrawals=withdrawals,
        extra_key_witness=extra_key_witness,
    )

    return record


def retry_query(query_func: tp.Callable, timeout: int = 20) -> tp.Any:
    """Wait a bit and retry a query until response is returned.

    A generic function that can be used by any query/check that raises `AssertionError` with
    `NO_RESPONSE_STR` until the expected data is returned.
    """
    end_time = time.time() + timeout
    repeat = 0

    while True:
        if repeat:
            sleep_time = 2 + repeat * repeat
            LOGGER.warning(f"Sleeping {sleep_time}s before repeating query for the {repeat} time.")
            time.sleep(sleep_time)
        try:
            response = query_func()
            break
        except AssertionError as exc:
            if NO_RESPONSE_STR in str(exc) and time.time() < end_time:
                repeat += 1
                continue
            raise

    return response


def get_tx_record_retry(txhash: str, retry_num: int = 3) -> dbsync_types.TxRecord:
    """Retry `get_tx_record` when data is anticipated and are not available yet.

    Under load it might be necessary to wait a bit and retry the query.
    """
    retry_num = retry_num if retry_num >= 0 else 0
    response = None

    # First try + number of retries
    for r in range(1 + retry_num):
        if r > 0:
            sleep_time = 2 + r * r
            LOGGER.warning(
                f"Sleeping {sleep_time}s before repeating TX SQL query for '{txhash}' "
                f"for the {r} time."
            )
            time.sleep(sleep_time)
        try:
            response = get_tx_record(txhash=txhash)
            break
        except RuntimeError:
            if r == retry_num:
                raise

    assert response is not None
    return response


def get_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput, retry_num: int = 3
) -> tp.Optional[dbsync_types.TxRecord]:
    """Get a transaction data from db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    txhash = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    response = get_tx_record_retry(txhash=txhash, retry_num=retry_num)

    return response


def check_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput, retry_num: int = 3
) -> tp.Optional[dbsync_types.TxRecord]:
    """Check a transaction in db-sync."""
    response = get_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output, retry_num=retry_num)

    if response is not None:
        dbsync_check_tx.check_tx(
            cluster_obj=cluster_obj, tx_raw_output=tx_raw_output, response=response
        )
    return response


def check_tx_phase_2_failure(
    cluster_obj: clusterlib.ClusterLib,
    tx_raw_output: clusterlib.TxRawOutput,
    collateral_charged: int,
    retry_num: int = 3,
) -> tp.Optional[dbsync_types.TxRecord]:
    """Check a transaction in db-sync when a phase 2 failure happens."""
    if not configuration.HAS_DBSYNC:
        return None

    txhash = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)
    response = get_tx_record_retry(txhash=txhash, retry_num=retry_num)

    # In case of a phase 2 failure, the collateral output becomes the output of the tx.

    assert (
        not response.collateral_outputs
    ), "Collateral outputs are present in dbsync when the tx have a phase 2 failure"

    db_txouts = {dbsync_check_tx.utxodata2txout(r) for r in response.txouts}
    tx_out = {
        dbsync_check_tx.utxodata2txout(r)
        for r in cluster_obj.g_query.get_utxo(tx_raw_output=tx_raw_output)
    }

    assert db_txouts == tx_out, f"The TX outputs don't match ({db_txouts} != {tx_out})"

    # In case of a phase 2 failure, the fee charged is the collateral amount.

    if tx_raw_output.total_collateral_amount:
        expected_fee = round(tx_raw_output.total_collateral_amount)
    elif tx_raw_output.return_collateral_txouts and not tx_raw_output.total_collateral_amount:
        expected_fee = collateral_charged
    else:
        protocol_params = cluster_obj.g_query.get_protocol_params()
        expected_fee = round(tx_raw_output.fee * protocol_params["collateralPercentage"] / 100)

    assert response.fee == expected_fee, f"TX fee doesn't match ({response.fee} != {expected_fee})"

    return response


def check_pool_deregistration(
    pool_id: str, retiring_epoch: int
) -> tp.Optional[dbsync_types.PoolDataRecord]:
    """Check pool retirement in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    db_pool_data = get_pool_data(pool_id)
    assert db_pool_data, f"No data returned from db-sync for pool {pool_id}"

    assert (
        db_pool_data.retire_announced_tx_id and db_pool_data.retiring_epoch
    ), f"Stake pool `{pool_id}` not retired"

    assert (
        retiring_epoch == db_pool_data.retiring_epoch
    ), f"Mismatch in epoch values: {retiring_epoch} vs {db_pool_data.retiring_epoch}"

    return db_pool_data


def check_pool_data(  # noqa: C901
    ledger_pool_data: dict, pool_id: str
) -> tp.Optional[dbsync_types.PoolDataRecord]:
    """Check comparison for pool data between ledger and db-sync."""
    # pylint: disable=too-many-branches
    if not configuration.HAS_DBSYNC:
        return None

    db_pool_data = get_pool_data(pool_id)
    assert db_pool_data, f"No data returned from db-sync for pool {pool_id}"

    errors_list = []

    if ledger_pool_data["publicKey"] != db_pool_data.hash:
        errors_list.append(
            "'publicKey' value is different than expected; "
            f"Expected: {ledger_pool_data['publicKey']} vs Returned: {db_pool_data.hash}"
        )

    if ledger_pool_data["cost"] != db_pool_data.fixed_cost:
        errors_list.append(
            "'cost' value is different than expected; "
            f"Expected: {ledger_pool_data['cost']} vs Returned: {db_pool_data.fixed_cost}"
        )

    metadata = ledger_pool_data.get("metadata") or {}

    metadata_hash = metadata.get("hash") or ""
    if metadata_hash != db_pool_data.metadata_hash:
        errors_list.append(
            "'metadata hash' value is different than expected; "
            f"Expected: {metadata_hash} vs "
            f"Returned: {db_pool_data.metadata_hash}"
        )

    metadata_url = metadata.get("url") or ""
    if metadata_url != db_pool_data.metadata_url:
        errors_list.append(
            "'metadata url' value is different than expected; "
            f"Expected: {metadata_url} vs "
            f"Returned: {db_pool_data.metadata_url}"
        )

    if sorted(ledger_pool_data["owners"]) != sorted(db_pool_data.owners):
        errors_list.append(
            "'owners' value is different than expected; "
            f"Expected: {ledger_pool_data['owners']} vs Returned: {db_pool_data.owners}"
        )

    if ledger_pool_data["vrf"] != db_pool_data.vrf_key_hash:
        errors_list.append(
            "'vrf' value is different than expected; "
            f"Expected: {ledger_pool_data['vrf']} vs Returned: {db_pool_data.vrf_key_hash}"
        )

    if ledger_pool_data["pledge"] != db_pool_data.pledge:
        errors_list.append(
            "'pledge' value is different than expected; "
            f"Expected: {ledger_pool_data['pledge']} vs Returned: {db_pool_data.pledge}"
        )

    if ledger_pool_data["margin"] != db_pool_data.margin:
        errors_list.append(
            "'margin' value is different than expected; "
            f"Expected: {ledger_pool_data['margin']} vs Returned: {db_pool_data.margin}"
        )

    ledger_reward_credential = ledger_pool_data["rewardAccount"]["credential"]
    # The "KeyHash" is present in cardano-node >= 8.4.0
    ledger_reward_address = (
        ledger_reward_credential.get("key hash") or ledger_reward_credential["keyHash"]
    )
    if ledger_reward_address != db_pool_data.reward_addr:
        errors_list.append(
            "'reward address' value is different than expected; "
            f"Expected: {ledger_reward_address} vs Returned: {db_pool_data.reward_addr}"
        )

    if ledger_pool_data["relays"] and ledger_pool_data["relays"] != db_pool_data.relays:
        errors_list.append(
            "'relays' value is different than expected; "
            f"Expected: {ledger_pool_data['relays']} vs Returned: {db_pool_data.relays}"
        )

    if errors_list:
        errors_str = "\n\n".join(errors_list)
        msg = f"{errors_str}\n\nStake Pool Details: \n{ledger_pool_data}"
        raise AssertionError(msg)

    return db_pool_data


def check_pool_off_chain_data(
    ledger_pool_data: dict, pool_id: str
) -> dbsync_queries.PoolOffChainDataDBRow:
    """Check comparison for pool off chain data between ledger and db-sync."""
    db_pool_off_chain_data = list(dbsync_queries.query_off_chain_pool_data(pool_id))
    assert db_pool_off_chain_data, f"{NO_RESPONSE_STR} no off chain data for pool {pool_id}"

    metadata_hash = (ledger_pool_data.get("metadata") or {}).get("hash") or ""
    db_metadata_hash = db_pool_off_chain_data[0].hash.hex()

    assert metadata_hash == db_metadata_hash, (
        "'metadata hash' value is different than expected; "
        f"Expected: {metadata_hash} vs Returned: {db_metadata_hash}"
    )

    return db_pool_off_chain_data[0]


def check_pool_off_chain_fetch_error(
    ledger_pool_data: dict, pool_id: str
) -> dbsync_queries.PoolOffChainFetchErrorDBRow:
    """Check expected error on `PoolOffChainFetchError`."""
    db_pool_off_chain_fetch_error = list(dbsync_queries.query_off_chain_pool_fetch_error(pool_id))
    assert (
        db_pool_off_chain_fetch_error
    ), f"{NO_RESPONSE_STR} no off chain fetch error for pool {pool_id}"

    fetch_error_str = db_pool_off_chain_fetch_error[0].fetch_error or ""
    metadata_url = (ledger_pool_data.get("metadata") or {}).get("url") or ""

    assert (
        f'Connection failure error when fetching metadata from PoolUrl "{metadata_url}"'
        in fetch_error_str
    ) or (
        f"Error Offchain Pool: Connection failure error when fetching metadata from {metadata_url}"
        in fetch_error_str  # in db-sync > 13.2.0.1
    ), f"The error is not the expected one: {fetch_error_str}"

    return db_pool_off_chain_fetch_error[0]


def check_plutus_cost(
    redeemer_record: dbsync_types.RedeemerRecord, cost_record: tp.Dict[str, tp.Any]
) -> None:
    """Compare cost of Plutus script with data from db-sync."""
    errors = []
    if redeemer_record.unit_steps != cost_record["executionUnits"]["steps"]:
        errors.append(
            f'time: {redeemer_record.unit_steps} vs {cost_record["executionUnits"]["steps"]}'
        )
    if redeemer_record.unit_mem != cost_record["executionUnits"]["memory"]:
        errors.append(
            f'space: {redeemer_record.unit_mem} vs {cost_record["executionUnits"]["memory"]}'
        )
    if redeemer_record.fee != cost_record["lovelaceCost"]:
        errors.append(f'fixed cost: {redeemer_record.fee} vs {cost_record["lovelaceCost"]}')
    if redeemer_record.script_hash != cost_record["scriptHash"]:
        errors.append(f'script hash: {redeemer_record.script_hash} vs {cost_record["scriptHash"]}')

    if errors:
        raise AssertionError("\n".join(errors))


def check_plutus_costs(
    redeemer_records: tp.List[dbsync_types.RedeemerRecord],
    cost_records: tp.List[tp.Dict[str, tp.Any]],
) -> None:
    """Compare cost of multiple Plutus scripts with data from db-sync."""
    # Sort records first by total cost, second by hash
    sorted_costs = sorted(
        cost_records,
        key=lambda x: (
            x["executionUnits"]["memory"]  # type: ignore
            + x["executionUnits"]["steps"]
            + x["lovelaceCost"],
            x["scriptHash"],
        ),
    )
    sorted_db = sorted(
        redeemer_records, key=lambda x: (x.unit_mem + x.unit_steps + x.fee, x.script_hash)
    )

    if len(sorted_costs) != len(sorted_db):
        msg = f"Number of cost records is different:\n{sorted_costs}\nvs\n{sorted_db}"
        raise AssertionError(msg)

    errors = []
    for db_record, cost_record in zip(sorted_db, sorted_costs):
        try:
            check_plutus_cost(redeemer_record=db_record, cost_record=cost_record)
        except AssertionError as err:
            errors.append(f"{db_record.script_hash}:\n{err}")

    if errors:
        raise AssertionError("\n".join(errors))


def check_param_proposal(protocol_params: dict) -> tp.Optional[dbsync_queries.ParamProposalDBRow]:
    """Check expected values in the `param_proposal` table in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    param_proposal_db = dbsync_queries.query_param_proposal()

    params_mapping = {
        "coins_per_utxo_size": protocol_params["utxoCostPerByte"],
        "collateral_percent": protocol_params["collateralPercentage"],
        "influence": protocol_params["poolPledgeInfluence"],
        "key_deposit": protocol_params["stakeAddressDeposit"],
        "max_bh_size": protocol_params["maxBlockHeaderSize"],
        "max_block_ex_mem": protocol_params["maxBlockExecutionUnits"]["memory"],
        "max_block_ex_steps": protocol_params["maxBlockExecutionUnits"]["steps"],
        "max_block_size": protocol_params["maxBlockBodySize"],
        "max_collateral_inputs": protocol_params["maxCollateralInputs"],
        "max_epoch": protocol_params["poolRetireMaxEpoch"],
        "max_tx_ex_mem": protocol_params["maxTxExecutionUnits"]["memory"],
        "max_tx_ex_steps": protocol_params["maxTxExecutionUnits"]["steps"],
        "max_tx_size": protocol_params["maxTxSize"],
        "max_val_size": protocol_params["maxValueSize"],
        "min_fee_a": protocol_params["txFeePerByte"],
        "min_fee_b": protocol_params["txFeeFixed"],
        "min_pool_cost": protocol_params["minPoolCost"],
        "min_utxo_value": protocol_params.get("minUTxOValue"),  # removed in node 8.12.0
        "optimal_pool_count": protocol_params["stakePoolTargetNum"],
        "pool_deposit": protocol_params["stakePoolDeposit"],
    }

    failures = []

    for param_db, protocol_value in params_mapping.items():
        db_value = getattr(param_proposal_db, param_db)
        if db_value and (db_value != protocol_value):
            failures.append(f"Param value for {param_db}: {db_value}. Expected: {protocol_value}")

    if failures:
        failures_str = "\n".join(failures)
        msg = f"Unexpected parameter proposal values in db-sync:\n{failures_str}"
        raise AssertionError(msg)

    return param_proposal_db


def _get_float_pparam(pparam: tp.Any) -> tp.Optional[float]:
    if pparam is None:
        return None
    if isinstance(pparam, dict):
        numerator = pparam.get("numerator", 0)
        denominator = pparam.get("denominator", 1)
        return float(numerator / denominator)
    return float(pparam)


def map_params_to_db_convention(pp: dict) -> tp.Dict[str, tp.Any]:
    # Get the prices of memory and steps
    prices = pp.get("executionUnitPrices", {})
    price_mem = _get_float_pparam(prices.get("priceMemory"))
    price_steps = _get_float_pparam(prices.get("priceSteps"))

    dvt = pp.get("dRepVotingThresholds", {})
    pvt = pp.get("poolVotingThresholds", {})

    params_mapping = {
        # Network proposals group
        "max_block_size": pp.get("maxBlockBodySize"),
        "max_tx_size": pp.get("maxTxSize"),
        "max_bh_size": pp.get("maxBlockHeaderSize"),
        "max_val_size": pp.get("maxValueSize"),
        "max_tx_ex_mem": pp.get("maxTxExecutionUnits", {}).get("memory"),
        "max_tx_ex_steps": pp.get("maxTxExecutionUnits", {}).get("steps"),
        "max_block_ex_mem": pp.get("maxBlockExecutionUnits", {}).get("memory"),
        "max_block_ex_steps": pp.get("maxBlockExecutionUnits", {}).get("steps"),
        "max_collateral_inputs": pp.get("maxCollateralInputs"),
        # Economic proposals group
        "min_fee_a": pp.get("txFeePerByte"),
        "min_fee_b": pp.get("txFeeFixed"),
        "key_deposit": pp.get("stakeAddressDeposit"),
        "pool_deposit": pp.get("stakePoolDeposit"),
        "monetary_expand_rate": _get_float_pparam(pp.get("monetaryExpansion")),
        "treasury_growth_rate": _get_float_pparam(pp.get("treasuryCut")),
        "min_pool_cost": pp.get("minPoolCost"),
        "coins_per_utxo_size": pp.get("utxoCostPerByte"),
        "min_fee_ref_script_cost_per_byte": pp.get("minFeeRefScriptCostPerByte"),
        "price_mem": price_mem,
        "price_step": price_steps,
        # Technical proposals group
        "influence": _get_float_pparam(pp.get("poolPledgeInfluence")),
        "max_epoch": pp.get("poolRetireMaxEpoch"),
        "optimal_pool_count": pp.get("stakePoolTargetNum"),
        "collateral_percent": pp.get("collateralPercentage"),
        # Governance proposal group
        # - DReps
        "dvt_committee_no_confidence": _get_float_pparam(dvt.get("committeeNoConfidence")),
        "dvt_committee_normal": _get_float_pparam(dvt.get("committeeNormal")),
        "dvt_hard_fork_initiation": _get_float_pparam(dvt.get("hardForkInitiation")),
        "dvt_motion_no_confidence": _get_float_pparam(dvt.get("motionNoConfidence")),
        "dvt_p_p_economic_group": _get_float_pparam(dvt.get("ppEconomicGroup")),
        "dvt_p_p_gov_group": _get_float_pparam(dvt.get("ppGovGroup")),
        "dvt_p_p_network_group": _get_float_pparam(dvt.get("ppNetworkGroup")),
        "dvt_p_p_technical_group": _get_float_pparam(dvt.get("ppTechnicalGroup")),
        "dvt_treasury_withdrawal": _get_float_pparam(dvt.get("treasuryWithdrawal")),
        "dvt_update_to_constitution": _get_float_pparam(dvt.get("updateToConstitution")),
        # - Pools
        "pvt_committee_no_confidence": _get_float_pparam(pvt.get("committeeNoConfidence")),
        "pvt_committee_normal": _get_float_pparam(pvt.get("committeeNormal")),
        "pvt_hard_fork_initiation": _get_float_pparam(pvt.get("hardForkInitiation")),
        "pvt_motion_no_confidence": _get_float_pparam(pvt.get("motionNoConfidence")),
        "pvtpp_security_group": _get_float_pparam(pvt.get("ppSecurityGroup")),
        # General
        "gov_action_lifetime": pp.get("govActionLifetime"),
        "gov_action_deposit": pp.get("govActionDeposit"),
        "drep_deposit": pp.get("dRepDeposit"),
        "drep_activity": pp.get("dRepActivity"),
        "committee_min_size": pp.get("committeeMinSize"),
        "committee_max_term_length": pp.get("committeeMaxTermLength"),
    }

    return params_mapping


def _check_param_proposal(
    param_proposal_db: tp.Union[dbsync_queries.ParamProposalDBRow, dbsync_queries.EpochParamDBRow],
    params_map: dict,
) -> list:
    """Check parameter proposal against db-sync."""
    failures = []

    for param_name, protocol_value in params_map.items():
        if protocol_value:
            db_value = getattr(param_proposal_db, param_name)
            if db_value and (db_value != protocol_value):
                failures.append(
                    f"Param value for {param_name}: {db_value}. Expected: {protocol_value}"
                )
    return failures


def check_conway_param_update_proposal(
    param_proposal_ledger: dict,
) -> tp.Optional[dbsync_queries.ParamProposalDBRow]:
    """Check comparison for param proposal between ledger and db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    param_proposal_db = dbsync_queries.query_param_proposal()
    params_map = map_params_to_db_convention(param_proposal_ledger)
    failures = []

    # Get cost models
    if param_proposal_db.cost_model_id:
        db_cost_model = dbsync_queries.query_cost_model(model_id=param_proposal_db.cost_model_id)
        pp_cost_model = param_proposal_ledger.get("costModels")
        if db_cost_model != pp_cost_model:
            failures.append(f"Cost model mismatch for {db_cost_model}. Expected: {pp_cost_model}")
    failures.extend(_check_param_proposal(param_proposal_db, params_map))

    if failures:
        failures_str = "\n".join(failures)
        msg = f"Unexpected parameter proposal values in db-sync:\n{failures_str}"
        raise AssertionError(msg)
    return param_proposal_db


def check_conway_param_update_enactment(
    gov_state: dict, epoch_no: int
) -> tp.Optional[dbsync_queries.EpochParamDBRow]:
    """Check params enactment between ledger and epoch param in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    curr_params_db = dbsync_queries.query_epoch_param(epoch_no)
    curr_params_ledger = gov_state["currentPParams"]
    params_map = map_params_to_db_convention(curr_params_ledger)
    failures = _check_param_proposal(curr_params_db, params_map)

    if failures:
        failures_str = "\n".join(failures)
        msg = f"Unexpected enacted param values in db-sync:\n{failures_str}"
        raise AssertionError(msg)
    return curr_params_db


def check_proposal_refunds(stake_address: str, refunds_num: int) -> None:
    """Check proposal refunds in db-sync."""
    if not configuration.HAS_DBSYNC:
        return

    failures = []
    rewards_rest = list(dbsync_queries.query_address_reward_rest(stake_address))
    assert refunds_num == len(
        rewards_rest
    ), f"Expected {refunds_num} refunds, got: {len(rewards_rest)}"
    for reward in rewards_rest:
        if reward.type != "proposal_refund":
            failures.append(f"Expected proposal refund, got: {reward.type}")
        if reward.spendable_epoch != reward.earned_epoch + 1:
            failures.append("Incorrect relation between spendable and earned epochs")

    if failures:
        failures_str = "\n".join(failures)
        msg = f"Wrong values for proposal refunds in db-sync:\n{failures_str}"
        raise AssertionError(msg)


def check_conway_gov_action_proposal_description(
    update_proposal: dict, txhash: str = "", action_ix: int = 0
) -> tp.Optional[dbsync_queries.GovActionProposalDBRow]:
    """Check expected values in the gov_action_proposal table in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    gov_actions_all = get_gov_action_proposals(txhash=txhash)
    assert gov_actions_all, "No data returned from db-sync for gov action proposal"
    assert len(gov_actions_all) >= action_ix + 1, "Unexpected number of gov actions"

    gov_action = gov_actions_all[action_ix]
    db_gov_prop_desc = gov_action.description["contents"][1]

    if db_gov_prop_desc != update_proposal:
        msg = f"Comparison {db_gov_prop_desc} failed in db-sync:\n" f"Expected {update_proposal}"
        raise AssertionError(msg)
    return gov_action


def get_gov_action_proposals(
    txhash: str = "", type: str = ""
) -> tp.List[dbsync_queries.GovActionProposalDBRow]:
    """Get government action proposal from db-sync."""
    gov_action_proposals = list(dbsync_queries.query_gov_action_proposal(txhash=txhash, type=type))
    return gov_action_proposals


def get_committee_member(cold_key: str) -> tp.Optional[dbsync_types.CommitteeRegistrationRecord]:
    """Get committee member data from db-sync."""
    cc_members = list(dbsync_queries.query_committee_registration(cold_key=cold_key))
    if not cc_members:
        return None

    # TODO: handle multiple records for the same CC member
    cc_member = cc_members[-1]
    cc_member_data = dbsync_types.CommitteeRegistrationRecord(
        id=cc_member.id,
        tx_id=cc_member.tx_id,
        cert_index=cc_member.cert_index,
        cold_key=cc_member.cold_key.hex(),
        hot_key=cc_member.hot_key.hex(),
    )

    return cc_member_data


def check_committee_member_registration(
    cc_member_cold_key: str,
) -> tp.Optional[dbsync_types.CommitteeRegistrationRecord]:
    """Check committee member registration in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    cc_member_data = get_committee_member(cold_key=cc_member_cold_key)

    assert cc_member_data, "No data returned from db-sync"
    assert (
        cc_member_data.cold_key == cc_member_cold_key
    ), "CC Member not present in registration table in db-sync"

    return cc_member_data


def get_deregistered_committee_member(
    cold_key: str,
) -> tp.Optional[dbsync_types.CommitteeDeregistrationRecord]:
    """Get deregistered committee member data from db-sync."""
    deregistered_cc_members = list(dbsync_queries.query_committee_deregistration(cold_key=cold_key))
    if not deregistered_cc_members:
        return None

    # TODO: handle multiple records for the same CC member
    deregistered_cc_member = deregistered_cc_members[-1]
    deregistered_cc_member_data = dbsync_types.CommitteeDeregistrationRecord(
        id=deregistered_cc_member.id,
        tx_id=deregistered_cc_member.tx_id,
        cert_index=deregistered_cc_member.cert_index,
        voting_anchor_id=deregistered_cc_member.voting_anchor_id,
        cold_key=deregistered_cc_member.cold_key.hex(),
    )

    return deregistered_cc_member_data


def check_committee_member_deregistration(
    cc_member_cold_key: str,
) -> tp.Optional[dbsync_types.CommitteeDeregistrationRecord]:
    """Check committee member deregistration in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    cc_member_data = get_deregistered_committee_member(cold_key=cc_member_cold_key)
    member_key = f"keyHash-{cc_member_cold_key}"

    assert cc_member_data, f"No data returned from db-sync for CC Member {member_key}"
    assert (
        cc_member_cold_key == cc_member_data.cold_key
    ), "CC Member not present in deregistration table in db-sync"

    return cc_member_data


def get_drep(drep_hash: str, drep_deposit: int) -> tp.Optional[dbsync_types.DrepRegistrationRecord]:
    """Get drep data from db-sync."""
    dreps = list(dbsync_queries.query_drep_registration(drep_hash, drep_deposit))
    if not dreps:
        return None

    drep = dreps[-1]
    drep_data = dbsync_types.DrepRegistrationRecord(
        id=drep.id,
        tx_id=drep.tx_id,
        cert_index=drep.cert_index,
        deposit=drep.deposit,
        drep_hash_id=drep.drep_hash_id,
        voting_anchor_id=drep.voting_anchor_id,
        hash_hex=drep.hash_raw.hex(),
        hash_bech32=drep.hash_view,
        has_script=drep.has_script,
    )

    return drep_data


def check_drep_registration(
    drep: governance_utils.DRepRegistration, drep_state: tp.List[tp.List[tp.Dict[str, tp.Any]]]
) -> tp.Optional[dbsync_types.DrepRegistrationRecord]:
    """Check drep registration in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    drep_data = get_drep(drep_hash=drep.drep_id, drep_deposit=drep.deposit)

    assert drep_data, f"No data returned from db-sync for DRep {drep.drep_id} registration"
    assert (
        drep_state[0][0]["keyHash"] == drep_data.hash_hex
    ), f"DRep {drep.drep_id} not present in registration table in db-sync"
    assert (
        drep_state[0][1]["deposit"] == drep_data.deposit
    ), f"Wrong deposit value for registered DRep {drep.drep_id} in db-sync"

    return drep_data


def check_drep_deregistration(
    drep: governance_utils.DRepRegistration,
) -> tp.Optional[dbsync_types.DrepRegistrationRecord]:
    """Check drep deregistration in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    drep_data = get_drep(drep_hash=drep.drep_id, drep_deposit=-drep.deposit)

    assert drep_data, f"No data returned from db-sync for DRep {drep.drep_id} deregistration"
    assert (
        drep.drep_id == drep_data.hash_hex
    ), f"Deregistered DRep {drep.drep_id} not present in registration table in db-sync"
    assert (
        drep.deposit == -drep_data.deposit
    ), f"Wrong deposit value for deregistered DRep {drep.drep_id} in db-sync"

    return drep_data


def check_votes(votes: governance_utils.VotedVotes, txhash: str) -> None:
    """Check votes in db-sync."""
    if not configuration.HAS_DBSYNC:
        return

    expected_votes_by_role = {
        "ConstitutionalCommittee": [v.vote.name for v in votes.cc],
        "DRep": [v.vote.name for v in votes.drep],
        "SPO": [v.vote.name for v in votes.spo],
    }

    dbsync_votes = list(dbsync_queries.query_voting_procedure(txhash=txhash))

    dbsync_votes_by_role: dict = {
        "ConstitutionalCommittee": [],
        "DRep": [],
        "SPO": [],
    }

    for d_vote in dbsync_votes:
        dbsync_votes_by_role[d_vote.voter_role].append(d_vote.vote.upper())

    assert expected_votes_by_role == dbsync_votes_by_role, "Votes didn't match in dbsync"


def check_committee_info(gov_state: dict, txid: str) -> None:
    """Check committee info in db-sync."""
    if not configuration.HAS_DBSYNC:
        return

    prop = governance_utils.lookup_proposal(gov_state=gov_state, action_txid=txid)

    # Check dbsync
    dbsync_committee_threshold = list(dbsync_queries.query_new_committee_info(txhash=txid))
    assert (
        dbsync_committee_threshold[0].quorum_denominator == 3
    ), "Incorrect committee threshold denominator in dbsync"
    assert (
        dbsync_committee_threshold[0].quorum_numerator == 2
    ), "Incorrect committee threshold numerator in dbsync"

    dbsync_committee_members = list(
        dbsync_queries.query_committee_members(committee_id=dbsync_committee_threshold[0].id)
    )
    size_of_proposed_cm = len(prop["proposalProcedure"]["govAction"]["contents"][2]) + len(
        gov_state["committee"]["members"]
    )
    assert (
        len(dbsync_committee_members) == size_of_proposed_cm
    ), "The number of committee members doesn't match in dbsync"


def check_treasury_withdrawal(
    actions_num: int, stake_address: str, transfer_amt: int, txhash: str
) -> None:
    """Check treasury_withdrawal in db-sync."""
    if not configuration.HAS_DBSYNC:
        return

    db_tr_withdrawals = list(dbsync_queries.query_treasury_withdrawal(txhash=txhash))
    assert len(db_tr_withdrawals) == actions_num, (
        f"Assertion failed: Expected {actions_num} records but got {len(db_tr_withdrawals)}."
        f"Data in db-sync: {db_tr_withdrawals}"
    )

    for row in db_tr_withdrawals:
        assert row.addr_view == stake_address, "Wrong stake address in db-sync"
        assert row.amount == transfer_amt, "Wrong transfer amount in db-sync"
        assert row.enacted_epoch, "Action not marked as enacted in db-sync"
        assert (
            row.enacted_epoch == row.ratified_epoch + 1
        ), "Wrong relation between enacted and ratified epochs in db-sync"
        assert (
            row.enacted_epoch == row.dropped_epoch
        ), "Wrong relation between enacted and dropped epochs in db-sync"


def check_reward_rest(
    actions_num: int,
    stake_address: str,
    transfer_amt: int,
) -> None:
    """Check reward_rest in db-sync."""
    if not configuration.HAS_DBSYNC:
        return

    db_rewards = list(dbsync_queries.query_address_reward_rest(stake_address))
    assert len(db_rewards) == actions_num, (
        f"Assertion failed: Expected {actions_num} records but got {len(db_rewards)}."
        f"Data in db-sync: {db_rewards}"
    )

    for row in db_rewards:
        assert row.address == stake_address, "Wrong stake address in db-sync"
        assert row.amount == transfer_amt, "Wrong transfer amount in db-sync"
        assert (
            row.spendable_epoch == row.earned_epoch + 1
        ), "Wrong relation between earned and spendable epochs in db-sync"
        assert row.type == "treasury", "Type not marked as treasury in db-sync"


def check_off_chain_drep_registration(
    drep_data: dbsync_types.DrepRegistrationRecord, metadata: dict
) -> None:
    """Check DRep off chain data in db-sync."""
    if not configuration.HAS_DBSYNC:
        return

    errors = []

    drep_off_chain_metadata = list(
        dbsync_queries.query_off_chain_vote_drep_data(voting_anchor_id=drep_data.voting_anchor_id)
    )

    assert (
        drep_off_chain_metadata
    ), f"{NO_RESPONSE_STR} no off chain drep metadata for drep {drep_data.id}"

    db_metadata = drep_off_chain_metadata[0]
    expected_metadata = metadata["body"]

    if db_metadata.payment_address != expected_metadata["paymentAddress"]:
        errors.append("'paymentAddress' value is different than expected;")

    if db_metadata.given_name != expected_metadata["givenName"]:
        errors.append("'givenName' value is different than expected;")

    if db_metadata.objectives != expected_metadata["objectives"]:
        errors.append("'objectives' value is different than expected;")

    if db_metadata.motivations != expected_metadata["motivations"]:
        errors.append("'motivations' value is different than expected;")

    if db_metadata.qualifications != expected_metadata["qualifications"]:
        errors.append("'qualifications' value is different than expected;")

    if errors:
        raise AssertionError("\n".join(errors))
