"""Tests for smart contracts."""
import decimal
import logging
from pathlib import Path

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

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create new payment address."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            addr = cluster.gen_payment_addr_and_keys(
                name=f"token_transfer_ci{cluster_manager.cluster_instance_num}",
            )
            fixture_cache.value = addr

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addr,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=500_000_000,
        )

        return addr

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.testnets
    def test_txin_locking(
        self, cluster: clusterlib.ClusterLib, payment_addr: clusterlib.AddressRecord
    ):
        """Test locking a Tx output with a plutus script and spending the locked UTxO.

        Corresponds to Exercise 3 for Alonzo blue.

        * create a Tx ouput with a datum hash at the script address
        * check that the expected amount was locked at the script address
        * spend the locked UTxO
        * check that the expected amount was spent
        """
        amount = 50_000_000

        plutusrequiredspace = decimal.Decimal(70_000_000)
        plutusrequiredtime = decimal.Decimal(70_000_000)
        fee_redeem = int(plutusrequiredspace + plutusrequiredtime) + 10_000_000
        collateral_amount = fee_redeem

        datum_file = self.PLUTUS_DIR / "42.datum"
        redeemer_file = self.PLUTUS_DIR / "42.redeemer"

        temp_template = helpers.get_func_name()

        script_address = cluster.gen_script_addr(
            addr_name=temp_template, script_file=self.ALWAYS_SUCCEEDS_PLUTUS
        )

        script_init_balance = cluster.get_address_balance(script_address)

        # create a Tx ouput with a datum hash at the script address
        tx_files_datum = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
        datum_hash = cluster.get_hash_script_data(script_data_file=datum_file)
        txouts_datum = [
            clusterlib.TxOut(
                address=script_address, amount=amount + fee_redeem, datum_hash=datum_hash
            ),
            # for collateral
            clusterlib.TxOut(address=payment_addr.address, amount=collateral_amount),
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

        src_init_balance = cluster.get_address_balance(payment_addr.address)

        # spend the "locked" UTxO
        txid_body = cluster.get_txid(tx_body_file=tx_raw_output_datum.out_file)
        script_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body,
            utxo_ix=0,
            amount=amount + fee_redeem,
            address=script_address,
        )
        collateral_utxo = clusterlib.UTXOData(
            utxo_hash=txid_body, utxo_ix=1, amount=collateral_amount, address=payment_addr.address
        )
        plutus_txins = [
            clusterlib.PlutusTxIn(
                txin=script_utxo,
                collateral=collateral_utxo,
                script_file=self.ALWAYS_SUCCEEDS_PLUTUS,
                execution_units=(plutusrequiredspace, plutusrequiredtime),
                datum_file=datum_file,
                redeemer_file=redeemer_file,
            )
        ]
        txouts_spend = [
            clusterlib.TxOut(address=payment_addr.address, amount=amount),
        ]
        tx_files_spend = clusterlib.TxFiles(
            signing_key_files=[payment_addr.skey_file],
        )
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
        cluster.submit_tx(
            tx_file=tx_signed_spend, txins=[t.txin for t in tx_raw_output_spend.plutus_txins]
        )

        assert (
            cluster.get_address_balance(payment_addr.address) == src_init_balance + amount
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        assert (
            cluster.get_address_balance(script_address) == script_init_balance
        ), f"Incorrect balance for script address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_datum)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output_spend)
