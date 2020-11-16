import datetime
import json
import logging
from pathlib import Path
from typing import Any
from typing import List
from typing import Optional
from typing import Union

from _pytest.fixtures import FixtureRequest

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType

LOGGER = logging.getLogger(__name__)

TEST_TEMP_DIR = helpers.get_basetemp()


def get_timestamped_rand_str(rand_str_length: int = 4) -> str:
    """Return random string prefixed with timestamp.

    >>> len(get_timestamped_rand_str()) == len("200801_002401314_cinf")
    True
    """
    timestamp = datetime.datetime.now().strftime("%y%m%d_%H%M%S%f")[:-3]
    rand_str_component = clusterlib.get_rand_str(rand_str_length)
    rand_str_component = rand_str_component and f"_{rand_str_component}"
    return f"{timestamp}{rand_str_component}"


def withdraw_reward(
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    name_template: str,
    dst_addr_record: Optional[clusterlib.AddressRecord] = None,
) -> None:
    """Withdraw rewards to payment address."""
    dst_addr_record = dst_addr_record or pool_user.payment
    dst_address = dst_addr_record.address
    src_init_balance = cluster_obj.get_address_balance(dst_address)

    tx_files_withdrawal = clusterlib.TxFiles(
        signing_key_files=[dst_addr_record.skey_file, pool_user.stake.skey_file],
    )

    this_epoch = cluster_obj.get_last_block_epoch()

    tx_raw_withdrawal_output = cluster_obj.send_tx(
        src_address=dst_address,
        tx_name=f"{name_template}_reward_withdrawal",
        tx_files=tx_files_withdrawal,
        withdrawals=[clusterlib.TxOut(address=pool_user.stake.address, amount=-1)],
    )
    cluster_obj.wait_for_new_block(new_blocks=2)

    if this_epoch != cluster_obj.get_last_block_epoch():
        LOGGER.warning("New epoch during rewards withdrawal! Reward account may not be empty.")
    else:
        # check that reward is 0
        assert (
            cluster_obj.get_stake_addr_info(pool_user.stake.address).reward_account_balance == 0
        ), "Not all rewards were transfered"

    # check that rewards were transfered
    src_reward_balance = cluster_obj.get_address_balance(dst_address)
    assert (
        src_reward_balance
        == src_init_balance
        - tx_raw_withdrawal_output.fee
        + tx_raw_withdrawal_output.withdrawals[0].amount  # type: ignore
    ), f"Incorrect balance for destination address `{dst_address}`"


def deregister_stake_addr(
    cluster_obj: clusterlib.ClusterLib, pool_user: clusterlib.PoolUser, name_template: str
) -> clusterlib.TxRawOutput:
    """Deregister stake address."""
    # files for deregistering stake address
    stake_addr_dereg_cert = cluster_obj.gen_stake_addr_deregistration_cert(
        addr_name=f"{name_template}_addr0_dereg", stake_vkey_file=pool_user.stake.vkey_file
    )
    tx_files_deregister = clusterlib.TxFiles(
        certificate_files=[stake_addr_dereg_cert],
        signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
    )

    # withdraw rewards to payment address
    withdraw_reward(cluster_obj=cluster_obj, pool_user=pool_user, name_template=name_template)

    tx_raw_output = cluster_obj.send_tx(
        src_address=pool_user.payment.address,
        tx_name=f"{name_template}_dereg_stake_addr",
        tx_files=tx_files_deregister,
    )
    cluster_obj.wait_for_new_block(new_blocks=2)
    return tx_raw_output


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
        if cluster_obj.get_address_balance(d) < amount
    ]
    if not fund_dst:
        return

    with helpers.FileLockIfXdist(f"{TEST_TEMP_DIR}/{cluster_obj.genesis_utxo_addr}.lock"):
        tx_name = tx_name or get_timestamped_rand_str()
        tx_name = f"{tx_name}_genesis_funding"
        fund_tx_files = clusterlib.TxFiles(
            signing_key_files=[*cluster_obj.delegate_skeys, cluster_obj.genesis_utxo_skey]
        )

        cluster_obj.send_funds(
            src_address=cluster_obj.genesis_utxo_addr,
            destinations=fund_dst,
            tx_name=tx_name,
            tx_files=fund_tx_files,
            destination_dir=destination_dir,
        )
        cluster_obj.wait_for_new_block(new_blocks=2)


def return_funds_to_faucet(
    *src_addrs: clusterlib.AddressRecord,
    cluster_obj: clusterlib.ClusterLib,
    faucet_addr: str,
    amount: int = -1,
    tx_name: Optional[str] = None,
    destination_dir: FileType = ".",
) -> None:
    """Send `amount` from all `src_addrs` to `faucet_addr`.

    The amount of "-1" means all available funds.
    """
    tx_name = tx_name or get_timestamped_rand_str()
    tx_name = f"{tx_name}_return_funds"
    with helpers.FileLockIfXdist(f"{TEST_TEMP_DIR}/{faucet_addr}.lock"):
        try:
            logging.disable(logging.ERROR)
            for src in src_addrs:
                fund_dst = [clusterlib.TxOut(address=faucet_addr, amount=amount)]
                fund_tx_files = clusterlib.TxFiles(signing_key_files=[src.skey_file])
                # try to return funds; don't mind if there's not enough funds for fees etc.
                try:
                    cluster_obj.send_funds(
                        src_address=src.address,
                        destinations=fund_dst,
                        tx_name=tx_name,
                        tx_files=fund_tx_files,
                        destination_dir=destination_dir,
                    )
                    cluster_obj.wait_for_new_block(new_blocks=2)
                except Exception:
                    pass
        finally:
            logging.disable(logging.NOTSET)


def fund_from_faucet(
    *dst_addrs: Union[clusterlib.AddressRecord, clusterlib.PoolUser],
    cluster_obj: clusterlib.ClusterLib,
    faucet_data: dict,
    amount: int = 3_000_000,
    tx_name: Optional[str] = None,
    request: Optional[FixtureRequest] = None,
    destination_dir: FileType = ".",
    force: bool = False,
) -> None:
    """Send `amount` from faucet addr to all `dst_addrs`."""
    # get payment AddressRecord out of PoolUser
    dst_addr_records: List[clusterlib.AddressRecord] = [
        (r.payment if hasattr(r, "payment") else r) for r in dst_addrs  # type: ignore
    ]

    fund_dst = [
        clusterlib.TxOut(address=d.address, amount=amount)
        for d in dst_addr_records
        if force or cluster_obj.get_address_balance(d.address) < amount
    ]
    if not fund_dst:
        return

    if request:
        request.addfinalizer(
            lambda: return_funds_to_faucet(
                *dst_addr_records,
                cluster_obj=cluster_obj,
                faucet_addr=faucet_data["payment"].address,
                tx_name=tx_name,
                destination_dir=destination_dir,
            )
        )

    src_address = faucet_data["payment"].address
    with helpers.FileLockIfXdist(f"{TEST_TEMP_DIR}/{src_address}.lock"):
        tx_name = tx_name or get_timestamped_rand_str()
        tx_name = f"{tx_name}_funding"
        fund_tx_files = clusterlib.TxFiles(signing_key_files=[faucet_data["payment"].skey_file])

        cluster_obj.send_funds(
            src_address=src_address,
            destinations=fund_dst,
            tx_name=tx_name,
            tx_files=fund_tx_files,
            destination_dir=destination_dir,
        )
        cluster_obj.wait_for_new_block(new_blocks=2)


def create_payment_addr_records(
    *names: str,
    cluster_obj: clusterlib.ClusterLib,
    stake_vkey_file: Optional[FileType] = None,
    destination_dir: FileType = ".",
) -> List[clusterlib.AddressRecord]:
    """Create new payment address(es)."""
    addrs = [
        cluster_obj.gen_payment_addr_and_keys(
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
        cluster_obj.gen_stake_addr_and_keys(name=name, destination_dir=destination_dir)
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
    payment_addrs = []
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
        payment_addrs.append(payment_addr_rec)
        pool_users.append(pool_user)

    return pool_users


def wait_for_stake_distribution(cluster_obj: clusterlib.ClusterLib) -> dict:
    """Wait to 3rd epoch (if necessary) and return stake distribution info."""
    last_block_epoch = cluster_obj.get_last_block_epoch()
    if last_block_epoch < 3:
        new_epochs = 3 - last_block_epoch
        LOGGER.info(f"Waiting {new_epochs} epoch(s) to get stake distribution.")
        cluster_obj.wait_for_new_epoch(new_epochs)
    return cluster_obj.get_stake_distribution()


def time_to_next_epoch_start(cluster_obj: clusterlib.ClusterLib) -> float:
    """How many seconds to start of new epoch."""
    slots_to_go = (
        cluster_obj.get_last_block_epoch() + 1
    ) * cluster_obj.epoch_length - cluster_obj.get_last_block_slot_no()
    return float(slots_to_go * cluster_obj.slot_length)


def load_registered_pool_data(
    cluster_obj: clusterlib.ClusterLib, pool_name: str, pool_id: str
) -> clusterlib.PoolData:
    """Load data of existing registered pool."""
    if pool_id.startswith("pool"):
        pool_id = helpers.decode_bech32(pool_id)

    pool_state: dict = cluster_obj.get_registered_stake_pools_ledger_state().get(pool_id) or {}
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
    pool_ledger_state: dict, pool_creation_data: clusterlib.PoolData
) -> str:
    """Check that actual pool state corresponds with pool creation data."""
    errors_list = []

    if pool_ledger_state["cost"] != pool_creation_data.pool_cost:
        errors_list.append(
            "'cost' value is different than expected; "
            f"Expected: {pool_creation_data.pool_cost} vs Returned: {pool_ledger_state['cost']}"
        )

    if pool_ledger_state["margin"] != pool_creation_data.pool_margin:
        errors_list.append(
            "'margin' value is different than expected; "
            f"Expected: {pool_creation_data.pool_margin} vs Returned: {pool_ledger_state['margin']}"
        )

    if pool_ledger_state["pledge"] != pool_creation_data.pool_pledge:
        errors_list.append(
            "'pledge' value is different than expected; "
            f"Expected: {pool_creation_data.pool_pledge} vs Returned: {pool_ledger_state['pledge']}"
        )

    if pool_ledger_state["relays"] != (pool_creation_data.pool_relay_dns or []):
        errors_list.append(
            "'relays' value is different than expected; "
            f"Expected: {pool_creation_data.pool_relay_dns} vs "
            f"Returned: {pool_ledger_state['relays']}"
        )

    if pool_creation_data.pool_metadata_url and pool_creation_data.pool_metadata_hash:
        metadata = pool_ledger_state.get("metadata") or {}

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
    elif pool_ledger_state["metadata"] is not None:
        errors_list.append(
            "'metadata' value is different than expected; "
            f"Expected: None vs Returned: {pool_ledger_state['metadata']}"
        )

    if errors_list:
        for err in errors_list:
            LOGGER.error(err)
        LOGGER.error(f"Stake Pool Details: \n{pool_ledger_state}")

    return "\n\n".join(errors_list)


def update_params(
    cluster_obj: clusterlib.ClusterLib, cli_arg: str, param_name: str, param_value: Any
) -> None:
    """Update params using update proposal."""
    with helpers.FileLockIfXdist(f"{TEST_TEMP_DIR}/update_params.lock"):
        if str(cluster_obj.get_protocol_params()[param_name]) == str(param_value):
            LOGGER.info(f"Value for '{param_name}' is already {param_value}. Nothing to do.")
            return

        LOGGER.info("Waiting for new epoch to submit proposal.")
        cluster_obj.wait_for_new_epoch()

        cluster_obj.submit_update_proposal(
            cli_args=[cli_arg, str(param_value)],
            tx_name=f"{param_name}_{get_timestamped_rand_str()}",
        )

        LOGGER.info(f"Update Proposal submitted (cli_arg={cli_arg}, param_value={param_value})")
        cluster_obj.wait_for_new_epoch()

        updated_value = cluster_obj.get_protocol_params()[param_name]
        if str(updated_value) != str(param_value):
            raise AssertionError(
                f"Cluster update proposal failed! Param value: {updated_value}.\n"
                f"Tip:{cluster_obj.get_tip()}"
            )


def save_cli_coverage(
    cluster_obj: clusterlib.ClusterLib, request: FixtureRequest
) -> Optional[Path]:
    """Save CLI coverage info."""
    cli_coverage_dir = request.config.getoption("--cli-coverage-dir")
    if not (cli_coverage_dir and cluster_obj.cli_coverage):
        return None

    json_file = Path(cli_coverage_dir) / f"cli_coverage_{get_timestamped_rand_str(0)}.json"
    with open(json_file, "w") as out_json:
        json.dump(cluster_obj.cli_coverage, out_json, indent=4)
    LOGGER.info(f"Coverage files saved to '{cli_coverage_dir}'.")
    return json_file


def save_ledger_state(
    cluster_obj: clusterlib.ClusterLib,
    name_template: str,
    destination_dir: FileType = ".",
) -> Path:
    """Save ledger state."""
    name_template = name_template or get_timestamped_rand_str(0)
    json_file = Path(destination_dir) / f"{name_template}_ledger_state.json"
    cluster_obj.query_cli(["ledger-state", "--out-file", str(json_file)])
    return json_file
