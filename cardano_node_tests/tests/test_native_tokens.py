"""Tests for native tokens.

* minting
* burning
* locking
* transactions
"""
import itertools
import json
import logging
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.tmpdir import TempdirFactory
from packaging import version

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class TokenRecord(NamedTuple):
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


@pytest.fixture
def issuers_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new issuers addresses."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

        addrs = clusterlib_utils.create_payment_addr_records(
            *[f"token_minting_ci{cluster_manager.cluster_instance}_{i}" for i in range(3)],
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=900_000_000,
    )

    return addrs


@pytest.fixture
def simple_script_policyid(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    issuers_addrs: List[clusterlib.AddressRecord],
) -> Tuple[Path, str]:
    """Return script and it's PolicyId."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

    temp_template = "test_native_tokens_simple"
    issuer_addr = issuers_addrs[1]

    # create simple script
    keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
    script_content = {"keyHash": keyhash, "type": "sig"}
    script = Path(f"{temp_template}.script")
    with open(f"{temp_template}.script", "w") as out_json:
        json.dump(script_content, out_json)

    policyid = cluster.get_policyid(script)

    return script, policyid


@pytest.fixture
def multisig_script_policyid(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    issuers_addrs: List[clusterlib.AddressRecord],
) -> Tuple[Path, str]:
    """Return multisig script and it's PolicyId."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

    temp_template = "test_native_tokens_multisig"
    payment_vkey_files = [p.vkey_file for p in issuers_addrs]

    # create multisig script
    multisig_script = cluster.build_multisig_script(
        script_name=temp_template,
        script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
        payment_vkey_files=payment_vkey_files[1:],
    )
    policyid = cluster.get_policyid(multisig_script)

    return multisig_script, policyid


def _mint_or_burn_witness(
    cluster_obj: clusterlib.ClusterLib,
    new_tokens: List[TokenRecord],
    temp_template: str,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
) -> None:
    """Mint or burn tokens, depending on the `amount` value. Sign using witnesses.

    Positive `amount` value means minting, negative means burning.
    """
    _issuers_addrs = [n.issuers_addrs for n in new_tokens]
    issuers_addrs = list(itertools.chain.from_iterable(_issuers_addrs))
    issuers_skey_files = [p.skey_file for p in issuers_addrs]
    src_address = new_tokens[0].token_mint_addr.address

    # create TX body
    ttl = cluster_obj.calculate_tx_ttl()
    fee = cluster_obj.calculate_tx_fee(
        src_address=src_address,
        tx_name=temp_template,
        ttl=ttl,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=len(issuers_skey_files) * 2,
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=src_address,
        tx_name=temp_template,
        fee=fee,
        ttl=ttl,
        invalid_hereafter=invalid_hereafter,
        invalid_before=invalid_before,
        mint=[
            clusterlib.TxOut(address=n.token_mint_addr.address, amount=n.amount, coin=n.token)
            for n in new_tokens
        ],
    )

    # create witness file for each required key
    witness_files = [
        cluster_obj.witness_tx(
            tx_body_file=tx_raw_output.out_file,
            witness_name=f"{temp_template}_skey{idx}",
            signing_key_files=[skey],
        )
        for idx, skey in enumerate(issuers_skey_files)
    ]
    witness_files.extend(
        [
            cluster_obj.witness_tx(
                tx_body_file=tx_raw_output.out_file,
                witness_name=f"{temp_template}_script{idx}",
                script_file=token.script,
            )
            for idx, token in enumerate(new_tokens)
        ]
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
    new_tokens: List[TokenRecord],
    temp_template: str,
) -> None:
    """Mint or burn tokens, depending on the `amount` value. Sign using skeys.

    Positive `amount` value means minting, negative means burning.
    """
    _issuers_addrs = [n.issuers_addrs for n in new_tokens]
    issuers_addrs = list(itertools.chain.from_iterable(_issuers_addrs))
    issuers_skey_files = [p.skey_file for p in issuers_addrs]
    token_mint_addr_skey_files = [t.token_mint_addr.skey_file for t in new_tokens]
    src_address = new_tokens[0].token_mint_addr.address

    tx_files = clusterlib.TxFiles(
        signing_key_files=[*issuers_skey_files, *token_mint_addr_skey_files]
    )

    # build and sign a transaction
    ttl = cluster_obj.calculate_tx_ttl()
    fee = cluster_obj.calculate_tx_fee(
        src_address=src_address,
        tx_name=temp_template,
        tx_files=tx_files,
        ttl=ttl,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=len(issuers_skey_files) * 2,
    )
    tx_raw_output = cluster_obj.build_raw_tx(
        src_address=src_address,
        tx_name=temp_template,
        tx_files=tx_files,
        fee=fee,
        ttl=ttl,
        mint=[
            clusterlib.TxOut(address=n.token_mint_addr.address, amount=n.amount, coin=n.token)
            for n in new_tokens
        ],
    )
    out_file_signed = cluster_obj.sign_tx(
        tx_body_file=tx_raw_output.out_file,
        signing_key_files=tx_files.signing_key_files,
        tx_name=temp_template,
        script_files=[n.script for n in new_tokens],
    )

    # submit signed transaction
    cluster_obj.submit_tx(out_file_signed)
    cluster_obj.wait_for_new_block(new_blocks=2)


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Mary+ TX",
)
class TestMinting:
    """Tests for minting and burning tokens."""

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_burning_witnesses(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning of tokens, sign the transaction using witnesses."""
        temp_template = helpers.get_func_name()
        asset_name = f"couttscoin{clusterlib.get_rand_str(4)}"
        amount = 5

        payment_vkey_files = [p.vkey_file for p in issuers_addrs]
        token_mint_addr = issuers_addrs[0]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(
            token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            script=multisig_script,
        )

        # token minting
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_burning_sign(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning of tokens, sign the transaction using skeys."""
        temp_template = helpers.get_func_name()
        asset_name = f"couttscoin{clusterlib.get_rand_str(4)}"
        amount = 5

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

        token_mint = TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=[issuer_addr],
            token_mint_addr=token_mint_addr,
            script=script,
        )

        # token minting
        _mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        _mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

    @pytest.mark.parametrize("tokens_num", (5, 10, 50, 100, 1000))
    @allure.link(helpers.get_vcs_link())
    def test_multi_minting_and_burning_witnesses(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        multisig_script_policyid: Tuple[Path, str],
        tokens_num: int,
    ):
        """Test minting and burning multiple different tokens, sign the TX using witnesses."""
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(8)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        script, policyid = multisig_script_policyid

        tokens_to_mint = []
        for tnum in range(tokens_num):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=script,
                )
            )

        # token minting
        minting_args = {
            "cluster_obj": cluster,
            "new_tokens": tokens_to_mint,
            "temp_template": f"{temp_template}_mint",
        }

        # TODO: remove once testnets are upgraded to node > 1.25.1
        too_high = (
            1000
            if VERSIONS.node < version.parse("1.25.1")
            or VERSIONS.git_rev == "9a7331cce5e8bc0ea9c6bfa1c28773f4c5a7000f"  # 1.25.1
            else 100
        )
        if tokens_num >= too_high:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                _mint_or_burn_witness(**minting_args)  # type: ignore
            if tokens_num >= 1000:
                assert "(UtxoFailure (MaxTxSizeUTxO" in str(excinfo.value)
            else:
                assert "(UtxoFailure (OutputTooBigUTxO" in str(excinfo.value)
            return

        _mint_or_burn_witness(**minting_args)  # type: ignore

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

    @pytest.mark.parametrize("tokens_num", (5, 10, 50, 100, 1000))
    @allure.link(helpers.get_vcs_link())
    def test_multi_minting_and_burning_sign(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        simple_script_policyid: Tuple[Path, str],
        tokens_num: int,
    ):
        """Test minting and burning multiple different tokens, sign the TX using skeys."""
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(8)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]
        script, policyid = simple_script_policyid

        tokens_to_mint = []
        for tnum in range(tokens_num):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=[issuer_addr],
                    token_mint_addr=token_mint_addr,
                    script=script,
                )
            )

        # token minting
        minting_args = {
            "cluster_obj": cluster,
            "new_tokens": tokens_to_mint,
            "temp_template": f"{temp_template}_mint",
        }

        # TODO: remove once testnets are upgraded to node > 1.25.1
        too_high = (
            1000
            if VERSIONS.node < version.parse("1.25.1")
            or VERSIONS.git_rev == "9a7331cce5e8bc0ea9c6bfa1c28773f4c5a7000f"  # 1.25.1
            else 100
        )
        if tokens_num >= too_high:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                _mint_or_burn_sign(**minting_args)  # type: ignore
            if tokens_num >= 1000:
                assert "(UtxoFailure (MaxTxSizeUTxO" in str(excinfo.value)
            else:
                assert "(UtxoFailure (OutputTooBigUTxO" in str(excinfo.value)
            return

        _mint_or_burn_sign(**minting_args)  # type: ignore

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        _mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_partial_burning(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and partial burning of tokens."""
        temp_template = helpers.get_func_name()
        asset_name = f"couttscoin{clusterlib.get_rand_str(4)}"
        amount = 50

        payment_vkey_files = [p.vkey_file for p in issuers_addrs]
        token_mint_addr = issuers_addrs[0]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(
            token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            script=multisig_script,
        )

        # token minting
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        burn_amount = amount - 10
        token_burn = token_mint._replace(amount=-burn_amount)
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert (
            token_utxo and token_utxo[0].amount == amount - burn_amount
        ), "The token was not burned"

        # burn the rest of tokens
        final_burn = token_mint._replace(amount=-10)
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[final_burn],
            temp_template=f"{temp_template}_burn",
        )


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Mary+ TX",
)
class TestPolicies:
    """Tests for minting and burning tokens using minting policies."""

    @allure.link(helpers.get_vcs_link())
    def test_valid_policy_after(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning tokens after given slot."""
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )
        policyid = cluster.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_mint,
            temp_template=f"{temp_template}_mint",
            invalid_before=100,
            invalid_hereafter=cluster.get_tip()["slotNo"] + 1000,
        )

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.get_tip()["slotNo"] + 1000,
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_valid_policy_before(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning tokens before given slot."""
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        before_slot = cluster.get_tip()["slotNo"] + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )
        policyid = cluster.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_mint,
            temp_template=f"{temp_template}_mint",
            invalid_before=100,
            invalid_hereafter=cluster.get_tip()["slotNo"] + 1000,
        )

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        _mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.get_tip()["slotNo"] + 1000,
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

    @allure.link(helpers.get_vcs_link())
    def test_policy_before_past(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens when the "before" slot is in the past."""
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        before_slot = cluster.get_tip()["slotNo"] - 1

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )
        policyid = cluster.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting - valid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=1,
                invalid_hereafter=before_slot,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # token minting - invalid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=1,
                invalid_hereafter=before_slot + 1,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"

    @allure.link(helpers.get_vcs_link())
    def test_policy_before_future(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens.

        The "before" slot is in the future and the given range is invalid.
        """
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        before_slot = cluster.get_tip()["slotNo"] + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )
        policyid = cluster.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=1,
                invalid_hereafter=before_slot + 1,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"

    @allure.link(helpers.get_vcs_link())
    def test_policy_after_future(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens.

        The "after" slot is in the future and the given range is invalid.
        """
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        after_slot = cluster.get_tip()["slotNo"] + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )
        policyid = cluster.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting - valid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=after_slot,
                invalid_hereafter=after_slot + 100,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # token minting - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=1,
                invalid_hereafter=after_slot,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"

    @allure.link(helpers.get_vcs_link())
    def test_policy_after_past(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens.

        The "after" slot is in the past.
        """
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        after_slot = cluster.get_tip()["slotNo"] - 1

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )
        policyid = cluster.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting - valid slot, invalid range - `invalid_hereafter` is in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            _mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=1,
                invalid_hereafter=after_slot,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"


@pytest.mark.testnets
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
    ) -> TokenRecord:
        """Mint new token, sign using skeys."""
        rand = clusterlib.get_rand_str(4)
        temp_template = f"test_tx_new_token_{rand}"
        asset_name = f"couttscoin{rand}"
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

        token_mint = TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=[issuer_addr],
            token_mint_addr=token_mint_addr,
            script=script,
        )

        # token minting
        _mint_or_burn_sign(
            cluster_obj=cluster_obj,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster_obj.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        return token_mint

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[f"token_transfer_ci{cluster_manager.cluster_instance}_{i}" for i in range(10)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

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
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> TokenRecord:
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            new_token = self._new_token(cluster_obj=cluster, payment_addrs=payment_addrs)
            fixture_cache.value = new_token

        return new_token

    @pytest.mark.parametrize("amount", (1, 10, 200, 2000, 100_000))
    @allure.link(helpers.get_vcs_link())
    def test_transfer_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: TokenRecord,
        amount: int,
    ):
        """Test sending tokens to payment address.

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
        new_token: TokenRecord,
    ):
        """Test sending multiple different tokens to payment address.

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


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Mary+ TX",
)
class TestNegative:
    """Negative tests for minting tokens."""

    def _mint_tx(
        self,
        cluster_obj: clusterlib.ClusterLib,
        new_tokens: List[TokenRecord],
        temp_template: str,
    ) -> Path:
        """Return signed TX for minting new token. Sign using skeys."""
        _issuers_addrs = [n.issuers_addrs for n in new_tokens]
        issuers_addrs = list(itertools.chain.from_iterable(_issuers_addrs))
        issuers_skey_files = [p.skey_file for p in issuers_addrs]
        token_mint_addr_skey_files = [t.token_mint_addr.skey_file for t in new_tokens]
        src_address = new_tokens[0].token_mint_addr.address

        tx_files = clusterlib.TxFiles(
            signing_key_files=[*issuers_skey_files, *token_mint_addr_skey_files]
        )

        # build and sign a transaction
        ttl = cluster_obj.calculate_tx_ttl()
        tx_raw_output = cluster_obj.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee=100_000,
            ttl=ttl,
            mint=[
                clusterlib.TxOut(address=n.token_mint_addr.address, amount=n.amount, coin=n.token)
                for n in new_tokens
            ],
        )
        out_file_signed = cluster_obj.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
            script_files=[n.script for n in new_tokens],
        )

        return out_file_signed

    @hypothesis.given(
        asset_name=st.text(
            alphabet=st.characters(
                blacklist_categories=["C"], blacklist_characters=[" ", "+", "\xa0"]
            ),
            min_size=33,
            max_size=1000,
        )
    )
    @helpers.hypothesis_settings()
    def test_long_name(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        simple_script_policyid: Tuple[Path, str],
        asset_name: str,
    ):
        """Try to create token with asset name that is longer than allowed.

        The name can also contain characters that are not allowed. Expect failure.
        """
        temp_template = "test_long_name"

        script, policyid = simple_script_policyid
        token = f"{policyid}.{asset_name}"
        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]
        amount = 20_000_000

        token_mint = TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=[issuer_addr],
            token_mint_addr=token_mint_addr,
            script=script,
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            self._mint_tx(
                cluster_obj=cluster,
                new_tokens=[token_mint],
                temp_template=f"{temp_template}_mint",
            )
        exc_val = str(excinfo.value)
        assert (
            "name exceeds 32 bytes" in exc_val
            or "expecting letter or digit, white space" in exc_val
            or "expecting alphanumeric asset name" in exc_val
        )
