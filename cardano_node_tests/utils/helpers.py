import logging
from pathlib import Path
from typing import NamedTuple

from cardano_node_tests.utils.clusterlib import ClusterLib
from cardano_node_tests.utils.clusterlib import TxFiles
from cardano_node_tests.utils.clusterlib import TxOut
from cardano_node_tests.utils.types import FileType
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)


class CreatedAddresses(NamedTuple):
    addr: str
    vkey_file: Path
    skey_file: Path


def read_address_from_file(location: FileType):
    with open(Path(location).expanduser()) as in_file:
        return in_file.read().strip()


def fund_addr_from_genesis(
    cluster_obj: ClusterLib, *dst_addrs: UnpackableSequence, amount: int = 2000000
):
    fund_dst = [TxOut(address=d, amount=amount) for d in dst_addrs]
    fund_tx_files = TxFiles(
        signing_key_files=[cluster_obj.delegate_skey, cluster_obj.genesis_utxo_skey]
    )
    cluster_obj.send_funds(cluster_obj.genesis_utxo_addr, fund_dst, tx_files=fund_tx_files)
    cluster_obj.wait_for_new_tip(slots_to_wait=2)


def create_addrs(cluster_obj, temp_dir, *names):
    """Create new payment addresses."""
    addrs = []
    for name in names:
        key_pair = cluster_obj.gen_payment_key_pair(temp_dir, name)
        addr = cluster_obj.get_payment_addr(payment_vkey_file=key_pair.vkey_file)
        addrs.append(
            CreatedAddresses(addr=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file)
        )

    LOGGER.debug(f"{len(addrs)} address(es) created")
    return addrs


def setup_addresses(cluster_obj: ClusterLib, destination_dir: FileType) -> dict:
    destination_dir = Path(destination_dir).expanduser()
    destination_dir.mkdir(parents=True, exist_ok=True)
    addrs = ["user1", "pool-owner1"]

    LOGGER.debug("Creating addresses and keys for tests." "")
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
    fund_addr_from_genesis(cluster_obj, *[d["payment_addr"] for d in addrs_data.values()])

    return addrs_data
