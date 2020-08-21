import logging
from pathlib import Path
from typing import Any
from typing import Dict

import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.tmpdir import TempdirFactory

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir and change to it."""
    tmp_path = Path(tmp_path_factory.mktemp("test_staking"))
    with helpers.change_cwd(tmp_path):
        yield tmp_path


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


def _delegate_stake_addr(
    cluster_obj: clusterlib.ClusterLib,
    addrs_data: dict,
    temp_template: str,
    request: FixtureRequest,
    delegate_with_pool_id: bool = False,
):
    """Submit registration certificate and delegate to pool."""
    node_cold = addrs_data["node-pool1"]["cold_key_pair"]
    stake_pool_id = cluster_obj.get_stake_pool_id(node_cold.vkey_file)

    # create key pairs and addresses
    stake_addr_rec = helpers.create_stake_addr_records(
        f"addr0_{temp_template}", cluster_obj=cluster_obj
    )[0]
    payment_addr_rec = helpers.create_payment_addr_records(
        f"addr0_{temp_template}", cluster_obj=cluster_obj, stake_vkey_file=stake_addr_rec.vkey_file,
    )[0]

    pool_user = clusterlib.PoolUser(payment=payment_addr_rec, stake=stake_addr_rec)

    # create stake address registration cert
    stake_addr_reg_cert_file = cluster_obj.gen_stake_addr_registration_cert(
        addr_name=f"addr0_{temp_template}", stake_vkey_file=stake_addr_rec.vkey_file
    )

    # create stake address delegation cert
    deleg_kwargs: Dict[str, Any] = {
        "addr_name": f"addr0_{temp_template}",
        "stake_vkey_file": stake_addr_rec.vkey_file,
    }
    if delegate_with_pool_id:
        deleg_kwargs["stake_pool_id"] = stake_pool_id
    else:
        deleg_kwargs["node_cold_vkey_file"] = node_cold.vkey_file

    stake_addr_deleg_cert_file = cluster_obj.gen_stake_addr_delegation_cert(**deleg_kwargs)

    # fund source address
    helpers.fund_from_faucet(
        payment_addr_rec, cluster_obj=cluster_obj, faucet_data=addrs_data["user1"], request=request,
    )

    src_address = payment_addr_rec.address
    src_init_balance = cluster_obj.get_address_balance(src_address)

    # register stake address and delegate it to pool
    tx_files = clusterlib.TxFiles(
        certificate_files=[stake_addr_reg_cert_file, stake_addr_deleg_cert_file],
        signing_key_files=[payment_addr_rec.skey_file, stake_addr_rec.skey_file],
    )
    tx_raw_output = cluster_obj.send_tx(src_address=src_address, tx_files=tx_files)
    cluster_obj.wait_for_new_block(new_blocks=2)

    # check that the balance for source address was correctly updated
    assert (
        cluster_obj.get_address_balance(src_address)
        == src_init_balance - tx_raw_output.fee - cluster_obj.get_key_deposit()
    ), f"Incorrect balance for source address `{src_address}`"

    helpers.wait_for_stake_distribution(cluster_obj)

    # check that the stake address was delegated
    stake_addr_info = cluster_obj.get_stake_addr_info(stake_addr_rec.address)
    assert stake_addr_info.delegation, f"Stake address was not delegated yet: {stake_addr_info}"

    assert stake_pool_id == stake_addr_info.delegation, "Stake address delegated to wrong pool"

    return pool_user


class TestDelegateAddr:
    def test_delegate_using_pool_id(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Submit registration certificate and delegate to pool using pool id."""
        cluster = cluster_session
        temp_template = "test_delegate_using_addr"

        _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
            delegate_with_pool_id=True,
        )

    def test_delegate_using_vkey(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """Submit registration certificate and delegate to pool using cold vkey."""
        cluster = cluster_session
        temp_template = "test_delegate_using_cert"

        _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
        )

    def test_deregister(
        self,
        cluster_session: clusterlib.ClusterLib,
        addrs_data_session: dict,
        request: FixtureRequest,
    ):
        """De-register stake address."""
        cluster = cluster_session
        temp_template = "test_deregister_addr"

        # submit registration certificate and delegate to pool using certificate
        pool_user = _delegate_stake_addr(
            cluster_obj=cluster,
            addrs_data=addrs_data_session,
            temp_template=temp_template,
            request=request,
        )

        stake_addr_dereg_cert = cluster.gen_stake_addr_deregistration_cert(
            addr_name=f"addr0_{temp_template}", stake_vkey_file=pool_user.stake.vkey_file
        )

        src_address = pool_user.payment.address
        src_init_balance = cluster.get_address_balance(src_address)

        # de-register stake address
        tx_files = clusterlib.TxFiles(
            certificate_files=[stake_addr_dereg_cert],
            signing_key_files=[pool_user.payment.skey_file, pool_user.stake.skey_file],
        )
        tx_raw_output = cluster.send_tx(src_address=src_address, tx_files=tx_files)
        cluster.wait_for_new_block(new_blocks=2)

        # check that the key deposit was returned
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee + cluster.get_key_deposit()
        ), f"Incorrect balance for source address `{src_address}`"

        helpers.wait_for_stake_distribution(cluster)

        # check that the stake address is no longer delegated
        stake_addr_info = cluster.get_stake_addr_info(pool_user.stake.address)
        assert (
            not stake_addr_info.delegation
        ), f"Stake address is still delegated: {stake_addr_info}"
