"""Utilities that extends the functionality of `cardano-clusterlib`."""

import base64
import dataclasses
import enum
import itertools
import json
import logging
import math
import pathlib as pl
import random
import time
import typing as tp

import cardano_clusterlib.types as cl_types
import cbor2
from cardano_clusterlib import clusterlib
from cardano_clusterlib import txtools as cl_txtools

from cardano_node_tests.utils import custom_clusterlib
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils.faucet import fund_from_faucet  # noqa: F401 # for compatibility

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, order=True)
class UpdateProposal:
    arg: str
    value: tp.Any
    name: str = ""
    check_func: tp.Callable | None = None


@dataclasses.dataclass(frozen=True, order=True)
class NativeTokenRec:
    token: str
    amount: int
    issuers_addrs: list[clusterlib.AddressRecord]
    token_mint_addr: clusterlib.AddressRecord
    script: pl.Path


@dataclasses.dataclass(frozen=True, order=True)
class Token:
    coin: str
    amount: int


@dataclasses.dataclass(frozen=True, order=True)
class TxMetadata:
    metadata: dict
    aux_data: list


@dataclasses.dataclass(frozen=True, order=True)
class ChainAccount:
    reserves: int
    treasury: int


class BuildMethods:
    BUILD: tp.Final[str] = "build"
    BUILD_RAW: tp.Final[str] = "build_raw"
    BUILD_EST: tp.Final[str] = "build_estimate"


class KeyGenMethods(enum.StrEnum):
    DIRECT = "direct"
    MNEMONIC = "mnemonic"


def build_and_submit_tx(
    *,
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    src_address: str,
    submit_method: str = "",
    build_method: str = "",
    txins: clusterlib.OptionalUTXOData = (),
    txouts: clusterlib.OptionalTxOuts = (),
    readonly_reference_txins: clusterlib.OptionalUTXOData = (),
    script_txins: clusterlib.OptionalScriptTxIn = (),
    return_collateral_txouts: clusterlib.OptionalTxOuts = (),
    total_collateral_amount: int | None = None,
    mint: clusterlib.OptionalMint = (),
    tx_files: clusterlib.TxFiles | None = None,
    complex_certs: clusterlib.OptionalScriptCerts = (),
    complex_proposals: clusterlib.OptionalScriptProposals = (),
    change_address: str = "",
    fee_buffer: int | None = None,
    raw_fee: int | None = None,
    required_signers: cl_types.OptionalFiles = (),
    required_signer_hashes: list[str] | None = None,
    withdrawals: clusterlib.OptionalTxOuts = (),
    script_withdrawals: clusterlib.OptionalScriptWithdrawals = (),
    script_votes: clusterlib.OptionalScriptVotes = (),
    deposit: int | None = None,
    current_treasury_value: int | None = None,
    treasury_donation: int | None = None,
    invalid_hereafter: int | None = None,
    invalid_before: int | None = None,
    witness_override: int | None = None,
    witness_count_add: int = 0,
    byron_witness_count: int = 0,
    reference_script_size: int = 0,
    script_valid: bool = True,
    calc_script_cost_file: cl_types.FileType | None = None,
    join_txouts: bool = True,
    destination_dir: cl_types.FileType = ".",
    cli_asset_balancing: bool | None = None,
) -> clusterlib.TxRawOutput:
    """
    Build and submit a transaction.

    Use `submit_method` to switch between `cardano-cli transaction submit` and submit-api.
    Use `build_method` to select one of the `cardano-cli transaction build*` commands:

        * build
        * build_raw
        * build_estimate

    """
    tx_files = tx_files or clusterlib.TxFiles()
    submit_method = submit_method or submit_utils.SubmitMethods.CLI

    build_method = build_method or BuildMethods.BUILD_RAW

    if build_method == BuildMethods.BUILD_RAW:
        # Resolve withdrawal amounts here (where -1 for total rewards amount is used) so the
        # resolved values can be passed around, and it is not needed to resolve them again
        # every time `_get_withdrawals` is called.
        withdrawals, script_withdrawals, *__ = cl_txtools._get_withdrawals(
            clusterlib_obj=cluster_obj,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
        )
        # Get UTxOs for src address here so the records can be passed around, and it is
        # not necessary to get them once for fee calculation and again for the final transaction
        # building.
        src_addr_utxos = (
            cluster_obj.g_query.get_utxo(address=src_address)
            if raw_fee is None and not txins
            else None
        )
        fee = (
            raw_fee
            if raw_fee is not None
            else cluster_obj.g_transaction.calculate_tx_fee(
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
                complex_proposals=complex_proposals,
                required_signers=required_signers,
                required_signer_hashes=required_signer_hashes,
                withdrawals=withdrawals,
                script_withdrawals=script_withdrawals,
                script_votes=script_votes,
                deposit=deposit,
                current_treasury_value=current_treasury_value,
                treasury_donation=treasury_donation,
                invalid_hereafter=invalid_hereafter,
                invalid_before=invalid_before,
                src_addr_utxos=src_addr_utxos,
                witness_count_add=witness_count_add,
                byron_witness_count=byron_witness_count,
                reference_script_size=reference_script_size,
                join_txouts=join_txouts,
                destination_dir=destination_dir,
            )
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
            complex_proposals=complex_proposals,
            fee=fee,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            script_votes=script_votes,
            deposit=deposit,
            current_treasury_value=current_treasury_value,
            treasury_donation=treasury_donation,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            src_addr_utxos=src_addr_utxos,
            join_txouts=join_txouts,
            destination_dir=destination_dir,
        )
    elif build_method == BuildMethods.BUILD_EST:
        skip_asset_balancing = True if cli_asset_balancing is None else cli_asset_balancing
        tx_output = cluster_obj.g_transaction.build_estimate_tx(
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
            complex_proposals=complex_proposals,
            change_address=change_address,
            fee_buffer=fee_buffer,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            script_votes=script_votes,
            deposit=deposit,
            current_treasury_value=current_treasury_value,
            treasury_donation=treasury_donation,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            script_valid=script_valid,
            witness_count_add=witness_count_add,
            byron_witness_count=byron_witness_count,
            reference_script_size=reference_script_size,
            join_txouts=join_txouts,
            destination_dir=destination_dir,
            skip_asset_balancing=skip_asset_balancing,
        )
    elif build_method == BuildMethods.BUILD:
        skip_asset_balancing = True if cli_asset_balancing is None else cli_asset_balancing
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
            complex_proposals=complex_proposals,
            change_address=change_address,
            fee_buffer=fee_buffer,
            required_signers=required_signers,
            required_signer_hashes=required_signer_hashes,
            withdrawals=withdrawals,
            script_withdrawals=script_withdrawals,
            script_votes=script_votes,
            deposit=deposit,
            treasury_donation=treasury_donation,
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
        err = f"Unsupported build method '{build_method}'"
        raise ValueError(err)

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


def register_stake_address(
    *,
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    name_template: str,
    deposit_amt: int = -1,
) -> clusterlib.TxRawOutput:
    """Register stake address."""
    # Files for registering stake address
    addr_reg_cert = cluster_obj.g_stake_address.gen_stake_addr_registration_cert(
        addr_name=name_template,
        deposit_amt=deposit_amt,
        stake_vkey_file=pool_user.stake.vkey_file,
    )
    tx_files = clusterlib.TxFiles(
        certificate_files=[addr_reg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )
    tx_output = build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_reg_stake_addr",
        src_address=pool_user.payment.address,
        tx_files=tx_files,
        deposit=None if deposit_amt == -1 else deposit_amt,
    )

    if not cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        msg = f"The address '{pool_user.stake.address}' was not registered."
        raise RuntimeError(msg)

    return tx_output


def deregister_stake_address(
    *,
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    name_template: str,
    deposit_amt: int = -1,
) -> clusterlib.TxRawOutput | None:
    """Deregister stake address."""
    stake_addr_info = cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address)
    if not stake_addr_info:
        return None

    stake_addr_dereg_cert = cluster_obj.g_stake_address.gen_stake_addr_deregistration_cert(
        addr_name=f"{name_template}_addr0",
        deposit_amt=deposit_amt,
        stake_vkey_file=pool_user.stake.vkey_file,
    )
    tx_files = clusterlib.TxFiles(
        certificate_files=[stake_addr_dereg_cert],
        signing_key_files=[
            pool_user.payment.skey_file,
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
    tx_output = build_and_submit_tx(
        cluster_obj=cluster_obj,
        name_template=f"{name_template}_dereg_stake_addr",
        src_address=pool_user.payment.address,
        tx_files=tx_files,
        withdrawals=withdrawals,
        deposit=None if deposit_amt == -1 else -deposit_amt,
    )

    if cluster_obj.g_query.get_stake_addr_info(pool_user.stake.address):
        msg = f"The address '{pool_user.stake.address}' is still registered."
        raise RuntimeError(msg)

    return tx_output


def gen_payment_addr_and_keys_from_mnemonic(
    *,
    name: str,
    cluster_obj: clusterlib.ClusterLib,
    size: tp.Literal[12, 15, 18, 21, 24] = 24,
    stake_vkey_file: cl_types.FileType | None = None,
    stake_script_file: cl_types.FileType | None = None,
    destination_dir: cl_types.FileType = ".",
) -> clusterlib.AddressRecord:
    """Generate payment address and key pair.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        size: Number of words in the mnemonic (12, 15, 18, 21, or 24).
        name: A name of the address and key pair.
        stake_vkey_file: A path to corresponding stake vkey file (optional).
        stake_script_file: A path to corresponding payment script file (optional).
        destination_dir: A path to directory for storing artifacts (optional).

    Returns:
        clusterlib.AddressRecord: A data container containing the address and
            key pair / script file.
    """
    mnemonic_file = pl.Path(destination_dir) / f"{name}_mnemonic"
    cluster_obj.g_key.gen_mnemonic(size=size, out_file=mnemonic_file)
    skey_file = cluster_obj.g_key.derive_from_mnemonic(
        key_name=name,
        key_type=clusterlib.KeyType.PAYMENT,
        mnemonic_file=mnemonic_file,
        key_number=0,
        destination_dir=destination_dir,
    )
    vkey_file = cluster_obj.g_key.gen_verification_key(
        key_name=name, signing_key_file=skey_file, destination_dir=destination_dir
    )
    key_pair = clusterlib.KeyPair(vkey_file=vkey_file, skey_file=skey_file)
    addr = cluster_obj.g_address.gen_payment_addr(
        addr_name=name,
        payment_vkey_file=key_pair.vkey_file,
        stake_vkey_file=stake_vkey_file,
        stake_script_file=stake_script_file,
        destination_dir=destination_dir,
    )

    return clusterlib.AddressRecord(
        address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file
    )


def create_payment_addr_records(
    *names: str,
    cluster_obj: clusterlib.ClusterLib,
    stake_vkey_file: cl_types.FileType | None = None,
    key_gen_method: KeyGenMethods = KeyGenMethods.DIRECT,
    destination_dir: cl_types.FileType = ".",
) -> list[clusterlib.AddressRecord]:
    """Create new payment address(es)."""
    if key_gen_method == KeyGenMethods.DIRECT:
        addrs = [
            cluster_obj.g_address.gen_payment_addr_and_keys(
                name=name,
                stake_vkey_file=stake_vkey_file,
                destination_dir=destination_dir,
            )
            for name in names
        ]
    elif key_gen_method == KeyGenMethods.MNEMONIC:
        size = random.choice((12, 15, 18, 21, 24))
        addrs = [
            gen_payment_addr_and_keys_from_mnemonic(
                name=f"{name}_size{size}",
                cluster_obj=cluster_obj,
                size=size,  # type: ignore
                stake_vkey_file=stake_vkey_file,
                destination_dir=destination_dir,
            )
            for name in names
        ]
    else:
        err = f"Unsupported key generation method '{key_gen_method}'"
        raise ValueError(err)

    LOGGER.debug(f"Created {len(addrs)} payment address(es)")
    return addrs


def create_stake_addr_records(
    *names: str,
    cluster_obj: clusterlib.ClusterLib,
    destination_dir: cl_types.FileType = ".",
) -> list[clusterlib.AddressRecord]:
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
    *,
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    no_of_addr: int = 1,
    payment_key_gen_method: KeyGenMethods = KeyGenMethods.DIRECT,
    destination_dir: cl_types.FileType = ".",
) -> list[clusterlib.PoolUser]:
    """Create PoolUsers."""
    pool_users = []
    for i in range(1, no_of_addr + 1):
        # Create key pairs and addresses
        stake_addr_rec = create_stake_addr_records(
            f"{name_template}_addr_{i}", cluster_obj=cluster_obj, destination_dir=destination_dir
        )[0]
        payment_addr_rec = create_payment_addr_records(
            f"{name_template}_addr_{i}",
            cluster_obj=cluster_obj,
            stake_vkey_file=stake_addr_rec.vkey_file,
            key_gen_method=payment_key_gen_method,
            destination_dir=destination_dir,
        )[0]
        # Create pool user struct
        pool_user = clusterlib.PoolUser(payment=payment_addr_rec, stake=stake_addr_rec)
        pool_users.append(pool_user)

    return pool_users


def wait_for_rewards(*, cluster_obj: clusterlib.ClusterLib) -> None:
    """Wait until 4th epoch, if necessary, for first reward distribution."""
    epoch = cluster_obj.g_query.get_epoch()
    if epoch >= 4:
        return

    new_epochs = 4 - epoch
    LOGGER.info(f"Waiting {new_epochs} epoch(s) to get first rewards.")
    cluster_obj.wait_for_epoch(epoch_no=4, padding_seconds=10)


def get_chain_account_state(*, ledger_state: dict) -> ChainAccount:
    """Get chain account state from ledger state dict."""
    state_before = ledger_state["stateBefore"]
    account_state = (
        state_before.get("esChainAccountState")  # In cardano-node >= 10.6.0
        or state_before.get("esAccountState")
    )
    if account_state is None:
        err = "Neither 'esChainAccountState' nor 'esAccountState' found in ledger state"
        raise KeyError(err)
    return ChainAccount(reserves=account_state["reserves"], treasury=account_state["treasury"])


def load_registered_pool_data(
    *, cluster_obj: clusterlib.ClusterLib, pool_name: str, pool_id: str
) -> clusterlib.PoolData:
    """Load data of existing registered pool."""
    if pool_id.startswith("pool"):
        pool_id = helpers.decode_bech32(pool_id)

    pool_state: dict = cluster_obj.g_query.get_pool_state(stake_pool_id=pool_id).pool_params
    metadata = helpers.get_pool_param("spsMetadata", pool_params=pool_state) or {}

    # TODO: extend to handle more relays records
    relays_list = helpers.get_pool_param("spsRelays", pool_params=pool_state) or []
    relay = relays_list[0] if relays_list else {}
    relay = relay.get("single host address") or {}

    pool_data = clusterlib.PoolData(
        pool_name=pool_name,
        pool_pledge=helpers.get_pool_param("spsPledge", pool_params=pool_state),
        pool_cost=helpers.get_pool_param("spsCost", pool_params=pool_state),
        pool_margin=helpers.get_pool_param("spsMargin", pool_params=pool_state),
        pool_metadata_url=metadata.get("url") or "",
        pool_metadata_hash=metadata.get("hash") or "",
        pool_relay_ipv4=relay.get("IPv4") or "",
        pool_relay_port=relay.get("port") or 0,
    )

    return pool_data


def check_pool_data(  # noqa: C901
    *,
    pool_params: dict,
    pool_creation_data: clusterlib.PoolData,
) -> str:
    """Check that actual pool state corresponds with pool creation data."""
    errors_list = []

    def _get_param(key: str) -> tp.Any:
        return helpers.get_pool_param(key, pool_params=pool_params)

    if _get_param("spsCost") != pool_creation_data.pool_cost:
        errors_list.append(
            "'cost' value is different than expected; "
            f"Expected: {pool_creation_data.pool_cost} vs Returned: {_get_param('spsCost')}"
        )

    if _get_param("spsMargin") != pool_creation_data.pool_margin:
        errors_list.append(
            "'margin' value is different than expected; "
            f"Expected: {pool_creation_data.pool_margin} vs Returned: {_get_param('spsMargin')}"
        )

    if _get_param("spsPledge") != pool_creation_data.pool_pledge:
        errors_list.append(
            "'pledge' value is different than expected; "
            f"Expected: {pool_creation_data.pool_pledge} vs Returned: {_get_param('spsPledge')}"
        )

    if _get_param("spsRelays") != (pool_creation_data.pool_relay_dns or []):
        errors_list.append(
            "'relays' value is different than expected; "
            f"Expected: {pool_creation_data.pool_relay_dns} vs "
            f"Returned: {_get_param('spsRelays')}"
        )

    if pool_creation_data.pool_metadata_url and pool_creation_data.pool_metadata_hash:
        metadata = _get_param("spsMetadata") or {}

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
    elif _get_param("spsMetadata") is not None:
        errors_list.append(
            "'metadata' value is different than expected; "
            f"Expected: None vs Returned: {_get_param('spsMetadata')}"
        )

    if errors_list:
        for err in errors_list:
            LOGGER.error(err)
        LOGGER.error(f"Stake Pool Details: \n{pool_params}")

    return "\n\n".join(errors_list)


def check_updated_params(*, update_proposals: list[UpdateProposal], protocol_params: dict) -> None:
    """Compare update proposals with actual protocol parameters."""
    if not protocol_params:
        msg = "Protocol parameters dictionary is empty!"
        raise ValueError(msg)

    failures = []
    for u in update_proposals:
        if u.check_func:
            if not u.check_func(update_proposal=u, protocol_params=protocol_params):
                failures.append(f"Check function failed for {u.name or u}")
        elif u.name:
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
        msg = f"Update proposal check failed!\n{failures_str}"
        raise AssertionError(msg)


def get_pparams_update_args(*, update_proposals: list[UpdateProposal]) -> list[str]:
    """Get cli arguments for pparams update action."""
    if not update_proposals:
        return []

    _cli_args = [(u.arg, str(u.value)) for u in update_proposals]
    cli_args = list(itertools.chain.from_iterable(_cli_args))
    return cli_args


def mint_or_burn_witness(
    *,
    cluster_obj: clusterlib.ClusterLib,
    new_tokens: list[NativeTokenRec],
    temp_template: str,
    invalid_hereafter: int | None = None,
    invalid_before: int | None = None,
    submit_method: str = submit_utils.SubmitMethods.CLI,
    build_method: str = BuildMethods.BUILD_RAW,
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

    # Create TX body
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
        # Meet the minimum required UTxO value
        lovelace_amount = 2_000_000 + math.ceil(len(mint_txouts) / 8) * 1_000_000
        txouts = [
            clusterlib.TxOut(address=new_tokens[0].token_mint_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

    if build_method == BuildMethods.BUILD:
        tx_output = cluster_obj.g_transaction.build_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            fee_buffer=2_000_000,
            mint=mint,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_override=len(signing_key_files),
        )
    elif build_method == BuildMethods.BUILD_RAW:
        fee = cluster_obj.g_transaction.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
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
    elif build_method == BuildMethods.BUILD_EST:
        tx_output = cluster_obj.g_transaction.build_estimate_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_count_add=len(signing_key_files),
        )
    else:
        msg = f"Unsupported build method: {build_method}"
        raise ValueError(msg)

    # Sign incrementally (just to check that it works)
    if sign_incrementally and len(signing_key_files) >= 1:
        # Create witness file for first required key
        witness_file = cluster_obj.g_transaction.witness_tx(
            tx_body_file=tx_output.out_file,
            witness_name=f"{temp_template}_skey0",
            signing_key_files=signing_key_files[:1],
        )
        # Sign Tx using witness file
        tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
            tx_body_file=tx_output.out_file,
            witness_files=[witness_file],
            tx_name=f"{temp_template}_sign0",
        )
        # Incrementally sign the already signed Tx with rest of required skeys
        for idx, skey in enumerate(signing_key_files[1:], start=1):
            tx_witnessed_file = cluster_obj.g_transaction.sign_tx(
                tx_file=tx_witnessed_file,
                signing_key_files=[skey],
                tx_name=f"{temp_template}_sign{idx}",
            )
    else:
        # Create witness file for each required key
        witness_files = [
            cluster_obj.g_transaction.witness_tx(
                tx_body_file=tx_output.out_file,
                witness_name=f"{temp_template}_skey{idx}",
                signing_key_files=[skey],
            )
            for idx, skey in enumerate(signing_key_files)
        ]

        # Sign Tx using witness files
        tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
            tx_body_file=tx_output.out_file,
            witness_files=witness_files,
            tx_name=temp_template,
        )

    # Submit signed TX
    assert tx_witnessed_file is not None  # for pyrefly
    submit_utils.submit_tx(
        submit_method=submit_method,
        cluster_obj=cluster_obj,
        tx_file=tx_witnessed_file,
        txins=tx_output.txins,
    )

    return tx_output


def mint_or_burn_sign(
    *,
    cluster_obj: clusterlib.ClusterLib,
    new_tokens: list[NativeTokenRec],
    temp_template: str,
    submit_method: str = submit_utils.SubmitMethods.CLI,
    build_method: str = BuildMethods.BUILD_RAW,
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

    # Build and sign a transaction
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
        # Meet the minimum required UTxO value
        lovelace_amount = 2_000_000 + math.ceil(len(mint_txouts) / 8) * 1_000_000
        txouts = [
            clusterlib.TxOut(address=new_tokens[0].token_mint_addr.address, amount=lovelace_amount),
            *mint_txouts,
        ]

    if build_method == BuildMethods.BUILD:
        tx_output = cluster_obj.g_transaction.build_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            fee_buffer=2000_000,
            mint=mint,
            witness_override=len(signing_key_files),
        )
    elif build_method == BuildMethods.BUILD_RAW:
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
    elif build_method == BuildMethods.BUILD_EST:
        tx_output = cluster_obj.g_transaction.build_estimate_tx(
            src_address=token_mint_addr.address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            witness_count_add=len(signing_key_files),
        )
    else:
        msg = f"Unsupported build method: {build_method}"
        raise ValueError(msg)

    # Sign incrementally (just to check that it works)
    if sign_incrementally and len(signing_key_files) >= 1:
        out_file_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=signing_key_files[:1],
            tx_name=f"{temp_template}_sign0",
        )
        # Incrementally sign the already signed Tx with rest of required skeys
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
    assert out_file_signed is not None  # for pyrefly
    submit_utils.submit_tx(
        submit_method=submit_method,
        cluster_obj=cluster_obj,
        tx_file=out_file_signed,
        txins=tx_output.txins,
    )

    return tx_output


def withdraw_reward_w_build(
    *,
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
    # Sign incrementally (just to check that it works)
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

    # Check that reward is 0
    if (
        cluster_obj.g_query.get_stake_addr_info(stake_addr_record.address).reward_account_balance
        != 0
    ):
        msg = "Not all rewards were transferred."
        raise RuntimeError(msg)

    # Check that rewards were transferred
    src_reward_balance = cluster_obj.g_query.get_address_balance(dst_address)
    assert tx_raw_withdrawal_output.withdrawals  # for typechecking
    if (
        src_reward_balance
        != src_init_balance
        - tx_raw_withdrawal_output.fee
        + tx_raw_withdrawal_output.withdrawals[0].amount
    ):
        msg = f"Incorrect balance for destination address `{dst_address}`."
        raise RuntimeError(msg)

    return tx_raw_withdrawal_output


def new_tokens(
    *asset_names: str,
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    token_mint_addr: clusterlib.AddressRecord,
    issuer_addr: clusterlib.AddressRecord,
    amount: int,
) -> list[NativeTokenRec]:
    """Mint new token, sign using skeys."""
    # Create simple script
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
            raise ValueError(msg)

        tokens_to_mint.append(
            NativeTokenRec(
                token=token,
                amount=amount,
                issuers_addrs=[issuer_addr],
                token_mint_addr=token_mint_addr,
                script=script,
            )
        )

    # Token minting
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
            raise ValueError(msg)

    return tokens_to_mint


def _get_ledger_state_cmd(*, cluster_obj: clusterlib.ClusterLib) -> str:
    cardano_cli_args = [
        "cardano-cli",
        "latest",
        "query",
        "ledger-state",
        *cluster_obj.magic_args,
    ]
    ledger_state_cmd = " ".join(cardano_cli_args)

    # Record cli coverage
    if hasattr(cluster_obj, "cli_coverage"):
        custom_clusterlib.record_cli_coverage(
            cli_args=cardano_cli_args,
            # pyrefly: ignore  # missing-attribute
            coverage_dict=cluster_obj.cli_coverage,  # pyright: ignore [reportAttributeAccessIssue]
        )

    return ledger_state_cmd


def get_delegation_state(*, cluster_obj: clusterlib.ClusterLib) -> dict:
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


def get_blocks_before(*, cluster_obj: clusterlib.ClusterLib) -> dict[str, int]:
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


def get_ledger_state(*, cluster_obj: clusterlib.ClusterLib) -> dict:
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
    *,
    cluster_obj: clusterlib.ClusterLib,
    state_name: str,
    ledger_state: dict | None = None,
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
    ledger_state = ledger_state or get_ledger_state(cluster_obj=cluster_obj)
    with open(json_file, "w", encoding="utf-8") as fp_out:
        json.dump(ledger_state, fp_out, indent=4)
    return json_file


def wait_for_epoch_interval(
    *,
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
        raise ValueError(msg)

    start_epoch = cluster_obj.g_query.get_epoch()

    # Wait for new block so we start counting with an up-to-date slot number
    cluster_obj.wait_for_new_block()

    for __ in range(40):
        s_from_epoch_start = cluster_obj.time_from_epoch_start()

        # Return if we are in the required interval
        if start_abs <= s_from_epoch_start <= stop_abs:
            break

        # If we are already after the required interval, wait for next epoch
        if stop_abs < s_from_epoch_start:
            if force_epoch:
                msg = (
                    f"Cannot reach the given interval ({start_abs}s to {stop_abs}s) in this epoch."
                )
                raise RuntimeError(msg)
            if cluster_obj.g_query.get_epoch() >= start_epoch + 2:
                msg = (
                    f"Was unable to reach the given interval ({start_abs}s to {stop_abs}s) "
                    "in past 3 epochs."
                )
                raise RuntimeError(msg)
            cluster_obj.wait_for_new_epoch()
            continue

        # Sleep until `start_abs`
        to_sleep = start_abs - s_from_epoch_start
        if to_sleep > 0:
            # `to_sleep` is float, wait for at least 1 second
            time.sleep(max(1.0, to_sleep))

        # We can finish if slot number of last minted block doesn't need
        # to match the time interval
        if not check_slot:
            break
    else:
        msg = f"Failed to wait for given interval from {start_abs}s to {stop_abs}s."
        raise RuntimeError(msg)


def load_body_metadata(*, tx_body_file: pl.Path) -> tp.Any:
    """Load metadata from file containing transaction body."""
    with open(tx_body_file, encoding="utf-8") as body_fp:
        tx_body_json = json.load(body_fp)

    cbor_body = bytes.fromhex(tx_body_json["cborHex"])
    loaded_body = cbor2.loads(cbor_body)
    metadata = loaded_body[-1]

    if not metadata:
        return []

    return metadata


def load_tx_metadata(*, tx_body_file: pl.Path) -> TxMetadata:
    """Load transaction metadata from file containing transaction body."""
    metadata_section = load_body_metadata(tx_body_file=tx_body_file)

    if not metadata_section:
        return TxMetadata(metadata={}, aux_data=[])

    # The `metadata_section` can be either list or `CBORTag`- check if it is `CBORTag`
    try:
        metadata_value = metadata_section.value
    except AttributeError:
        pass
    else:
        return TxMetadata(
            metadata=metadata_value.get(0) or {}, aux_data=metadata_value.get(1) or []
        )

    # Now we know the `metadata_section` is list
    try:
        metadata: dict = metadata_section[0]
    except KeyError:
        return TxMetadata(metadata=metadata_section, aux_data=[])

    try:
        aux_data: list = metadata_section[1]
    except KeyError:
        return TxMetadata(metadata=metadata, aux_data=[])

    return TxMetadata(metadata=metadata, aux_data=aux_data)


def datum_hash_from_txout(*, cluster_obj: clusterlib.ClusterLib, txout: clusterlib.TxOut) -> str:
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
    *,
    cluster_obj: clusterlib.ClusterLib,
    plutus_version: int,
    redeemer_file: pl.Path,
    tx_file: pl.Path | None = None,
) -> None:
    """Run the `create-script-context` command (available in plutus-apps)."""
    if plutus_version == 1:
        version_arg = "--plutus-v1"
    elif plutus_version == 2:
        version_arg = "--plutus-v2"
    else:
        msg = f"Unknown plutus version: {plutus_version}"
        raise ValueError(msg)

    if tx_file:
        cmd_args = [
            "create-script-context",
            "--generate-tx",
            str(tx_file),
            version_arg,
            *cluster_obj.magic_args,
            "--out-file",
            str(redeemer_file),
        ]
    else:
        cmd_args = ["create-script-context", version_arg, "--out-file", str(redeemer_file)]

    helpers.run_command(cmd_args)
    if not redeemer_file.exists():
        err = f"Expected file not created: {redeemer_file}"
        raise FileNotFoundError(err)


def cli_has(command: str) -> bool:
    """Check if a cardano-cli subcommand or argument is available.

    E.g. `cli_has("query leadership-schedule --next")`
    """
    full_command = f"cardano-cli {command}"
    return helpers.tool_has(full_command)


def check_txins_spent(
    *, cluster_obj: clusterlib.ClusterLib, txins: list[clusterlib.UTXOData], wait_blocks: int = 2
) -> None:
    """Check that txins were spent."""
    if wait_blocks > 0:
        cluster_obj.wait_for_new_block(wait_blocks)

    utxo_data = cluster_obj.g_query.get_utxo(utxo=txins)

    if utxo_data:
        msg = f"Some txins were not spent: {txins}"
        raise AssertionError(msg)


def create_reference_utxo(
    *,
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    script_file: pl.Path,
    amount: int,
) -> tuple[clusterlib.UTXOData, clusterlib.TxRawOutput]:
    """Create a reference script UTxO."""
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
    if not reference_utxos:
        err = "No reference script UTxO."
        raise RuntimeError(err)
    reference_utxo = reference_utxos[0]

    return reference_utxo, tx_raw_output


def get_utxo_ix_offset(*, utxos: list[clusterlib.UTXOData], txouts: list[clusterlib.TxOut]) -> int:
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
    *,
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
    if not secret_file.exists():
        err = f"Expected file not created: {secret_file}"
        raise FileNotFoundError(err)

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
    if not skey_file.exists():
        err = f"Expected file not created: {skey_file}"
        raise FileNotFoundError(err)

    vkey_file = cluster_obj.g_key.gen_verification_key(
        key_name=f"{name_template}_byron", signing_key_file=skey_file
    )
    address = cluster_obj.g_address.gen_payment_addr(
        addr_name=f"{name_template}_byron", payment_vkey_file=vkey_file
    )

    return clusterlib.AddressRecord(address=address, vkey_file=vkey_file, skey_file=skey_file)


def get_plutus_b64(*, script_file: cl_types.FileType) -> str:
    """Get base64 encoded binary version of Plutus script from file."""
    with open(script_file, encoding="utf-8") as fp_in:
        script_str = json.load(fp_in)

    script_cbor_hex = script_str["cborHex"]
    script_bytes = bytes.fromhex(script_cbor_hex)
    script_cbor_bytes = cbor2.loads(script_bytes)
    script_base64 = base64.b64encode(script_cbor_bytes).decode()
    return script_base64


def get_snapshot_rec(*, ledger_snapshot: dict) -> dict[str, int | list]:
    """Get uniform record for ledger state snapshot."""
    hashes: dict[str, int | list] = {}

    for rk, r_value in ledger_snapshot.items():
        # In node 8.4+ the format is not list of dicts, but a dict like
        # {'keyHash-12d36d11cd0e570dde3c87360d4fb6074a1925e08a1a55513d7f7641': 1500000,
        #  'scriptHash-9c8e9da7f81e3ca90485f32ebefc98137c8ac260a072a00c4aaf142d': 17998926079, ...}
        r_hash = rk.split("-")[1]
        if r_hash in hashes:
            hashes[r_hash] += r_value
        else:
            hashes[r_hash] = r_value

    return hashes


def get_snapshot_delegations(*, ledger_snapshot: dict) -> dict[str, list[str]]:
    """Get delegations data from ledger state snapshot."""
    delegations: dict[str, list[str]] = {}

    for rk, r_pool_id in ledger_snapshot.items():
        # In node 8.4+ the format is not list of dicts, but dict like
        # {'keyHash-12d36d11cd0e570dde3c87360d4fb6074a1925e08a1a55513d7f7641': POOL_ID,
        #  'scriptHash-9c8e9da7f81e3ca90485f32ebefc98137c8ac260a072a00c4aaf142d: POOL_ID, ...}
        r_hash = rk.split("-")[1]
        if r_pool_id in delegations:
            delegations[r_pool_id].append(r_hash)
        else:
            delegations[r_pool_id] = [r_hash]

    return delegations


def create_collaterals(
    *,
    cluster: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    temp_template: str,
    tx_outs: list[clusterlib.TxOut],
) -> list[clusterlib.UTXOData]:
    """Create collateral UTxOs as required."""
    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )
    tx_output = cluster.g_transaction.build_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_collaterals_tx",
        tx_files=tx_files,
        txouts=tx_outs,
        fee_buffer=2_000_000,
        # Don't join 'change' and 'collateral' txouts, we need separate UTxOs
        join_txouts=False,
    )
    tx_signed = cluster.g_transaction.sign_tx(
        tx_body_file=tx_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=f"{temp_template}_collaterals_signed",
    )
    cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_output.txins)

    utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
    utxo_ix_offset = get_utxo_ix_offset(utxos=utxos, txouts=tx_output.txouts)
    # Return collateral UTxOs according to the order in `tx_outs`
    return utxos[utxo_ix_offset : utxo_ix_offset + len(tx_outs)]


def build_stake_multisig_script(
    *,
    cluster_obj: clusterlib.ClusterLib,
    script_name: str,
    script_type_arg: str,
    stake_vkey_files: tp.Iterable[pl.Path],
    required: int = 0,
    slot: int = 0,
    slot_type_arg: str = "",
) -> pl.Path:
    """Build a stake multi-signature script.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        script_name: A name of the script.
        script_type_arg: A script type, see `MultiSigTypeArgs`.
        stake_vkey_files: A list of paths to stake vkey files.
        required: A number of required keys for the "atLeast" script type (optional).
        slot: A slot that sets script validity, depending on value of `slot_type_arg`
            (optional).
        slot_type_arg: A slot validity type, see `MultiSlotTypeArgs` (optional).

    Returns:
        Path: A path to the script file.
    """
    out_file = pl.Path(f"{script_name}_multisig.script")

    scripts_l: list[dict] = [
        {
            "keyHash": cluster_obj.g_stake_address.get_stake_vkey_hash(stake_vkey_file=f),
            "type": "sig",
        }
        for f in stake_vkey_files
    ]
    if slot:
        scripts_l.append({"slot": slot, "type": slot_type_arg})

    script: dict = {
        "scripts": scripts_l,
        "type": script_type_arg,
    }

    if script_type_arg == clusterlib.MultiSigTypeArgs.AT_LEAST:
        script["required"] = required

    with open(out_file, "w", encoding="utf-8") as fp_out:
        json.dump(script, fp_out, indent=4)

    return out_file


def get_just_lovelace_utxos(
    *, address_utxos: list[clusterlib.UTXOData]
) -> list[clusterlib.UTXOData]:
    """Get UTxOs with just Lovelace."""
    return cl_txtools._get_usable_utxos(
        address_utxos=address_utxos, coins={clusterlib.DEFAULT_COIN}
    )
