"""Utilities that extends the functionality of `cardano-clusterlib`."""

import base64
import dataclasses
import itertools
import json
import logging
import math
import pathlib as pl
import time
import typing as tp

import cardano_clusterlib.types as cl_types
import cbor2
from cardano_clusterlib import clusterlib
from cardano_clusterlib import txtools as cl_txtools

from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils import temptools
from cardano_node_tests.utils.faucet import fund_from_faucet  # noqa: F401 # for compatibility

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, order=True)
class UpdateProposal:
    arg: str
    value: tp.Any
    name: str = ""


@dataclasses.dataclass(frozen=True, order=True)
class TokenRecord:
    token: str
    amount: int
    issuers_addrs: tp.List[clusterlib.AddressRecord]
    token_mint_addr: clusterlib.AddressRecord
    script: pl.Path


@dataclasses.dataclass(frozen=True, order=True)
class TxMetadata:
    metadata: dict
    aux_data: list


def build_and_submit_tx(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    src_address: str,
    submit_method: str = "",
    use_build_cmd: bool = False,
    txins: clusterlib.OptionalUTXOData = (),
    txouts: clusterlib.OptionalTxOuts = (),
    readonly_reference_txins: clusterlib.OptionalUTXOData = (),
    script_txins: clusterlib.OptionalScriptTxIn = (),
    return_collateral_txouts: clusterlib.OptionalTxOuts = (),
    total_collateral_amount: tp.Optional[int] = None,
    mint: clusterlib.OptionalMint = (),
    tx_files: tp.Optional[clusterlib.TxFiles] = None,
    complex_certs: clusterlib.OptionalScriptCerts = (),
    change_address: str = "",
    fee_buffer: tp.Optional[int] = None,
    raw_fee: tp.Optional[int] = None,
    required_signers: cl_types.OptionalFiles = (),
    required_signer_hashes: tp.Optional[tp.List[str]] = None,
    withdrawals: clusterlib.OptionalTxOuts = (),
    script_withdrawals: clusterlib.OptionalScriptWithdrawals = (),
    deposit: tp.Optional[int] = None,
    invalid_hereafter: tp.Optional[int] = None,
    invalid_before: tp.Optional[int] = None,
    witness_override: tp.Optional[int] = None,
    witness_count_add: int = 0,
    script_valid: bool = True,
    calc_script_cost_file: tp.Optional[cl_types.FileType] = None,
    join_txouts: bool = True,
    destination_dir: cl_types.FileType = ".",
    skip_asset_balancing: bool = False,
) -> clusterlib.TxRawOutput:
    """
    Build and submit a transaction.

    Use `use_build_cmd` to switch between `transaction build` and `transaction build-raw`.
    Use `submit_method` to switch between `cardano-cli transaction submit` and submit-api.
    """
    # pylint: disable=too-many-arguments,too-many-locals
    tx_files = tx_files or clusterlib.TxFiles()
    submit_method = submit_method or submit_utils.SubmitMethods.CLI

    if use_build_cmd:
        witness_override = (
            len(tx_files.signing_key_files) + witness_count_add
            if witness_override is None
            else witness_override
        )
        tx_output = cluster_obj.g_transaction.build_tx(
            src_address=src_address,
            tx_name=name_template,
            txins=txins,
            txouts=txouts,
            readonly_reference_txins=readonly_reference_txins,
            script_txins=script_txins,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            change_address=change_address,
            fee_buffer=fee_buffer,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            deposit=deposit,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_override=witness_override,
            script_valid=script_valid,
            calc_script_cost_file=calc_script_cost_file,
            join_txouts=join_txouts,
            destination_dir=destination_dir,
            skip_asset_balancing=skip_asset_balancing,
        )
    else:
        # Resolve withdrawal amounts here (where -1 for total rewards amount is used) so the
        # resolved values can be passed around, and it is not needed to resolve them again
        # every time `_get_withdrawals` is called.
        withdrawals, script_withdrawals, *__ = cl_txtools._get_withdrawals(
            clusterlib_obj=cluster_obj,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
        )
        fee = raw_fee or cluster_obj.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=name_template,
            txins=txins,
            txouts=txouts,
            readonly_reference_txins=readonly_reference_txins,
            script_txins=script_txins,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_count_add=witness_count_add,
            join_txouts=join_txouts,
            destination_dir=destination_dir,
        )
        tx_output = cluster_obj.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=name_template,
            txins=txins,
            txouts=txouts,
            readonly_reference_txins=readonly_reference_txins,
            script_txins=script_txins,
            return_collateral_txouts=return_collateral_txouts,
            total_collateral_amount=total_collateral_amount,
            mint=mint,
            tx_files=tx_files,
            complex_certs=complex_certs,
            fee=fee,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            deposit=deposit,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            join_txouts=join_txouts,
            destination_dir=destination_dir,
        )

    tx_signed = cluster_obj.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_output.tx_files.signing_key_files,
        tx_name=name_template,
    )

    submit_utils.submit_tx(
        submit_method=submit_method,
        cluster_obj=cluster_obj,
        tx_file=tx_signed,
        txins=tx_output.txins,
    )

    return tx_output


def get_pool_state(
    cluster_obj: clusterlib.ClusterLib,
    pool_id: str,
) -> clusterlib.PoolParamsTop:
    """Get pool state using the available command."""
    return (
        cluster_obj.g_query.get_pool_state(pool_id)
        if cli_has("query pool-state")
        else cluster_obj.g_query.get_pool_params(pool_id)
    )


def register_stake_address(
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    name_template: str,
    deposit_amt: int = -1,
) -> clusterlib.TxRawOutput:
    """Register stake address."""
    # files for registering stake address
    addr_reg_cert = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
        addr_name=name_template,
        deposit_amt=deposit_amt,
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
        msg = f"The address {pool_user.stake.address} was not registered."
        raise AssertionError(msg)

    return tx_raw_output


def deregister_stake_address(
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    name_template: str,
    deposit_amt: int = -1,
) -> tp.Tuple[clusterlib.TxRawOutput, clusterlib.TxRawOutput]:
    """Deregister stake address."""
    # files for deregistering stake address
    stake_addr_dereg_cert = cluster_obj.g_stake_address.gen_stake_addr_deregistration_cert(
        addr_name=f"{name_template}_addr0_dereg",
        deposit_amt=deposit_amt,
        stake_vkey_file=pool_user.stake.vkey_file,
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
    tx_name: tp.Optional[str] = None,
    destination_dir: cl_types.FileType = ".",
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


def create_payment_addr_records(
    *names: str,
    cluster_obj: clusterlib.ClusterLib,
    stake_vkey_file: tp.Optional[cl_types.FileType] = None,
    destination_dir: cl_types.FileType = ".",
) -> tp.List[clusterlib.AddressRecord]:
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
    destination_dir: cl_types.FileType = ".",
) -> tp.List[clusterlib.AddressRecord]:
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
    destination_dir: cl_types.FileType = ".",
) -> tp.List[clusterlib.PoolUser]:
    """Create PoolUsers."""
    pool_users = []
    for i in range(no_of_addr):
        # create key pairs and addresses
        stake_addr_rec = create_stake_addr_records(
            f"{name_template}_addr{i}", cluster_obj=cluster_obj, destination_dir=destination_dir
        )[0]
        payment_addr_rec = create_payment_addr_records(
            f"{name_template}_addr{i}",
            cluster_obj=cluster_obj,
            stake_vkey_file=stake_addr_rec.vkey_file,
            destination_dir=destination_dir,
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


def check_updated_params(update_proposals: tp.List[UpdateProposal], protocol_params: dict) -> None:
    """Compare update proposals with actual protocol parameters."""
    failures = []
    for u in update_proposals:
        if not u.name:
            continue

        # Nested dictionaries - keys are separated with comma (,)
        names = u.name.split(",")
        nested = protocol_params
        for n in names:
            nested = nested[n.strip()]
        updated_value = nested

        if str(updated_value) != str(u.value):
            failures.append(f"Param value for {u.name}: {updated_value}.\nExpected: {u.value}")

    if failures:
        failures_str = "\n".join(failures)
        msg = f"Update proposal failed!\n{failures_str}"
        raise AssertionError(msg)


def get_pparams_update_args(
    update_proposals: tp.List[UpdateProposal],
) -> tp.List[str]:
    """Get cli arguments for pparams update action."""
    if not update_proposals:
        return []

    _cli_args = [(u.arg, str(u.value)) for u in update_proposals]
    cli_args = list(itertools.chain.from_iterable(_cli_args))
    return cli_args


def update_params(
    cluster_obj: clusterlib.ClusterLib,
    src_addr_record: clusterlib.AddressRecord,
    update_proposals: tp.List[UpdateProposal],
) -> None:
    """Update params using update proposal."""
    if not update_proposals:
        return

    cli_args = get_pparams_update_args(update_proposals=update_proposals)

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
    update_proposals: tp.List[UpdateProposal],
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
            pl.Path(src_addr_record.skey_file),
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
    new_tokens: tp.List[TokenRecord],
    temp_template: str,
    invalid_hereafter: tp.Optional[int] = None,
    invalid_before: tp.Optional[int] = None,
    submit_method: str = submit_utils.SubmitMethods.CLI,
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
        tx_output = cluster_obj.g_transaction.build_tx(
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
            # TODO: workaround for https://github.com/IntersectMBO/cardano-node/issues/1892
            witness_count_add=int(len(signing_key_files) * 1.5),
        )
        tx_output = cluster_obj.g_transaction.build_raw_tx(
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
            tx_body_file=tx_output.out_file,
            witness_name=f"{temp_template}_skey0",
            signing_key_files=signing_key_files[:1],
        )
        # sign Tx using witness file
        tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
            tx_body_file=tx_output.out_file,
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
                tx_body_file=tx_output.out_file,
                witness_name=f"{temp_template}_skey{idx}",
                signing_key_files=[skey],
            )
            for idx, skey in enumerate(signing_key_files)
        ]

        # sign Tx using witness files
        tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
            tx_body_file=tx_output.out_file,
            witness_files=witness_files,
            tx_name=temp_template,
        )

    # Submit signed TX
    submit_utils.submit_tx(
        submit_method=submit_method,
        cluster_obj=cluster_obj,
        tx_file=tx_witnessed_file,
        txins=tx_output.txins,
    )

    return tx_output


def mint_or_burn_sign(
    cluster_obj: clusterlib.ClusterLib,
    new_tokens: tp.List[TokenRecord],
    temp_template: str,
    submit_method: str = submit_utils.SubmitMethods.CLI,
    use_build_cmd: bool = False,
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
        tx_output = cluster_obj.g_transaction.build_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            fee_buffer=2000_000,
            mint=mint,
            witness_override=len(signing_key_files),
        )
    else:
        fee = cluster_obj.g_transaction.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            # TODO: workaround for https://github.com/IntersectMBO/cardano-node/issues/1892
            witness_count_add=len(signing_key_files),
        )
        tx_output = cluster_obj.g_transaction.build_raw_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            fee=fee,
        )

    # sign incrementally (just to check that it works)
    if sign_incrementally and len(signing_key_files) >= 1:
        out_file_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
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
            tx_body_file=tx_output.out_file,
            signing_key_files=signing_key_files,
            tx_name=temp_template,
        )

    # Submit signed transaction
    submit_utils.submit_tx(
        submit_method=submit_method,
        cluster_obj=cluster_obj,
        tx_file=out_file_signed,
        txins=tx_output.txins,
    )

    return tx_output


def withdraw_reward_w_build(
    cluster_obj: clusterlib.ClusterLib,
    stake_addr_record: clusterlib.AddressRecord,
    dst_addr_record: clusterlib.AddressRecord,
    tx_name: str,
    verify: bool = True,
    destination_dir: cl_types.FileType = ".",
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
        msg = "Not all rewards were transferred."
        raise AssertionError(msg)

    # check that rewards were transferred
    src_reward_balance = cluster_obj.g_query.get_address_balance(dst_address)
    if (
        src_reward_balance
        != src_init_balance
        - tx_raw_withdrawal_output.fee
        + tx_raw_withdrawal_output.withdrawals[0].amount  # type: ignore
    ):
        msg = f"Incorrect balance for destination address `{dst_address}`."
        raise AssertionError(msg)

    return tx_raw_withdrawal_output


def new_tokens(
    *asset_names: str,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    token_mint_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    amount: int,
) -> tp.List[TokenRecord]:
    """Mint new token, sign using skeys."""
    # create simple script
    keyhash = cluster_obj.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
    script_content = {"keyHash": keyhash, "type": "sig"}
    script = pl.Path(f"{temp_template}.script")
    with open(f"{temp_template}.script", "w", encoding="utf-8") as out_json:
        json.dump(script_content, out_json)

    policyid = cluster_obj.g_transaction.get_policyid(script)

    tokens_to_mint = []
    for asset_name in asset_names:
        token = f"{policyid}.{asset_name}"

        if cluster_obj.g_query.get_utxo(address=token_mint_addr.address, coins=[token]):
            msg = "The token already exists."
            raise AssertionError(msg)

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
            msg = "The token was not minted."
            raise AssertionError(msg)

    return tokens_to_mint


def _get_ledger_state_cmd(
    cluster_obj: clusterlib.ClusterLib,
) -> str:
    cardano_cli_args = [
        "cardano-cli",
        "query",
        "ledger-state",
        *cluster_obj.magic_args,
        f"--{cluster_obj.protocol}-mode",
    ]
    ledger_state_cmd = " ".join(cardano_cli_args)

    # Record cli coverage
    clusterlib.record_cli_coverage(
        cli_args=cardano_cli_args, coverage_dict=cluster_obj.cli_coverage
    )

    return ledger_state_cmd


def get_delegation_state(
    cluster_obj: clusterlib.ClusterLib,
) -> dict:
    """Get `delegationState` section of ledger state."""
    ledger_state_cmd = _get_ledger_state_cmd(cluster_obj=cluster_obj)

    # Get rid of a huge amount of data we don't have any use for
    cmd = (
        f"{ledger_state_cmd} | jq -n --stream -c "
        "'fromstream(3|truncate_stream(inputs|select(.[0][2] == \"delegationState\")))'"
    )

    deleg_state_raw = helpers.run_in_bash(cmd).decode("utf-8").strip()
    if not deleg_state_raw:
        return {}

    deleg_state: dict = json.loads(deleg_state_raw)
    return deleg_state


def get_blocks_before(
    cluster_obj: clusterlib.ClusterLib,
) -> tp.Dict[str, int]:
    """Get `blocksBefore` section of ledger state with bech32 encoded pool ids."""
    ledger_state_cmd = _get_ledger_state_cmd(cluster_obj=cluster_obj)

    # Get rid of a huge amount of data we don't have any use for
    cmd = (
        f"{ledger_state_cmd} | jq -n --stream -c "
        "'fromstream(1|truncate_stream(inputs|select(.[0][0] == \"blocksBefore\")))'"
    )

    blocks_before_raw = helpers.run_in_bash(cmd).decode("utf-8").strip()
    if not blocks_before_raw:
        return {}

    blocks_before: dict = json.loads(blocks_before_raw)
    return {
        helpers.encode_bech32(prefix="pool", data=key): val for key, val in blocks_before.items()
    }


def get_ledger_state(
    cluster_obj: clusterlib.ClusterLib,
) -> dict:
    """Return the current ledger state info."""
    ledger_state_cmd = _get_ledger_state_cmd(cluster_obj=cluster_obj)

    # Get rid of a huge amount of data we don't have any use for
    cmd = (
        f"{ledger_state_cmd} | jq -n --stream -c "
        "'fromstream(inputs|select((length == 2 and .[0][1] == \"esLState\")|not))'"
    )

    ledger_state_raw = helpers.run_in_bash(cmd).decode("utf-8").strip()
    if not ledger_state_raw:
        return {}

    ledger_state: dict = json.loads(ledger_state_raw)
    return ledger_state


def save_ledger_state(
    cluster_obj: clusterlib.ClusterLib,
    state_name: str,
    ledger_state: tp.Optional[dict] = None,
    destination_dir: cl_types.FileType = ".",
) -> pl.Path:
    """Save ledger state to file.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        state_name: A name of the ledger state (can be epoch number, etc.).
        ledger_state: A dict with ledger state to save (optional).
        destination_dir: A path to directory for storing the state JSON file (optional).

    Returns:
        Path: A path to the generated state JSON file.
    """
    json_file = pl.Path(destination_dir) / f"{state_name}_ledger_state.json"
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
        msg = f"The 'start' ({start_abs}) needs to be <= 'stop' ({stop_abs})."
        raise AssertionError(msg)

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
                msg = (
                    f"Cannot reach the given interval ({start_abs}s to {stop_abs}s) in this epoch."
                )
                raise AssertionError(msg)
            if cluster_obj.g_query.get_epoch() >= start_epoch + 2:
                msg = (
                    f"Was unable to reach the given interval ({start_abs}s to {stop_abs}s) "
                    "in past 3 epochs."
                )
                raise AssertionError(msg)
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
        msg = f"Failed to wait for given interval from {start_abs}s to {stop_abs}s."
        raise AssertionError(msg)


def load_body_metadata(tx_body_file: pl.Path) -> tp.Any:
    """Load metadata from file containing transaction body."""
    with open(tx_body_file, encoding="utf-8") as body_fp:
        tx_body_json = json.load(body_fp)

    cbor_body = bytes.fromhex(tx_body_json["cborHex"])
    loaded_body = cbor2.loads(cbor_body)
    metadata = loaded_body[-1]

    if not metadata:
        return []

    return metadata


def load_tx_metadata(tx_body_file: pl.Path) -> TxMetadata:
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
    redeemer_file: pl.Path,
    tx_file: tp.Optional[pl.Path] = None,
) -> None:
    """Run the `create-script-context` command (available in plutus-apps)."""
    if plutus_version == 1:
        version_arg = "--plutus-v1"
    elif plutus_version == 2:
        version_arg = "--plutus-v2"
    else:
        msg = f"Unknown plutus version: {plutus_version}"
        raise AssertionError(msg)

    if tx_file:
        cmd_args = [
            "create-script-context",
            "--generate-tx",
            str(tx_file),
            version_arg,
            f"--{cluster_obj.protocol}-mode",
            *cluster_obj.magic_args,
            "--out-file",
            str(redeemer_file),
        ]
    else:
        cmd_args = ["create-script-context", version_arg, "--out-file", str(redeemer_file)]

    helpers.run_command(cmd_args)
    assert redeemer_file.exists()


def cli_has(command: str) -> bool:
    """Check if a cardano-cli subcommand or argument is available.

    E.g. `cli_has("query leadership-schedule --next")`
    """
    full_command = f"cardano-cli {command}"
    return helpers.tool_has(full_command)


def check_txins_spent(
    cluster_obj: clusterlib.ClusterLib, txins: tp.List[clusterlib.UTXOData], wait_blocks: int = 2
) -> None:
    """Check that txins were spent."""
    if wait_blocks > 0:
        cluster_obj.wait_for_new_block(wait_blocks)

    utxo_data = cluster_obj.g_query.get_utxo(utxo=txins)

    if utxo_data:
        msg = f"Some txins were not spent: {txins}"
        raise AssertionError(msg)


def create_reference_utxo(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    script_file: pl.Path,
    amount: int,
) -> tp.Tuple[clusterlib.UTXOData, clusterlib.TxRawOutput]:
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
        # TODO: workaround for https://github.com/IntersectMBO/cardano-node/issues/1892
        witness_count_add=2,
        join_txouts=False,
    )

    txid = cluster_obj.g_transaction.get_txid(tx_body_file=tx_raw_output.out_file)

    reference_utxos = cluster_obj.g_query.get_utxo(txin=f"{txid}#0")
    assert reference_utxos, "No reference script UTxO"
    reference_utxo = reference_utxos[0]

    return reference_utxo, tx_raw_output


def get_utxo_ix_offset(
    utxos: tp.List[clusterlib.UTXOData], txouts: tp.List[clusterlib.TxOut]
) -> int:
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
    destination_dir: cl_types.FileType = ".",
) -> clusterlib.AddressRecord:
    """Generate a Byron address and keys."""
    destination_dir = pl.Path(destination_dir).expanduser().resolve()

    secret_file = destination_dir / f"{name_template}_byron_orig.key"
    skey_file = destination_dir / f"{name_template}_byron.skey"

    # Generate Byron key
    cluster_obj.cli(
        [
            "cardano-cli",
            "byron",
            "key",
            "keygen",
            "--secret",
            str(secret_file),
        ],
        add_default_args=False,
    )
    assert secret_file.exists()

    # Generate Shelley address and keys out of the Byron key
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


def get_plutus_b64(script_file: cl_types.FileType) -> str:
    """Get base64 encoded binary version of Plutus script from file."""
    with open(script_file, encoding="utf-8") as fp_in:
        script_str = json.load(fp_in)

    script_cbor_hex = script_str["cborHex"]
    script_bytes = bytes.fromhex(script_cbor_hex)
    script_cbor_bytes = cbor2.loads(script_bytes)
    script_base64 = base64.b64encode(script_cbor_bytes).decode()
    return script_base64


def get_snapshot_rec(ledger_snapshot: dict) -> tp.Dict[str, tp.Union[int, list]]:
    """Get uniform record for ledger state snapshot."""
    hashes: tp.Dict[str, tp.Union[int, list]] = {}

    for r in ledger_snapshot:
        r_hash_rec = r[0]
        # In node 8.4+ the format is not list of dicts, but a dict like
        # {'keyHash-12d36d11cd0e570dde3c87360d4fb6074a1925e08a1a55513d7f7641': 1500000,
        #  'scriptHash-9c8e9da7f81e3ca90485f32ebefc98137c8ac260a072a00c4aaf142d': 17998926079, ...}
        if r_hash_rec in ("k", "s"):
            r_hash = r.split("-")[1]
            r_value = ledger_snapshot[r]
        else:
            r_hash = r_hash_rec.get("key hash") or r_hash_rec.get("script hash")
            r_value = r[1]

        if r_hash in hashes:
            hashes[r_hash] += r_value
        else:
            hashes[r_hash] = r_value

    return hashes


def get_snapshot_delegations(ledger_snapshot: dict) -> tp.Dict[str, tp.List[str]]:
    """Get delegations data from ledger state snapshot."""
    delegations: tp.Dict[str, tp.List[str]] = {}

    for r in ledger_snapshot:
        r_hash_rec = r[0]
        # In node 8.4+ the format is not list of dicts, but dict like
        # {'keyHash-12d36d11cd0e570dde3c87360d4fb6074a1925e08a1a55513d7f7641': POOL_ID,
        #  'scriptHash-9c8e9da7f81e3ca90485f32ebefc98137c8ac260a072a00c4aaf142d: POOL_ID, ...}
        if r_hash_rec in ("k", "s"):
            r_hash = r.split("-")[1]
            r_pool_id = ledger_snapshot[r]
        else:
            r_hash = r_hash_rec.get("key hash") or r_hash_rec.get("script hash")
            r_pool_id = r[1]

        if r_pool_id in delegations:
            delegations[r_pool_id].append(r_hash)
        else:
            delegations[r_pool_id] = [r_hash]

    return delegations
