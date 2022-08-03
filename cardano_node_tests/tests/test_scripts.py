"""Tests for multisig transactions and scripts.

* multisig
* time locking
* auxiliary scripts
* reference UTxO
"""
import json
import logging
import random
from pathlib import Path
from typing import List
from typing import Optional
from typing import Tuple

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import cluster_management
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

DATA_DIR = Path(__file__).parent / "data"

JSON_METADATA_FILE = DATA_DIR / "tx_metadata.json"
CBOR_METADATA_FILE = DATA_DIR / "tx_metadata.cbor"


def multisig_tx(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    src_address: str,
    dst_address: str,
    amount: int,
    payment_skey_files: List[Path],
    multisig_script: Optional[Path] = None,
    invalid_hereafter: Optional[int] = None,
    invalid_before: Optional[int] = None,
    use_build_cmd: bool = False,
) -> clusterlib.TxRawOutput:
    """Build and submit multisig transaction."""
    # create TX body
    script_txins = (
        # empty `txins` means Tx inputs will be selected automatically by ClusterLib magic
        [clusterlib.ScriptTxIn(txins=[], script_file=multisig_script)]
        if multisig_script
        else []
    )
    destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]
    witness_count = len(payment_skey_files)

    if use_build_cmd:
        tx_raw_output = cluster_obj.build_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            script_txins=script_txins,
            fee_buffer=2_000_000,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_override=witness_count,
        )
    else:
        ttl = cluster_obj.calculate_tx_ttl()
        fee = cluster_obj.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            script_txins=script_txins,
            invalid_hereafter=invalid_hereafter or ttl,
            invalid_before=invalid_before,
            witness_count_add=witness_count,
        )
        tx_raw_output = cluster_obj.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            script_txins=script_txins,
            fee=fee,
            ttl=ttl,
            invalid_hereafter=invalid_hereafter or ttl,
            invalid_before=invalid_before,
        )

    # create witness file for each key
    witness_files = [
        cluster_obj.witness_tx(
            tx_body_file=tx_raw_output.out_file,
            witness_name=f"{temp_template}_skey{idx}",
            signing_key_files=[skey],
        )
        for idx, skey in enumerate(payment_skey_files)
    ]

    # sign TX using witness files
    tx_witnessed_file = cluster_obj.assemble_tx(
        tx_body_file=tx_raw_output.out_file,
        witness_files=witness_files,
        tx_name=temp_template,
    )

    # submit signed TX
    cluster_obj.submit_tx(tx_file=tx_witnessed_file, txins=tx_raw_output.txins)

    # check final balances
    out_utxos = cluster_obj.get_utxo(tx_raw_output=tx_raw_output)
    assert (
        clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
        == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
    ), f"Incorrect balance for source address `{src_address}`"
    assert (
        clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
    ), f"Incorrect balance for script address `{dst_address}`"

    return tx_raw_output


def _create_reference_utxo(
    temp_template: str,
    cluster_obj: clusterlib.ClusterLib,
    payment_addr: clusterlib.AddressRecord,
    dst_addr: clusterlib.AddressRecord,
    script_file: Path,
    amount: int,
) -> Tuple[clusterlib.UTXOData, clusterlib.TxRawOutput]:
    """Create a reference script UTxO with Simple Script."""
    # pylint: disable=too-many-arguments
    tx_files = clusterlib.TxFiles(
        signing_key_files=[payment_addr.skey_file],
    )

    txouts = [
        clusterlib.TxOut(
            address=dst_addr.address,
            amount=amount,
            reference_script_file=script_file,
        )
    ]

    tx_raw_output = cluster_obj.send_tx(
        src_address=payment_addr.address,
        tx_name=f"{temp_template}_step1",
        txouts=txouts,
        tx_files=tx_files,
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
        witness_count_add=2,
    )

    txid = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)

    reference_utxos = cluster_obj.get_utxo(txin=f"{txid}#0")
    assert reference_utxos, "No reference script UTxO"
    reference_utxo = reference_utxos[0]

    dbsync_utils.check_tx(cluster_obj=cluster_obj, tx_raw_output=tx_raw_output)

    return reference_utxo, tx_raw_output


@pytest.mark.testnets
@pytest.mark.smoke
class TestBasic:
    """Basic tests for multisig transactions."""

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
                *[f"multi_addr_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(20)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=10_000_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    def test_script_addr_length(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Check that script address length is the same as length of other addresses.

        There was an issue that script address was 32 bytes instead of 28 bytes.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # check script address length
        assert len(script_address) == len(payment_addrs[0].address)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_multisig_all(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send funds to and from script address using the *all* script."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=5_000_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=2_000_000,
            payment_skey_files=payment_skey_files,
            multisig_script=multisig_script,
            use_build_cmd=use_build_cmd,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_multisig_any(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send funds using the *any* script.

        * send funds to script address
        * send funds from script address using single witness
        * send funds from script address using multiple witnesses
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        tx_raw_outputs = []

        # send funds to script address
        tx_raw_outputs.append(
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_to",
                src_address=payment_addrs[0].address,
                dst_address=script_address,
                amount=50_000_000,
                payment_skey_files=[payment_skey_files[0]],
                use_build_cmd=use_build_cmd,
            )
        )

        # send funds from script address using single witness
        expected_fee = 204_969
        for i in range(5):
            tx_raw_outputs.append(
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=f"{temp_template}_from_single_{i}",
                    src_address=script_address,
                    dst_address=payment_addrs[0].address,
                    amount=2_000_000,
                    payment_skey_files=[payment_skey_files[random.randrange(0, skeys_len)]],
                    multisig_script=multisig_script,
                    use_build_cmd=use_build_cmd,
                )
            )

            # check expected fees
            assert helpers.is_in_interval(
                tx_raw_outputs[-1].fee, expected_fee, frac=0.15
            ), "TX fee doesn't fit the expected interval"

        # send funds from script address using multiple witnesses
        for i in range(5):
            num_of_skeys = random.randrange(2, skeys_len)
            tx_raw_outputs.append(
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=f"{temp_template}_from_multi_{i}",
                    src_address=script_address,
                    dst_address=payment_addrs[0].address,
                    amount=2_000_000,
                    payment_skey_files=random.sample(payment_skey_files, k=num_of_skeys),
                    multisig_script=multisig_script,
                    use_build_cmd=use_build_cmd,
                )
            )

        for tx_out in tx_raw_outputs:
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_multisig_atleast(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send funds to and from script address using the *atLeast* script."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)
        required = skeys_len - 4

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=required,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        tx_raw_outputs = []

        # send funds to script address
        tx_raw_outputs.append(
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_to",
                src_address=payment_addrs[0].address,
                dst_address=script_address,
                amount=20_000_000,
                payment_skey_files=[payment_skey_files[0]],
                use_build_cmd=use_build_cmd,
            )
        )

        # send funds from script address
        for i in range(5):
            num_of_skeys = random.randrange(required, skeys_len)
            tx_raw_outputs.append(
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=f"{temp_template}_from_{i}",
                    src_address=script_address,
                    dst_address=payment_addrs[0].address,
                    amount=2_000_000,
                    payment_skey_files=random.sample(payment_skey_files, k=num_of_skeys),
                    multisig_script=multisig_script,
                    use_build_cmd=use_build_cmd,
                )
            )

        for tx_out in tx_raw_outputs:
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_normal_tx_to_script_addr(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send funds to script address using TX signed with skeys (not using witness files)."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        src_address = payment_addrs[0].address
        amount = 2_000_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=[p.vkey_file for p in payment_addrs],
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # record initial balances
        src_init_balance = cluster.get_address_balance(src_address)
        dst_init_balance = cluster.get_address_balance(script_address)

        # send funds to script address
        destinations = [clusterlib.TxOut(address=script_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        if use_build_cmd:
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
                tx_name=temp_template,
                destinations=destinations,
                tx_files=tx_files,
            )

        # check final balances
        assert (
            cluster.get_address_balance(src_address)
            == src_init_balance - amount - tx_raw_output.fee
        ), f"Incorrect balance for source address `{src_address}`"

        assert (
            cluster.get_address_balance(script_address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{script_address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_normal_tx_from_script_addr(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send funds from script address using TX signed with skeys (not using witness files)."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        dst_addr = payment_addrs[1]
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_500_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # record initial balances
        src_init_balance = cluster.get_address_balance(script_address)
        dst_init_balance = cluster.get_address_balance(dst_addr.address)

        # send funds from script address
        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        # empty `txins` means Tx inputs will be selected automatically by ClusterLib magic
        script_txins = [clusterlib.ScriptTxIn(txins=[], script_file=multisig_script)]

        if use_build_cmd:
            tx_out_from = cluster.build_tx(
                src_address=script_address,
                tx_name=f"{temp_template}_from",
                txouts=destinations,
                script_txins=script_txins,
                fee_buffer=2_000_000,
                tx_files=tx_files,
                witness_override=2,
            )
            tx_signed = cluster.sign_tx(
                tx_body_file=tx_out_from.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_from",
            )
            cluster.submit_tx(tx_file=tx_signed, txins=tx_out_from.txins)
        else:
            tx_out_from = cluster.send_tx(
                src_address=script_address,
                tx_name=f"{temp_template}_from",
                txouts=destinations,
                script_txins=script_txins,
                tx_files=tx_files,
            )

        # check final balances
        assert (
            cluster.get_address_balance(script_address)
            == src_init_balance - amount - tx_out_from.fee
        ), f"Incorrect balance for script address `{script_address}`"

        assert (
            cluster.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_multisig_empty_all(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds from script address using the *all* script with zero skeys."""
        temp_template = common.get_test_id(cluster)

        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=(),
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_000_000,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=1_500_000,
            payment_skey_files=[payment_skey_files[0]],
            multisig_script=multisig_script,
        )

        # check expected fees
        expected_fee = 176_809
        assert helpers.is_in_interval(
            tx_out_from.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_multisig_no_required_atleast(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Send funds from script address using the *atLeast* script with no required witnesses."""
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=0,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=3_000_000,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address
        try:
            tx_out_from = multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_000_000,
                payment_skey_files=[],
                multisig_script=multisig_script,
            )
        except clusterlib.CLIError as err:
            if "Missing: (--witness-file FILE)" in str(err):
                pytest.xfail("See cardano-node issue #3835")
                return
            raise

        # check expected fees
        expected_fee = 176_765
        assert helpers.is_in_interval(
            tx_out_from.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)


@pytest.mark.testnets
@pytest.mark.smoke
class TestNegative:
    """Transaction tests that are expected to fail."""

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
                    f"multi_neg_addr_ci{cluster_manager.cluster_instance_num}_{i}"
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

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_multisig_all_missing_skey(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *all* script, omit one skey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_raw_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=3_000_000,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address, omit one skey
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_000_000,
                payment_skey_files=payment_skey_files[:-1],
                multisig_script=multisig_script,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_multisig_any_unlisted_skey(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *any* script with unlisted skey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs[:-1]]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_raw_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=3_000_000,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address, use skey that is not listed in the script
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=2_000_000,
                payment_skey_files=[payment_skey_files[-1]],
                multisig_script=multisig_script,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.dbsync
    def test_multisig_atleast_low_num_of_skeys(
        self, cluster: clusterlib.ClusterLib, payment_addrs: List[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *atLeast* script.

        Num of skeys < required. Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)
        required = skeys_len - 4

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=required,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_raw_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=3_000_000,
            payment_skey_files=[payment_skey_files[0]],
        )

        # send funds from script address, use lower number of skeys then required
        for num_of_skeys in range(1, required):
            with pytest.raises(clusterlib.CLIError) as excinfo:
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=f"{temp_template}_from_fail{num_of_skeys}",
                    src_address=script_address,
                    dst_address=payment_addrs[0].address,
                    amount=1_000_000,
                    payment_skey_files=random.sample(payment_skey_files, k=num_of_skeys),
                    multisig_script=multisig_script,
                )
            assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


@pytest.mark.testnets
@pytest.mark.smoke
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALLEGRA,
    reason="runs only with Allegra+ TX",
)
class TestTimeLocking:
    """Tests for time locking."""

    # TODO: convert to property-based tests

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
                    f"multi_addr_time_locking_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(20)
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

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_script_after(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Check that it is possible to spend from script address after given slot."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_000_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address
        invalid_hereafter = cluster.get_slot_no() + 1_000
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=2_000_000,
            payment_skey_files=payment_skey_files,
            multisig_script=multisig_script,
            invalid_before=100,
            invalid_hereafter=invalid_hereafter,
            use_build_cmd=use_build_cmd,
        )

        # check expected fees
        expected_fee = 280_693 if use_build_cmd else 323_857
        assert helpers.is_in_interval(
            tx_out_from.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        # check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_from)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_script_before(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Check that it is possible to spend from script address before given slot."""
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_000_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            src_address=script_address,
            dst_address=payment_addrs[0].address,
            amount=2_000_000,
            payment_skey_files=payment_skey_files,
            multisig_script=multisig_script,
            invalid_before=100,
            invalid_hereafter=cluster.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        # check expected fees
        expected_fee = 279_241 if use_build_cmd else 323_989
        assert helpers.is_in_interval(
            tx_out_from.fee, expected_fee, frac=0.15
        ), "TX fee doesn't fit the expected interval"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_before_past(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "before" slot is in the past.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.get_slot_no() - 1

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_raw_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_000_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address - valid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail1",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_500_000,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                invalid_before=1,
                invalid_hereafter=before_slot,
                use_build_cmd=use_build_cmd,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # send funds from script address - invalid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail2",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_500_000,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                invalid_before=1,
                invalid_hereafter=before_slot + 1,
                use_build_cmd=use_build_cmd,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_before_future(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "before" slot is in the future and the given range is invalid.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_raw_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_000_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_500_000,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                invalid_before=1,
                invalid_hereafter=before_slot + 1,
                use_build_cmd=use_build_cmd,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_after_future(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "after" slot is in the future and the given range is invalid.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        after_slot = cluster.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_raw_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_000_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address - valid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail1",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_500_000,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                invalid_before=after_slot,
                invalid_hereafter=after_slot + 100,
                use_build_cmd=use_build_cmd,
            )
        assert "OutsideValidityIntervalUTxO" in str(excinfo.value)

        # send funds from script address - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail2",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_500_000,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                invalid_before=1,
                invalid_hereafter=after_slot,
                use_build_cmd=use_build_cmd,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_after_past(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "after" slot is in the past.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        after_slot = cluster.get_slot_no() - 1

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_raw_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_000_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address - valid slot,
        # invalid range - `invalid_hereafter` is in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                src_address=script_address,
                dst_address=payment_addrs[0].address,
                amount=1_500_000,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                invalid_before=1,
                invalid_hereafter=after_slot,
                use_build_cmd=use_build_cmd,
            )
        assert "ScriptWitnessNotValidatingUTXOW" in str(excinfo.value)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)


@pytest.mark.testnets
@pytest.mark.smoke
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALLEGRA,
    reason="runs only with Allegra+ TX",
)
class TestAuxiliaryScripts:
    """Tests for auxiliary scripts."""

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
                    f"multi_addr_aux_scripts_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(20)
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

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_tx_script_metadata_json(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send transaction with auxiliary script and metadata JSON.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=10,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        tx_files = clusterlib.TxFiles(
            metadata_json_files=[JSON_METADATA_FILE],
            signing_key_files=[payment_addrs[0].skey_file],
            auxiliary_script_files=[multisig_script],
        )

        if use_build_cmd:
            tx_raw_output = cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=temp_template,
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
            tx_raw_output = cluster.send_tx(
                src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
            )

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)

        cbor_body_script = cbor_body_metadata.aux_data[0][1]
        assert len(cbor_body_script) == len(payment_vkey_files) + 1, "Auxiliary script not present"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_tx_script_metadata_cbor(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send transaction with auxiliary script and metadata CBOR.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=2,
            slot=1_000,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        tx_files = clusterlib.TxFiles(
            metadata_cbor_files=[CBOR_METADATA_FILE],
            signing_key_files=[payment_addrs[0].skey_file],
            auxiliary_script_files=[multisig_script],
        )

        if use_build_cmd:
            tx_raw_output = cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=temp_template,
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
            tx_raw_output = cluster.send_tx(
                src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
            )

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)

        cbor_body_script = cbor_body_metadata.aux_data[0][2]
        assert len(cbor_body_script) == len(payment_vkey_files) + 1, "Auxiliary script not present"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert (
                db_metadata == cbor_body_metadata.metadata
            ), "Metadata in db-sync doesn't match the original metadata"

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.dbsync
    def test_tx_script_no_metadata(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Send transaction with auxiliary script and no other metadata.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            auxiliary_script_files=[multisig_script],
        )

        if use_build_cmd:
            tx_raw_output = cluster.build_tx(
                src_address=payment_addrs[0].address,
                tx_name=temp_template,
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
            tx_raw_output = cluster.send_tx(
                src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
            )

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_raw_output.out_file)

        cbor_body_script = cbor_body_metadata.aux_data[0][1]
        assert len(cbor_body_script) == len(payment_vkey_files), "Auxiliary script not present"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_raw_output)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_tx_script_invalid(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Build transaction with invalid auxiliary script.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            # not valid script file
            auxiliary_script_files=[JSON_METADATA_FILE],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster.build_tx(
                    src_address=payment_addrs[0].address,
                    tx_name=temp_template,
                    fee_buffer=2_000_000,
                    tx_files=tx_files,
                )
            else:
                cluster.build_raw_tx(
                    src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
                )
        assert 'Error in $: key "type" not found' in str(excinfo.value)


@pytest.mark.testnets
@pytest.mark.smoke
class TestIncrementalSigning:
    """Tests for incremental signing."""

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
                    f"multi_addr_inc_signing_ci{cluster_manager.cluster_instance_num}_{i}"
                    for i in range(20)
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

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.MARY,
        reason="runs only with Mary+ TX",
    )
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("tx_is", ("witnessed", "signed"))
    @pytest.mark.dbsync
    def test_incremental_signing(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        tx_is: str,
    ):
        """Send funds from script address using TX that is signed incrementally.

        Test with Tx body created by both `transaction build` and `transaction build-raw`.
        Test with Tx created by both `transaction sign` and `transaction assemble`.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}_{tx_is}"
        dst_addr = payment_addrs[1]
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.get_slot_no() + 10_000

        # create multisig script
        multisig_script = cluster.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=4_500_000,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # record initial balances
        src_init_balance = cluster.get_address_balance(script_address)
        dst_init_balance = cluster.get_address_balance(dst_addr.address)

        # send funds from script address
        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            metadata_json_files=[JSON_METADATA_FILE],
            metadata_cbor_files=[CBOR_METADATA_FILE],
            signing_key_files=payment_skey_files,
        )
        # empty `txins` means Tx inputs will be selected automatically by ClusterLib magic
        script_txins = [clusterlib.ScriptTxIn(txins=[], script_file=multisig_script)]

        invalid_hereafter = cluster.get_slot_no() + 1_000
        if use_build_cmd:
            tx_out_from = cluster.build_tx(
                src_address=script_address,
                tx_name=f"{temp_template}_from",
                txouts=destinations,
                script_txins=script_txins,
                fee_buffer=2_000_000,
                tx_files=tx_files,
                invalid_hereafter=invalid_hereafter,
                invalid_before=100,
                witness_override=len(payment_skey_files),
            )
        else:
            ttl = cluster.calculate_tx_ttl()
            fee = cluster.calculate_tx_fee(
                src_address=script_address,
                tx_name=f"{temp_template}_from",
                txouts=destinations,
                script_txins=script_txins,
                tx_files=tx_files,
                ttl=ttl,
                witness_count_add=len(payment_skey_files),
            )
            tx_out_from = cluster.build_raw_tx(
                src_address=script_address,
                tx_name=f"{temp_template}_from",
                txouts=destinations,
                script_txins=script_txins,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
                invalid_hereafter=invalid_hereafter,
                invalid_before=100,
            )

        # sign or witness Tx body with first 2 skey and thus create Tx file that will be used for
        # incremental signing
        if tx_is == "signed":
            tx_signed = cluster.sign_tx(
                tx_body_file=tx_out_from.out_file,
                signing_key_files=payment_skey_files[:2],
                tx_name=f"{temp_template}_from0",
            )
        else:
            # sign Tx body using witness files
            witness_files = [
                cluster.witness_tx(
                    tx_body_file=tx_out_from.out_file,
                    witness_name=f"{temp_template}_from_skey{idx}",
                    signing_key_files=[skey],
                )
                for idx, skey in enumerate(payment_skey_files[:2])
            ]
            tx_signed = cluster.assemble_tx(
                tx_body_file=tx_out_from.out_file,
                witness_files=witness_files,
                tx_name=f"{temp_template}_from0",
            )

        # incrementally sign the already signed Tx with rest of required skeys
        for idx, skey in enumerate(payment_skey_files[2:], start=1):
            # sign multiple times with the same skey to see that it doesn't affect Tx fee
            for r in range(5):
                tx_signed = cluster.sign_tx(
                    tx_file=tx_signed,
                    signing_key_files=[skey],
                    tx_name=f"{temp_template}_from{idx}_r{r}",
                )
        cluster.submit_tx(tx_file=tx_signed, txins=tx_out_from.txins)

        # check final balances
        assert (
            cluster.get_address_balance(script_address)
            == src_init_balance - amount - tx_out_from.fee
        ), f"Incorrect balance for script address `{script_address}`"

        assert (
            cluster.get_address_balance(dst_addr.address) == dst_init_balance + amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)


@pytest.mark.testnets
@pytest.mark.smoke
@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.BABBAGE,
    reason="runs only with Babbage+ TX",
)
class TestReferenceUTxO:
    """Tests for Simple Scripts V1 and V2 on reference UTxOs."""

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
                *[f"multi_addr_ref_ci{cluster_manager.cluster_instance_num}_{i}" for i in range(5)],
                cluster_obj=cluster,
            )
            fixture_cache.value = addrs

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            addrs[0],
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
            amount=100_000_000,
        )

        return addrs

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("script_version", ("simple_v1", "simple_v2"))
    @pytest.mark.dbsync
    def test_script_reference_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: List[clusterlib.AddressRecord],
        use_build_cmd: bool,
        script_version: str,
    ):
        """Send funds from script address where script is on reference UTxO."""
        temp_template = f"{common.get_test_id(cluster)}_{script_version}_{use_build_cmd}"
        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        fund_amount = 4_500_000
        amount = 2_000_000

        # create multisig script
        if script_version == "simple_v1":
            invalid_before = None
            invalid_hereafter = None

            reference_type = clusterlib.ScriptTypes.SIMPLE_V1
            script_type_str = "SimpleScriptV1"

            multisig_script = Path(f"{temp_template}_multisig.script")
            script_content = {
                "keyHash": cluster.get_payment_vkey_hash(dst_addr.vkey_file),
                "type": "sig",
            }
            with open(multisig_script, "w", encoding="utf-8") as fp_out:
                json.dump(script_content, fp_out, indent=4)
        else:
            invalid_before = 100
            invalid_hereafter = cluster.get_slot_no() + 1_000

            reference_type = clusterlib.ScriptTypes.SIMPLE_V2
            script_type_str = "SimpleScriptV2"

            multisig_script = cluster.build_multisig_script(
                script_name=temp_template,
                script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
                payment_vkey_files=[p.vkey_file for p in payment_addrs],
                slot=invalid_before,
                slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
            )

        # create reference UTxO
        reference_utxo, __ = _create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=src_addr,
            dst_addr=dst_addr,
            script_file=multisig_script,
            amount=4_000_000,
        )
        assert reference_utxo.reference_script

        # create script address
        script_address = cluster.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            src_address=src_addr.address,
            dst_address=script_address,
            amount=fund_amount,
            payment_skey_files=[src_addr.skey_file],
            use_build_cmd=use_build_cmd,
        )

        # send funds from script address
        destinations = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[dst_addr.skey_file],
        )
        # empty `txins` means Tx inputs will be selected automatically by ClusterLib magic
        script_txins = [clusterlib.ScriptTxIn(txins=[], script_file=multisig_script)]
        script_txins = [
            clusterlib.ScriptTxIn(
                txins=[],
                reference_txin=reference_utxo,
                reference_type=reference_type,
            )
        ]

        if use_build_cmd:
            tx_out_from = cluster.build_tx(
                src_address=script_address,
                tx_name=f"{temp_template}_from",
                txouts=destinations,
                script_txins=script_txins,
                fee_buffer=2_000_000,
                tx_files=tx_files,
                invalid_hereafter=invalid_hereafter,
                invalid_before=invalid_before,
                witness_override=2,
            )
            tx_signed = cluster.sign_tx(
                tx_body_file=tx_out_from.out_file,
                signing_key_files=tx_files.signing_key_files,
                tx_name=f"{temp_template}_from",
            )
            cluster.submit_tx(tx_file=tx_signed, txins=tx_out_from.txins)
        else:
            tx_out_from = cluster.send_tx(
                src_address=script_address,
                tx_name=f"{temp_template}_from",
                txouts=destinations,
                script_txins=script_txins,
                tx_files=tx_files,
                invalid_hereafter=invalid_hereafter,
                invalid_before=invalid_before,
            )

        # check final balances
        out_utxos = cluster.get_utxo(tx_raw_output=tx_out_from)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=script_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_out_from.txins) - tx_out_from.fee - amount
        ), f"Incorrect balance for script address `{script_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_addr.address}`"

        # check that reference UTxO was NOT spent
        assert cluster.get_utxo(utxo=reference_utxo), "Reference input was spent"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        # TODO: check reference script in db-sync (the `tx_out_from`)

        # check expected script type
        # TODO: moved the check to the end of the test because of XFAIL
        if (
            script_type_str == "SimpleScriptV1"
            and reference_utxo.reference_script["script"]["type"] == "SimpleScriptV2"
        ):
            pytest.xfail("Reported 'SimpleScriptV2', see node issue #4261")
        assert reference_utxo.reference_script["script"]["type"] == script_type_str
