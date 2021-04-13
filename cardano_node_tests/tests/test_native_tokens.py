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
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.fixture(scope="module")
def create_temp_dir(tmp_path_factory: TempdirFactory):
    """Create a temporary dir."""
    p = Path(tmp_path_factory.getbasetemp()).joinpath(helpers.get_id_for_mktemp(__file__)).resolve()
    p.mkdir(exist_ok=True, parents=True)
    return p


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
            *[f"token_minting_ci{cluster_manager.cluster_instance}_{i}" for i in range(5)],
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


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY or VERSIONS.node < version.parse("1.24.0"),
    reason="runs on version >= 1.24.0 and with Mary+ TX",
)
class TestMinting:
    """Tests for minting and burning tokens."""

    @pytest.mark.parametrize("aname_type", ("asset_name", "empty_asset_name"))
    @allure.link(helpers.get_vcs_link())
    def test_minting_and_burning_witnesses(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        aname_type: str,
    ):
        """Test minting and burning of tokens, sign the transaction using witnesses."""
        expected_fee = 201141

        temp_template = helpers.get_func_name()
        asset_name = f"couttscoin{clusterlib.get_rand_str(4)}" if aname_type == "asset_name" else ""
        amount = 5

        payment_vkey_files = [p.vkey_file for p in issuers_addrs]
        token_mint_addr = issuers_addrs[0]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            # Create unique script/policyid for an empty asset name. When asset name is empty, the
            # asset ID is just policyid and no other token with the same policyid can be created.
            payment_vkey_files=payment_vkey_files[1:]
            if aname_type == "asset_name"
            else payment_vkey_files[2:],
        )

        policyid = cluster.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}" if asset_name else policyid

        assert not cluster.get_utxo(
            token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            script=multisig_script,
        )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @pytest.mark.parametrize("aname_type", ("asset_name", "empty_asset_name"))
    @allure.link(helpers.get_vcs_link())
    def test_minting_and_burning_sign(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        aname_type: str,
    ):
        """Test minting and burning of tokens, sign the transaction using skeys."""
        expected_fee = 188821

        temp_template = helpers.get_func_name()
        asset_name = f"couttscoin{clusterlib.get_rand_str(4)}" if aname_type == "asset_name" else ""
        amount = 5

        token_mint_addr = issuers_addrs[0]
        # Create unique script/policyid for an empty asset name. When asset name is empty, the asset
        # ID is just policyid and no other token with the same policyid can be created.
        issuer_addr = issuers_addrs[1] if aname_type == "asset_name" else issuers_addrs[2]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(f"{temp_template}.script", "w") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        token = f"{policyid}.{asset_name}" if asset_name else policyid

        assert not cluster.get_utxo(
            token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=[issuer_addr],
            token_mint_addr=token_mint_addr,
            script=script,
        )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @allure.link(helpers.get_vcs_link())
    def test_minting_multiple_scripts(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
    ):
        """Test minting and burning of tokens identified by policyid and policyid + asset name.

        Sign the transaction using skeys.
        """
        num_of_scripts = 5
        expected_fee = 263621

        temp_template = helpers.get_func_name()
        amount = 5
        token_mint_addr = issuers_addrs[0]
        i_addrs = clusterlib_utils.create_payment_addr_records(
            *[f"token_minting_{temp_template}_{i}" for i in range(num_of_scripts)],
            cluster_obj=cluster,
        )

        tokens_mint = []
        for i in range(num_of_scripts):
            # create simple script
            keyhash = cluster.get_payment_vkey_hash(i_addrs[i].vkey_file)
            script_content = {"keyHash": keyhash, "type": "sig"}
            script = Path(f"{temp_template}_{i}.script")
            with open(script, "w") as out_json:
                json.dump(script_content, out_json)

            asset_name = f"couttscoin{clusterlib.get_rand_str(4)}"
            policyid = cluster.get_policyid(script)
            aname_token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[aname_token]
            ), "The token already exists"
            assert not cluster.get_utxo(
                token_mint_addr.address, coins=[policyid]
            ), "The policyid already exists"

            # for each script mint both token identified by policyid + asset name and token
            # identified by just policyid
            tokens_mint.extend(
                [
                    clusterlib_utils.TokenRecord(
                        token=aname_token,
                        asset_name=asset_name,
                        amount=amount,
                        issuers_addrs=[i_addrs[i]],
                        token_mint_addr=token_mint_addr,
                        script=script,
                    ),
                    clusterlib_utils.TokenRecord(
                        token=policyid,
                        asset_name="",
                        amount=amount,
                        issuers_addrs=[i_addrs[i]],
                        token_mint_addr=token_mint_addr,
                        script=script,
                    ),
                ]
            )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=tokens_mint,
            temp_template=f"{temp_template}_mint",
        )

        for t in tokens_mint:
            utxo_mint = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert (
                utxo_mint and utxo_mint[0].amount == amount
            ), f"The {t.token} token was not minted"

        # token burning
        tokens_burn = [t._replace(amount=-amount) for t in tokens_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=tokens_burn,
            temp_template=f"{temp_template}_burn",
        )

        for t in tokens_burn:
            utxo_burn = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not utxo_burn, f"The {t.token} token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @allure.link(helpers.get_vcs_link())
    def test_minting_burning_diff_tokens_single_tx(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting one token and burning other token in single transaction.

        Sign the transaction using skeys.
        """
        expected_fee = 188821

        temp_template = helpers.get_func_name()
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
        asset_names = [
            f"couttscoin{clusterlib.get_rand_str(4)}",
            f"couttscoin{clusterlib.get_rand_str(4)}",
        ]
        tokens = [f"{policyid}.{an}" for an in asset_names]

        assert not cluster.get_utxo(
            token_mint_addr.address, coins=tokens
        ), "The token already exists"

        tokens_mint = [
            clusterlib_utils.TokenRecord(
                token=t,
                asset_name=asset_names[i],
                amount=amount,
                issuers_addrs=[issuer_addr],
                token_mint_addr=token_mint_addr,
                script=script,
            )
            for i, t in enumerate(tokens)
        ]

        # first token minting
        clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[tokens_mint[0]],
            temp_template=f"{temp_template}_mint",
        )

        token1_mint_utxo = cluster.get_utxo(token_mint_addr.address, coins=[tokens[0]])
        assert token1_mint_utxo and token1_mint_utxo[0].amount == amount, "The token was not minted"

        # second token minting and first token burning in single TX
        token_burn1 = tokens_mint[0]._replace(amount=-amount)
        tx_out_mint_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn1, tokens_mint[1]],
            temp_template=f"{temp_template}_mint_burn",
        )

        token1_burn_utxo = cluster.get_utxo(token_mint_addr.address, coins=[tokens[0]])
        assert not token1_burn_utxo, "The token was not burnt"
        token2_mint_utxo = cluster.get_utxo(token_mint_addr.address, coins=[tokens[1]])
        assert token2_mint_utxo and token2_mint_utxo[0].amount == amount, "The token was not minted"

        # second token burning
        token_burn2 = tokens_mint[1]._replace(amount=-amount)
        clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn2],
            temp_template=f"{temp_template}_mint_burn",
        )

        token2_burn_utxo = cluster.get_utxo(token_mint_addr.address, coins=[tokens[1]])
        assert not token2_burn_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @allure.link(helpers.get_vcs_link())
    def test_minting_burning_same_token_single_tx(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting one token and burning the same token in single transaction.

        Sign the transaction using skeys.
        """
        expected_fee = 188821

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

        # token minting and burning in same TX
        tx_files = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, token_mint_addr.skey_file]
        )

        # build and sign a transaction
        fee = cluster.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            tx_files=tx_files,
            fee=fee,
            mint=[
                clusterlib.TxOut(address=token_mint_addr.address, amount=amount, coin=token),
                clusterlib.TxOut(address=token_mint_addr.address, amount=-(amount - 1), coin=token),
            ],
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_mint_burn",
            script_files=[script],
        )

        # submit signed transaction
        cluster.submit_tx(out_file_signed)
        cluster.wait_for_new_block(new_blocks=2)

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == 1, "The token was not minted"

        # check expected fees
        assert helpers.is_in_interval(
            tx_raw_output.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @pytest.mark.parametrize(
        "tokens_db",
        (
            (5, 351093),
            (10, 538533),
            (50, 2038053),
            (100, 3912453),
            (1000, 288789),
        ),
    )
    @allure.link(helpers.get_vcs_link())
    def test_multi_minting_and_burning_witnesses(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        multisig_script_policyid: Tuple[Path, str],
        tokens_db: Tuple[int, int],
    ):
        """Test minting and burning multiple different tokens, sign the TX using witnesses."""
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(8)
        amount = 5
        tokens_num, expected_fee = tokens_db

        token_mint_addr = issuers_addrs[0]
        script, policyid = multisig_script_policyid

        tokens_to_mint = []
        for tnum in range(tokens_num):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
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

        if tokens_num >= 500:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                clusterlib_utils.mint_or_burn_witness(**minting_args)  # type: ignore
            if tokens_num >= 1000:
                assert "(UtxoFailure (MaxTxSizeUTxO" in str(excinfo.value)
            else:
                assert "(UtxoFailure (OutputTooBigUTxO" in str(excinfo.value)
            return

        tx_out_mint = clusterlib_utils.mint_or_burn_witness(**minting_args)  # type: ignore

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @pytest.mark.parametrize(
        "tokens_db",
        (
            (5, 288789),
            (10, 413749),
            (50, 1413429),
            (100, 2663029),
            (1000, 0),
        ),
    )
    @allure.link(helpers.get_vcs_link())
    def test_multi_minting_and_burning_sign(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        simple_script_policyid: Tuple[Path, str],
        tokens_db: Tuple[int, int],
    ):
        """Test minting and burning multiple different tokens, sign the TX using skeys."""
        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(8)
        amount = 5
        tokens_num, expected_fee = tokens_db

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]
        script, policyid = simple_script_policyid

        tokens_to_mint = []
        for tnum in range(tokens_num):
            asset_name = f"couttscoin{rand}{tnum}"
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
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

        if tokens_num >= 500:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                clusterlib_utils.mint_or_burn_sign(**minting_args)  # type: ignore
            if tokens_num >= 1000:
                assert "(UtxoFailure (MaxTxSizeUTxO" in str(excinfo.value)
            else:
                assert "(UtxoFailure (OutputTooBigUTxO" in str(excinfo.value)
            return

        tx_out_mint = clusterlib_utils.mint_or_burn_sign(**minting_args)  # type: ignore

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @allure.link(helpers.get_vcs_link())
    def test_minting_and_partial_burning(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and partial burning of tokens."""
        expected_fee = 201141

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

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
            asset_name=asset_name,
            amount=amount,
            issuers_addrs=issuers_addrs,
            token_mint_addr=token_mint_addr,
            script=multisig_script,
        )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
        )

        token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        burn_amount = amount - 10
        token_burn = token_mint._replace(amount=-burn_amount)
        clusterlib_utils.mint_or_burn_witness(
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
        clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[final_burn],
            temp_template=f"{temp_template}_burn",
        )

        # check expected fee
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"


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
        expected_fee = 351093

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
                clusterlib_utils.TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_mint,
            temp_template=f"{temp_template}_mint",
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1000,
        )

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1000,
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

    @allure.link(helpers.get_vcs_link())
    def test_valid_policy_before(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting and burning tokens before given slot."""
        expected_fee = 351093

        temp_template = helpers.get_func_name()
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        before_slot = cluster.get_slot_no() + 10_000

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
                clusterlib_utils.TokenRecord(
                    token=token,
                    asset_name=asset_name,
                    amount=amount,
                    issuers_addrs=issuers_addrs,
                    token_mint_addr=token_mint_addr,
                    script=multisig_script,
                )
            )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_mint,
            temp_template=f"{temp_template}_mint",
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1000,
        )

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1000,
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

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

        before_slot = cluster.get_slot_no() - 1

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
                clusterlib_utils.TokenRecord(
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
            clusterlib_utils.mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=1,
                invalid_hereafter=before_slot,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # token minting - invalid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            clusterlib_utils.mint_or_burn_witness(
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

        before_slot = cluster.get_slot_no() + 10_000

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
                clusterlib_utils.TokenRecord(
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
            clusterlib_utils.mint_or_burn_witness(
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

        after_slot = cluster.get_slot_no() + 10_000

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
                clusterlib_utils.TokenRecord(
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
            clusterlib_utils.mint_or_burn_witness(
                cluster_obj=cluster,
                new_tokens=tokens_to_mint,
                temp_template=f"{temp_template}_mint",
                invalid_before=after_slot,
                invalid_hereafter=after_slot + 100,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # token minting - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            clusterlib_utils.mint_or_burn_witness(
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

        after_slot = cluster.get_slot_no() - 1

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
                clusterlib_utils.TokenRecord(
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
            clusterlib_utils.mint_or_burn_witness(
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
    ) -> clusterlib_utils.TokenRecord:
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            rand = clusterlib.get_rand_str(4)
            temp_template = f"test_tx_new_token_{rand}"
            asset_name = f"couttscoin{rand}"

            new_tokens = clusterlib_utils.new_tokens(
                asset_name,
                cluster_obj=cluster,
                temp_template=temp_template,
                token_mint_addr=payment_addrs[0],
                issuer_addr=payment_addrs[1],
                amount=20_000_000,
            )
            new_token = new_tokens[0]
            fixture_cache.value = new_token

        return new_token

    @pytest.mark.parametrize("amount", (1, 10, 200, 2000, 100_000))
    @allure.link(helpers.get_vcs_link())
    def test_transfer_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: clusterlib_utils.TokenRecord,
        amount: int,
    ):
        """Test sending tokens to payment address.

        * send tokens from 1 source address to 1 destination address
        * check expected token balances for both source and destination addresses
        * check fees in Lovelace
        """
        temp_template = f"{helpers.get_func_name()}_{amount}"
        amount_lovelace = 10

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        src_init_balance = cluster.get_address_balance(src_address)
        src_init_balance_token = cluster.get_address_balance(src_address, coin=new_token.token)
        dst_init_balance_token = cluster.get_address_balance(dst_address, coin=new_token.token)

        destinations = [
            clusterlib.TxOut(address=dst_address, amount=amount, coin=new_token.token),
            clusterlib.TxOut(address=dst_address, amount=amount_lovelace),
        ]
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
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - amount_lovelace
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
        new_token: clusterlib_utils.TokenRecord,
    ):
        """Test sending multiple different tokens to payment addresses.

        * send multiple different tokens from 1 source address to 2 destination addresses
        * check expected token balances for both source and destination addresses for each token
        * check fees in Lovelace
        """
        temp_template = helpers.get_func_name()
        amount = 1000
        amount_lovelace = 10
        rand = clusterlib.get_rand_str(5)

        new_tokens = clusterlib_utils.new_tokens(
            *[f"couttscoin{rand}{i}" for i in range(5)],
            cluster_obj=cluster,
            temp_template=f"{temp_template}_{rand}",
            token_mint_addr=payment_addrs[0],
            issuer_addr=payment_addrs[1],
            amount=1_000_000,
        )
        new_tokens.append(new_token)

        src_address = new_token.token_mint_addr.address
        dst_address1 = payment_addrs[1].address
        dst_address2 = payment_addrs[2].address

        src_init_balance = cluster.get_address_balance(src_address)

        src_init_balance_tokens = []
        dst_init_balance_tokens1 = []
        dst_init_balance_tokens2 = []
        destinations = []
        for t in new_tokens:
            src_init_balance_tokens.append(cluster.get_address_balance(src_address, coin=t.token))
            dst_init_balance_tokens1.append(cluster.get_address_balance(dst_address1, coin=t.token))
            dst_init_balance_tokens2.append(cluster.get_address_balance(dst_address2, coin=t.token))

            destinations.append(clusterlib.TxOut(address=dst_address1, amount=amount, coin=t.token))
            destinations.append(clusterlib.TxOut(address=dst_address2, amount=amount, coin=t.token))

        destinations.append(clusterlib.TxOut(address=dst_address1, amount=amount_lovelace))
        destinations.append(clusterlib.TxOut(address=dst_address2, amount=amount_lovelace))

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
            cluster.get_address_balance(src_address)
            == src_init_balance - tx_raw_output.fee - amount_lovelace * 2
        ), f"Incorrect Lovelace balance for source address `{src_address}`"

        for idx, token in enumerate(new_tokens):
            assert (
                cluster.get_address_balance(src_address, coin=token.token)
                == src_init_balance_tokens[idx] - amount * 2
            ), f"Incorrect token #{idx} balance for source address `{src_address}`"

            assert (
                cluster.get_address_balance(dst_address1, coin=token.token)
                == dst_init_balance_tokens1[idx] + amount
            ), f"Incorrect token #{idx} balance for destination address `{dst_address1}`"

            assert (
                cluster.get_address_balance(dst_address2, coin=token.token)
                == dst_init_balance_tokens2[idx] + amount
            ), f"Incorrect token #{idx} balance for destination address `{dst_address2}`"

    @allure.link(helpers.get_vcs_link())
    def test_transfer_no_ada(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: clusterlib_utils.TokenRecord,
    ):
        """Try to create an UTxO with just native tokens, no ADA. Expect failure."""
        temp_template = helpers.get_func_name()
        amount = 10

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount, coin=new_token.token)]
        tx_files = clusterlib.TxFiles(signing_key_files=[new_token.token_mint_addr.skey_file])

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
            )
        assert "OutputTooSmallUTxO" in str(excinfo.value)


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
        new_tokens: List[clusterlib_utils.TokenRecord],
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
        tx_raw_output = cluster_obj.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            tx_files=tx_files,
            fee=100_000,
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

        token_mint = clusterlib_utils.TokenRecord(
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
