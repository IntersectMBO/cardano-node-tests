import json
import logging
import os
import subprocess
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional

from _pytest.fixtures import FixtureRequest

from cardano_node_tests.utils.clusterlib import CLIError
from cardano_node_tests.utils.clusterlib import ClusterLib
from cardano_node_tests.utils.clusterlib import ColdKeyPair
from cardano_node_tests.utils.clusterlib import KeyPair
from cardano_node_tests.utils.clusterlib import PoolData
from cardano_node_tests.utils.clusterlib import TxFiles
from cardano_node_tests.utils.clusterlib import TxOut
from cardano_node_tests.utils.types import FileType
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)


class AddressRecord(NamedTuple):
    address: str
    vkey_file: Path
    skey_file: Path


def read_address_from_file(location: FileType):
    with open(Path(location).expanduser()) as in_file:
        return in_file.read().strip()


def write_json(location: FileType, content: dict):
    with open(Path(location).expanduser(), "w") as out_file:
        out_file.write(json.dumps(content))
    return location


def run_shell_command(command: str, workdir: FileType = ""):
    """Run command in shell."""
    cmd = f"bash -c '{command}'"
    cmd = cmd if not workdir else f"cd {workdir}; {cmd}"
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True)
    __, stderr = p.communicate()
    if p.returncode != 0:
        raise AssertionError(f"An error occurred while running `{cmd}`: {stderr.decode()}")


def fund_from_genesis(
    cluster_obj: ClusterLib, *dst_addrs: UnpackableSequence, amount: int = 2_000_000
):
    """Send `amount` from genesis addr to all `dst_addrs`."""
    fund_dst = [TxOut(address=d, amount=amount) for d in dst_addrs]
    fund_tx_files = TxFiles(
        signing_key_files=[cluster_obj.delegate_skey, cluster_obj.genesis_utxo_skey]
    )
    cluster_obj.send_funds(cluster_obj.genesis_utxo_addr, fund_dst, tx_files=fund_tx_files)
    cluster_obj.wait_for_new_tip(slots_to_wait=2)


def return_funds_to_faucet(
    cluster_obj: ClusterLib, faucet_addr: str, *src_addrs: AddressRecord, amount: int = -1,
):
    """Send `amount` from all `src_addrs` to `faucet_addr`."""
    try:
        logging.disable(logging.ERROR)
        for src in src_addrs:
            fund_dst = [TxOut(address=faucet_addr, amount=amount)]
            fund_tx_files = TxFiles(signing_key_files=[src.skey_file])
            # try to return funds; don't mind if there's not enough funds for fees etc.
            try:
                cluster_obj.send_funds(src.address, fund_dst, tx_files=fund_tx_files)
            except CLIError:
                pass
    finally:
        logging.disable(logging.NOTSET)
    cluster_obj.wait_for_new_tip(slots_to_wait=2)


def fund_from_faucet(
    cluster_obj: ClusterLib,
    faucet_data: dict,
    *dst_addrs: AddressRecord,
    amount: int = 3_000_000,
    request: Optional[FixtureRequest] = None,
):
    """Send `amount` from faucet addr to all `dst_addrs`."""
    if request:
        request.addfinalizer(
            lambda: return_funds_to_faucet(cluster_obj, faucet_data["payment_addr"], *dst_addrs)
        )

    fund_dst = [TxOut(address=d.address, amount=amount) for d in dst_addrs]
    fund_tx_files = TxFiles(signing_key_files=[faucet_data["payment_key_pair"].skey_file])
    cluster_obj.send_funds(faucet_data["payment_addr"], fund_dst, tx_files=fund_tx_files)
    cluster_obj.wait_for_new_tip(slots_to_wait=2)


def create_payment_addrs(
    cluster_obj: ClusterLib,
    temp_dir: FileType,
    *names: UnpackableSequence,
    stake_vkey_file: Optional[FileType] = None,
) -> List[AddressRecord]:
    """Create new payment address(es)."""
    addrs = []
    for name in names:
        key_pair = cluster_obj.gen_payment_key_pair(temp_dir, name)
        addr = cluster_obj.get_payment_addr(
            payment_vkey_file=key_pair.vkey_file, stake_vkey_file=stake_vkey_file
        )
        addrs.append(
            AddressRecord(address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file)
        )

    LOGGER.debug(f"Created {len(addrs)} payment address(es)")
    return addrs


def create_stake_addrs(
    cluster_obj: ClusterLib, temp_dir: FileType, *names: UnpackableSequence
) -> List[AddressRecord]:
    """Create new stake address(es)."""
    addrs = []
    for name in names:
        key_pair = cluster_obj.gen_stake_key_pair(temp_dir, name)
        addr = cluster_obj.get_stake_addr(stake_vkey_file=key_pair.vkey_file)
        addrs.append(
            AddressRecord(address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file)
        )

    LOGGER.debug(f"Created {len(addrs)} stake address(es)")
    return addrs


def load_pools_data():
    data_dir = get_cluster_env()["state_dir"] / "nodes"
    pools = ("node-pool1", "node-pool2")

    addrs_data = {}
    for addr_name in pools:
        addr_data_dir = data_dir / addr_name
        addrs_data[addr_name] = {
            "payment_key_pair": KeyPair(
                vkey_file=addr_data_dir / "owner-utxo.vkey",
                skey_file=addr_data_dir / "owner-utxo.skey",
            ),
            "stake_key_pair": KeyPair(
                vkey_file=addr_data_dir / "owner-stake.vkey",
                skey_file=addr_data_dir / "owner-stake.skey",
            ),
            "payment_addr": read_address_from_file(addr_data_dir / "owner.addr"),
            "stake_addr": read_address_from_file(addr_data_dir / "owner-stake.addr"),
            "stake_addr_registration_cert": read_address_from_file(
                addr_data_dir / "stake.reg.cert"
            ),
            "cold_key_pair": ColdKeyPair(
                vkey_file=addr_data_dir / "cold.vkey",
                skey_file=addr_data_dir / "cold.skey",
                counter_file=addr_data_dir / "cold.counter",
            ),
        }

    return addrs_data


def get_cluster_env() -> dict:
    socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"]).expanduser().resolve()
    state_dir = socket_path.parent
    work_dir = state_dir.parent
    repo_dir = Path(os.environ.get("CARDANO_NODE_REPO_PATH") or work_dir)

    cluster_env = {
        "socket_path": socket_path,
        "state_dir": state_dir,
        "repo_dir": repo_dir,
        "work_dir": work_dir,
    }
    return cluster_env


def wait_for_stake_distribution(cluster_obj: ClusterLib):
    last_block_epoch = cluster_obj.get_last_block_epoch()
    if last_block_epoch < 3:
        epochs_to_wait = 3 - last_block_epoch
        LOGGER.info(f"Waiting {epochs_to_wait} epoch(s) to get stake distribution.")
        cluster_obj.wait_for_new_epoch(epochs_to_wait=epochs_to_wait)
    return cluster_obj.get_stake_distribution()


def setup_test_addrs(cluster_obj: ClusterLib, destination_dir: FileType) -> dict:
    """Create addresses and their keys for usage in tests."""
    destination_dir = Path(destination_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    addrs = ("user1", "pool-owner1")

    LOGGER.debug("Creating addresses and keys for tests.")
    addrs_data = {}
    for addr_name in addrs:
        payment_key_pair = cluster_obj.gen_payment_key_pair(
            destination_dir=destination_dir, key_name=addr_name
        )
        stake_key_pair = cluster_obj.gen_stake_key_pair(
            destination_dir=destination_dir, key_name=addr_name
        )
        payment_addr = cluster_obj.get_payment_addr(
            payment_vkey_file=payment_key_pair.vkey_file, stake_vkey_file=stake_key_pair.vkey_file,
        )
        stake_addr = cluster_obj.get_stake_addr(stake_vkey_file=stake_key_pair.vkey_file)
        stake_addr_registration_cert = cluster_obj.gen_stake_addr_registration_cert(
            destination_dir=destination_dir,
            addr_name=addr_name,
            stake_vkey_file=stake_key_pair.vkey_file,
        )

        addrs_data[addr_name] = {
            "payment_key_pair": payment_key_pair,
            "stake_key_pair": stake_key_pair,
            "payment_addr": payment_addr,
            "stake_addr": stake_addr,
            "stake_addr_registration_cert": stake_addr_registration_cert,
        }

    LOGGER.debug("Funding created addresses." "")
    fund_from_genesis(
        cluster_obj, *[d["payment_addr"] for d in addrs_data.values()], amount=10_000_000_000
    )

    pools_data = load_pools_data()
    return {**addrs_data, **pools_data}


def setup_cluster() -> ClusterLib:
    """Prepare env and start cluster."""
    cluster_env = get_cluster_env()

    LOGGER.info("Starting cluster.")
    run_shell_command("start-cluster", workdir=cluster_env["work_dir"])

    cluster_obj = ClusterLib(cluster_env["state_dir"])
    cluster_obj.refresh_pparams()

    return cluster_obj


def stop_cluster():
    LOGGER.info("Stopping cluster.")
    cluster_env = get_cluster_env()
    try:
        run_shell_command("stop-cluster", workdir=cluster_env["work_dir"])
    except Exception as exc:  # pylint: disable=broad-except
        LOGGER.debug(f"Failed to stop cluster: {exc}")


def check_pool_data(pool_ledger_state: dict, pool_creation_data: PoolData) -> str:  # noqa: C901
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
