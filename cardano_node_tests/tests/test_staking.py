import logging

import pytest

from cardano_node_tests.utils.clusterlib import CLIError
from cardano_node_tests.utils.clusterlib import TxFiles
from cardano_node_tests.utils.helpers import create_payment_addrs
from cardano_node_tests.utils.helpers import create_stake_addrs
from cardano_node_tests.utils.helpers import fund_from_faucet
from cardano_node_tests.utils.helpers import wait_for_stake_distribution

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("test_staking")


def test_delegate_using_addr(cluster_session, addrs_data_session, temp_dir):
    """Submit registration certificate and delegate do pool using address."""
    cluster = cluster_session

    stake_addr = create_stake_addrs(cluster, temp_dir, "addr_delegate_using_addr")[0]
    payment_addr = create_payment_addrs(
        cluster, temp_dir, "addr_delegate_using_addr", stake_vkey_file=stake_addr.vkey_file
    )[0]
    stake_addr_reg_cert_file = cluster.gen_stake_addr_registration_cert(
        temp_dir, "addr_delegate_using_addr", stake_addr.vkey_file
    )

    src_address = payment_addr.address

    # fund source address
    fund_from_faucet(cluster, addrs_data_session["user1"], src_address)

    src_init_balance = cluster.get_address_balance(src_address)

    tx_files = TxFiles(
        certificate_files=[stake_addr_reg_cert_file],
        signing_key_files=[payment_addr.skey_file, stake_addr.skey_file],
    )

    tx_raw_data = cluster.send_tx(src_address, tx_files=tx_files)
    cluster.wait_for_new_tip(slots_to_wait=2)

    assert (
        cluster.get_address_balance(src_address)
        == src_init_balance - tx_raw_data.fee - cluster.get_key_deposit()
    ), f"Incorrect balance for source address `{src_address}`"

    # delegate the addr0 stake address to one stake pool id
    first_pool_id_in_stake_dist = list(wait_for_stake_distribution(cluster))[0]

    src_init_balance = cluster.get_address_balance(src_address)

    delegation_fee = cluster.calculate_tx_fee(src_address, tx_files=tx_files)

    try:
        cluster.delegate_stake_addr(
            stake_addr_skey=stake_addr.skey_file,
            pool_id=first_pool_id_in_stake_dist,
            delegation_fee=delegation_fee,
        )
    except CLIError as excinfo:
        if "command not implemented yet" in str(excinfo):
            return

    cluster.wait_for_new_tip(slots_to_wait=2)

    stake_addr_info = cluster.get_stake_addr_info(stake_addr)
    assert (
        stake_addr_info.delegation is not None
    ), f"Stake address was not delegated yet: {stake_addr_info}"

    assert (
        cluster.get_address_balance(src_address) == src_init_balance - delegation_fee
    ), f"Incorrect balance for source address `{src_address}`"
