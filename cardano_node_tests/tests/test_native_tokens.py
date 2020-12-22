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
from typing import NamedTuple

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


class NewToken(NamedTuple):
    token: str
    asset_name: str
    amount: int
    issuers_addrs: List[clusterlib.AddressRecord]
    token_mint_addr: clusterlib.AddressRecord
    script: Path


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
    issuers_addrs: List[clusterlib.AddressRecord],
    token_mint_addr: str,
    amount: int,
    script: Path,
    asset_name: str,
    temp_template: str,
) -> None:
    """Mint of burn tokens, based on the `amount value`. Sign using witnesses.

    Positive `amount` value means minting, negative means burning.
    """
    policyid = cluster_obj.get_policyid(script)
    token = f"{policyid}.{asset_name}"

    issuers_skey_files = [p.skey_file for p in issuers_addrs]

    # create TX body
    ttl = cluster_obj.calculate_tx_ttl()
    fee = cluster_obj.calculate_tx_fee(
        src_address=token_mint_addr,
        tx_name=temp_template,
        ttl=ttl,
        witness_count_add=len(issuers_skey_files),
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=token_mint_addr,
        tx_name=temp_template,
        fee=fee,
        ttl=ttl,
        mint=[clusterlib.TxOut(address=token_mint_addr, amount=amount, coin=token)],
    )

    # create witness file for each required key
    witness_files = [
        cluster_obj.witness_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=f"{temp_template}_skey{idx}",
            signing_key_files=[skey],
        )
        for idx, skey in enumerate(issuers_skey_files)
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
    issuer_addr: clusterlib.AddressRecord,
    token_mint_addr: clusterlib.AddressRecord,
    amount: int,
    script: Path,
    asset_name: str,
    temp_template: str,
) -> None:
    """Mint of burn tokens, based on the `amount value`. Sign using skeys.

    Positive `amount` value means minting, negative means burning.
    """
    policyid = cluster_obj.get_policyid(script)
    token = f"{policyid}.{asset_name}"

    tx_files = clusterlib.TxFiles(
        signing_key_files=[issuer_addr.skey_file, token_mint_addr.skey_file]
    )

    # build and sign a transaction
    ttl = cluster_obj.calculate_tx_ttl()
    fee = cluster_obj.calculate_tx_fee(
        src_address=token_mint_addr.address,
        tx_name=temp_template,
        tx_files=tx_files,
        ttl=ttl,
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=token_mint_addr.address,
        tx_name=temp_template,
        tx_files=tx_files,
        fee=fee,
        ttl=ttl,
        mint=[clusterlib.TxOut(address=token_mint_addr.address, amount=amount, coin=token)],
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
    """Tests for minting nad burning tokens."""

    @pytest.fixture
    def issuers_addrs(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new issuers addresses."""
        data_key = id(TestMinting)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"token_minting_ci{cluster_manager.cluster_instance}_{i}" for i in range(3)],
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
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning of tokens, sign using witnesses."""
        temp_template = helpers.get_func_name()
        asset_name = "couttscoin"

        payment_vkey_files = [p.vkey_file for p in issuers_addrs]
        token_mint_addr = issuers_addrs[0].address

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(token_mint_addr, coins=[token]), "The token already exists"

        # token minting
        _min_or_burn_witness(
            cluster_obj=cluster,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            amount=5,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr, coins=[token])
        assert token_utxo and token_utxo[0].amount == 5, "The token was not minted"

        # token burning
        _min_or_burn_witness(
            cluster_obj=cluster,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            amount=-5,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr, coins=[token])
        assert not token_utxo, "The token was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_burning_sign(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning of tokens, sign using skeys."""
        temp_template = helpers.get_func_name()
        asset_name = f"counttscoin{clusterlib.get_rand_str(4)}"

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(f"{temp_template}.script", "w") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(
            token_mint_addr.address, coins=[token]
        ), "The token already exists"

        # token minting
        _mint_or_burn_sign(
            cluster_obj=cluster,
            issuer_addr=issuer_addr,
            token_mint_addr=token_mint_addr,
            amount=5,
            script=script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == 5, "The token was not minted"

        # token burning
        _mint_or_burn_sign(
            cluster_obj=cluster,
            issuer_addr=issuer_addr,
            token_mint_addr=token_mint_addr,
            amount=-5,
            script=script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_partial_burning(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and partial burning of tokens."""
        temp_template = helpers.get_func_name()
        asset_name = f"counttscoin{clusterlib.get_rand_str(4)}"
        amount = 50

        payment_vkey_files = [p.vkey_file for p in issuers_addrs]
        token_mint_addr = issuers_addrs[0].address

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(token_mint_addr, coins=[token]), "The token already exists"

        # token minting
        _min_or_burn_witness(
            cluster_obj=cluster,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            amount=amount,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        burn_amount = amount // 2
        _min_or_burn_witness(
            cluster_obj=cluster,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            amount=-burn_amount,
            script=multisig_script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr, coins=[token])
        assert (
            token_utxo and token_utxo[0].amount == amount - burn_amount
        ), "The token was not burned"


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Mary+ TX",
)
class TestTransfer:
    """Tests for transfering tokens."""

    def _new_token(
        self,
        cluster_obj: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> NewToken:
        """Mint new token, sign using skeys."""
        rand = clusterlib.get_rand_str(4)
        temp_template = f"test_tx_new_token_{rand}"
        asset_name = f"counttscoin{rand}"
        amount = 20_000_000

        token_mint_addr = payment_addrs[0]
        issuer_addr = payment_addrs[1]

        # create simple script
        keyhash = cluster_obj.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(f"{temp_template}.script", "w") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster_obj.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        assert not cluster_obj.get_utxo(
            token_mint_addr.address, coins=[token]
        ), "The token already exists"

        # token minting
        _mint_or_burn_sign(
            cluster_obj=cluster_obj,
            issuer_addr=issuer_addr,
            token_mint_addr=token_mint_addr,
            amount=amount,
            script=script,
            asset_name=asset_name,
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster_obj.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        new_token = NewToken(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=[issuer_addr],
            token_mint_addr=token_mint_addr,
            script=script,
        )

        return new_token

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        data_key = id(TestTransfer)
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"token_transfer_ci{cluster_manager.cluster_instance}_{i}" for i in range(10)],
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

    @pytest.fixture
    def new_token(
        self,
        cluster_manager: parallel_run.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> NewToken:
        data_key = id(TestTransfer) + 1
        cached_value = cluster_manager.cache.test_data.get(data_key)
        if cached_value:
            return cached_value  # type: ignore

        new_token = self._new_token(cluster_obj=cluster, payment_addrs=payment_addrs)
        cluster_manager.cache.test_data[data_key] = new_token

        return new_token

    @pytest.mark.parametrize("amount", (1, 10, 200, 2000, 100_000))
    @allure.link(helpers.get_vcs_link())
    def test_transfer_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: NewToken,
        amount: int,
    ):
        """Send tokens to payment address.

        * send tokens from 1 source address to 1 destination address
        * check expected token balances for both source and destination addresses
        * check fees in Lovelace
        """
        temp_template = f"{helpers.get_func_name()}_{amount}"

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        src_init_balance = cluster.get_address_balance(src_address)
        src_init_balance_token = cluster.get_address_balance(src_address, coin=new_token.token)
        dst_init_balance_token = cluster.get_address_balance(dst_address, coin=new_token.token)

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount, coin=new_token.token)]
        tx_files = clusterlib.TxFiles(signing_key_files=[new_token.token_mint_addr.skey_file])

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address, coin=new_token.token)
            == src_init_balance_token - amount
        ), f"Incorrect token balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(src_address) == src_init_balance - tx_raw_output.fee
        ), f"Incorrect Lovelace balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address, coin=new_token.token)
            == dst_init_balance_token + amount
        ), f"Incorrect token balance for destination address `{dst_address}`"

    @allure.link(helpers.get_vcs_link())
    def test_transfer_multiple_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: NewToken,
    ):
        """Send multiple different tokens to payment address.

        * send multiple different tokens from 1 source address to 1 destination address
        * check expected token balances for both source and destination addresses for each token
        * check fees in Lovelace
        """
        temp_template = helpers.get_func_name()
        amount = 1000

        new_tokens = [
            self._new_token(cluster_obj=cluster, payment_addrs=payment_addrs) for __ in range(5)
        ]
        new_tokens.append(new_token)

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        src_init_balance = cluster.get_address_balance(src_address)
        src_init_balance_tokens = [
            cluster.get_address_balance(src_address, coin=t.token) for t in new_tokens
        ]
        dst_init_balance_tokens = [
            cluster.get_address_balance(dst_address, coin=t.token) for t in new_tokens
        ]

        destinations = [
            clusterlib.TxOut(address=dst_address, amount=amount, coin=t.token) for t in new_tokens
        ]
        tx_files = clusterlib.TxFiles(
            signing_key_files={t.token_mint_addr.skey_file for t in new_tokens}
        )

        tx_raw_output = cluster.send_funds(
            src_address=src_address,
            destinations=destinations,
            tx_name=temp_template,
            tx_files=tx_files,
        )
        cluster.wait_for_new_block(new_blocks=2)

        assert (
            cluster.get_address_balance(src_address) == src_init_balance - tx_raw_output.fee
        ), f"Incorrect Lovelace balance for source address `{src_address}`"

        for idx, token in enumerate(new_tokens):
            assert (
                cluster.get_address_balance(src_address, coin=token.token)
                == src_init_balance_tokens[idx] - amount
            ), f"Incorrect token #{idx} balance for source address `{src_address}`"

            assert (
                cluster.get_address_balance(dst_address, coin=token.token)
                == dst_init_balance_tokens[idx] + amount
            ), f"Incorrect token #{idx} balance for destination address `{dst_address}`"
