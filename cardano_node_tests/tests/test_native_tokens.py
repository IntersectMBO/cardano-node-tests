"""Tests for native tokens.

* minting
* burning
* locking
* transactions
"""
import datetime
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
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
MAX_TOKEN_AMOUNT = 2**64


@pytest.fixture
def issuers_addrs_fresh(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new issuers addresses."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            # recreate addresses if leftover MAs are detected in-between the tests
            cached_addrs: List[clusterlib.AddressRecord] = fixture_cache.value
            utxos = cluster.get_utxo(address=cached_addrs[0].address)
            for u in utxos:
                if u.coin != clusterlib.DEFAULT_COIN:
                    break
            else:
                # no MAs found in UTxOs
                return cached_addrs

        addrs = clusterlib_utils.create_payment_addr_records(
            *[
                f"token_minting_fresh_ci{cluster_manager.cluster_instance_num}_"
                f"{helpers.get_timestamped_rand_str()}_{i}"
                for i in range(5)
            ],
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=9_000_000_000,
    )

    return addrs


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
            *[f"token_minting_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(5)],
            cluster_obj=cluster,
        )
        fixture_cache.value = addrs

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=9000_000_000,
    )

    return addrs


@pytest.fixture
def simple_script_policyid(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
    issuers_addrs: List[clusterlib.AddressRecord],
) -> Tuple[Path, str]:
    """Return script and its PolicyId."""
    with cluster_manager.cache_fixture() as fixture_cache:
        if fixture_cache.value:
            return fixture_cache.value  # type: ignore

    temp_template = f"test_native_tokens_simple_ci{cluster_manager.cluster_instance_num}"
    issuer_addr = issuers_addrs[1]

    # create simple script
    keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
    script_content = {"keyHash": keyhash, "type": "sig"}
    script = Path(f"{temp_template}.script")
    with open(script, "w", encoding="utf-8") as out_json:
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

    temp_template = f"test_native_tokens_multisig_ci{cluster_manager.cluster_instance_num}"
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
    VERSIONS.transaction_era < VERSIONS.MARY,
    reason="runs only with Mary+ TX",
)
class TestMinting:
    """Tests for minting and burning tokens."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("aname_type", ("asset_name", "empty_asset_name"))
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_minting_and_burning_witnesses(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs_fresh: List[clusterlib.AddressRecord],
        aname_type: str,
        use_build_cmd: bool,
    ):
        """Test minting and burning of tokens, sign the transaction using witnesses.

        * mint 2 tokens - one identified by policyid + asset name
          and one identified by just policyid
        * burn the minted tokens
        * check fees in Lovelace
        * check output of the `transaction view` command
        * (optional) check transactions in db-sync
        """
        expected_fee = 201141

        temp_template = f"{common.get_test_id(cluster)}_{aname_type}_{use_build_cmd}"
        asset_name_dec = (
            f"couttscoin{clusterlib.get_rand_str(4)}" if aname_type == "asset_name" else ""
        )
        asset_name = asset_name_dec.encode("utf-8").hex()
        amount = 5

        token_mint_addr = issuers_addrs_fresh[0]

        # create issuers
        if aname_type == "asset_name":
            _issuers_vkey_files = [p.vkey_file for p in issuers_addrs_fresh]
            payment_vkey_files = _issuers_vkey_files[1:]
            token_issuers = issuers_addrs_fresh
        else:
            # create unique script/policyid for an empty asset name
            _empty_issuers = clusterlib_utils.create_payment_addr_records(
                *[f"token_minting_{temp_template}_{i}" for i in range(4)],
                cluster_obj=cluster,
            )
            payment_vkey_files = [p.vkey_file for p in _empty_issuers]
            token_issuers = [issuers_addrs_fresh[0], *_empty_issuers]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        policyid = cluster.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}" if asset_name else policyid

        assert not cluster.get_utxo(
            address=token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
            amount=amount,
            issuers_addrs=token_issuers,
            token_mint_addr=token_mint_addr,
            script=multisig_script,
        )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
            use_build_cmd=use_build_cmd,
        )

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
            use_build_cmd=use_build_cmd,
        )

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_burn)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("aname_type", ("asset_name", "empty_asset_name"))
    @pytest.mark.dbsync
    def test_minting_and_burning_sign(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        aname_type: str,
    ):
        """Test minting and burning of tokens, sign the transaction using skeys.

        * mint 2 tokens - one identified by policyid + asset name
          and one identified by just policyid
        * burn the minted tokens
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        expected_fee = 188_821

        temp_template = f"{common.get_test_id(cluster)}_{aname_type}"
        asset_name_dec = (
            f"couttscoin{clusterlib.get_rand_str(4)}" if aname_type == "asset_name" else ""
        )
        asset_name = asset_name_dec.encode("utf-8").hex()
        amount = 5

        token_mint_addr = issuers_addrs[0]
        if aname_type == "asset_name":
            issuer_addr = issuers_addrs[1]
        else:
            # create unique script/policyid for an empty asset name
            issuer_addr = clusterlib_utils.create_payment_addr_records(
                f"token_minting_{temp_template}",
                cluster_obj=cluster,
            )[0]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        token = f"{policyid}.{asset_name}" if asset_name else policyid

        assert not cluster.get_utxo(
            address=token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
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

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_minting_multiple_scripts(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
    ):
        """Test minting of tokens using several different scripts in single transaction.

        * create tokens issuers
        * create a script for each issuer
        * mint 2 tokens with each script - one identified by policyid + asset name
          and one identified by just policyid. The minting is done in single transaction,
          the transaction is signed using skeys.
        * check that the tokens were minted
        * burn the minted tokens
        * check that the tokens were burnt
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        num_of_scripts = 5
        expected_fee = 263_621

        temp_template = common.get_test_id(cluster)
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
            with open(script, "w", encoding="utf-8") as out_json:
                json.dump(script_content, out_json)

            asset_name_dec = f"couttscoin{clusterlib.get_rand_str(4)}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            policyid = cluster.get_policyid(script)
            aname_token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[aname_token]
            ), "The token already exists"
            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[policyid]
            ), "The policyid already exists"

            # for each script mint both token identified by policyid + asset name and token
            # identified by just policyid
            tokens_mint.extend(
                [
                    clusterlib_utils.TokenRecord(
                        token=aname_token,
                        amount=amount,
                        issuers_addrs=[i_addrs[i]],
                        token_mint_addr=token_mint_addr,
                        script=script,
                    ),
                    clusterlib_utils.TokenRecord(
                        token=policyid,
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
            utxo_mint = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
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
            utxo_burn = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not utxo_burn, f"The {t.token} token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_burn)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_minting_burning_diff_tokens_single_tx(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting one token and burning other token in single transaction.

        Sign transactions using skeys.

        * create a script
        * 1st TX - mint first token
        * 2nd TX - mint second token, burn first token
        * 3rd TX - burn second token
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        expected_fee = 188_821

        temp_template = common.get_test_id(cluster)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        asset_names = [
            f"couttscoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex(),
            f"couttscoin{clusterlib.get_rand_str(4)}".encode("utf-8").hex(),
        ]
        tokens = [f"{policyid}.{an}" for an in asset_names]

        assert not cluster.get_utxo(
            address=token_mint_addr.address, coins=tokens
        ), "The token already exists"

        tokens_mint = [
            clusterlib_utils.TokenRecord(
                token=t,
                amount=amount,
                issuers_addrs=[issuer_addr],
                token_mint_addr=token_mint_addr,
                script=script,
            )
            for t in tokens
        ]

        # first token minting
        tx_out_mint1 = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[tokens_mint[0]],
            temp_template=f"{temp_template}_mint",
            sign_incrementally=True,
        )

        token1_mint_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[tokens[0]])
        assert token1_mint_utxo and token1_mint_utxo[0].amount == amount, "The token was not minted"

        # second token minting and first token burning in single TX
        token_burn1 = tokens_mint[0]._replace(amount=-amount)
        tx_out_mint_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn1, tokens_mint[1]],
            temp_template=f"{temp_template}_mint_burn",
            sign_incrementally=True,
        )

        token1_burn_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[tokens[0]])
        assert not token1_burn_utxo, "The token was not burnt"
        token2_mint_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[tokens[1]])
        assert token2_mint_utxo and token2_mint_utxo[0].amount == amount, "The token was not minted"

        # second token burning
        token_burn2 = tokens_mint[1]._replace(amount=-amount)
        tx_out_burn2 = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn2],
            temp_template=f"{temp_template}_burn",
            sign_incrementally=True,
        )

        token2_burn_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[tokens[1]])
        assert not token2_burn_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint_burn)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_minting_burning_same_token_single_tx(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting one token and burning the same token in single transaction.

        Sign transactions using skeys.

        * create a script
        * specify amount to mint and amount to burn in the same transaction
        * check that the expected amount was minted (to_mint_amount - to_burn_amount)
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        expected_fee = 188821

        temp_template = common.get_test_id(cluster)
        asset_name_dec = f"couttscoin{clusterlib.get_rand_str(4)}"
        asset_name = asset_name_dec.encode("utf-8").hex()
        amount = 5

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(
            address=token_mint_addr.address, coins=[token]
        ), "The token already exists"

        # build and sign a transaction
        tx_files = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, token_mint_addr.skey_file],
        )
        mint = [
            clusterlib.Mint(
                txouts=[
                    clusterlib.TxOut(address=token_mint_addr.address, amount=amount, coin=token),
                    clusterlib.TxOut(
                        address=token_mint_addr.address, amount=-(amount - 1), coin=token
                    ),
                ],
                script_file=script,
            ),
        ]
        fee = cluster.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            mint=mint,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            # token minting and burning in the same TX
            mint=mint,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_mint_burn",
        )

        # submit signed transaction
        cluster.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == 1, "The token was not minted"

        # check expected fees
        assert helpers.is_in_interval(
            tx_raw_output.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "tokens_db",
        (
            (5, 226_133),
            (10, 259_353),
            (50, 444_549),
            (100, 684_349),
            (1_000, 0),
        ),
    )
    @pytest.mark.dbsync
    def test_bundle_minting_and_burning_witnesses(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        multisig_script_policyid: Tuple[Path, str],
        tokens_db: Tuple[int, int],
    ):
        """Test minting and burning multiple different tokens that are in single bundle.

        Sign the TX using witnesses.

        * mint several tokens using a single script
        * burn the minted tokens
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        rand = clusterlib.get_rand_str(8)
        temp_template = f"{common.get_test_id(cluster)}_{rand}"
        amount = 5
        tokens_num, expected_fee = tokens_db

        token_mint_addr = issuers_addrs[0]
        script, policyid = multisig_script_policyid

        tokens_to_mint = []
        for tnum in range(tokens_num):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            if tokens_num >= 1_000:
                assert "(UtxoFailure (MaxTxSizeUTxO" in str(excinfo.value)
            else:
                assert "(UtxoFailure (OutputTooBigUTxO" in str(excinfo.value)
            return

        tx_out_mint = clusterlib_utils.mint_or_burn_witness(**minting_args)  # type: ignore

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_burn)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "tokens_db",
        (
            (5, 215_617),
            (10, 246_857),
            (50, 426_333),
            (100, 666_133),
            (1_000, 0),
        ),
    )
    @pytest.mark.dbsync
    def test_bundle_minting_and_burning_sign(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        simple_script_policyid: Tuple[Path, str],
        tokens_db: Tuple[int, int],
    ):
        """Test minting and burning multiple different tokens that are in single bundle.

        Sign the TX using skeys.

        * mint several tokens using a single script
        * burn the minted tokens
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        rand = clusterlib.get_rand_str(8)
        temp_template = f"{common.get_test_id(cluster)}_{rand}"
        amount = 5
        tokens_num, expected_fee = tokens_db

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]
        script, policyid = simple_script_policyid

        tokens_to_mint = []
        for tnum in range(tokens_num):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            if tokens_num >= 1_000:
                assert "(UtxoFailure (MaxTxSizeUTxO" in str(excinfo.value)
            else:
                assert "(UtxoFailure (OutputTooBigUTxO" in str(excinfo.value)
            return

        tx_out_mint = clusterlib_utils.mint_or_burn_sign(**minting_args)  # type: ignore

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_minting_and_partial_burning(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs_fresh: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Test minting and partial burning of tokens.

        * mint a token
        * burn part of the minted token, check the expected amount
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        expected_fee = 201_141

        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        asset_name_dec = f"couttscoin{clusterlib.get_rand_str(4)}"
        asset_name = asset_name_dec.encode("utf-8").hex()
        amount = 50

        payment_vkey_files = [p.vkey_file for p in issuers_addrs_fresh]
        token_mint_addr = issuers_addrs_fresh[0]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(
            address=token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
            amount=amount,
            issuers_addrs=issuers_addrs_fresh,
            token_mint_addr=token_mint_addr,
            script=multisig_script,
        )

        # token minting
        tx_out_mint = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_mint],
            temp_template=f"{temp_template}_mint",
            use_build_cmd=use_build_cmd,
            sign_incrementally=True,
        )

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        # the `transaction build` command doesn't balance MAs, so use the `build-raw` with
        # clusterlib magic for this partial burning
        burn_amount = amount - 10
        token_burn = token_mint._replace(amount=-burn_amount)
        tx_out_burn1 = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn1",
            sign_incrementally=True,
        )

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert (
            token_utxo and token_utxo[0].amount == amount - burn_amount
        ), "The token was not burned"

        # burn the rest of tokens
        final_burn = token_mint._replace(amount=-10)
        tx_out_burn2 = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[final_burn],
            temp_template=f"{temp_template}_burn2",
            use_build_cmd=use_build_cmd,
            sign_incrementally=True,
        )

        # check expected fee
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_minting_unicode_asset_name(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
    ):
        """Test minting and burning of token with unicode non-ascii chars in its asset name.

        Tests https://github.com/input-output-hk/cardano-node/issues/2337

        * mint a token that has non-ascii characters in its asset name
        * burn the minted token
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        expected_fee = 188_821

        temp_template = common.get_test_id(cluster)
        asset_name_dec = f"ěůřščžďťň{clusterlib.get_rand_str(4)}"
        asset_name = asset_name_dec.encode("utf-8").hex()
        amount = 5

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(
            address=token_mint_addr.address, coins=[token]
        ), "The token already exists"

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
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

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert (
            token_utxo and token_utxo[0].amount == amount
        ), "The token was not minted or expected chars are not present in the asset name"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY,
    reason="runs only with Mary+ TX",
)
class TestPolicies:
    """Tests for minting and burning tokens using minting policies."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_valid_policy_after(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs_fresh: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Test minting and burning of tokens after a given slot, check fees in Lovelace."""
        expected_fee = 228_113

        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs_fresh[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs_fresh]

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
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
                    amount=amount,
                    issuers_addrs=issuers_addrs_fresh,
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
            invalid_hereafter=cluster.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_valid_policy_before(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs_fresh: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Test minting and burning of tokens before a given slot, check fees in Lovelace."""
        expected_fee = 228_113

        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs_fresh[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs_fresh]

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
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
                    amount=amount,
                    issuers_addrs=issuers_addrs_fresh,
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
            invalid_hereafter=cluster.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        for t in tokens_to_mint:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        for t in tokens_to_burn:
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    def test_policy_before_past(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens when the "before" slot is in the past."""
        temp_template = common.get_test_id(cluster)
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
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"

    @allure.link(helpers.get_vcs_link())
    def test_policy_before_future(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens when the policy is not met.

        The "before" slot is in the future and the given range is invalid.
        """
        temp_template = common.get_test_id(cluster)
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
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"

    @allure.link(helpers.get_vcs_link())
    def test_policy_after_future(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens when the policy is not met.

        The "after" slot is in the future and the given range is invalid.
        """
        temp_template = common.get_test_id(cluster)
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
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"

    @allure.link(helpers.get_vcs_link())
    def test_policy_after_past(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test that it's NOT possible to mint tokens when the policy is not met.

        The "after" slot is in the past.
        """
        temp_template = common.get_test_id(cluster)
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
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            assert not cluster.get_utxo(
                address=token_mint_addr.address, coins=[token]
            ), "The token already exists"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[t.token])
            assert not token_utxo, "The token was minted unexpectedly"


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY,
    reason="runs only with Mary+ TX",
)
class TestTransfer:
    """Tests for transfering tokens."""

    NEW_TOKENS_NUM = 20_000_000

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
                *[
                    f"token_transfer_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(10)
                ],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
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
            temp_template = f"test_tx_new_token_ci{cluster_manager.cluster_instance_num}_{rand}"
            asset_name_dec = f"couttscoin{rand}"
            asset_name = asset_name_dec.encode("utf-8").hex()

            new_tokens = clusterlib_utils.new_tokens(
                asset_name,
                cluster_obj=cluster,
                temp_template=temp_template,
                token_mint_addr=payment_addrs[0],
                issuer_addr=payment_addrs[1],
                amount=self.NEW_TOKENS_NUM,
            )
            new_token = new_tokens[0]
            fixture_cache.value = new_token

        return new_token

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("amount", (1, 10, 200, 2_000, 100_000))
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_transfer_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: clusterlib_utils.TokenRecord,
        amount: int,
        use_build_cmd: bool,
    ):
        """Test sending tokens to payment address.

        * send tokens from 1 source address to 1 destination address
        * check expected token balances for both source and destination addresses
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{amount}_{use_build_cmd}"

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        src_init_balance = cluster.get_address_balance(src_address)
        src_init_balance_token = cluster.get_address_balance(src_address, coin=new_token.token)
        dst_init_balance_token = cluster.get_address_balance(dst_address, coin=new_token.token)

        ma_destinations = [
            clusterlib.TxOut(address=dst_address, amount=amount, coin=new_token.token),
        ]

        min_value = cluster.calculate_min_req_utxo(txouts=ma_destinations)
        assert min_value.coin.lower() == clusterlib.DEFAULT_COIN
        assert min_value.value, "No Lovelace required for `min-ada-value`"
        amount_lovelace = min_value.value

        destinations = [
            *ma_destinations,
            clusterlib.TxOut(address=dst_address, amount=amount_lovelace),
        ]

        tx_files = clusterlib.TxFiles(signing_key_files=[new_token.token_mint_addr.skey_file])

        if use_build_cmd:
            # TODO: add ADA txout for change address - see node issue #3057
            destinations.append(clusterlib.TxOut(address=src_address, amount=amount_lovelace))

            tx_raw_output = cluster.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                fee_buffer=2_000_000,
                tx_files=tx_files,
            )
            tx_signed = cluster.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
            )

        assert (
            cluster.get_address_balance(src_address, coin=new_token.token)
            == src_init_balance_token - amount
        ), f"Incorrect token balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - amount_lovelace - tx_raw_output.fee
        ), f"Incorrect Lovelace balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(dst_address, coin=new_token.token)
            == dst_init_balance_token + amount
        ), f"Incorrect token balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.dbsync
    def test_transfer_multiple_tokens(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: clusterlib_utils.TokenRecord,
        use_build_cmd: bool,
    ):
        """Test sending multiple different tokens to payment addresses.

        * send multiple different tokens from 1 source address to 2 destination addresses
        * check expected token balances for both source and destination addresses for each token
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        amount = 1_000
        rand = clusterlib.get_rand_str(5)

        new_tokens = clusterlib_utils.new_tokens(
            *[f"couttscoin{rand}{i}".encode("utf-8").hex() for i in range(5)],
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
        ma_destinations_address1 = []
        ma_destinations_address2 = []
        for t in new_tokens:
            src_init_balance_tokens.append(cluster.get_address_balance(src_address, coin=t.token))
            dst_init_balance_tokens1.append(cluster.get_address_balance(dst_address1, coin=t.token))
            dst_init_balance_tokens2.append(cluster.get_address_balance(dst_address2, coin=t.token))

            ma_destinations_address1.append(
                clusterlib.TxOut(address=dst_address1, amount=amount, coin=t.token)
            )
            ma_destinations_address2.append(
                clusterlib.TxOut(address=dst_address2, amount=amount, coin=t.token)
            )

        min_value_address1 = cluster.calculate_min_req_utxo(txouts=ma_destinations_address1)
        assert min_value_address1.coin.lower() == clusterlib.DEFAULT_COIN
        assert min_value_address1.value, "No Lovelace required for `min-ada-value`"
        amount_lovelace_address1 = min_value_address1.value

        min_value_address2 = cluster.calculate_min_req_utxo(txouts=ma_destinations_address2)
        assert min_value_address2.coin.lower() == clusterlib.DEFAULT_COIN
        assert min_value_address2.value, "No Lovelace required for `min-ada-value`"
        amount_lovelace_address2 = min_value_address2.value

        destinations = [
            *ma_destinations_address1,
            clusterlib.TxOut(address=dst_address1, amount=amount_lovelace_address1),
            *ma_destinations_address2,
            clusterlib.TxOut(address=dst_address2, amount=amount_lovelace_address2),
        ]

        tx_files = clusterlib.TxFiles(
            signing_key_files={t.token_mint_addr.skey_file for t in new_tokens}
        )

        if use_build_cmd:
            # TODO: add ADA txout for change address
            destinations.append(
                clusterlib.TxOut(
                    address=src_address, amount=amount_lovelace_address1 + amount_lovelace_address2
                )
            )

            tx_raw_output = cluster.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                fee_buffer=2_000_000,
                tx_files=tx_files,
            )
            tx_signed = cluster.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
            )

        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance
            - amount_lovelace_address1
            - amount_lovelace_address2
            - tx_raw_output.fee
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

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    @pytest.mark.skipif(
        cluster_nodes.get_cluster_type().type != cluster_nodes.ClusterType.LOCAL,
        reason="runs only on local cluster",
    )
    def test_transfer_no_ada(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: clusterlib_utils.TokenRecord,
        use_build_cmd: bool,
    ):
        """Try to create an UTxO with just native tokens, no ADA. Expect failure."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        amount = 10

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount, coin=new_token.token)]
        tx_files = clusterlib.TxFiles(signing_key_files=[new_token.token_mint_addr.skey_file])

        if use_build_cmd:
            expected_error = "Minimum required UTxO:"
            # TODO: add ADA txout for change address
            destinations.append(clusterlib.TxOut(address=src_address, amount=3500_000))

            with pytest.raises(clusterlib.CLIError) as excinfo:
                cluster.build_tx(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=destinations,
                    fee_buffer=2_000_000,
                    tx_files=tx_files,
                )
            assert expected_error in str(excinfo.value)
        else:
            expected_error = "OutputTooSmallUTxO"

            try:
                cluster.send_funds(
                    src_address=src_address,
                    destinations=destinations,
                    tx_name=temp_template,
                    tx_files=tx_files,
                )
            except clusterlib.CLIError as err:
                if expected_error not in str(err):
                    raise
            else:
                msg = (
                    "https://github.com/input-output-hk/cardano-ledger/pull/2722 "
                    "still not in cardano-node"
                )
                if datetime.date.today() < datetime.date(2022, 4, 30):
                    pytest.xfail(msg)
                else:
                    pytest.fail(msg)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        token_amount=st.integers(min_value=NEW_TOKENS_NUM + 1, max_value=MAX_TOKEN_AMOUNT)
    )
    @common.hypothesis_settings()
    @pytest.mark.parametrize(
        "use_build_cmd",
        (
            False,
            pytest.param(
                True,
                marks=pytest.mark.skipif(not common.BUILD_USABLE, reason=common.BUILD_SKIP_MSG),
            ),
        ),
        ids=("build_raw", "build"),
    )
    def test_transfer_invalid_token_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: clusterlib_utils.TokenRecord,
        use_build_cmd: bool,
        token_amount: int,
    ):
        """Test sending an invalid amount of tokens to payment address."""
        temp_template = f"{common.get_test_id(cluster)}_{token_amount}_{use_build_cmd}"

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        ma_destinations = [
            clusterlib.TxOut(address=dst_address, amount=token_amount, coin=new_token.token),
        ]

        min_amount_lovelace = 4_000_000

        destinations = [
            *ma_destinations,
            clusterlib.TxOut(address=dst_address, amount=min_amount_lovelace),
        ]

        tx_files = clusterlib.TxFiles(signing_key_files=[new_token.token_mint_addr.skey_file])

        if use_build_cmd:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                # add ADA txout for change address - see node issue #3057
                destinations.append(
                    clusterlib.TxOut(address=src_address, amount=min_amount_lovelace)
                )

                try:
                    logging.disable(logging.ERROR)
                    cluster.build_tx(
                        src_address=src_address,
                        tx_name=temp_template,
                        txouts=destinations,
                        fee_buffer=2_000_000,
                        tx_files=tx_files,
                    )
                finally:
                    logging.disable(logging.NOTSET)

            assert "Non-Ada assets are unbalanced" in str(excinfo.value)
        else:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                try:
                    logging.disable(logging.ERROR)
                    cluster.send_funds(
                        src_address=src_address,
                        destinations=destinations,
                        tx_name=temp_template,
                        tx_files=tx_files,
                    )
                finally:
                    logging.disable(logging.NOTSET)

            assert "ValueNotConservedUTxO" in str(excinfo.value)


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY,
    reason="runs only with Mary+ TX",
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
        token_mint_addr_skey_files = [n.token_mint_addr.skey_file for n in new_tokens]
        src_address = new_tokens[0].token_mint_addr.address

        # build and sign a transaction
        tx_files = clusterlib.TxFiles(
            signing_key_files=[*issuers_skey_files, *token_mint_addr_skey_files],
        )
        mint = [
            clusterlib.Mint(
                txouts=[
                    clusterlib.TxOut(
                        address=n.token_mint_addr.address, amount=n.amount, coin=n.token
                    )
                ],
                script_file=n.script,
            )
            for n in new_tokens
        ]
        tx_raw_output = cluster_obj.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            mint=mint,
            tx_files=tx_files,
            fee=100_000,
        )
        out_file_signed = cluster_obj.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        return out_file_signed

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        asset_name=st.text(
            alphabet=st.characters(
                blacklist_categories=["C"], blacklist_characters=[" ", "+", "\xa0"]
            ),
            min_size=33,
            max_size=1_000,
        )
    )
    @common.hypothesis_settings()
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
        temp_template = f"test_long_name_ci{cluster.cluster_id}"

        script, policyid = simple_script_policyid
        asset_name_enc = asset_name.encode("utf-8").hex()
        token = f"{policyid}.{asset_name_enc}"
        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]
        amount = 20_000_000

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
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
        assert "name exceeds 32 bytes" in exc_val or "expecting hexadecimal digit" in exc_val

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(token_amount=st.integers(min_value=MAX_TOKEN_AMOUNT + 1))
    @common.hypothesis_settings()
    def test_minting_amount_above_the_allowed(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        simple_script_policyid: Tuple[Path, str],
        token_amount: int,
    ):
        """Test minting a token amount above the maximum allowed."""
        temp_template = common.get_test_id(cluster)

        asset_name_enc = temp_template.encode("utf-8").hex()

        script, policyid = simple_script_policyid
        token = f"{policyid}.{asset_name_enc}"
        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
            amount=token_amount,
            issuers_addrs=[issuer_addr],
            token_mint_addr=token_mint_addr,
            script=script,
        )

        # token minting
        with pytest.raises(clusterlib.CLIError) as excinfo:
            self._mint_tx(
                cluster_obj=cluster,
                new_tokens=[token_mint],
                temp_template=f"{temp_template}_mint",
            )

        assert "the number exceeds the max bound" in str(excinfo.value)


@pytest.mark.testnets
@pytest.mark.skipif(
    VERSIONS.transaction_era != VERSIONS.DEFAULT_TX_ERA,
    reason="runs only with default TX era",
)
class TestCLITxOutSyntax:
    """Tests of syntax for specifying muti-asset values and txouts."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_multiasset_txouts_syntax(
        self, cluster: clusterlib.ClusterLib, issuers_addrs: List[clusterlib.AddressRecord]
    ):
        """Test syntax for specifying multi-asset values and txouts via CLI.

        Test it by minting one token and burning the same token in single transaction.

        * create a script
        * specify amount to mint and amount to burn in the same transaction
        * assemble CLI arguments for `transaction build` and test syntax for multi-asset values
          and txouts
        * build Tx body using the assembled CLI arguments, sign and submit the Tx
        * check that the expected amount was minted (to_mint_amount - to_burn_amount)
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        expected_fee = 187_105

        temp_template = common.get_test_id(cluster)
        asset_name_dec = f"couttscoin{clusterlib.get_rand_str(4)}"
        asset_name = asset_name_dec.encode("utf-8").hex()
        amount = 5_000

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        # create simple script
        keyhash = cluster.get_payment_vkey_hash(issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        assert not cluster.get_utxo(
            address=token_mint_addr.address, coins=[token]
        ), "The token already exists"

        # Build transaction body. The `tx_raw_output` will be used as blueprint for assembling
        # CLI arguments for `transaction build`.
        tx_files = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, token_mint_addr.skey_file],
        )
        mint = [
            clusterlib.Mint(
                txouts=[
                    clusterlib.TxOut(address=token_mint_addr.address, amount=amount, coin=token),
                    clusterlib.TxOut(
                        address=token_mint_addr.address, amount=-(amount - 1_000), coin=token
                    ),
                ],
                script_file=script,
            ),
        ]
        fee = cluster.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            mint=mint,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.build_raw_tx(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            # token minting and burning in the same TX
            mint=mint,
            tx_files=tx_files,
            fee=fee,
        )

        # assemble CLI arguments for `transaction build` using data from `tx_raw_output`

        assert tx_raw_output.txins
        assert tx_raw_output.txouts
        assert tx_raw_output.mint

        # test syntax for multi-asset values and txouts, see
        # https://github.com/input-output-hk/cardano-node/pull/2072
        coin_txouts = [f"{t.amount} {t.coin}" for t in tx_raw_output.txouts]
        txout_parts = [
            "-7000",
            "8500",
            f"-4000 {token}",
            "-1500 lovelace",
            f"4000 {token}",
            *coin_txouts,
        ]
        txout_joined = "+".join(txout_parts)
        txout_str = f"{tx_raw_output.txouts[0].address}+{txout_joined}"

        txins_combined = [f"{x.utxo_hash}#{x.utxo_ix}" for x in tx_raw_output.txins]
        mint_str = "+".join(f"{x.amount} {x.coin}" for x in tx_raw_output.mint[0].txouts)
        out_file = (
            tx_raw_output.out_file.parent
            / f"{tx_raw_output.out_file.stem}_assembled{tx_raw_output.out_file.suffix}"
        )
        build_raw_args = [
            "transaction",
            "build-raw",
            "--fee",
            str(tx_raw_output.fee),
            "--mint-script-file",
            str(tx_raw_output.mint[0].script_file),
            *cluster._prepend_flag("--tx-in", txins_combined),
            "--tx-out",
            txout_str,
            "--mint",
            mint_str,
            "--cddl-format" if cluster.use_cddl else "--cli-format",
            "--out-file",
            str(out_file),
        ]

        # build transaction body
        cluster.cli(build_raw_args)

        # create signed transaction
        out_file_signed = cluster.sign_tx(
            tx_body_file=out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_mint_burn",
        )

        # submit signed transaction
        cluster.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        token_utxo = cluster.get_utxo(address=token_mint_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == 1_000, "The token was not minted"

        # check expected fees
        assert helpers.is_in_interval(
            tx_raw_output.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
