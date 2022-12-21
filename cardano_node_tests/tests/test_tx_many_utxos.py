"""Tests for transactions with many UTxOs."""
import functools
import logging
import random
import time
from typing import List
from typing import Optional
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


@pytest.mark.skipif(
    VERSIONS.cluster_era != VERSIONS.transaction_era,
    reason="expensive test, skip when cluster era is different from TX era",
)
@pytest.mark.order(5)
@pytest.mark.long
@pytest.mark.xdist_group(name="many_utxos")
class TestManyUTXOs:
    """Test transaction with many UTxOs and small amounts of Lovelace."""

    @pytest.fixture
    def cluster(self, cluster_manager: cluster_management.ClusterManager) -> clusterlib.ClusterLib:
        return cluster_manager.get(
            mark="many_utxos",
            lock_resources=[cluster_management.Resources.PERF],
            prio=True,
        )

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
                *[f"tiny_tx_addr_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(3)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=800_000_000_000,
        )

        return addrs

    def _from_to_transactions(
        self,
        cluster_obj: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        out_addrs: List[clusterlib.AddressRecord],
        tx_name: str,
        amount: int,
    ):
        """Send `amount` of Lovelace to each address in `out_addrs`."""
        src_address = payment_addr.address
        dst_addresses = [rec.address for rec in out_addrs]

        # create TX data
        txouts = [clusterlib.TxOut(address=addr, amount=amount) for addr in dst_addresses]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addr.skey_file])

        # send TX
        cluster_obj.g_transaction.send_tx(
            src_address=src_address,  # change is returned to `src_address`
            tx_name=tx_name,
            txouts=txouts,
            tx_files=tx_files,
            join_txouts=False,
        )

    @pytest.fixture
    def many_utxos(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
    ) -> Tuple[clusterlib.AddressRecord, clusterlib.AddressRecord]:
        """Generate many UTxOs (100000+) with 1-2 ADA."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            temp_template = common.get_test_id(cluster)

            LOGGER.info("Generating lot of UTxO addresses, it will take a while.")
            start = time.time()
            payment_addr = payment_addrs[0]
            out_addrs1 = [payment_addrs[1] for __ in range(200)]
            out_addrs2 = [payment_addrs[2] for __ in range(200)]
            out_addrs = [*out_addrs1, *out_addrs2]

            for i in range(25):
                for multiple in range(1, 21):
                    less_than_1_ada = int(float(multiple / 20) * 1_000_000)
                    amount = less_than_1_ada + 1_000_000

                    # repeat transaction when "BadInputsUTxO" error happens
                    excp: Optional[clusterlib.CLIError] = None
                    for r in range(2):
                        if r > 0:
                            cluster.wait_for_new_block(2)
                        try:
                            self._from_to_transactions(
                                cluster_obj=cluster,
                                payment_addr=payment_addr,
                                tx_name=f"{temp_template}_{amount}_r{r}_{i}",
                                out_addrs=out_addrs,
                                amount=amount,
                            )
                        except clusterlib.CLIError as err:
                            # The "BadInputsUTxO" error happens when a single UTxO is used in two
                            # transactions. This can happen from time to time, we stress
                            # the network here and waiting for 2 blocks may not be enough to get a
                            # transaction through.
                            if "BadInputsUTxO" not in str(err):
                                raise
                            excp = err
                        else:
                            break
                    else:
                        if excp:
                            raise excp

            # create 200 UTxOs with 10 ADA
            cluster.wait_for_new_block(2)
            self._from_to_transactions(
                cluster_obj=cluster,
                payment_addr=payment_addr,
                tx_name=f"{temp_template}_big",
                out_addrs=out_addrs2,
                amount=10_000_000,
            )
            end = time.time()

            retval = payment_addrs[1], payment_addrs[2]
            fixture_cache.value = retval

        num_of_utxo = len(cluster.g_query.get_utxo(address=payment_addrs[1].address)) + len(
            cluster.g_query.get_utxo(address=payment_addrs[2].address)
        )
        LOGGER.info(f"Generated {num_of_utxo} of UTxO addresses in {end - start} seconds.")

        return retval

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    @pytest.mark.parametrize("amount", (1_500_000, 5_000_000, 10_000_000))
    def test_mini_transactions(
        self,
        cluster: clusterlib.ClusterLib,
        many_utxos: Tuple[clusterlib.AddressRecord, clusterlib.AddressRecord],
        amount: int,
    ):
        """Test transaction with many UTxOs (300+) with small amounts of ADA (1-10).

        * use source address with many UTxOs (100000+)
        * use destination address with many UTxOs (100000+)
        * sent transaction with many UTxOs (300+) with tiny amounts of Lovelace from source address
          to destination address
        * check expected balances for both source and destination addresses
        """
        temp_template = f"{common.get_test_id(cluster)}_{amount}"
        big_funds_idx = -190

        src_address = many_utxos[0].address
        dst_address = many_utxos[1].address

        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[many_utxos[0].skey_file])

        # sort UTxOs by amount
        utxos_sorted = sorted(cluster.g_query.get_utxo(address=src_address), key=lambda x: x.amount)

        # select 350 UTxOs, so we are in a limit of command line arguments length and size of the TX
        txins = random.sample(utxos_sorted[:big_funds_idx], k=350)
        # add several UTxOs with "big funds" so we can pay fees
        txins.extend(utxos_sorted[-30:])

        ttl = cluster.g_transaction.calculate_tx_ttl()
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txins=txins,
            txouts=destinations,
            tx_files=tx_files,
            ttl=ttl,
        )

        # optimize list of txins so the total amount of funds in selected UTxOs is close
        # to the amount of needed funds
        needed_funds = amount + fee + 5_000_000  # add a buffer
        total_funds = functools.reduce(lambda x, y: x + y.amount, txins, 0)
        funds_optimized = total_funds
        txins_optimized = txins[:]
        while funds_optimized > needed_funds:
            popped_txin = txins_optimized.pop()
            funds_optimized -= popped_txin.amount
            if funds_optimized < needed_funds:
                txins_optimized.append(popped_txin)
                break

        # build, sign and submit the transaction
        data_for_build = clusterlib.collect_data_for_build(
            clusterlib_obj=cluster,
            src_address=src_address,
            txins=txins_optimized,
            txouts=destinations,
            fee=fee,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx_bare(
            out_file=f"{temp_template}_tx.body",
            txins=data_for_build.txins,
            txouts=data_for_build.txouts,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )
        tx_signed_file = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=temp_template,
            signing_key_files=tx_files.signing_key_files,
        )
        cluster.g_transaction.submit_tx(tx_file=tx_signed_file, txins=tx_raw_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
