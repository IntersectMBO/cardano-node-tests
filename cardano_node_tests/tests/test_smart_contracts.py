"""Tests for smart contracts."""
import logging
from pathlib import Path
from typing import List

import allure
import pytest
from _pytest.tmpdir import TempdirFactory
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = Path(__file__).parent / "data"


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


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
class TestPlutus:
    """Tests for Plutus smart contracts."""

    PLUTUS_DIR = DATA_DIR / "plutus"
    ALWAYS_SUCCEEDS_PLUTUS = PLUTUS_DIR / "always-succeeds-spending.plutus"
    GUESSING_GAME_PLUTUS = PLUTUS_DIR / "custom-guess-42-datum-42.plutus"
    MINTING_PLUTUS = PLUTUS_DIR / "anyone-can-mint.plutus"

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.AddressRecord]:
        """Create new payment address."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addrs = clusterlib_utils.create_payment_addr_records(
                *[f"plutus_payment_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(3)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source address
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=10_000_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    @pytest.mark.parametrize("script", ("always_succeeds", "guessing_game_42", "guessing_game_43"))
    def test_txin_locking(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        script: str,
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Corresponds to Exercise 3 for Alonzo blue.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent when success is expected
        * OR check that the amount was not transferred and collateral UTxO was spent
          when failure is expected
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals,too-many-statements
        temp_template = f"{helpers.get_func_name()}_{script}"
        payment_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]
        amount = 50_000_000

        plutusrequiredspace = 700_000_000
        plutusrequiredtime = 700_000_000

        datum_file = self.PLUTUS_DIR / "typed-42.datum"
        redeemer_file = self.PLUTUS_DIR / "typed-42.redeemer"
        script_file = self.GUESSING_GAME_PLUTUS
        expect_failure = False

        if script == "always_succeeds":
            plutusrequiredspace = 70_000_000
            plutusrequiredtime = 70_000_000

            datum_file = self.PLUTUS_DIR / "42.datum"
            redeemer_file = self.PLUTUS_DIR / "42.redeemer"
            script_file = self.ALWAYS_SUCCEEDS_PLUTUS
        elif script.endswith("_43"):
            datum_file = self.PLUTUS_DIR / "typed-43.datum"
            redeemer_file = self.PLUTUS_DIR / "typed-43.redeemer"
            expect_failure = True

        script_address = cluster.gen_script_addr(addr_name=temp_template, script_file=script_file)

        fee_redeem = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_amount = fee_redeem

        script_init_balance = cluster.get_address_balance(script_address)

        # Step 1: create a Tx ouput with a datum hash at the script address

        tx_files_datum = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        datum_hash = cluster.get_hash_script_data(script_data_file=datum_file)
        txouts_datum = [
            clusterlib.TxOut(
                address=script_address, amount=amount + fee_redeem, datum_hash=datum_hash
            ),
            # for collateral
            clusterlib.TxOut(address=dst_addr.address, amount=collateral_amount),
        ]
        fee_datum = cluster.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts_datum,
            tx_name=f"{temp_template}_datum_hash",
            tx_files=tx_files_datum,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output_datum = cluster.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_datum_hash",
            txouts=txouts_datum,
            tx_files=tx_files_datum,
            fee=fee_datum,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_datum = cluster.sign_tx(
            tx_body_file=tx_raw_output_datum.out_file,
            signing_key_files=tx_files_datum.signing_key_files,
            tx_name=f"{temp_template}_datum_hash",
        )
        cluster.submit_tx(tx_file=tx_signed_datum, txins=tx_raw_output_datum.txins)

        script_datum_balance = cluster.get_address_balance(script_address)
        assert (
            script_datum_balance == script_init_balance + amount + fee_redeem
        ), f"Incorrect balance for script address `{script_address}`"

        dst_init_balance = cluster.get_address_balance(dst_addr.address)

        # Step 2: spend the "locked" UTxO

        txid_body = cluster.get_txid(tx_body_file=tx_raw_output_datum.out_file)
        script_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body,
            utxo_ix=0,
            amount=amount + fee_redeem,
            address=script_address,
        )
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body, utxo_ix=1, amount=collateral_amount, address=dst_addr.address
        )
        plutus_txins = [
            clusterlib.PlutusTxIn(
                txin=script_utxo,
                collateral=collateral_utxo,
                script_file=script_file,
                execution_units=(plutusrequiredspace, plutusrequiredtime),
                datum_file=datum_file,
                redeemer_file=redeemer_file,
            )
        ]
        tx_files_spend = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        txouts_spend = [
            clusterlib.TxOut(address=dst_addr.address, amount=amount),
        ]
        tx_raw_output_spend = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_spend_tx.body",
            txouts=txouts_spend,
            tx_files=tx_files_spend,
            fee=fee_redeem,
            plutus_txins=plutus_txins,
        )
        tx_signed_spend = cluster.sign_tx(
            tx_body_file=tx_raw_output_spend.out_file,
            signing_key_files=tx_files_spend.signing_key_files,
            tx_name=f"{temp_template}_spend",
        )

        if expect_failure:
            cluster.submit_tx(tx_file=tx_signed_spend, txins=[collateral_utxo])

            assert (
                cluster.get_address_balance(dst_addr.address)
                == dst_init_balance - collateral_amount
            ), f"Incorrect balance for destination address `{dst_addr.address}`"

            assert (
                cluster.get_address_balance(script_address) == script_datum_balance
            ), f"Incorrect balance for script address `{script_address}`"

            return

        cluster.submit_tx(
            tx_file=tx_signed_spend, txins=[t.txin for t in tx_raw_output_spend.plutus_txins]
        )

        assert (
            cluster.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        assert (
            cluster.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_datum)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_spend)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_minting(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Test minting a token with a plutus script.

        * fund the token issuer and create a UTxO for collateral
        * check that the expected amount was transferred to token issuer's address
        * mint the token using a plutus script
        * check that the token was minted and collateral UTxO was not spent
        * (optional) check transactions in db-sync
        """
        # pylint: disable=too-many-locals
        temp_template = helpers.get_func_name()
        payment_addr = payment_addrs[0]
        issuer_addr = payment_addrs[2]

        lovelace_amount = 5000
        token_amount = 5
        plutusrequiredspace = 700_000_000
        plutusrequiredtime = 700_000_000
        fee_step2 = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_amount = fee_step2

        redeemer_file = self.PLUTUS_DIR / "42.redeemer"

        issuer_init_balance = cluster.get_address_balance(issuer_addr.address)

        # Step 1: fund the token issuer

        tx_files_step1 = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        txouts_step1 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount + fee_step2),
            # for collateral
            clusterlib.TxOut(address=issuer_addr.address, amount=collateral_amount),
        ]
        fee_step1 = cluster.calculate_tx_fee(
            src_address=payment_addr.address,
            txouts=txouts_step1,
            tx_name=f"{temp_template}_step1",
            tx_files=tx_files_step1,
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
            witness_count_add=2,
        )
        tx_raw_output_step1 = cluster.build_raw_tx(
            src_address=payment_addr.address,
            tx_name=f"{temp_template}_step1",
            txouts=txouts_step1,
            tx_files=tx_files_step1,
            fee=fee_step1,
            # don't join 'change' and 'collateral' txouts, we need separate UTxOs
            join_txouts=False,
        )
        tx_signed_step1 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step1.out_file,
            signing_key_files=tx_files_step1.signing_key_files,
            tx_name=f"{temp_template}_step1",
        )
        cluster.submit_tx(tx_file=tx_signed_step1, txins=tx_raw_output_step1.txins)

        issuer_step1_balance = cluster.get_address_balance(issuer_addr.address)
        assert (
            issuer_step1_balance
            == issuer_init_balance + lovelace_amount + fee_step2 + collateral_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        # Step 2: mint the "qacoin"

        txid_body = cluster.get_txid(tx_body_file=tx_raw_output_step1.out_file)
        mint_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body,
            utxo_ix=0,
            amount=lovelace_amount + fee_step2,
            address=issuer_addr.address,
        )
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body, utxo_ix=1, amount=collateral_amount, address=issuer_addr.address
        )
        plutus_mint_data = [
            clusterlib.PlutusMint(
                txin=mint_utxo,
                collateral=collateral_utxo,
                script_file=self.MINTING_PLUTUS,
                execution_units=(plutusrequiredspace, plutusrequiredtime),
                redeemer_file=redeemer_file,
            )
        ]

        policyid = cluster.get_policyid(self.MINTING_PLUTUS)
        token = f"{policyid}.qacoin"
        mint = [clusterlib.TxOut(address=issuer_addr.address, amount=token_amount, coin=token)]

        tx_files_step2 = clusterlib.TxFiles(
            signing_key_files=[issuer_addr.skey_file],
        )
        txouts_step2 = [
            clusterlib.TxOut(address=issuer_addr.address, amount=lovelace_amount),
            *mint,
        ]
        tx_raw_output_step2 = cluster.build_raw_tx_bare(
            out_file=f"{temp_template}_step2_tx.body",
            txouts=txouts_step2,
            tx_files=tx_files_step2,
            fee=fee_step2,
            plutus_mint=plutus_mint_data,
            mint=mint,
        )
        tx_signed_step2 = cluster.sign_tx(
            tx_body_file=tx_raw_output_step2.out_file,
            signing_key_files=tx_files_step2.signing_key_files,
            tx_name=f"{temp_template}_step2",
        )
        cluster.submit_tx(tx_file=tx_signed_step2, txins=[mint_utxo])

        assert (
            cluster.get_address_balance(issuer_addr.address)
            == issuer_init_balance + collateral_amount + lovelace_amount
        ), f"Incorrect balance for token issuer address `{issuer_addr.address}`"

        token_utxo = cluster.get_utxo(issuer_addr.address, coins=[token])
        assert token_utxo and token_utxo[0].amount == token_amount, "The token was not minted"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step1)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_step2)
