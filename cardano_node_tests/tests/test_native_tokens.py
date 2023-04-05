"""Tests for native tokens.

* minting
* burning
* locking
* transactions
"""
import itertools
import json
import logging
import re
from pathlib import Path
from typing import List
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

MAX_TOKEN_AMOUNT = common.MAX_UINT64


@pytest.fixture
def issuers_addrs(
    cluster_manager: cluster_management.ClusterManager,
    cluster: clusterlib.ClusterLib,
) -> List[clusterlib.AddressRecord]:
    """Create new issuers addresses."""
    temp_template = common.get_test_id(cluster)
    addrs = clusterlib_utils.create_payment_addr_records(
        *[f"{temp_template}_issuer_addr_{i}" for i in range(5)],
        cluster_obj=cluster,
    )

    # fund source addresses
    clusterlib_utils.fund_from_faucet(
        addrs[0],
        cluster_obj=cluster,
        faucet_data=cluster_manager.cache.addrs_data["user1"],
        amount=9_000_000,
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
    keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
    script_content = {"keyHash": keyhash, "type": "sig"}
    script = Path(f"{temp_template}.script")
    with open(script, "w", encoding="utf-8") as out_json:
        json.dump(script_content, out_json)

    policyid = cluster.g_transaction.get_policyid(script)

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
    multisig_script = cluster.g_transaction.build_multisig_script(
        script_name=temp_template,
        script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
        payment_vkey_files=payment_vkey_files[1:],
    )
    policyid = cluster.g_transaction.get_policyid(multisig_script)

    return multisig_script, policyid


@common.SKIPIF_TOKENS_UNUSABLE
@pytest.mark.testnets
class TestMinting:
    """Tests for minting and burning tokens."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("aname_type", ("asset_name", "empty_asset_name"))
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_minting_and_burning_witnesses(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
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

        token_mint_addr = issuers_addrs[0]

        # create issuers
        if aname_type == "asset_name":
            _issuers_vkey_files = [p.vkey_file for p in issuers_addrs]
            payment_vkey_files = _issuers_vkey_files[1:]
            token_issuers = issuers_addrs
        else:
            # create unique script/policyid for an empty asset name
            _empty_issuers = clusterlib_utils.create_payment_addr_records(
                *[f"token_minting_{temp_template}_{i}" for i in range(4)],
                cluster_obj=cluster,
            )
            payment_vkey_files = [p.vkey_file for p in _empty_issuers]
            token_issuers = [issuers_addrs[0], *_empty_issuers]

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        policyid = cluster.g_transaction.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}" if asset_name else policyid

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

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
            use_build_cmd=use_build_cmd,
        )

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn, coins=[token])
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
    @pytest.mark.smoke
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
        keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.g_transaction.get_policyid(script)
        token = f"{policyid}.{asset_name}" if asset_name else policyid

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

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint, coins=[token])
        assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        token_burn = token_mint._replace(amount=-amount)
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn],
            temp_template=f"{temp_template}_burn",
        )

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint, coins=[token])
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
    @pytest.mark.smoke
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
            keyhash = cluster.g_address.get_payment_vkey_hash(
                payment_vkey_file=i_addrs[i].vkey_file
            )
            script_content = {"keyHash": keyhash, "type": "sig"}
            script = Path(f"{temp_template}_{i}.script")
            with open(script, "w", encoding="utf-8") as out_json:
                json.dump(script_content, out_json)

            asset_name_dec = f"couttscoin{clusterlib.get_rand_str(4)}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            policyid = cluster.g_transaction.get_policyid(script)
            aname_token = f"{policyid}.{asset_name}"

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

        mint_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint)
        for t in tokens_mint:
            utxo_mint = clusterlib.filter_utxos(utxos=mint_utxos, coin=t.token)
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

        burn_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn)
        for t in tokens_burn:
            utxo_burn = clusterlib.filter_utxos(utxos=burn_utxos, coin=t.token)
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
    @pytest.mark.smoke
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
        keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.g_transaction.get_policyid(script)
        asset_names = [
            f"couttscoin{clusterlib.get_rand_str(4)}".encode().hex(),
            f"couttscoin{clusterlib.get_rand_str(4)}".encode().hex(),
        ]
        tokens = [f"{policyid}.{an}" for an in asset_names]

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

        token1_mint_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint1, coins=[tokens[0]])
        assert token1_mint_utxo and token1_mint_utxo[0].amount == amount, "The token was not minted"

        # second token minting and first token burning in single TX
        token_burn1 = tokens_mint[0]._replace(amount=-amount)
        tx_out_mint_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn1, tokens_mint[1]],
            temp_template=f"{temp_template}_mint_burn",
            sign_incrementally=True,
        )

        mint_burn_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint_burn)
        token1_burn_utxo = clusterlib.filter_utxos(
            utxos=mint_burn_utxos, address=token_mint_addr.address, coin=tokens[0]
        )
        assert not token1_burn_utxo, "The token was not burnt"
        token2_mint_utxo = clusterlib.filter_utxos(
            utxos=mint_burn_utxos, address=token_mint_addr.address, coin=tokens[1]
        )
        assert token2_mint_utxo and token2_mint_utxo[0].amount == amount, "The token was not minted"

        # second token burning
        token_burn2 = tokens_mint[1]._replace(amount=-amount)
        tx_out_burn2 = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=[token_burn2],
            temp_template=f"{temp_template}_burn",
            sign_incrementally=True,
        )

        token2_burn_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn2, coins=[tokens[1]])
        assert not token2_burn_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint_burn)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn2)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
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
        burn_amount = amount - 1

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        # create simple script
        keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.g_transaction.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        # build and sign a transaction
        tx_files = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, token_mint_addr.skey_file],
        )
        mint = [
            clusterlib.Mint(
                txouts=[
                    clusterlib.TxOut(address=token_mint_addr.address, amount=amount, coin=token),
                    clusterlib.TxOut(
                        address=token_mint_addr.address, amount=-burn_amount, coin=token
                    ),
                ],
                script_file=script,
            ),
        ]
        txouts = [
            clusterlib.TxOut(address=token_mint_addr.address, amount=2_000_000),
            clusterlib.TxOut(
                address=token_mint_addr.address, amount=amount - burn_amount, coin=token
            ),
        ]

        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            txouts=txouts,
            mint=mint,
            tx_files=tx_files,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            txouts=txouts,
            # token minting and burning in the same TX
            mint=mint,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_mint_burn",
        )

        # submit signed transaction
        cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output, coins=[token])
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
        cluster_manager: cluster_management.ClusterManager,
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
            try:
                # disable logging of "Not enough funds to make the transaction"
                logging.disable(logging.ERROR)
                with pytest.raises(clusterlib.CLIError) as excinfo:
                    clusterlib_utils.mint_or_burn_witness(**minting_args)  # type: ignore
                assert "OutputTooBigUTxO" in str(excinfo.value)
            finally:
                logging.disable(logging.NOTSET)
            return

        if tokens_num >= 10:
            # add more funds to mint address
            clusterlib_utils.fund_from_faucet(
                token_mint_addr,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=40_000_000,
            )

        tx_out_mint = clusterlib_utils.mint_or_burn_witness(**minting_args)  # type: ignore

        mint_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        burn_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn)
        for t in tokens_to_burn:
            token_utxo = clusterlib.filter_utxos(
                utxos=burn_utxos, address=token_mint_addr.address, coin=t.token
            )
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
        cluster_manager: cluster_management.ClusterManager,
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
            try:
                # disable logging of "Not enough funds to make the transaction"
                logging.disable(logging.ERROR)
                with pytest.raises(clusterlib.CLIError) as excinfo:
                    clusterlib_utils.mint_or_burn_sign(**minting_args)  # type: ignore
                assert "OutputTooBigUTxO" in str(excinfo.value)
            finally:
                logging.disable(logging.NOTSET)
            return

        if tokens_num >= 10:
            # add more funds to mint address
            clusterlib_utils.fund_from_faucet(
                token_mint_addr,
                cluster_obj=cluster,
                faucet_data=cluster_manager.cache.addrs_data["user1"],
                amount=40_000_000,
            )

        tx_out_mint = clusterlib_utils.mint_or_burn_sign(**minting_args)  # type: ignore

        mint_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_sign(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
        )

        burn_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn)
        for t in tokens_to_burn:
            token_utxo = clusterlib.filter_utxos(
                utxos=burn_utxos, address=token_mint_addr.address, coin=t.token
            )
            assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_minting_and_partial_burning(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
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

        payment_vkey_files = [p.vkey_file for p in issuers_addrs]
        token_mint_addr = issuers_addrs[0]

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
        )

        policyid = cluster.g_transaction.get_policyid(multisig_script)
        token = f"{policyid}.{asset_name}"

        token_mint = clusterlib_utils.TokenRecord(
            token=token,
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
            use_build_cmd=use_build_cmd,
            sign_incrementally=True,
        )

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint, coins=[token])
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

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn1, coins=[token])
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
    @pytest.mark.smoke
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
        keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.g_transaction.get_policyid(script)
        token = f"{policyid}.{asset_name}"

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

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint, coins=[token])
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

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn, coins=[token])
        assert not token_utxo, "The token was not burnt"

        # check expected fees
        assert helpers.is_in_interval(
            tx_out_mint.fee, expected_fee, frac=0.15
        ) and helpers.is_in_interval(
            tx_out_burn.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_mint)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_burn)


@common.SKIPIF_TOKENS_UNUSABLE
@pytest.mark.testnets
@pytest.mark.smoke
class TestPolicies:
    """Tests for minting and burning tokens using minting policies."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_valid_policy_after(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Test minting and burning of tokens after a given slot, check fees in Lovelace."""
        expected_fee = 228_113

        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )
        policyid = cluster.g_transaction.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            invalid_hereafter=cluster.g_query.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        mint_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.g_query.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        burn_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn)
        for t in tokens_to_burn:
            token_utxo = clusterlib.filter_utxos(
                utxos=burn_utxos, address=token_mint_addr.address, coin=t.token
            )
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
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_valid_policy_before(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Test minting and burning of tokens before a given slot, check fees in Lovelace."""
        expected_fee = 228_113

        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        rand = clusterlib.get_rand_str(4)
        amount = 5

        token_mint_addr = issuers_addrs[0]
        payment_vkey_files = [p.vkey_file for p in issuers_addrs]

        before_slot = cluster.g_query.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )
        policyid = cluster.g_transaction.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

            tokens_to_mint.append(
                clusterlib_utils.TokenRecord(
                    token=token,
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
            invalid_hereafter=cluster.g_query.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        mint_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_mint)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
            assert token_utxo and token_utxo[0].amount == amount, "The token was not minted"

        # token burning
        tokens_to_burn = [t._replace(amount=-amount) for t in tokens_to_mint]
        tx_out_burn = clusterlib_utils.mint_or_burn_witness(
            cluster_obj=cluster,
            new_tokens=tokens_to_burn,
            temp_template=f"{temp_template}_burn",
            invalid_before=100,
            invalid_hereafter=cluster.g_query.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        burn_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_burn)
        for t in tokens_to_burn:
            token_utxo = clusterlib.filter_utxos(
                utxos=burn_utxos, address=token_mint_addr.address, coin=t.token
            )
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

        before_slot = cluster.g_query.get_slot_no() - 1

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )
        policyid = cluster.g_transaction.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

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

        mint_utxos = cluster.g_query.get_utxo(address=token_mint_addr.address)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
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

        before_slot = cluster.g_query.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )
        policyid = cluster.g_transaction.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

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

        mint_utxos = cluster.g_query.get_utxo(address=token_mint_addr.address)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
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

        after_slot = cluster.g_query.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )
        policyid = cluster.g_transaction.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

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

        mint_utxos = cluster.g_query.get_utxo(address=token_mint_addr.address)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
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

        after_slot = cluster.g_query.get_slot_no() - 1

        # create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files[1:],
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )
        policyid = cluster.g_transaction.get_policyid(multisig_script)

        tokens_to_mint = []
        for tnum in range(5):
            asset_name_dec = f"couttscoin{rand}{tnum}"
            asset_name = asset_name_dec.encode("utf-8").hex()
            token = f"{policyid}.{asset_name}"

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

        mint_utxos = cluster.g_query.get_utxo(address=token_mint_addr.address)
        for t in tokens_to_mint:
            token_utxo = clusterlib.filter_utxos(
                utxos=mint_utxos, address=token_mint_addr.address, coin=t.token
            )
            assert not token_utxo, "The token was minted unexpectedly"


@common.SKIPIF_TOKENS_UNUSABLE
@pytest.mark.testnets
@pytest.mark.smoke
class TestTransfer:
    """Tests for transferring tokens."""

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
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("amount", (1, 10, 200, 2_000, 100_000))
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
        xfail_msgs = []

        src_address = new_token.token_mint_addr.address
        dst_address = payment_addrs[2].address

        ma_destinations = [
            clusterlib.TxOut(address=dst_address, amount=amount, coin=new_token.token),
        ]

        # destinations with both native token and Lovelace (it doesn't matter on the amounts) for
        # calculating minimum required Lovelace value for tx output
        calc_destinations = [
            *ma_destinations,
            clusterlib.TxOut(address=dst_address, amount=2_000_000),
        ]

        min_value = cluster.g_transaction.calculate_min_req_utxo(txouts=calc_destinations)
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
            destinations.append(clusterlib.TxOut(address=src_address, amount=2_000_000))

            # TODO: see node issue #4297
            if VERSIONS.transaction_era == VERSIONS.ALONZO:
                err_str = ""
                try:
                    cluster.g_transaction.build_tx(
                        src_address=src_address,
                        tx_name=temp_template,
                        txouts=destinations,
                        fee_buffer=2_000_000,
                        tx_files=tx_files,
                        skip_asset_balancing=common.SKIP_ASSET_BALANCING,
                    )
                except clusterlib.CLIError as err:
                    err_str = str(err)
                    if "Minimum required UTxO:" not in err_str:
                        raise
                    xfail_msgs.append(
                        "`transaction build` min required UTxO calculation is broken, "
                        "see node issue #4297"
                    )

                _min_reported_utxo = re.search("Minimum required UTxO: Lovelace ([0-9]+)", err_str)
                assert _min_reported_utxo
                min_reported_utxo = _min_reported_utxo.group(1)
                amount_lovelace = int(min_reported_utxo)

                destinations = [
                    *ma_destinations,
                    clusterlib.TxOut(address=dst_address, amount=amount_lovelace),
                    clusterlib.TxOut(address=src_address, amount=2_000_000),
                ]

            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                fee_buffer=2_000_000,
                tx_files=tx_files,
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.g_transaction.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
            )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)

        out_src_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=src_address)
        assert (
            clusterlib.calculate_utxos_balance(utxos=out_src_utxos)
            == clusterlib.calculate_utxos_balance(utxos=tx_raw_output.txins)
            - tx_raw_output.fee
            - amount_lovelace
        ), f"Incorrect Lovelace balance for source address `{src_address}`"

        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address, coin=new_token.token)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(utxos=tx_raw_output.txins, coin=new_token.token)
            - amount
        ), f"Incorrect token balance for source address `{src_address}`"

        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address, coin=new_token.token)[
                0
            ].amount
            == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        if xfail_msgs:
            pytest.xfail("\n".join(xfail_msgs))

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
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
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        amount = 1_000
        rand = clusterlib.get_rand_str(5)
        xfail_msgs = []

        new_tokens = clusterlib_utils.new_tokens(
            *[f"couttscoin{rand}{i}".encode().hex() for i in range(5)],
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

        ma_destinations_address1 = []
        ma_destinations_address2 = []
        for t in new_tokens:
            ma_destinations_address1.append(
                clusterlib.TxOut(address=dst_address1, amount=amount, coin=t.token)
            )
            ma_destinations_address2.append(
                clusterlib.TxOut(address=dst_address2, amount=amount, coin=t.token)
            )

        # destinations with both native token and Lovelace (it doesn't matter on the amounts) for
        # calculating minimum required Lovelace value for tx output
        calc_destinations_address1 = [
            *ma_destinations_address1,
            clusterlib.TxOut(address=dst_address1, amount=2_000_000),
        ]
        calc_destinations_address2 = [
            *ma_destinations_address2,
            clusterlib.TxOut(address=dst_address2, amount=2_000_000),
        ]

        min_value_address1 = cluster.g_transaction.calculate_min_req_utxo(
            txouts=calc_destinations_address1
        )
        assert min_value_address1.coin.lower() == clusterlib.DEFAULT_COIN
        assert min_value_address1.value, "No Lovelace required for `min-ada-value`"
        amount_lovelace_address1 = min_value_address1.value

        min_value_address2 = cluster.g_transaction.calculate_min_req_utxo(
            txouts=calc_destinations_address2
        )
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
            destinations.append(clusterlib.TxOut(address=src_address, amount=4_000_000))

            # TODO: see node issue #4297
            if VERSIONS.transaction_era == VERSIONS.ALONZO:
                err_str = ""
                try:
                    cluster.g_transaction.build_tx(
                        src_address=src_address,
                        tx_name=temp_template,
                        txouts=destinations,
                        fee_buffer=2_000_000,
                        tx_files=tx_files,
                    )
                except clusterlib.CLIError as err:
                    err_str = str(err)
                    if "Minimum required UTxO:" not in err_str:
                        raise
                    xfail_msgs.append(
                        "`transaction build` min required UTxO calculation is broken, "
                        "see node issue #4297"
                    )

                _min_reported_utxo = re.search("Minimum required UTxO: Lovelace ([0-9]+)", err_str)
                assert _min_reported_utxo
                min_reported_utxo = _min_reported_utxo.group(1)
                amount_lovelace_address1 = amount_lovelace_address2 = int(min_reported_utxo)

                destinations = [
                    *ma_destinations_address1,
                    clusterlib.TxOut(address=dst_address1, amount=amount_lovelace_address1),
                    *ma_destinations_address2,
                    clusterlib.TxOut(address=dst_address2, amount=amount_lovelace_address2),
                    clusterlib.TxOut(address=src_address, amount=4_000_000),
                ]

            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=src_address,
                tx_name=temp_template,
                txouts=destinations,
                fee_buffer=2_000_000,
                tx_files=tx_files,
                skip_asset_balancing=common.SKIP_ASSET_BALANCING,
            )
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_raw_output.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=temp_template,
            )
            cluster.g_transaction.submit_tx(tx_file=tx_signed, txins=tx_raw_output.txins)
        else:
            tx_raw_output = cluster.g_transaction.send_funds(
                src_address=src_address,
                destinations=destinations,
                tx_name=temp_template,
                tx_files=tx_files,
            )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)

        out_src_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=src_address)
        assert (
            clusterlib.calculate_utxos_balance(utxos=out_src_utxos)
            == clusterlib.calculate_utxos_balance(utxos=tx_raw_output.txins)
            - tx_raw_output.fee
            - amount_lovelace_address1
            - amount_lovelace_address2
        ), f"Incorrect Lovelace balance for source address `{src_address}`"

        for idx, token in enumerate(new_tokens):
            assert (
                clusterlib.filter_utxos(utxos=out_utxos, address=src_address, coin=token.token)[
                    0
                ].amount
                == clusterlib.calculate_utxos_balance(utxos=tx_raw_output.txins, coin=token.token)
                - amount * 2
            ), f"Incorrect token #{idx} balance for source address `{src_address}`"

            assert (
                clusterlib.filter_utxos(utxos=out_utxos, address=dst_address1, coin=token.token)[
                    0
                ].amount
                == amount
            ), f"Incorrect token #{idx} balance for destination address `{dst_address1}`"

            assert (
                clusterlib.filter_utxos(utxos=out_utxos, address=dst_address2, coin=token.token)[
                    0
                ].amount
                == amount
            ), f"Incorrect token #{idx} balance for destination address `{dst_address2}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

        if xfail_msgs:
            pytest.xfail("\n".join(xfail_msgs))

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
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
                cluster.g_transaction.build_tx(
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
                cluster.g_transaction.send_funds(
                    src_address=src_address,
                    destinations=destinations,
                    tx_name=temp_template,
                    tx_files=tx_files,
                )
            except clusterlib.CLIError as err:
                if expected_error not in str(err):
                    raise

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        token_amount=st.integers(min_value=NEW_TOKENS_NUM + 1, max_value=MAX_TOKEN_AMOUNT)
    )
    @hypothesis.example(token_amount=NEW_TOKENS_NUM + 1)
    @hypothesis.example(token_amount=MAX_TOKEN_AMOUNT)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_USE_BUILD_CMD
    def test_transfer_invalid_token_amount(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        new_token: clusterlib_utils.TokenRecord,
        use_build_cmd: bool,
        token_amount: int,
    ):
        """Test sending an invalid amount of tokens to payment address."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}_{common.unique_time_str()}"

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
                    cluster.g_transaction.build_tx(
                        src_address=src_address,
                        tx_name=temp_template,
                        txouts=destinations,
                        fee_buffer=2_000_000,
                        tx_files=tx_files,
                    )
                finally:
                    logging.disable(logging.NOTSET)

            exc_val = str(excinfo.value)
            assert "Non-Ada assets are unbalanced" in exc_val or re.search(
                r"Negative quantity \(-[0-9]*\) in transaction output", exc_val
            ), exc_val
        else:
            with pytest.raises(clusterlib.CLIError) as excinfo:
                try:
                    logging.disable(logging.ERROR)
                    cluster.g_transaction.send_funds(
                        src_address=src_address,
                        destinations=destinations,
                        tx_name=temp_template,
                        tx_files=tx_files,
                        fee=80_000,
                    )
                finally:
                    logging.disable(logging.NOTSET)

            exc_val = str(excinfo.value)
            assert "ValueNotConservedUTxO" in exc_val, exc_val


@common.SKIPIF_TOKENS_UNUSABLE
@pytest.mark.testnets
@pytest.mark.smoke
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
        issuers_skey_files = {p.skey_file for p in issuers_addrs}
        token_mint_addrs = {n.token_mint_addr.address for n in new_tokens}
        token_mint_addr_skey_files = {n.token_mint_addr.skey_file for n in new_tokens}
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

        txouts_mint = list(itertools.chain.from_iterable(r.txouts for r in mint))
        txouts_lovelace = [clusterlib.TxOut(address=a, amount=2_000_000) for a in token_mint_addrs]
        txouts = [*txouts_mint, *txouts_lovelace]

        tx_raw_output = cluster_obj.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            mint=mint,
            tx_files=tx_files,
            fee=100_000,
        )
        out_file_signed = cluster_obj.g_transaction.sign_tx(
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
    @common.hypothesis_settings(max_examples=300)
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
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

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
        assert (
            "the bytestring should be no longer than 32 bytes long" in exc_val
            or "name exceeds 32 bytes" in exc_val
            or "expecting hexadecimal digit" in exc_val
        ), exc_val

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(token_amount=st.integers(min_value=MAX_TOKEN_AMOUNT + 1))
    @hypothesis.example(token_amount=MAX_TOKEN_AMOUNT + 1)
    @common.hypothesis_settings(max_examples=300)
    def test_minting_amount_above_the_allowed(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        simple_script_policyid: Tuple[Path, str],
        token_amount: int,
    ):
        """Test minting a token amount above the maximum allowed."""
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

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

        exc_val = str(excinfo.value)
        assert "the number exceeds the max bound" in exc_val, exc_val


@common.SKIPIF_WRONG_ERA
@pytest.mark.testnets
@pytest.mark.smoke
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
        keyhash = cluster.g_address.get_payment_vkey_hash(payment_vkey_file=issuer_addr.vkey_file)
        script_content = {"keyHash": keyhash, "type": "sig"}
        script = Path(f"{temp_template}.script")
        with open(script, "w", encoding="utf-8") as out_json:
            json.dump(script_content, out_json)

        policyid = cluster.g_transaction.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        # Build transaction body. The `tx_raw_blueprint` will be used as blueprint for assembling
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
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            mint=mint,
            tx_files=tx_files,
        )
        tx_raw_blueprint = cluster.g_transaction.build_raw_tx(
            src_address=token_mint_addr.address,
            tx_name=f"{temp_template}_mint_burn",
            # token minting and burning in the same TX
            mint=mint,
            tx_files=tx_files,
            fee=fee,
        )

        # assemble CLI arguments for `transaction build` using data from `tx_raw_blueprint`

        assert tx_raw_blueprint.txins
        assert tx_raw_blueprint.txouts
        assert tx_raw_blueprint.mint

        # test syntax for multi-asset values and txouts, see
        # https://github.com/input-output-hk/cardano-node/pull/2072
        coin_txouts = [f"{t.amount} {t.coin}" for t in tx_raw_blueprint.txouts]
        txout_parts = [
            "-7000",
            "8500",
            f"-4000 {token}",
            "-1500 lovelace",
            f"4000 {token}",
            *coin_txouts,
        ]
        txout_joined = "+".join(txout_parts)
        txout_str = f"{tx_raw_blueprint.txouts[0].address}+{txout_joined}"

        txins_combined = [f"{x.utxo_hash}#{x.utxo_ix}" for x in tx_raw_blueprint.txins]
        mint_str = "+".join(f"{x.amount} {x.coin}" for x in tx_raw_blueprint.mint[0].txouts)
        out_file = (
            tx_raw_blueprint.out_file.parent
            / f"{tx_raw_blueprint.out_file.stem}_assembled{tx_raw_blueprint.out_file.suffix}"
        )
        build_raw_args = [
            "transaction",
            "build-raw",
            "--fee",
            str(tx_raw_blueprint.fee),
            "--mint-script-file",
            str(tx_raw_blueprint.mint[0].script_file),
            *helpers.prepend_flag("--tx-in", txins_combined),
            "--tx-out",
            txout_str,
            "--mint",
            mint_str,
            "--out-file",
            str(out_file),
        ]

        # build transaction body
        cluster.cli(build_raw_args)

        # create signed transaction
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_mint_burn",
        )

        tx_raw_output = tx_raw_blueprint._replace(out_file=out_file)

        # submit signed transaction
        cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output, coins=[token])
        assert token_utxo and token_utxo[0].amount == 1_000, "The token was not minted"

        # check expected fees
        assert helpers.is_in_interval(
            tx_raw_output.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


@pytest.mark.testnets
@pytest.mark.smoke
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.BABBAGE,
    reason="runs only with Babbage+ TX",
)
class TestReferenceUTxO:
    """Tests for Simple Scripts V1 and V2 on reference UTxOs."""

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("script_version", ("simple_v1", "simple_v2"))
    @pytest.mark.dbsync
    def test_script_reference_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        issuers_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        script_version: str,
    ):
        """Test minting and burning a token using reference script.

        Mint and burn token in the same transaction
        Sign transactions using skeys.

        * create a Simple Script
        * create a reference UTxO with the script
        * specify amount to mint and amount to burn in the same transaction
        * check that the expected amount was minted (to_mint_amount - to_burn_amount)
        * check fees in Lovelace
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        expected_fee = 188821

        temp_template = f"{common.get_test_id(cluster)}_{script_version}_{use_build_cmd}"

        asset_name_dec = f"couttscoin{clusterlib.get_rand_str(4)}"
        asset_name = asset_name_dec.encode("utf-8").hex()
        amount = 5
        burn_amount = amount - 1

        token_mint_addr = issuers_addrs[0]
        issuer_addr = issuers_addrs[1]

        # create simple script
        if script_version == "simple_v1":
            invalid_before = None
            invalid_hereafter = None

            reference_type = clusterlib.ScriptTypes.SIMPLE_V1

            keyhash = cluster.g_address.get_payment_vkey_hash(
                payment_vkey_file=issuer_addr.vkey_file
            )
            script_content = {"keyHash": keyhash, "type": "sig"}
            script = Path(f"{temp_template}.script")
            with open(script, "w", encoding="utf-8") as out_json:
                json.dump(script_content, out_json)
        else:
            invalid_before = 100
            invalid_hereafter = cluster.g_query.get_slot_no() + 1_000

            reference_type = clusterlib.ScriptTypes.SIMPLE_V2

            payment_vkey_files = [p.vkey_file for p in issuers_addrs]
            script = cluster.g_transaction.build_multisig_script(
                script_name=temp_template,
                script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
                payment_vkey_files=payment_vkey_files[1:],
                slot=invalid_before,
                slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
            )

        policyid = cluster.g_transaction.get_policyid(script)
        token = f"{policyid}.{asset_name}"

        # create reference UTxO
        reference_utxo, tx_out_reference = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=issuers_addrs[0],
            dst_addr=issuer_addr,
            script_file=script,
            amount=4_000_000,
        )
        assert reference_utxo.reference_script

        # build and sign a transaction
        tx_files = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file, token_mint_addr.skey_file],
        )
        mint = [
            clusterlib.Mint(
                txouts=[
                    clusterlib.TxOut(address=token_mint_addr.address, amount=amount, coin=token),
                    clusterlib.TxOut(
                        address=token_mint_addr.address, amount=-burn_amount, coin=token
                    ),
                ],
                reference_txin=reference_utxo,
                reference_type=reference_type,
                policyid=policyid,
            ),
        ]
        txouts = [
            clusterlib.TxOut(address=token_mint_addr.address, amount=2_000_000),
            clusterlib.TxOut(
                address=token_mint_addr.address, amount=amount - burn_amount, coin=token
            ),
        ]

        if use_build_cmd:
            tx_raw_output = cluster.g_transaction.build_tx(
                src_address=token_mint_addr.address,
                tx_name=temp_template,
                txouts=txouts,
                fee_buffer=2_000_000,
                mint=mint,
                tx_files=tx_files,
                invalid_hereafter=invalid_hereafter,
                invalid_before=invalid_before,
                witness_override=2,
            )
        else:
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=token_mint_addr.address,
                tx_name=f"{temp_template}_mint_burn",
                txouts=txouts,
                mint=mint,
                tx_files=tx_files,
                invalid_hereafter=invalid_hereafter,
                invalid_before=invalid_before,
                # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
                witness_count_add=2,
            )
            tx_raw_output = cluster.g_transaction.build_raw_tx(
                src_address=token_mint_addr.address,
                tx_name=f"{temp_template}_mint_burn",
                txouts=txouts,
                # token minting and burning in the same TX
                mint=mint,
                tx_files=tx_files,
                fee=fee,
                invalid_hereafter=invalid_hereafter,
                invalid_before=invalid_before,
            )

        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=f"{temp_template}_mint_burn",
        )

        # submit signed transaction
        cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        token_utxo = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output, coins=[token])
        assert (
            token_utxo and token_utxo[0].amount == amount - burn_amount
        ), "The token was not minted / burned"

        # check that reference UTxO was NOT spent
        assert cluster.g_query.get_utxo(utxo=reference_utxo), "Reference input was spent"

        # check expected fees
        assert helpers.is_in_interval(
            tx_raw_output.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_reference)
        # TODO: check reference script in db-sync (the `tx_raw_output`)
