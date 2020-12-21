"""Tests for native tokens.

* minting
* burning
* locking
* transactions
"""
import json
import logging
from pathlib import Path
from typing import List

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from packaging import version

from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import parallel_run
from cardano_node_tests.utils.devops_cluster import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    return Path(tmp_path_factory.mktemp(helpers.get_id_for_mktemp(__file__))).resolve()


@pytest.fixture
def temp_dir(create_temp_dir: Path):
    """Change to a temporary dir."""
    with helpers.change_cwd(create_temp_dir):
        yield create_temp_dir


# use the "temp_dir" fixture for all tests automatically
pytestmark = pytest.mark.usefixtures("temp_dir")


def _min_or_burn_witness(
    cluster_obj: clusterlib.ClusterLib,
    payment_addrs: List[clusterlib.AddressRecord],
    src_address: str,
    amount: int,
    script: Path,
    asset_name: str,
    temp_template: str,
) -> None:
    """Mint of burn tokens, based on the `amount value`. Sign using witnesses.

    Positive `amount` value means minting, negative means burning.
    """
    policyid = cluster_obj.get_policyid(script)
    coin = f"{policyid}.{asset_name}"

    payment_skey_files = [p.skey_file for p in payment_addrs]

    # create TX body
    ttl = cluster_obj.calculate_tx_ttl()
    fee = cluster_obj.calculate_tx_fee(
        src_address=src_address,
        tx_name=temp_template,
        ttl=ttl,
        witness_count_add=len(payment_skey_files),
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=src_address,
        tx_name=temp_template,
        fee=fee,
        ttl=ttl,
        mint=[clusterlib.TxOut(address=src_address, amount=amount, coin=coin)],
    )

    # create witness file for each required key
    witness_files = [
        cluster_obj.witness_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=f"{temp_template}_skey{idx}",
            signing_key_files=[skey],
        )
        for idx, skey in enumerate(payment_skey_files)
    ]
    witness_files.append(
        cluster_obj.witness_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=f"{temp_template}_script",
            script_file=script,
        )
    )

    # sign TX using witness files
    tx_witnessed_file = cluster_obj.assemble_tx(
        tx_body_file=tx_raw_output.out_file,
        witness_files=witness_files,
        tx_name=temp_template,
    )

    # submit signed TX
    cluster_obj.submit_tx(tx_witnessed_file)
    cluster_obj.wait_for_new_block(new_blocks=2)


def _mint_or_burn_sign(
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    src_addr: clusterlib.AddressRecord,
    amount: int,
    script: Path,
    asset_name: str,
    temp_template: str,
) -> None:
    """Mint of burn tokens, based on the `amount value`. Sign using skeys.

    Positive `amount` value means minting, negative means burning.
    """
    policyid = cluster_obj.get_policyid(script)
    coin = f"{policyid}.{asset_name}"

    tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file, src_addr.skey_file])

    # build and sign a transaction
    ttl = cluster_obj.calculate_tx_ttl()
    fee = cluster_obj.calculate_tx_fee(
        src_address=src_addr.address,
        tx_name=temp_template,
        tx_files=tx_files,
        ttl=ttl,
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=src_addr.address,
        tx_name=temp_template,
        tx_files=tx_files,
        fee=fee,
        ttl=ttl,
        mint=[clusterlib.TxOut(address=src_addr.address, amount=amount, coin=coin)],
    )
    out_file_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=temp_template,
        script_file=script,
    )

    # submit signed transaction
    cluster_obj.submit_tx(out_file_signed)
    cluster_obj.wait_for_new_block(new_blocks=2)


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Mary+ TX",
)
class TestMinting:
    """Tests for auxiliary scripts."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        data_key = id(TestMinting)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"token_scripts_ci{cluster_manager.cluster_instance}_{i}" for i in range(3)],
            cluster_obj=cluster,
        )
        cluster_manager.cache.test_data[data_key] = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=20_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_burning_witnesses(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning of coins, sign using witnesses."""
        temp_template = helpers.get_func_name()
        asset_name = "couttscoin"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        src_address = payment_addrs[0].address

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.get_policyid(multisig_script)
        coin = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(src_address, coins=[coin]), "The coin already exists"

        # coin minting
        _min_or_burn_witness(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            src_address=src_address,
            amount=5,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_mint",
        )

        couttscoin_utxo = cluster.get_utxo(src_address, coins=[coin])
        assert couttscoin_utxo and couttscoin_utxo[0].amount == 5, "The coin was not minted"

        # coin burning
        _min_or_burn_witness(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            src_address=src_address,
            amount=-5,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_burn",
        )

        couttscoin_utxo = cluster.get_utxo(src_address, coins=[coin])
        assert not couttscoin_utxo, "The coin was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_burning_sign(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning of coins, sign using skeys."""
        temp_template = helpers.get_func_name()
        asset_name = f"counttscoin{clusterlib.get_rand_str(4)}"

        src_addr = payment_addrs[0]
        payment_addr = payment_addrs[1]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(payment_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(f"{temp_template}.script", "w") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        coin = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(src_addr.address, coins=[coin]), "The coin already exists"

        # coin minting
        _mint_or_burn_sign(
            cluster_obj=cluster,
            payment_addr=payment_addr,
            src_addr=src_addr,
            amount=5,
            script=script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_mint",
        )

        couttscoin_utxo = cluster.get_utxo(src_addr.address, coins=[coin])
        assert couttscoin_utxo and couttscoin_utxo[0].amount == 5, "The coin was not minted"

        # coin burning
        _mint_or_burn_sign(
            cluster_obj=cluster,
            payment_addr=payment_addr,
            src_addr=src_addr,
            amount=-5,
            script=script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_burn",
        )

        couttscoin_utxo = cluster.get_utxo(src_addr.address, coins=[coin])
        assert not couttscoin_utxo, "The coin was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_partial_burning(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and partial burning of coins."""
        temp_template = helpers.get_func_name()
        asset_name = f"counttscoin{clusterlib.get_rand_str(4)}"
        amount = 50

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        src_address = payment_addrs[0].address

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.get_policyid(multisig_script)
        coin = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(src_address, coins=[coin]), "The coin already exists"

        # coin minting
        _min_or_burn_witness(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            src_address=src_address,
            amount=amount,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_mint",
        )

        couttscoin_utxo = cluster.get_utxo(src_address, coins=[coin])
        assert couttscoin_utxo and couttscoin_utxo[0].amount == amount, "The coin was not minted"

        # coin burning
        burn_amount = amount // 2
        _min_or_burn_witness(
            cluster_obj=cluster,
            payment_addrs=payment_addrs,
            src_address=src_address,
            amount=-burn_amount,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_burn",
        )

        couttscoin_utxo = cluster.get_utxo(src_address, coins=[coin])
        assert (
            couttscoin_utxo and couttscoin_utxo[0].amount == amount - burn_amount
        ), "The coin was not burned"
