"""Utilities that extends the functionality of `cardano-clusterlib`."""
# pylint: disable=abstract-class-instantiated
import contextlib
import itertools
import json
import logging
import math
import time
from pathlib import Path
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Union

import cbor2
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)


class UpdateProposal(NamedTuple):
    arg: str
    value: Any
    name: str = ""


class TokenRecord(NamedTuple):
    token: str
    amount: int
    issuers_addrs: List[clusterlib.AddressRecord]
    token_mint_addr: clusterlib.AddressRecord
    script: Path


class TxMetadata(NamedTuple):
    metadata: dict
    aux_data: list


@helpers.callonce
def _cli_has_query_pool_state() -> bool:
    """Return if `query pool-state` is available."""
    return cli_has("query pool-state")


def get_pool_state(
    cluster_obj: clusterlib.ClusterLib,
    pool_id: str,
) -> clusterlib.PoolParamsTop:
    """Get pool state using the available command."""
    return (
        cluster_obj.g_query.get_pool_state(pool_id)
        if _cli_has_query_pool_state()
        else cluster_obj.g_query.get_pool_params(pool_id)
    )


def register_stake_address(
    cluster_obj: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser, name_template: str
) -> clusterlib.TxRawOutput:
    """Register stake address."""
    # files for registering stake address
    addr_reg_cert = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
        addr_name=name_template,
        stake_vkey_file=pool_user.stake.vkey_file,
    )
    tx_files = clusterlib.TxFiles(
        certificate_files=[addr_reg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    tx_raw_output = cluster_obj.g_transaction.send_tx(
        src_address=pool_user.payment.address,
        tx_name=f"{name_template}_reg_stake_addr",
        tx_files=tx_files,
    )

    if not cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        raise AssertionError(f"The address {pool_user.stake.address} was not registered.")

    return tx_raw_output


def deregister_stake_address(
    cluster_obj: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser, name_template: str
) -> Tuple[clusterlib.TxRawOutput, clusterlib.TxRawOutput]:
    """Deregister stake address."""
    # files for deregistering stake address
    stake_addr_dereg_cert = cluster_obj.g_stake_address.gen_stake_addr_deregistration_cert(
        addr_name=f"{name_template}_addr0_dereg", stake_vkey_file=pool_user.stake.vkey_file
    )
    tx_files_deregister = clusterlib.TxFiles(
        certificate_files=[stake_addr_dereg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    # withdraw rewards to payment address
    tx_raw_output_withdrawal = cluster_obj.g_stake_address.withdraw_reward(
        stake_addr_record=pool_user.stake,
        dst_addr_record=pool_user.payment,
        tx_name=name_template,
    )

    # deregister the stake address
    tx_raw_output_dereg = cluster_obj.g_transaction.send_tx(
        src_address=pool_user.payment.address,
        tx_name=f"{name_template}_dereg_stake_addr",
        tx_files=tx_files_deregister,
    )
    return tx_raw_output_withdrawal, tx_raw_output_dereg


def fund_from_genesis(
    *dst_addrs: str,
    cluster_obj: clusterlib.ClusterLib,
    amount: int = 2_000_000,
    tx_name: Optional[str] = None,
    destination_dir: FileType = ".",
) -> None:
    """Send `amount` from genesis addr to all `dst_addrs`."""
    fund_dst = [
        clusterlib.TxOut(address=d, amount=amount)
        for d in dst_addrs
        if cluster_obj.g_query.get_address_balance(d) < amount
    ]
    if not fund_dst:
        return

    with locking.FileLockIfXdist(
        f"{temptools.get_basetemp()}/{cluster_obj.g_genesis.genesis_utxo_addr}.lock"
    ):
        tx_name = tx_name or helpers.get_timestamped_rand_str()
        tx_name = f"{tx_name}_genesis_funding"
        fund_tx_files = clusterlib.TxFiles(
            signing_key_files=[
                *cluster_obj.g_genesis.genesis_keys.delegate_skeys,
                cluster_obj.g_genesis.genesis_keys.genesis_utxo_skey,
            ]
        )

        cluster_obj.g_transaction.send_funds(
            src_address=cluster_obj.g_genesis.genesis_utxo_addr,
            destinations=fund_dst,
            tx_name=tx_name,
            tx_files=fund_tx_files,
            destination_dir=destination_dir,
        )


def return_funds_to_faucet(
    *src_addrs: clusterlib.AddressRecord,
    cluster_obj: clusterlib.ClusterLib,
    faucet_addr: str,
    amount: Union[int, List[int]] = -1,
    tx_name: Optional[str] = None,
    destination_dir: FileType = ".",
) -> None:
    """Send `amount` from all `src_addrs` to `faucet_addr`.

    The amount of "-1" means all available funds.
    """
    tx_name = tx_name or helpers.get_timestamped_rand_str()
    tx_name = f"{tx_name}_return_funds"
    if isinstance(amount, int):
        amount = [amount] * len(src_addrs)

    with locking.FileLockIfXdist(f"{temptools.get_basetemp()}/{faucet_addr}.lock"):
        try:
            logging.disable(logging.ERROR)
            for addr, amount_rec in zip(src_addrs, amount):
                fund_dst = [clusterlib.TxOut(address=faucet_addr, amount=amount_rec)]
                fund_tx_files = clusterlib.TxFiles(signing_key_files=[addr.skey_file])
                # try to return funds; don't mind if there's not enough funds for fees etc.
                with contextlib.suppress(Exception):
                    cluster_obj.g_transaction.send_funds(
                        src_address=addr.address,
                        destinations=fund_dst,
                        tx_name=tx_name,
                        tx_files=fund_tx_files,
                        destination_dir=destination_dir,
                    )
        finally:
            logging.disable(logging.NOTSET)


def fund_from_faucet(
    *dst_addrs: Union[clusterlib.AddressRecord, clusterlib.PoolUser],
    cluster_obj: clusterlib.ClusterLib,
    faucet_data: dict,
    amount: Union[int, List[int]] = 1000_000_000,
    tx_name: Optional[str] = None,
    destination_dir: FileType = ".",
    force: bool = False,
) -> Optional[clusterlib.TxRawOutput]:
    """Send `amount` from faucet addr to all `dst_addrs`."""
    # get payment AddressRecord out of PoolUser
    dst_addr_records: List[clusterlib.AddressRecord] = [
        (r.payment if hasattr(r, "payment") else r) for r in dst_addrs  # type: ignore
    ]
    if isinstance(amount, int):
        amount = [amount] * len(dst_addr_records)

    fund_dst = [
        clusterlib.TxOut(address=d.address, amount=a)
        for d, a in zip(dst_addr_records, amount)
        if force or cluster_obj.g_query.get_address_balance(d.address) < a
    ]
    if not fund_dst:
        return None

    src_address = faucet_data["payment"].address
    with locking.FileLockIfXdist(f"{temptools.get_basetemp()}/{src_address}.lock"):
        tx_name = tx_name or helpers.get_timestamped_rand_str()
        tx_name = f"{tx_name}_funding"
        fund_tx_files = clusterlib.TxFiles(signing_key_files=[faucet_data["payment"].skey_file])

        tx_raw_output = cluster_obj.g_transaction.send_funds(
            src_address=src_address,
            destinations=fund_dst,
            tx_name=tx_name,
            tx_files=fund_tx_files,
            destination_dir=destination_dir,
        )

    return tx_raw_output


def create_payment_addr_records(
    *names: str,
    cluster_obj: clusterlib.ClusterLib,
    stake_vkey_file: Optional[FileType] = None,
    destination_dir: FileType = ".",
) -> List[clusterlib.AddressRecord]:
    """Create new payment address(es)."""
    addrs = [
        cluster_obj.g_address.gen_payment_addr_and_keys(
            name=name,
            stake_vkey_file=stake_vkey_file,
            destination_dir=destination_dir,
        )
        for name in names
    ]

    LOGGER.debug(f"Created {len(addrs)} payment address(es)")
    return addrs


def create_stake_addr_records(
    *names: str,
    cluster_obj: clusterlib.ClusterLib,
    destination_dir: FileType = ".",
) -> List[clusterlib.AddressRecord]:
    """Create new stake address(es)."""
    addrs = [
        cluster_obj.g_stake_address.gen_stake_addr_and_keys(
            name=name, destination_dir=destination_dir
        )
        for name in names
    ]

    LOGGER.debug(f"Created {len(addrs)} stake address(es)")
    return addrs


def create_pool_users(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    no_of_addr: int = 1,
) -> List[clusterlib.PoolUser]:
    """Create PoolUsers."""
    pool_users = []
    for i in range(no_of_addr):
        # create key pairs and addresses
        stake_addr_rec = create_stake_addr_records(
            f"{name_template}_addr{i}", cluster_obj=cluster_obj
        )[0]
        payment_addr_rec = create_payment_addr_records(
            f"{name_template}_addr{i}",
            cluster_obj=cluster_obj,
            stake_vkey_file=stake_addr_rec.vkey_file,
        )[0]
        # create pool user struct
        pool_user = clusterlib.PoolUser(payment=payment_addr_rec, stake=stake_addr_rec)
        pool_users.append(pool_user)

    return pool_users


def wait_for_rewards(cluster_obj: clusterlib.ClusterLib) -> None:
    """Wait until 4th epoch, if necessary, for first reward distribution."""
    epoch = cluster_obj.g_query.get_epoch()
    if epoch >= 4:
        return

    new_epochs = 4 - epoch
    LOGGER.info(f"Waiting {new_epochs} epoch(s) to get first rewards.")
    cluster_obj.wait_for_new_epoch(new_epochs, padding_seconds=10)


def load_registered_pool_data(
    cluster_obj: clusterlib.ClusterLib, pool_name: str, pool_id: str
) -> clusterlib.PoolData:
    """Load data of existing registered pool."""
    if pool_id.startswith("pool"):
        pool_id = helpers.decode_bech32(pool_id)

    pool_state: dict = get_pool_state(cluster_obj=cluster_obj, pool_id=pool_id).pool_params
    metadata = pool_state.get("metadata") or {}

    # TODO: extend to handle more relays records
    relays_list = pool_state.get("relays") or []
    relay = relays_list[0] if relays_list else {}
    relay = relay.get("single host address") or {}

    pool_data = clusterlib.PoolData(
        pool_name=pool_name,
        pool_pledge=pool_state["pledge"],
        pool_cost=pool_state["cost"],
        pool_margin=pool_state["margin"],
        pool_metadata_url=metadata.get("url") or "",
        pool_metadata_hash=metadata.get("hash") or "",
        pool_relay_ipv4=relay.get("IPv4") or "",
        pool_relay_port=relay.get("port") or 0,
    )

    return pool_data


def check_pool_data(  # noqa: C901
    pool_params: dict, pool_creation_data: clusterlib.PoolData
) -> str:
    """Check that actual pool state corresponds with pool creation data."""
    errors_list = []

    if pool_params["cost"] != pool_creation_data.pool_cost:
        errors_list.append(
            "'cost' value is different than expected; "
            f"Expected: {pool_creation_data.pool_cost} vs Returned: {pool_params['cost']}"
        )

    if pool_params["margin"] != pool_creation_data.pool_margin:
        errors_list.append(
            "'margin' value is different than expected; "
            f"Expected: {pool_creation_data.pool_margin} vs Returned: {pool_params['margin']}"
        )

    if pool_params["pledge"] != pool_creation_data.pool_pledge:
        errors_list.append(
            "'pledge' value is different than expected; "
            f"Expected: {pool_creation_data.pool_pledge} vs Returned: {pool_params['pledge']}"
        )

    if pool_params["relays"] != (pool_creation_data.pool_relay_dns or []):
        errors_list.append(
            "'relays' value is different than expected; "
            f"Expected: {pool_creation_data.pool_relay_dns} vs "
            f"Returned: {pool_params['relays']}"
        )

    if pool_creation_data.pool_metadata_url and pool_creation_data.pool_metadata_hash:
        metadata = pool_params.get("metadata") or {}

        metadata_hash = metadata.get("hash")
        if metadata_hash != pool_creation_data.pool_metadata_hash:
            errors_list.append(
                "'metadata hash' value is different than expected; "
                f"Expected: {pool_creation_data.pool_metadata_hash} vs "
                f"Returned: {metadata_hash}"
            )

        metadata_url = metadata.get("url")
        if metadata_url != pool_creation_data.pool_metadata_url:
            errors_list.append(
                "'metadata url' value is different than expected; "
                f"Expected: {pool_creation_data.pool_metadata_url} vs "
                f"Returned: {metadata_url}"
            )
    elif pool_params["metadata"] is not None:
        errors_list.append(
            "'metadata' value is different than expected; "
            f"Expected: None vs Returned: {pool_params['metadata']}"
        )

    if errors_list:
        for err in errors_list:
            LOGGER.error(err)
        LOGGER.error(f"Stake Pool Details: \n{pool_params}")

    return "\n\n".join(errors_list)


def check_updated_params(update_proposals: List[UpdateProposal], protocol_params: dict) -> None:
    """Compare update proposals with actual protocol parameters."""
    failures = []
    for u in update_proposals:
        if not u.name:
            continue

        # nested dictionaries - keys are separated with comma (,)
        names = u.name.split(",")
        nested = protocol_params
        for n in names:
            nested = nested[n.strip()]
        updated_value = nested

        if str(updated_value) != str(u.value):
            failures.append(f"Param value for {u.name}: {updated_value}.\nExpected: {u.value}")

    if failures:
        failures_str = "\n".join(failures)
        raise AssertionError(f"Cluster update proposal failed!\n{failures_str}")


def update_params(
    cluster_obj: clusterlib.ClusterLib,
    src_addr_record: clusterlib.AddressRecord,
    update_proposals: List[UpdateProposal],
) -> None:
    """Update params using update proposal."""
    if not update_proposals:
        return

    _cli_args = [(u.arg, str(u.value)) for u in update_proposals]
    cli_args = list(itertools.chain.from_iterable(_cli_args))

    cluster_obj.g_governance.submit_update_proposal(
        cli_args=cli_args,
        src_address=src_addr_record.address,
        src_skey_file=src_addr_record.skey_file,
        tx_name=helpers.get_timestamped_rand_str(),
    )

    LOGGER.info(f"Update Proposal submitted ({cli_args})")


def update_params_build(
    cluster_obj: clusterlib.ClusterLib,
    src_addr_record: clusterlib.AddressRecord,
    update_proposals: List[UpdateProposal],
) -> None:
    """Update params using update proposal.

    Uses `cardano-cli transaction build` command for building the transactions.
    """
    if not update_proposals:
        return

    _cli_args = [(u.arg, str(u.value)) for u in update_proposals]
    cli_args = list(itertools.chain.from_iterable(_cli_args))
    temp_template = helpers.get_timestamped_rand_str()

    # assumption is update proposals are submitted near beginning of epoch
    epoch = cluster_obj.g_query.get_epoch()

    out_file = cluster_obj.g_governance.gen_update_proposal(
        cli_args=cli_args,
        epoch=epoch,
        tx_name=temp_template,
    )
    tx_files = clusterlib.TxFiles(
        proposal_files=[out_file],
        signing_key_files=[
            *cluster_obj.g_genesis.genesis_keys.delegate_skeys,
            Path(src_addr_record.skey_file),
        ],
    )
    tx_output = cluster_obj.g_transaction.build_tx(
        src_address=src_addr_record.address,
        tx_name=f"{temp_template}_submit_proposal",
        tx_files=tx_files,
        fee_buffer=2000_000,
    )
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_submit_proposal",
    )
    cluster_obj.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    LOGGER.info(f"Update Proposal submitted ({cli_args})")


def mint_or_burn_witness(
    cluster_obj: clusterlib.ClusterLib,
    new_tokens: List[TokenRecord],
    temp_template: str,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    use_build_cmd: bool = False,
    sign_incrementally: bool = False,
) -> clusterlib.TxRawOutput:
    """Mint or burn tokens, depending on the `amount` value. Sign using witnesses.

    Positive `amount` value means minting, negative means burning.
    """
    _issuers_addrs = [t.issuers_addrs for t in new_tokens]
    issuers_addrs = set(itertools.chain.from_iterable(_issuers_addrs))
    issuers_skey_files = {p.skey_file for p in issuers_addrs}
    token_mint_addr = new_tokens[0].token_mint_addr
    signing_key_files = list({*issuers_skey_files, token_mint_addr.skey_file})

    # create TX body
    mint = [
        clusterlib.Mint(
            txouts=[
                clusterlib.TxOut(address=t.token_mint_addr.address, amount=t.amount, coin=t.token)
            ],
            script_file=t.script,
        )
        for t in new_tokens
    ]

    mint_txouts = [
        clusterlib.TxOut(address=t.token_mint_addr.address, amount=t.amount, coin=t.token)
        for t in new_tokens
        if t.amount >= 0
    ]
    txouts = []
    if mint_txouts:
        # meet the minimum required UTxO value
        lovelace_amount = 2_000_000 + math.ceil(len(mint_txouts) / 8) * 1_000_000
        txouts = [
            clusterlib.TxOut(address=new_tokens[0].token_mint_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

    if use_build_cmd:
        tx_raw_output = cluster_obj.g_transaction.build_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            fee_buffer=2000_000,
            mint=mint,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_override=len(signing_key_files),
        )
    else:
        fee = cluster_obj.g_transaction.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=len(signing_key_files),
        )
        tx_raw_output = cluster_obj.g_transaction.build_raw_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            fee=fee,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
        )

    # sign incrementally (just to check that it works)
    if sign_incrementally and len(signing_key_files) >= 1:
        # create witness file for first required key
        witness_file = cluster_obj.g_transaction.witness_tx(
            tx_body_file=tx_raw_output.out_file,
            witness_name=f"{temp_template}_skey0",
            signing_key_files=signing_key_files[:1],
        )
        # sign Tx using witness file
        tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
            tx_body_file=tx_raw_output.out_file,
            witness_files=[witness_file],
            tx_name=f"{temp_template}_sign0",
        )
        # incrementally sign the already signed Tx with rest of required skeys
        for idx, skey in enumerate(signing_key_files[1:], start=1):
            tx_witnessed_file = cluster_obj.g_transaction.sign_tx(
                tx_file=tx_witnessed_file,
                signing_key_files=[skey],
                tx_name=f"{temp_template}_sign{idx}",
            )
    else:
        # create witness file for each required key
        witness_files = [
            cluster_obj.g_transaction.witness_tx(
                tx_body_file=tx_raw_output.out_file,
                witness_name=f"{temp_template}_skey{idx}",
                signing_key_files=[skey],
            )
            for idx, skey in enumerate(signing_key_files)
        ]

        # sign Tx using witness files
        tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
            tx_body_file=tx_raw_output.out_file,
            witness_files=witness_files,
            tx_name=temp_template,
        )

    # submit signed TX
    cluster_obj.g_transaction.submit_tx(tx_file=tx_witnessed_file, txins=tx_raw_output.txins)

    return tx_raw_output


def mint_or_burn_sign(
    cluster_obj: clusterlib.ClusterLib,
    new_tokens: List[TokenRecord],
    temp_template: str,
    sign_incrementally: bool = False,
) -> clusterlib.TxRawOutput:
    """Mint or burn tokens, depending on the `amount` value. Sign using skeys.

    Positive `amount` value means minting, negative means burning.
    """
    _issuers_addrs = [t.issuers_addrs for t in new_tokens]
    issuers_addrs = set(itertools.chain.from_iterable(_issuers_addrs))
    issuers_skey_files = {p.skey_file for p in issuers_addrs}
    token_mint_addr = new_tokens[0].token_mint_addr
    signing_key_files = list({*issuers_skey_files, token_mint_addr.skey_file})

    # build and sign a transaction
    tx_files = clusterlib.TxFiles(signing_key_files=signing_key_files)
    mint = [
        clusterlib.Mint(
            txouts=[
                clusterlib.TxOut(address=t.token_mint_addr.address, amount=t.amount, coin=t.token)
            ],
            script_file=t.script,
        )
        for t in new_tokens
    ]
    mint_txouts = [
        clusterlib.TxOut(address=t.token_mint_addr.address, amount=t.amount, coin=t.token)
        for t in new_tokens
        if t.amount >= 0
    ]
    txouts = []
    if mint_txouts:
        # meet the minimum required UTxO value
        lovelace_amount = 2_000_000 + math.ceil(len(mint_txouts) / 8) * 1_000_000
        txouts = [
            clusterlib.TxOut(address=new_tokens[0].token_mint_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]
    fee = cluster_obj.g_transaction.calculate_tx_fee(
        src_address=token_mint_addr.address,
        tx_name=temp_template,
        txouts=txouts,
        mint=mint,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=len(issuers_skey_files),
    )
    tx_raw_output = cluster_obj.g_transaction.build_raw_tx(
        src_address=token_mint_addr.address,
        tx_name=temp_template,
        txouts=txouts,
        mint=mint,
        tx_files=tx_files,
        fee=fee,
    )

    # sign incrementally (just to check that it works)
    if sign_incrementally and len(signing_key_files) >= 1:
        out_file_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=signing_key_files[:1],
            tx_name=f"{temp_template}_sign0",
        )
        # incrementally sign the already signed Tx with rest of required skeys
        for idx, skey in enumerate(signing_key_files[1:], start=1):
            out_file_signed = cluster_obj.g_transaction.sign_tx(
                tx_file=out_file_signed,
                signing_key_files=[skey],
                tx_name=f"{temp_template}_sign{idx}",
            )
    else:
        out_file_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

    # submit signed transaction
    cluster_obj.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

    return tx_raw_output


def withdraw_reward_w_build(
    cluster_obj: clusterlib.ClusterLib,
    stake_addr_record: clusterlib.AddressRecord,
    dst_addr_record: clusterlib.AddressRecord,
    tx_name: str,
    verify: bool = True,
    destination_dir: clusterlib.FileType = ".",
) -> clusterlib.TxRawOutput:
    """Withdraw reward to payment address.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        stake_addr_record: An `AddressRecord` tuple for the stake address with reward.
        dst_addr_record: An `AddressRecord` tuple for the destination payment address.
        tx_name: A name of the transaction.
        verify: A bool indicating whether to verify that the reward was transferred correctly.
        destination_dir: A path to directory for storing artifacts (optional).
    """
    dst_address = dst_addr_record.address
    src_init_balance = cluster_obj.g_query.get_address_balance(dst_address)

    tx_files_withdrawal = clusterlib.TxFiles(
        signing_key_files=[dst_addr_record.skey_file, stake_addr_record.skey_file],
    )

    tx_raw_withdrawal_output = cluster_obj.g_transaction.build_tx(
        src_address=dst_address,
        tx_name=f"{tx_name}_reward_withdrawal",
        tx_files=tx_files_withdrawal,
        withdrawals=[clusterlib.TxOut(address=stake_addr_record.address, amount=-1)],
        fee_buffer=2000_000,
        witness_override=len(tx_files_withdrawal.signing_key_files),
        destination_dir=destination_dir,
    )
    # sign incrementally (just to check that it works)
    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_raw_withdrawal_output.out_file,
        signing_key_files=[dst_addr_record.skey_file],
        tx_name=f"{tx_name}_reward_withdrawal_sign0",
    )
    tx_signed_inc = cluster_obj.g_transaction.sign_tx(
        tx_file=tx_signed,
        signing_key_files=[stake_addr_record.skey_file],
        tx_name=f"{tx_name}_reward_withdrawal_sign1",
    )
    cluster_obj.g_transaction.submit_tx(tx_file=tx_signed_inc, txins=tx_raw_withdrawal_output.txins)

    if not verify:
        return tx_raw_withdrawal_output

    # check that reward is 0
    if (
        cluster_obj.g_query.get_stake_addr_info(stake_addr_record.address).reward_account_balance
        != 0
    ):
        raise AssertionError("Not all rewards were transferred.")

    # check that rewards were transferred
    src_reward_balance = cluster_obj.g_query.get_address_balance(dst_address)
    if (
        src_reward_balance
        != src_init_balance
        - tx_raw_withdrawal_output.fee
        + tx_raw_withdrawal_output.withdrawals[0].amount  # type: ignore
    ):
        raise AssertionError(f"Incorrect balance for destination address `{dst_address}`.")

    return tx_raw_withdrawal_output


def new_tokens(
    *asset_names: str,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    token_mint_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    amount: int,
) -> List[TokenRecord]:
    """Mint new token, sign using skeys."""
    # create simple script
    keyhash = cluster_obj.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
    script_content = {"keyHash": keyhash, "type": "sig"}
    script = Path(f"{temp_template}.script")
    with open(f"{temp_template}.script", "w", encoding="utf-8") as out_json:
        json.dump(script_content, out_json)

    policyid = cluster_obj.g_transaction.get_policyid(script)

    tokens_to_mint = []
    for asset_name in asset_names:
        token = f"{policyid}.{asset_name}"

        if cluster_obj.g_query.get_utxo(address=token_mint_addr.address, coins=[token]):
            raise AssertionError("The token already exists.")

        tokens_to_mint.append(
            TokenRecord(
                token=token,
                amount=amount,
                issuers_addrs=[issuer_addr],
                token_mint_addr=token_mint_addr,
                script=script,
            )
        )

    # token minting
    mint_or_burn_sign(
        cluster_obj=cluster_obj,
        new_tokens=tokens_to_mint,
        temp_template=f"{temp_template}_mint",
    )

    for token_rec in tokens_to_mint:
        token_utxo = cluster_obj.g_query.get_utxo(
            address=token_mint_addr.address, coins=[token_rec.token]
        )
        if not (token_utxo and token_utxo[0].amount == amount):
            raise AssertionError("The token was not minted.")

    return tokens_to_mint


def filtered_ledger_state(
    cluster_obj: clusterlib.ClusterLib,
) -> str:
    """Get filtered output of `query ledger-state`."""
    cardano_cli_args = [
        "cardano-cli",
        "query",
        "ledger-state",
        *cluster_obj.magic_args,
        f"--{cluster_obj.protocol}-mode",
    ]
    cardano_cmd = " ".join(cardano_cli_args)

    # record cli coverage
    clusterlib.record_cli_coverage(
        cli_args=cardano_cli_args, coverage_dict=cluster_obj.cli_coverage
    )

    # get rid of a huge amount of data we don't have any use for
    cmd = (
        f"{cardano_cmd} | jq -n --stream -c "
        "'fromstream(inputs|select((length == 2 and .[0][1] == \"esLState\")|not))'"
    )

    return helpers.run_in_bash(cmd).decode("utf-8").strip()


def get_blocks_before(
    cluster_obj: clusterlib.ClusterLib,
) -> Dict[str, int]:
    """Get `blocksBefore` section of ledger state with bech32 encoded pool ids."""
    cardano_cli_args = [
        "cardano-cli",
        "query",
        "ledger-state",
        *cluster_obj.magic_args,
        f"--{cluster_obj.protocol}-mode",
    ]
    cardano_cmd = " ".join(cardano_cli_args)

    # record cli coverage
    clusterlib.record_cli_coverage(
        cli_args=cardano_cli_args, coverage_dict=cluster_obj.cli_coverage
    )

    # get rid of a huge amount of data we don't have any use for
    cmd = (
        f"{cardano_cmd} | jq -n --stream -c "
        "'fromstream(1|truncate_stream(inputs|select(.[0][0] == \"blocksBefore\")))'"
    )

    out_str = helpers.run_in_bash(cmd).decode("utf-8").strip()
    out_json: dict = json.loads(out_str)
    return {helpers.encode_bech32(prefix="pool", data=key): val for key, val in out_json.items()}


def get_ledger_state(
    cluster_obj: clusterlib.ClusterLib,
) -> dict:
    """Return the current ledger state info."""
    f_ledger_state = filtered_ledger_state(cluster_obj)
    if not f_ledger_state:
        return {}
    ledger_state: dict = json.loads(f_ledger_state)
    return ledger_state


def save_ledger_state(
    cluster_obj: clusterlib.ClusterLib,
    state_name: str,
    ledger_state: Optional[dict] = None,
    destination_dir: FileType = ".",
) -> Path:
    """Save ledger state to file.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        state_name: A name of the ledger state (can be epoch number, etc.).
        ledger_state: A dict with ledger state to save (optional).
        destination_dir: A path to directory for storing the state JSON file (optional).

    Returns:
        Path: A path to the generated state JSON file.
    """
    json_file = Path(destination_dir) / f"{state_name}_ledger_state.json"
    ledger_state = ledger_state or get_ledger_state(cluster_obj)
    with open(json_file, "w", encoding="utf-8") as fp_out:
        json.dump(ledger_state, fp_out, indent=4)
    return json_file


def wait_for_epoch_interval(
    cluster_obj: clusterlib.ClusterLib,
    start: int,
    stop: int,
    force_epoch: bool = False,
    check_slot: bool = False,
) -> None:
    """Wait for time interval within an epoch.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        start: A start of the interval, in seconds. Negative number for counting from the
            end of an epoch.
        stop: An end of the interval, in seconds. Negative number for counting from the
            end of an epoch.
        force_epoch: A bool indicating whether the interval must be in current epoch
            (False by default).
        check_slot: A bool indicating whether to check if slot number matches time interval
            after waiting (False by default).
    """
    start_abs = start if start >= 0 else cluster_obj.epoch_length_sec + start
    stop_abs = stop if stop >= 0 else cluster_obj.epoch_length_sec + stop

    if start_abs > stop_abs:
        raise AssertionError(f"The 'start' ({start_abs}) needs to be <= 'stop' ({stop_abs}).")

    start_epoch = cluster_obj.g_query.get_epoch()

    # wait for new block so we start counting with an up-to-date slot number
    cluster_obj.wait_for_new_block()

    for __ in range(40):
        s_from_epoch_start = cluster_obj.time_from_epoch_start()

        # return if we are in the required interval
        if start_abs <= s_from_epoch_start <= stop_abs:
            break

        # if we are already after the required interval, wait for next epoch
        if stop_abs < s_from_epoch_start:
            if force_epoch:
                raise AssertionError(
                    f"Cannot reach the given interval ({start_abs}s to {stop_abs}s) in this epoch."
                )
            if cluster_obj.g_query.get_epoch() >= start_epoch + 2:
                raise AssertionError(
                    f"Was unable to reach the given interval ({start_abs}s to {stop_abs}s) "
                    "in past 3 epochs."
                )
            cluster_obj.wait_for_new_epoch()
            continue

        # sleep until `start_abs`
        to_sleep = start_abs - s_from_epoch_start
        if to_sleep > 0:
            # `to_sleep` is float, wait for at least 1 second
            time.sleep(to_sleep if to_sleep > 1 else 1)

        # we can finish if slot number of last minted block doesn't need
        # to match the time interval
        if not check_slot:
            break
    else:
        raise AssertionError(f"Failed to wait for given interval from {start_abs}s to {stop_abs}s.")


def load_body_metadata(tx_body_file: Path) -> Any:
    """Load metadata from file containing transaction body."""
    with open(tx_body_file, encoding="utf-8") as body_fp:
        tx_body_json = json.load(body_fp)

    cbor_body = bytes.fromhex(tx_body_json["cborHex"])
    loaded_body = cbor2.loads(cbor_body)
    metadata = loaded_body[-1]

    if not metadata:
        return []

    return metadata


def load_tx_metadata(tx_body_file: Path) -> TxMetadata:
    """Load transaction metadata from file containing transaction body."""
    metadata_section = load_body_metadata(tx_body_file=tx_body_file)

    if not metadata_section:
        return TxMetadata(metadata={}, aux_data=[])

    # the `metadata_section` can be either list or `CBORTag`- check if it is `CBORTag`
    try:
        metadata_value = metadata_section.value
    except AttributeError:
        pass
    else:
        return TxMetadata(
            metadata=metadata_value.get(0) or {}, aux_data=metadata_value.get(1) or []
        )

    # now we know the `metadata_section` is list
    try:
        metadata: dict = metadata_section[0]
    except KeyError:
        return TxMetadata(metadata=metadata_section, aux_data=[])

    try:
        aux_data: list = metadata_section[1]
    except KeyError:
        return TxMetadata(metadata=metadata, aux_data=[])

    return TxMetadata(metadata=metadata, aux_data=aux_data)


def datum_hash_from_txout(cluster_obj: clusterlib.ClusterLib, txout: clusterlib.TxOut) -> str:
    """Return datum hash from `clusterlib.TxOut`."""
    datum_hash = txout.datum_hash

    if datum_hash:
        return datum_hash

    script_data_file = (
        txout.datum_hash_file or txout.datum_embed_file or txout.inline_datum_file or ""
    )
    script_data_cbor_file = (
        txout.datum_hash_cbor_file
        or txout.datum_embed_cbor_file
        or txout.inline_datum_cbor_file
        or ""
    )
    script_data_value = (
        txout.datum_hash_value or txout.datum_embed_value or txout.inline_datum_value or ""
    )

    if script_data_file or script_data_cbor_file or script_data_value:
        datum_hash = cluster_obj.g_transaction.get_hash_script_data(
            script_data_file=script_data_file,
            script_data_cbor_file=script_data_cbor_file,
            script_data_value=script_data_value,
        )

    return datum_hash


def create_script_context(
    cluster_obj: clusterlib.ClusterLib,
    plutus_version: int,
    redeemer_file: Path,
    tx_file: Optional[Path] = None,
) -> None:
    """Run the `create-script-context` command (available in plutus-apps)."""
    version_args = []
    # TODO: remove once `--plutus-*` args are in main branch
    if helpers.tool_has("create-script-context --plutus-v1"):
        if plutus_version == 1:
            version_args = ["--plutus-v1"]
        elif plutus_version == 2:
            version_args = ["--plutus-v2"]
        else:
            raise AssertionError(f"Unknown plutus version: {plutus_version}")

    if tx_file:
        cmd_args = [
            "create-script-context",
            "--generate-tx",
            str(tx_file),
            *version_args,
            f"--{cluster_obj.protocol}-mode",
            *cluster_obj.magic_args,
            "--out-file",
            str(redeemer_file),
        ]
    else:
        cmd_args = ["create-script-context", *version_args, "--out-file", str(redeemer_file)]

    helpers.run_command(cmd_args)
    assert redeemer_file.exists()


def cli_has(command: str) -> bool:
    """Check if a cardano-cli subcommand or argument is available.

    E.g. `cli_has("query leadership-schedule --next")`
    """
    full_command = f"cardano-cli {command}"
    return helpers.tool_has(full_command)


def check_txins_spent(
    cluster_obj: clusterlib.ClusterLib, txins: List[clusterlib.UTXOData], wait_blocks: int = 2
) -> None:
    """Check that txins were spent."""
    if wait_blocks > 0:
        cluster_obj.wait_for_new_block(wait_blocks)

    utxo_data = cluster_obj.g_query.get_utxo(utxo=txins)

    if utxo_data:
        raise AssertionError(f"Some txins were not spent: {txins}")


def create_reference_utxo(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    script_file: Path,
    amount: int,
) -> Tuple[clusterlib.UTXOData, clusterlib.TxRawOutput]:
    """Create a reference script UTxO."""
    # pylint: disable=too-many-arguments
    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    txouts = [
        clusterlib.TxOut(
            address=dst_addr.address,
            amount=amount,
            reference_script_file=script_file,
        )
    ]

    tx_raw_output = cluster_obj.g_transaction.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_reference_utxo",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=2,
        join_txouts=False,
    )

    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    reference_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#0")
    assert reference_utxos, "No reference script UTxO"
    reference_utxo = reference_utxos[0]

    return reference_utxo, tx_raw_output


def get_utxo_ix_offset(utxos: List[clusterlib.UTXOData], txouts: List[clusterlib.TxOut]) -> int:
    """Get offset of index of the first user-defined txout.

    Change txout created by `transaction build` used to be UTxO with index 0, now it is the last
    UTxO. This function exists for backwards compatibility with the old behavior.
    """
    if not (txouts and utxos):
        return 0

    first_txout = txouts[0]
    filtered_utxos = clusterlib.filter_utxos(
        utxos=utxos, amount=first_txout.amount, address=first_txout.address, coin=first_txout.coin
    )
    if not filtered_utxos:
        return 0
    return filtered_utxos[0].utxo_ix


def gen_byron_addr(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    destination_dir: FileType = ".",
) -> clusterlib.AddressRecord:
    """Generate a Byron address and keys."""
    destination_dir = Path(destination_dir).expanduser().resolve()

    secret_file = destination_dir / f"{name_template}_byron_orig.key"
    skey_file = destination_dir / f"{name_template}_byron.skey"

    # generate Byron key
    cluster_obj.cli(
        [
            "byron",
            "key",
            "keygen",
            "--secret",
            str(secret_file),
        ]
    )
    assert secret_file.exists()

    # generate Shelley address and keys out of the Byron key
    cluster_obj.cli(
        [
            "key",
            "convert-byron-key",
            "--byron-signing-key-file",
            str(secret_file),
            "--out-file",
            str(skey_file),
            "--byron-payment-key-type",
        ]
    )
    assert skey_file.exists()

    vkey_file = cluster_obj.g_key.gen_verification_key(
        key_name=f"{name_template}_byron", signing_key_file=skey_file
    )
    address = cluster_obj.g_address.gen_payment_addr(
        addr_name=f"{name_template}_byron", payment_vkey_file=vkey_file
    )

    return clusterlib.AddressRecord(address=address, vkey_file=vkey_file, skey_file=skey_file)
