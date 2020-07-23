import logging
import os
import subprocess
from pathlib import Path
from typing import List
from typing import NamedTuple

from cardano_node_tests.utils.clusterlib import ClusterLib
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


def fund_from_faucet(
    cluster_obj: ClusterLib,
    faucet_data: dict,
    *dst_addrs: UnpackableSequence,
    amount: int = 2_000_000,
):
    """Send `amount` from faucet addr to all `dst_addrs`."""
    fund_dst = [TxOut(address=d, amount=amount) for d in dst_addrs]
    fund_tx_files = TxFiles(signing_key_files=[faucet_data["payment_key_pair"].skey_file])
    cluster_obj.send_funds(faucet_data["payment_addr"], fund_dst, tx_files=fund_tx_files)
    cluster_obj.wait_for_new_tip(slots_to_wait=2)


def create_addrs(
    cluster_obj: ClusterLib, temp_dir: FileType, *names: UnpackableSequence
) -> List[AddressRecord]:
    """Create new payment address(es)."""
    addrs = []
    for name in names:
        key_pair = cluster_obj.gen_payment_key_pair(temp_dir, name)
        addr = cluster_obj.get_payment_addr(payment_vkey_file=key_pair.vkey_file)
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


def setup_test_addrs(cluster_obj: ClusterLib, destination_dir: FileType) -> dict:
    """Create addresses and their keys for usage in tests."""
    destination_dir = Path(destination_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    addrs = ["user1", "pool-owner1"]

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
            stake_addr_vkey_file=stake_key_pair.vkey_file,
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


def setup_cluster() -> ClusterLib:
    """Prepare env and start cluster."""
    cluster_env = get_cluster_env()

    LOGGER.info("Starting cluster.")
    run_shell_command("start-cluster", workdir=cluster_env["work_dir"])

    cluster_obj = ClusterLib(cluster_env["state_dir"])
    cluster_obj.refresh_pparams()

    return cluster_obj
