"""Tests for multisig transactions and scripts.

* multisig
* time locking
* auxiliary scripts
* reference UTxO
"""

import json
import logging
import pathlib as pl
import random
import re

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from _pytest.fixtures import FixtureRequest
from _pytest.fixtures import SubRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import plutus_common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import submit_api
from cardano_node_tests.utils import submit_utils
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

DATA_DIR = pl.Path(__file__).parent / "data"

JSON_METADATA_FILE = DATA_DIR / "tx_metadata.json"
CBOR_METADATA_FILE = DATA_DIR / "tx_metadata.cbor"


def multisig_tx(
    cluster_obj: clusterlib.ClusterLib,
    temp_template: str,
    payment_address: str,
    dst_address: str,
    amount: int,
    payment_skey_files: list[pl.Path],
    multisig_script: pl.Path | None = None,
    script_utxos: list[clusterlib.UTXOData] | None = None,
    invalid_hereafter: int | None = None,
    invalid_before: int | None = None,
    use_build_cmd: bool = False,
    submit_method: str = submit_utils.SubmitMethods.CLI,
) -> clusterlib.TxRawOutput:
    """Build and submit multisig transaction."""
    if bool(multisig_script) ^ bool(script_utxos):
        err = "Both `multisig_script` and `script_utxos` must be provided"
        raise ValueError(err)

    script_utxos = script_utxos or []

    txins = []
    script_txins = []
    script_amount = 0
    if script_utxos:
        assert multisig_script  # For mypy
        script_txins = [clusterlib.ScriptTxIn(txins=script_utxos, script_file=multisig_script)]
        script_amount = clusterlib.calculate_utxos_balance(utxos=script_utxos)
        if (script_amount - amount) < 2_000_000:
            # Add additional funds to cover fee
            fee_txin = next(
                r
                for r in clusterlib_utils.get_just_lovelace_utxos(
                    address_utxos=cluster_obj.g_query.get_utxo(address=payment_address)
                )
                if r.amount >= 2_000_000
            )
            txins = [
                fee_txin,
            ]

    txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]
    witness_count = len(payment_skey_files)

    if use_build_cmd:
        tx_output = cluster_obj.g_transaction.build_tx(
            src_address=payment_address,
            tx_name=temp_template,
            txins=txins,
            txouts=txouts,
            script_txins=script_txins,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_override=witness_count,
        )
    else:
        fee = cluster_obj.g_transaction.calculate_tx_fee(
            src_address=payment_address,
            tx_name=temp_template,
            txins=txins,
            txouts=txouts,
            script_txins=script_txins,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_count_add=witness_count + 1,
        )
        tx_output = cluster_obj.g_transaction.build_raw_tx(
            src_address=payment_address,
            tx_name=temp_template,
            txins=txins,
            txouts=txouts,
            script_txins=script_txins,
            fee=fee,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
        )

    # Create witness file for each key
    witness_files = [
        cluster_obj.g_transaction.witness_tx(
            tx_body_file=tx_output.out_file,
            witness_name=f"{temp_template}_skey{idx}",
            signing_key_files=[skey],
        )
        for idx, skey in enumerate(payment_skey_files)
    ]

    # Sign TX using witness files
    tx_witnessed_file = cluster_obj.g_transaction.assemble_tx(
        tx_body_file=tx_output.out_file,
        witness_files=witness_files,
        tx_name=temp_template,
    )

    # Submit signed TX
    submit_utils.submit_tx(
        submit_method=submit_method,
        cluster_obj=cluster_obj,
        tx_file=tx_witnessed_file,
        txins=tx_output.txins or script_utxos,
    )

    # Check final balances
    out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    src_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=payment_address)
    dst_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)
    assert (
        src_out_utxos[0].amount
        == clusterlib.calculate_utxos_balance([*tx_output.txins, *script_utxos])
        - tx_output.fee
        - amount
    ), "Incorrect balance for source addresses"
    assert dst_out_utxos[0].amount == amount, (
        f"Incorrect balance for script address `{dst_address}`"
    )

    common.check_missing_utxos(cluster_obj=cluster_obj, utxos=out_utxos)

    return tx_output


class TestBasic:
    """Basic tests for multisig transactions."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=20,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_script_addr_length(
        self, cluster: clusterlib.ClusterLib, payment_addrs: list[clusterlib.AddressRecord]
    ):
        """Check that script address length is the same as length of other addresses.

        There was an issue that script address was 32 bytes instead of 28 bytes.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Check script address length
        assert len(script_address) == len(payment_addrs[0].address)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_multisig_all(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds to and from script address using the *all* script."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # Send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            payment_address=payment_addrs[0].address,
            dst_address=payment_addrs[1].address,
            amount=amount,
            payment_skey_files=payment_skey_files,
            multisig_script=multisig_script,
            script_utxos=script_utxos,
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_stake_keys_multisig_all(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Use stake keys to witness a Tx (instead of payment keys).

        Send funds to and from script address using the *all* script.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        # Create a multisig script that uses stake keys
        stake_key_recs = [
            cluster.g_stake_address.gen_stake_key_pair(key_name=f"{temp_template}_sig_{i}")
            for i in range(2, 6)
        ]
        multisig_script = clusterlib_utils.build_stake_multisig_script(
            cluster_obj=cluster,
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            stake_vkey_files=[r.vkey_file for r in stake_key_recs],
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_addrs[0].skey_file],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # Send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            payment_address=payment_addrs[0].address,
            dst_address=payment_addrs[1].address,
            amount=amount,
            payment_skey_files=[
                payment_addrs[0].skey_file,
                *[r.skey_file for r in stake_key_recs],
            ],
            multisig_script=multisig_script,
            script_utxos=script_utxos,
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_multisig_any(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds using the *any* script.

        * send funds to script address
        * send funds from script address using single witness
        * send funds from script address using multiple witnesses
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000
        repeat = 3

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        tx_outputs = []

        # Send funds to script address
        tx_outputs.append(
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_to",
                payment_address=payment_addrs[0].address,
                dst_address=script_address,
                amount=amount,
                payment_skey_files=[payment_skey_files[0]],
                use_build_cmd=use_build_cmd,
                submit_method=submit_method,
            )
        )

        # Send funds from script address using single witness
        from_single_tx_outputs = []
        for i in range(repeat):
            out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_outputs[-1])
            script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
            tx_output = multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_single_{i}",
                payment_address=payment_addrs[0].address,
                dst_address=script_address,
                amount=amount,
                payment_skey_files=[
                    payment_addrs[0].skey_file,
                    payment_skey_files[random.randrange(0, skeys_len)],
                ],
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                use_build_cmd=use_build_cmd,
                submit_method=submit_method,
            )
            tx_outputs.append(tx_output)
            from_single_tx_outputs.append(tx_output)

        # Send funds from script address using multiple witnesses
        for i in range(repeat):
            num_of_skeys = random.randrange(2, skeys_len)
            out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_outputs[-1])
            script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
            # On last iteration, return funds to the payment address
            dst_address = payment_addrs[1].address if i == repeat - 1 else script_address
            tx_outputs.append(
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=f"{temp_template}_from_multi_{i}",
                    payment_address=payment_addrs[0].address,
                    dst_address=dst_address,
                    amount=amount,
                    payment_skey_files=[
                        payment_addrs[0].skey_file,
                        *random.sample(payment_skey_files, k=num_of_skeys),
                    ],
                    multisig_script=multisig_script,
                    script_utxos=script_utxos,
                    use_build_cmd=use_build_cmd,
                    submit_method=submit_method,
                )
            )

        expected_fee = 204_969
        for tx_out in from_single_tx_outputs:
            # Check expected fees
            assert common.is_fee_in_interval(tx_out.fee, expected_fee, frac=0.15), (
                "TX fee doesn't fit the expected interval"
            )

        for tx_out in tx_outputs:
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_multisig_atleast(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds to and from script address using the *atLeast* script."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000
        repeat = 3

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)
        required = skeys_len - 4

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=required,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        tx_outputs = []

        # Send funds to script address
        tx_outputs.append(
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_to",
                payment_address=payment_addrs[0].address,
                dst_address=script_address,
                amount=amount,
                payment_skey_files=[payment_skey_files[0]],
                use_build_cmd=use_build_cmd,
                submit_method=submit_method,
            )
        )

        # Send funds from script address
        for i in range(repeat):
            num_of_skeys = random.randrange(required, skeys_len)
            out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_outputs[-1])
            script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
            # On last iteration, return funds to the payment address
            dst_address = payment_addrs[1].address if i == repeat - 1 else script_address
            tx_outputs.append(
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=f"{temp_template}_from_{i}",
                    payment_address=payment_addrs[0].address,
                    dst_address=dst_address,
                    amount=amount,
                    payment_skey_files=[
                        payment_addrs[0].skey_file,
                        *random.sample(payment_skey_files, k=num_of_skeys),
                    ],
                    multisig_script=multisig_script,
                    script_utxos=script_utxos,
                    use_build_cmd=use_build_cmd,
                    submit_method=submit_method,
                )
            )

        for tx_out in tx_outputs:
            dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_normal_tx_to_script_addr(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds to script address using TX signed with skeys (not using witness files)."""
        temp_template = common.get_test_id(cluster)
        src_address = payment_addrs[0].address
        amount = 1_000_000

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=[p.vkey_file for p in payment_addrs],
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        txouts = [clusterlib.TxOut(address=script_address, amount=amount)]
        tx_files = clusterlib.TxFiles(signing_key_files=[payment_addrs[0].skey_file])

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txouts=txouts,
            tx_files=tx_files,
        )

        # Check final balances
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        src_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=src_address)
        dst_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        assert (
            src_out_utxos[0].amount
            == clusterlib.calculate_utxos_balance(tx_output.txins) - tx_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert dst_out_utxos[0].amount == amount, (
            f"Incorrect balance for script address `{script_address}`"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_normal_tx_from_script_addr(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send funds from script address using TX signed with skeys (not using witness files)."""
        temp_template = common.get_test_id(cluster)
        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=src_addr.address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # Send funds from script address
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[src_addr.skey_file, dst_addr.skey_file],
        )
        fee_txin = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=src_addr.address)
            )
            if r.amount >= 2_000_000
        )
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        script_txins = [clusterlib.ScriptTxIn(txins=script_utxos, script_file=multisig_script)]

        tx_out_from = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_from",
            src_address=src_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txins=[fee_txin],
            txouts=txouts,
            script_txins=script_txins,
            tx_files=tx_files,
        )

        # Check final balances
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_from)
        src_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)
        dst_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)
        assert (
            src_out_utxos[0].amount
            == clusterlib.calculate_utxos_balance([*tx_out_from.txins, *script_utxos])
            - tx_out_from.fee
            - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert dst_out_utxos[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_addr.address}`"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_multisig_empty_all(
        self, cluster: clusterlib.ClusterLib, payment_addrs: list[clusterlib.AddressRecord]
    ):
        """Send funds from script address using the *all* script with zero skeys."""
        temp_template = common.get_test_id(cluster)
        amount = 10_000_000

        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=(),
            slot=cluster.g_query.get_slot_no() + 10_000,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
        )

        # Send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            # The `payment_address` will not be used for an additional UTxO, as there's
            # enough funds on the script UTxO to cover the fees.
            payment_address=payment_addrs[0].address,
            dst_address=payment_addrs[1].address,
            amount=1_000_000,
            payment_skey_files=[],
            multisig_script=multisig_script,
            script_utxos=script_utxos,
            invalid_before=100,
            invalid_hereafter=cluster.g_query.get_slot_no() + 1_000,
        )

        # Check expected fees
        expected_fee = 176_809
        assert common.is_fee_in_interval(tx_out_from.fee, expected_fee, frac=0.15), (
            "TX fee doesn't fit the expected interval"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_multisig_no_required_atleast(
        self, cluster: clusterlib.ClusterLib, payment_addrs: list[clusterlib.AddressRecord]
    ):
        """Send funds from script address using the *atLeast* script with no required witnesses."""
        temp_template = common.get_test_id(cluster)
        amount = 10_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=0,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
        )

        # Send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        try:
            tx_out_from = multisig_tx(
                cluster_obj=cluster,
                # The `payment_address` will not be used for an additional UTxO, as there's
                # enough funds on the script UTxO to cover the fees.
                temp_template=f"{temp_template}_from",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=1_000_000,
                payment_skey_files=[],
                multisig_script=multisig_script,
                script_utxos=script_utxos,
            )
        except clusterlib.CLIError as err:
            if "Missing: (--witness-file FILE)" in str(err):
                issues.node_3835.finish_test()
                return
            raise

        # Check expected fees
        expected_fee = 176_765
        assert common.is_fee_in_interval(tx_out_from.fee, expected_fee, frac=0.15), (
            "TX fee doesn't fit the expected interval"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)


class TestNegative:
    """Transaction tests that are expected to fail."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=10,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_multisig_all_missing_skey(
        self, cluster: clusterlib.ClusterLib, payment_addrs: list[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *all* script, omit one skey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
        )

        # Send funds from script address, omit one skey
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=amount,
                payment_skey_files=payment_skey_files[:-1],
                multisig_script=multisig_script,
                script_utxos=script_utxos,
            )
        err_str = str(excinfo.value)
        assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_multisig_any_unlisted_skey(
        self, cluster: clusterlib.ClusterLib, payment_addrs: list[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *any* script with unlisted skey.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs[1:-1]]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
        )

        # Send funds from script address, use skey that is not listed in the script
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=amount,
                payment_skey_files=[payment_skey_files[0], payment_skey_files[-1]],
                multisig_script=multisig_script,
                script_utxos=script_utxos,
            )
        err_str = str(excinfo.value)
        assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_multisig_atleast_low_num_of_skeys(
        self, cluster: clusterlib.ClusterLib, payment_addrs: list[clusterlib.AddressRecord]
    ):
        """Try to send funds from script address using the *atLeast* script.

        Num of skeys < required. Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        skeys_len = len(payment_skey_files)
        required = skeys_len - 4

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.AT_LEAST,
            payment_vkey_files=payment_vkey_files,
            required=required,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
        )

        # Send funds from script address, use lower number of skeys then required
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        for num_of_skeys in range(1, required - 1):
            with pytest.raises(clusterlib.CLIError) as excinfo:
                multisig_tx(
                    cluster_obj=cluster,
                    temp_template=f"{temp_template}_from_fail{num_of_skeys}",
                    payment_address=payment_addrs[0].address,
                    dst_address=payment_addrs[1].address,
                    amount=amount,
                    payment_skey_files=[
                        payment_addrs[0].skey_file,
                        *random.sample(payment_skey_files, k=num_of_skeys),
                    ],
                    multisig_script=multisig_script,
                    script_utxos=script_utxos,
                )
            err_str = str(excinfo.value)
            assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALLEGRA,
    reason="runs only with Allegra+ TX",
)
class TestTimeLocking:
    """Tests for time locking."""

    SCRIPT_AMOUNT = 2_000_000

    def _fund_script_time_locking(
        self,
        cluster_obj: clusterlib.ClusterLib,
        temp_template: str,
        payment_addrs: list[clusterlib.AddressRecord],
        slot: int,
        slot_type_arg: str,
        use_build_cmd: bool,
    ) -> tuple[pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput]:
        """Create and fund script address."""
        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster_obj.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=slot,
            slot_type_arg=slot_type_arg,
        )

        # Create script address
        script_address = cluster_obj.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_output = multisig_tx(
            cluster_obj=cluster_obj,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=self.SCRIPT_AMOUNT,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        out_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)

        return multisig_script, script_address, script_utxos, tx_output

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=20,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @pytest.fixture
    def fund_script_before_slot_in_past(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        request: SubRequest,
    ) -> tuple[pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int]:
        """Create and fund script address with "before" slot in the past."""
        temp_template = common.get_test_id(cluster)
        use_build_cmd = request.param

        last_slot_no = cluster.g_query.get_slot_no()
        before_slot = last_slot_no - 1

        multisig_script, script_address, script_utxos, tx_output = self._fund_script_time_locking(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addrs=payment_addrs,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
            use_build_cmd=use_build_cmd,
        )

        return multisig_script, script_address, script_utxos, tx_output, before_slot

    @pytest.fixture
    def fund_script_before_slot_in_future(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        request: SubRequest,
    ) -> tuple[pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int]:
        """Create and fund script address with "before" slot in the future."""
        temp_template = common.get_test_id(cluster)
        use_build_cmd = request.param

        last_slot_no = cluster.g_query.get_slot_no()
        before_slot = last_slot_no + 10_000

        multisig_script, script_address, script_utxos, tx_output = self._fund_script_time_locking(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addrs=payment_addrs,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
            use_build_cmd=use_build_cmd,
        )

        return multisig_script, script_address, script_utxos, tx_output, before_slot

    @pytest.fixture
    def fund_script_after_slot_in_future(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        request: SubRequest,
    ) -> tuple[pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int]:
        """Create and fund script address with "after" slot in the future."""
        temp_template = common.get_test_id(cluster)
        use_build_cmd = request.param

        last_slot_no = cluster.g_query.get_slot_no()
        after_slot = last_slot_no + 10_000

        multisig_script, script_address, script_utxos, tx_output = self._fund_script_time_locking(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addrs=payment_addrs,
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
            use_build_cmd=use_build_cmd,
        )

        return multisig_script, script_address, script_utxos, tx_output, after_slot

    @pytest.fixture
    def fund_script_after_slot_in_past(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        request: SubRequest,
    ) -> tuple[pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int]:
        """Create and fund script address with "after" slot in the past."""
        temp_template = common.get_test_id(cluster)
        use_build_cmd = request.param

        last_slot_no = cluster.g_query.get_slot_no()
        after_slot = last_slot_no - 1

        multisig_script, script_address, script_utxos, tx_output = self._fund_script_time_locking(
            cluster_obj=cluster,
            temp_template=temp_template,
            payment_addrs=payment_addrs,
            slot=after_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
            use_build_cmd=use_build_cmd,
        )

        return multisig_script, script_address, script_utxos, tx_output, after_slot

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "use_tx_validity", (True, False), ids=("tx_validity", "no_tx_validity")
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_script_after(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        use_tx_validity: bool,
    ):
        """Check that it is possible to spend from script address after given slot."""
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=self.SCRIPT_AMOUNT,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # Send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        invalid_hereafter = cluster.g_query.get_slot_no() + 1_000 if use_tx_validity else None
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            payment_address=payment_addrs[0].address,
            dst_address=payment_addrs[1].address,
            amount=self.SCRIPT_AMOUNT,
            payment_skey_files=payment_skey_files,
            multisig_script=multisig_script,
            script_utxos=script_utxos,
            invalid_before=100,
            invalid_hereafter=invalid_hereafter,
            use_build_cmd=use_build_cmd,
        )

        # Check expected fees
        expected_fee = 280_693 if use_build_cmd else 323_857
        assert common.is_fee_in_interval(tx_out_from.fee, expected_fee, frac=0.15), (
            "TX fee doesn't fit the expected interval"
        )

        # Check `transaction view` command
        tx_view.check_tx_view(cluster_obj=cluster, tx_raw_output=tx_out_from)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "use_tx_validity", (True, False), ids=("tx_validity", "no_tx_validity")
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_script_before(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        use_tx_validity: bool,
    ):
        """Check that it is possible to spend from script address before given slot."""
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.g_query.get_slot_no() + 10_000

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=self.SCRIPT_AMOUNT,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # Send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            payment_address=payment_addrs[0].address,
            dst_address=payment_addrs[1].address,
            amount=self.SCRIPT_AMOUNT,
            payment_skey_files=payment_skey_files,
            multisig_script=multisig_script,
            script_utxos=script_utxos,
            invalid_before=100 if use_tx_validity else None,
            invalid_hereafter=cluster.g_query.get_slot_no() + 1_000,
            use_build_cmd=use_build_cmd,
        )

        # Check expected fees
        expected_fee = 279_241 if use_build_cmd else 323_989
        assert common.is_fee_in_interval(tx_out_from.fee, expected_fee, frac=0.15), (
            "TX fee doesn't fit the expected interval"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("slot_type", ("before", "after"))
    @pytest.mark.smoke
    def test_tx_missing_validity(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        slot_type: str,
    ):
        """Check that it is NOT possible to spend from script address.

        The transaction validity interval is not specified.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        if slot_type == "before":
            slot_num = cluster.g_query.get_slot_no() + 10_000
            slot_type_arg = clusterlib.MultiSlotTypeArgs.BEFORE
        else:
            slot_num = 100
            slot_type_arg = clusterlib.MultiSlotTypeArgs.AFTER

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=slot_num,
            slot_type_arg=slot_type_arg,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=self.SCRIPT_AMOUNT,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # Send funds from script address - missing required validity interval
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=None,  # missing required validity interval for "after"
                invalid_hereafter=None,  # missing required validity interval for "before"
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)
        assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    def test_tx_negative_validity(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Check that it is NOT possible to spend from script address when validity is negative."""
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=1_000,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=self.SCRIPT_AMOUNT,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
        )

        # Send funds from script address - negative validity interval
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=-2,
                invalid_hereafter=-1,
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)

        # In node versions >= 1.36.0 we are checking error from
        # `cardano-cli transaction build/build-raw`
        if "SLOT must not be less than" in err_str:
            return

        # In node versions < 1.36.0 we were checking error from `cardano-cli transaction submit`
        assert "OutsideValidityIntervalUTxO" in err_str, err_str

        slot_no = 0
        _slot_search = re.search(
            r"ValidityInterval {invalidBefore = SJust \(SlotNo ([0-9]*)", err_str
        )
        if _slot_search is not None:
            slot_no = int(_slot_search.group(1))
        if slot_no > 0:
            issues.node_4863.finish_test()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "fund_script_before_slot_in_past",
        (False, pytest.param(True, marks=common.SKIPIF_BUILD_UNUSABLE)),
        ids=("build_raw", "build"),
        indirect=True,
    )
    @hypothesis.given(data=st.data())
    @common.hypothesis_settings(max_examples=50)
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_before_past(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        fund_script_before_slot_in_past: tuple[
            pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int
        ],
        data: st.DataObject,
        request: FixtureRequest,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "before" slot is in the past.
        """
        use_build_cmd = request.node.callspec.params["fund_script_before_slot_in_past"]
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        multisig_script, script_address, script_utxos, tx_output, before_slot = (
            fund_script_before_slot_in_past
        )

        slot_no = data.draw(st.integers(min_value=1, max_value=before_slot - 1))

        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Send funds from script address - valid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail1",
                payment_address=payment_addrs[0].address,
                dst_address=script_address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=1,
                invalid_hereafter=before_slot - slot_no,
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)
        assert "OutsideValidityIntervalUTxO" in err_str, err_str

        # Send funds from script address - invalid range, slot is already in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail2",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=1,
                invalid_hereafter=before_slot + slot_no,
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)
        assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "fund_script_before_slot_in_future",
        (False, pytest.param(True, marks=common.SKIPIF_BUILD_UNUSABLE)),
        ids=("build_raw", "build"),
        indirect=True,
    )
    @hypothesis.given(slot_no=st.integers(min_value=1, max_value=10_000))
    @common.hypothesis_settings(max_examples=50)
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_before_future(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        fund_script_before_slot_in_future: tuple[
            pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int
        ],
        slot_no: int,
        request: FixtureRequest,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "before" slot is in the future and the given range is invalid.
        """
        use_build_cmd = request.node.callspec.params["fund_script_before_slot_in_future"]
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        multisig_script, script_address, script_utxos, tx_output, before_slot = (
            fund_script_before_slot_in_future
        )

        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Send funds from script address - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=1,
                invalid_hereafter=before_slot + slot_no,
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)
        assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "fund_script_after_slot_in_future",
        (False, pytest.param(True, marks=common.SKIPIF_BUILD_UNUSABLE)),
        ids=("build_raw", "build"),
        indirect=True,
    )
    @hypothesis.given(data=st.data())
    @common.hypothesis_settings(max_examples=50)
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_after_future(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        fund_script_after_slot_in_future: tuple[
            pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int
        ],
        data: st.DataObject,
        request: FixtureRequest,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "after" slot is in the future and the given range is invalid.
        """
        use_build_cmd = request.node.callspec.params["fund_script_after_slot_in_future"]
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        multisig_script, script_address, script_utxos, tx_output, after_slot = (
            fund_script_after_slot_in_future
        )

        slot_no = data.draw(st.integers(min_value=1, max_value=after_slot - 1))

        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Send funds from script address - valid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail1",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=after_slot + slot_no,
                invalid_hereafter=after_slot + slot_no + 100,
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)
        assert "OutsideValidityIntervalUTxO" in err_str, err_str

        # Send funds from script address - invalid range, slot is in the future
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail2",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=slot_no,
                invalid_hereafter=after_slot,
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)
        assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize(
        "fund_script_after_slot_in_past",
        (False, pytest.param(True, marks=common.SKIPIF_BUILD_UNUSABLE)),
        ids=("build_raw", "build"),
        indirect=True,
    )
    @hypothesis.given(data=st.data())
    @common.hypothesis_settings(max_examples=50)
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_after_past(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        fund_script_after_slot_in_past: tuple[
            pl.Path, str, list[clusterlib.UTXOData], clusterlib.TxRawOutput, int
        ],
        data: st.DataObject,
        request: FixtureRequest,
    ):
        """Check that it's NOT possible to spend from the script address.

        The "after" slot is in the past.
        """
        use_build_cmd = request.node.callspec.params["fund_script_after_slot_in_past"]
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        multisig_script, script_address, script_utxos, tx_output, after_slot = (
            fund_script_after_slot_in_past
        )

        slot_no = data.draw(st.integers(min_value=1, max_value=after_slot - 1))

        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Send funds from script address - valid slot,
        # invalid range - `invalid_hereafter` is in the past
        with pytest.raises(clusterlib.CLIError) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=self.SCRIPT_AMOUNT,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=1,
                invalid_hereafter=after_slot - slot_no,
                use_build_cmd=use_build_cmd,
            )
        err_str = str(excinfo.value)
        assert "ScriptWitnessNotValidatingUTXOW" in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


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
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=20,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_tx_script_metadata_json(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send transaction with auxiliary script and metadata JSON.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
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

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addrs[0].address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
        )

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)

        cbor_body_script = cbor_body_metadata.aux_data[0][1]
        assert len(cbor_body_script) == len(payment_vkey_files) + 1, "Auxiliary script not present"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_tx_script_metadata_cbor(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send transaction with auxiliary script and metadata CBOR.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
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

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addrs[0].address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
        )

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)

        cbor_body_script = cbor_body_metadata.aux_data[0][2]
        assert len(cbor_body_script) == len(payment_vkey_files) + 1, "Auxiliary script not present"

        tx_db_record = dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)
        if tx_db_record:
            db_metadata = tx_db_record._convert_metadata()
            assert db_metadata == cbor_body_metadata.metadata, (
                "Metadata in db-sync doesn't match the original metadata"
            )

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_tx_script_no_metadata(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Send transaction with auxiliary script and no other metadata.

        Check that the auxiliary script is present in the TX body.
        """
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
            payment_vkey_files=payment_vkey_files,
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            auxiliary_script_files=[multisig_script],
        )

        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=payment_addrs[0].address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            tx_files=tx_files,
        )

        cbor_body_metadata = clusterlib_utils.load_tx_metadata(tx_body_file=tx_output.out_file)

        cbor_body_script = cbor_body_metadata.aux_data[0][1]
        assert len(cbor_body_script) == len(payment_vkey_files), "Auxiliary script not present"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_tx_script_invalid(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
    ):
        """Build transaction with invalid auxiliary script.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            # Not valid script file
            auxiliary_script_files=[JSON_METADATA_FILE],
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster.g_transaction.build_tx(
                    src_address=payment_addrs[0].address,
                    tx_name=temp_template,
                    fee_buffer=2_000_000,
                    tx_files=tx_files,
                )
            else:
                cluster.g_transaction.build_raw_tx(
                    src_address=payment_addrs[0].address, tx_name=temp_template, tx_files=tx_files
                )
        err_str = str(excinfo.value)
        assert 'Error in $: key "type" not found' in err_str, err_str


class TestIncrementalSigning:
    """Tests for incremental signing."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=20,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("tx_is", ("witnessed", "signed"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_incremental_signing(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
        tx_is: str,
    ):
        """Send funds from script address using TX that is signed incrementally.

        Test with Tx body created by both `transaction build` and `transaction build-raw`.
        Test with Tx created by both `transaction sign` and `transaction assemble`.
        """
        temp_template = common.get_test_id(cluster)

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        before_slot = cluster.g_query.get_slot_no() + 10_000

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=before_slot,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.BEFORE,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=src_addr.address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # Send funds from script address
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            metadata_json_files=[JSON_METADATA_FILE],
            metadata_cbor_files=[CBOR_METADATA_FILE],
            signing_key_files=payment_skey_files,
        )
        fee_txin = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=src_addr.address)
            )
            if r.amount >= 2_000_000
        )
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        script_txins = [clusterlib.ScriptTxIn(txins=script_utxos, script_file=multisig_script)]

        invalid_hereafter = cluster.g_query.get_slot_no() + 1_000
        if use_build_cmd:
            tx_out_from = cluster.g_transaction.build_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_from",
                txins=[fee_txin],
                txouts=txouts,
                script_txins=script_txins,
                fee_buffer=2_000_000,
                tx_files=tx_files,
                invalid_hereafter=invalid_hereafter,
                invalid_before=100,
                witness_override=len(payment_skey_files),
            )
        else:
            ttl = cluster.g_transaction.calculate_tx_ttl()
            fee = cluster.g_transaction.calculate_tx_fee(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_from",
                txins=[fee_txin],
                txouts=txouts,
                script_txins=script_txins,
                tx_files=tx_files,
                ttl=ttl,
                witness_count_add=len(payment_skey_files),
            )
            tx_out_from = cluster.g_transaction.build_raw_tx(
                src_address=src_addr.address,
                tx_name=f"{temp_template}_from",
                txins=[fee_txin],
                txouts=txouts,
                script_txins=script_txins,
                tx_files=tx_files,
                fee=fee,
                ttl=ttl,
                invalid_hereafter=invalid_hereafter,
                invalid_before=100,
            )

        # Sign or witness Tx body with first 2 skey and thus create Tx file that will be used for
        # incremental signing
        if tx_is == "signed":
            tx_signed = cluster.g_transaction.sign_tx(
                tx_body_file=tx_out_from.out_file,
                signing_key_files=payment_skey_files[:2],
                tx_name=f"{temp_template}_from0",
            )
        else:
            # Sign Tx body using witness files
            witness_files = [
                cluster.g_transaction.witness_tx(
                    tx_body_file=tx_out_from.out_file,
                    witness_name=f"{temp_template}_from_skey{idx}",
                    signing_key_files=[skey],
                )
                for idx, skey in enumerate(payment_skey_files[:2])
            ]
            tx_signed = cluster.g_transaction.assemble_tx(
                tx_body_file=tx_out_from.out_file,
                witness_files=witness_files,
                tx_name=f"{temp_template}_from0",
            )

        # Incrementally sign the already signed Tx with rest of required skeys
        for idx, skey in enumerate(payment_skey_files[2:], start=1):
            # Sign multiple times with the same skey to see that it doesn't affect Tx fee
            for r in range(5):
                tx_signed = cluster.g_transaction.sign_tx(
                    tx_file=tx_signed,
                    signing_key_files=[skey],
                    tx_name=f"{temp_template}_from{idx}_r{r}",
                )

        submit_utils.submit_tx(
            submit_method=submit_method,
            cluster_obj=cluster,
            tx_file=tx_signed,
            txins=tx_out_from.txins,
        )

        # Check final balances
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_from)
        src_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)
        dst_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)
        assert (
            src_out_utxos[0].amount
            == clusterlib.calculate_utxos_balance([*tx_out_from.txins, *script_utxos])
            - tx_out_from.fee
            - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert dst_out_utxos[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_addr.address}`"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALONZO,
    reason="runs only with Alonzo+ TX",
)
class TestDatum:
    """Tests for Simple Scripts V1 and V2 UTxOs with datum."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=5,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("script_version", ("simple_v1", "simple_v2"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_script_utxo_datum(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
        script_version: str,
    ):
        """Test creating UTxO with datum on Simple Scripts V1 and V2 address."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]

        # Create multisig script
        if script_version == "simple_v1":
            multisig_script = pl.Path(f"{temp_template}_multisig.script")
            script_content = {
                "keyHash": cluster.g_address.get_payment_vkey_hash(
                    payment_vkey_file=dst_addr.vkey_file
                ),
                "type": "sig",
            }
            with open(multisig_script, "w", encoding="utf-8") as fp_out:
                json.dump(script_content, fp_out, indent=4)
        else:
            multisig_script = cluster.g_transaction.build_multisig_script(
                script_name=temp_template,
                script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
                payment_vkey_files=[p.vkey_file for p in payment_addrs],
                slot=100,
                slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
            )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        txouts = [
            clusterlib.TxOut(
                address=script_address,
                amount=amount,
                datum_hash_file=plutus_common.DATUM_42_TYPED,
            )
        ]
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])

        # Create a UTxO on script address
        tx_output = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=temp_template,
            src_address=src_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txouts=txouts,
            tx_files=tx_files,
        )

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        datum_utxo = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)[0]
        assert datum_utxo.datum_hash, f"UTxO should have datum hash: {datum_utxo}"

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


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
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=5,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("script_version", ("simple_v1", "simple_v2"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_script_reference_utxo(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
        script_version: str,
    ):
        """Send funds from script address where script is on reference UTxO."""
        temp_template = common.get_test_id(cluster)

        src_addr = payment_addrs[0]
        dst_addr = payment_addrs[1]
        amount = 2_000_000

        # Create multisig script
        if script_version == "simple_v1":
            invalid_before = None
            invalid_hereafter = None

            reference_type = clusterlib.ScriptTypes.SIMPLE_V1
            script_type_str = "SimpleScriptV1"

            multisig_script = pl.Path(f"{temp_template}_multisig.script")
            script_content = {
                "keyHash": cluster.g_address.get_payment_vkey_hash(
                    payment_vkey_file=dst_addr.vkey_file
                ),
                "type": "sig",
            }
            with open(multisig_script, "w", encoding="utf-8") as fp_out:
                json.dump(script_content, fp_out, indent=4)
        else:
            invalid_before = 100
            invalid_hereafter = cluster.g_query.get_slot_no() + 1_000

            reference_type = clusterlib.ScriptTypes.SIMPLE_V2
            script_type_str = "SimpleScriptV2"

            multisig_script = cluster.g_transaction.build_multisig_script(
                script_name=temp_template,
                script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
                payment_vkey_files=[p.vkey_file for p in payment_addrs],
                slot=invalid_before,
                slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
            )

        # Create reference UTxO
        reference_utxo, tx_out_reference = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=src_addr,
            dst_addr=dst_addr,
            script_file=multisig_script,
            amount=4_000_000,
        )
        assert reference_utxo.reference_script

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=src_addr.address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[src_addr.skey_file],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # Send funds from script address
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[src_addr.skey_file, dst_addr.skey_file],
        )
        fee_txin = next(
            r
            for r in clusterlib_utils.get_just_lovelace_utxos(
                address_utxos=cluster.g_query.get_utxo(address=src_addr.address)
            )
            if r.amount >= 2_000_000
        )
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        script_txins = [
            clusterlib.ScriptTxIn(
                txins=script_utxos,
                reference_txin=reference_utxo,
                reference_type=reference_type,
            )
        ]

        tx_out_from = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_from",
            src_address=src_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txins=[fee_txin],
            txouts=txouts,
            script_txins=script_txins,
            tx_files=tx_files,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            witness_count_add=2,
        )

        # Check final balances
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_from)
        src_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=src_addr.address)
        dst_out_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=dst_addr.address)
        assert (
            src_out_utxos[0].amount
            == clusterlib.calculate_utxos_balance([*tx_out_from.txins, *script_utxos])
            - tx_out_from.fee
            - amount
        ), f"Incorrect balance for source address `{src_addr.address}`"
        assert dst_out_utxos[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_addr.address}`"
        )

        common.check_missing_utxos(cluster_obj=cluster, utxos=out_utxos)

        # Check that reference UTxO was NOT spent
        assert cluster.g_query.get_utxo(utxo=reference_utxo), "Reference input was spent"

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_reference)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

        # Check expected script type
        if (
            script_type_str == "SimpleScriptV1"
            and reference_utxo.reference_script["script"]["type"] == "SimpleScriptV2"
        ):
            issues.node_4261.finish_test()

        # In node >= 1.36.0 it is not necessary to distinguish between MultiSig and Timelock
        # scripts, both now report as "SimpleScript".
        assert reference_utxo.reference_script["script"]["type"] in (
            script_type_str,
            "SimpleScript",
        )

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("script_version", ("simple_v1", "simple_v2"))
    @pytest.mark.parametrize("address_type", ("shelley", "byron"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_spend_reference_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
        script_version: str,
        address_type: str,
    ):
        """Test spending a UTxO that holds a reference script.

        * create a Tx output with reference script (reference script UTxO)
        * spend the reference UTxO
        * check that the UTxO was spent
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000
        payment_addr = payment_addrs[0]

        reference_addr = payment_addrs[1]
        if address_type == "byron":
            # Create reference UTxO on Byron address
            reference_addr = clusterlib_utils.gen_byron_addr(
                cluster_obj=cluster, name_template=temp_template
            )

        # Create multisig script
        if script_version == "simple_v1":
            multisig_script = pl.Path(f"{temp_template}_multisig.script")
            script_content = {
                "keyHash": cluster.g_address.get_payment_vkey_hash(
                    payment_vkey_file=payment_addr.vkey_file
                ),
                "type": "sig",
            }
            with open(multisig_script, "w", encoding="utf-8") as fp_out:
                json.dump(script_content, fp_out, indent=4)
        else:
            multisig_script = cluster.g_transaction.build_multisig_script(
                script_name=temp_template,
                script_type_arg=clusterlib.MultiSigTypeArgs.ANY,
                payment_vkey_files=[p.vkey_file for p in payment_addrs],
                slot=100,
                slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
            )

        # Create reference UTxO
        reference_utxo, tx_out_reference = clusterlib_utils.create_reference_utxo(
            temp_template=temp_template,
            cluster_obj=cluster,
            payment_addr=payment_addr,
            dst_addr=reference_addr,
            script_file=multisig_script,
            amount=5_000_000,
        )
        assert reference_utxo.reference_script

        # Spend the reference UTxO
        txouts = [clusterlib.TxOut(address=payment_addr.address, amount=amount)]
        tx_files = clusterlib.TxFiles(
            signing_key_files=[reference_addr.skey_file],
        )

        # The `tx_out_spend`
        clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{temp_template}_spend",
            src_address=reference_addr.address,
            submit_method=submit_method,
            use_build_cmd=use_build_cmd,
            txins=[reference_utxo],
            txouts=txouts,
            tx_files=tx_files,
            witness_override=2 if address_type == "byron" else None,
            witness_count_add=0 if use_build_cmd else 2,
        )

        # Check that the reference UTxO was spent
        assert not cluster.g_query.get_utxo(utxo=reference_utxo), (
            f"Reference script UTxO was NOT spent: '{reference_utxo}`"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_reference)
        # TODO: check reference script in db-sync (the `tx_out_spend`)


@pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.ALLEGRA,
    reason="runs only with Allegra+ TX",
)
class TestNested:
    """Tests for nested scripts."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=20,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize("type_top", ("all", "any"))
    @pytest.mark.parametrize("type_nested", ("all", "any"))
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_nested_script(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        type_top: str,
        type_nested: str,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Check that it is possible to spend using a script with nested rules."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_addr = payment_addrs[0]
        dst_addr1 = payment_addrs[1]
        dst_addr2 = payment_addrs[2]
        dst_addr3 = payment_addrs[3]

        # Create multisig script
        multisig_script = pl.Path(f"{temp_template}_multisig.script")
        script_content = {
            "type": type_top,
            "scripts": [
                {
                    "type": "sig",
                    "keyHash": cluster.g_address.get_payment_vkey_hash(
                        payment_vkey_file=dst_addr1.vkey_file
                    ),
                },
                {
                    "type": type_nested,
                    "scripts": [
                        {"type": "after", "slot": 100},
                        {
                            "type": "sig",
                            "keyHash": cluster.g_address.get_payment_vkey_hash(
                                payment_vkey_file=dst_addr2.vkey_file
                            ),
                        },
                        {
                            "type": "sig",
                            "keyHash": cluster.g_address.get_payment_vkey_hash(
                                payment_vkey_file=dst_addr3.vkey_file
                            ),
                        },
                    ],
                },
            ],
        }
        with open(multisig_script, "w", encoding="utf-8") as fp_out:
            json.dump(script_content, fp_out, indent=4)

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=src_addr.address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[src_addr.skey_file],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # We don't need to include any additional signatures for the nested "any" case,
        # meeting the slot range is enough
        # There need to be at least one skey file, even for the any-any case, where the contition
        # is already met due to valid slot range. See cardano-node issue #3835.
        payment_skey_files = [src_addr.skey_file]
        if type_nested == "all":
            payment_skey_files.extend((dst_addr2.skey_file, dst_addr3.skey_file))
        if type_top == "all":
            payment_skey_files.append(dst_addr1.skey_file)

        # Send funds from script address
        invalid_hereafter = cluster.g_query.get_slot_no() + 1_000
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            payment_address=src_addr.address,
            dst_address=dst_addr1.address,
            amount=amount,
            payment_skey_files=payment_skey_files,
            multisig_script=multisig_script,
            script_utxos=script_utxos,
            invalid_before=100,
            invalid_hereafter=invalid_hereafter,
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.smoke
    @pytest.mark.testnets
    @pytest.mark.dbsync
    def test_nested_optional_all(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Check that it is possible to not meet conditions in nested "all" rule."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_addr = payment_addrs[0]
        dst_addr1 = payment_addrs[1]

        # Create multisig script
        multisig_script = pl.Path(f"{temp_template}_multisig.script")
        script_content = {
            "type": "any",
            "scripts": [
                {
                    "type": "sig",
                    "keyHash": cluster.g_address.get_payment_vkey_hash(
                        payment_vkey_file=dst_addr1.vkey_file
                    ),
                },
                {
                    "type": "all",
                    "scripts": [
                        {"type": "after", "slot": 100},
                        *[
                            {
                                "type": "sig",
                                "keyHash": cluster.g_address.get_payment_vkey_hash(
                                    payment_vkey_file=r.vkey_file
                                ),
                            }
                            for r in payment_addrs[2:]
                        ],
                    ],
                },
            ],
        }
        with open(multisig_script, "w", encoding="utf-8") as fp_out:
            json.dump(script_content, fp_out, indent=4)

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=src_addr.address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[src_addr.skey_file],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # Send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        tx_out_from = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_from",
            payment_address=src_addr.address,
            dst_address=dst_addr1.address,
            amount=amount,
            payment_skey_files=[src_addr.skey_file, dst_addr1.skey_file],
            multisig_script=multisig_script,
            script_utxos=script_utxos,
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_to)
        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_out_from)

    @allure.link(helpers.get_vcs_link())
    @submit_utils.PARAM_SUBMIT_METHOD
    @common.PARAM_USE_BUILD_CMD
    @pytest.mark.parametrize(
        "scenario", ("all1", "all2", "all3", "all4", "all5", "all6", "any1", "any2", "any3", "any4")
    )
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_invalid(  # noqa: C901
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        scenario: str,
        use_build_cmd: bool,
        submit_method: str,
    ):
        """Test scenarios where it's NOT possible to spend from a script address."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        dst_addr1 = payment_addrs[1]
        dst_addr2 = payment_addrs[2]
        dst_addr3 = payment_addrs[3]

        last_slot_no = cluster.g_query.get_slot_no()

        if scenario == "all1":
            type_top = "all"
            type_nested = "any"
            invalid_before = 10
            invalid_hereafter = last_slot_no + 1_000
            # `dst_addr1.skey_file` is needed and missing
            payment_skey_files = [dst_addr2.skey_file]
            script_top: list[dict] = []
            script_nested: list[dict] = [{"type": "after", "slot": invalid_before}]
            expected_err = "ScriptWitnessNotValidatingUTXOW"
        elif scenario == "all2":
            type_top = "all"
            type_nested = "any"
            payment_skey_files = [dst_addr1.skey_file, dst_addr2.skey_file]
            # Valid interval is in the future
            invalid_hereafter = last_slot_no + 1_000
            invalid_before = invalid_hereafter - 100
            script_top = []
            script_nested = [{"type": "after", "slot": invalid_before}]
            expected_err = "OutsideValidityIntervalUTxO"
        elif scenario == "all3":
            type_top = "all"
            type_nested = "any"
            payment_skey_files = [dst_addr1.skey_file, dst_addr2.skey_file]
            # Valid interval is in the past
            invalid_hereafter = last_slot_no - 10
            invalid_before = 10
            script_top = []
            script_nested = [{"type": "before", "slot": invalid_hereafter}]
            expected_err = "OutsideValidityIntervalUTxO"
        elif scenario == "all4":
            type_top = "all"
            type_nested = "all"
            invalid_before = 10
            invalid_hereafter = last_slot_no + 1_000
            # `dst_addr3.skey_file` is needed and missing
            payment_skey_files = [dst_addr1.skey_file, dst_addr2.skey_file]
            script_top = []
            script_nested = [{"type": "after", "slot": invalid_before}]
            expected_err = "ScriptWitnessNotValidatingUTXOW"
        elif scenario == "all5":
            type_top = "all"
            type_nested = "any"
            payment_skey_files = [dst_addr1.skey_file, dst_addr2.skey_file]
            invalid_before = 10
            invalid_hereafter = last_slot_no + 1_000
            # Conflicting intervals
            script_top = [{"type": "before", "slot": invalid_before + 10}]
            script_nested = [{"type": "after", "slot": invalid_before + 11}]
            expected_err = "ScriptWitnessNotValidatingUTXOW"
        elif scenario == "all6":
            type_top = "all"
            type_nested = "any"
            invalid_before = 10
            invalid_hereafter = last_slot_no + 1_000
            payment_skey_files = [dst_addr1.skey_file, dst_addr2.skey_file]
            # Valid interval is in the past
            script_top = [{"type": "before", "slot": last_slot_no - 100}]
            script_nested = []
            expected_err = "ScriptWitnessNotValidatingUTXOW"
        elif scenario == "any1":
            type_top = "any"
            type_nested = "all"
            invalid_before = 10
            invalid_hereafter = last_slot_no + 1_000
            # None of the "ANY" conditions are met:
            #  `dst_addr1.skey_file` is missing
            #  nested "ALL" condition is not met - `dst_addr3.skey_file` is missing
            payment_skey_files = [dst_addr2.skey_file]
            script_top = []
            script_nested = [{"type": "after", "slot": invalid_before}]
            expected_err = "ScriptWitnessNotValidatingUTXOW"
        elif scenario == "any2":
            type_top = "any"
            type_nested = "all"
            payment_skey_files = [dst_addr2.skey_file]
            # Valid interval is in the future
            invalid_hereafter = last_slot_no + 1_000
            invalid_before = last_slot_no + 200
            script_top = [{"type": "after", "slot": invalid_before}]
            script_nested = []
            expected_err = "OutsideValidityIntervalUTxO"
        elif scenario == "any3":
            type_top = "any"
            type_nested = "all"
            invalid_before = 10
            invalid_hereafter = last_slot_no + 1_000
            payment_skey_files = [dst_addr2.skey_file, dst_addr3.skey_file]
            # None of the "ANY" conditions are met:
            #  `dst_addr1.skey_file` is missing
            #  nested "ALL" condition is not met - the valid interval is in the past
            script_top = []
            script_nested = [{"type": "before", "slot": last_slot_no - 100}]
            expected_err = "ScriptWitnessNotValidatingUTXOW"
        elif scenario == "any4":
            type_top = "any"
            type_nested = "all"
            invalid_before = 10
            invalid_hereafter = last_slot_no + 1_000
            # None of the "ANY" conditions are met:
            #  `dst_addr1.skey_file` is missing
            #  valid interval is in the past
            #  nested "ALL" condition is not met - `dst_addr3.skey_file` is missing
            payment_skey_files = [dst_addr2.skey_file]
            script_top = [{"type": "before", "slot": last_slot_no - 100}]
            script_nested = []
            expected_err = "ScriptWitnessNotValidatingUTXOW"
        else:
            msg = f"Unknown scenario: {scenario}"
            raise AssertionError(msg)

        # Create multisig script
        multisig_script = pl.Path(f"{temp_template}_multisig.script")
        script_content = {
            "type": type_top,
            "scripts": [
                {
                    "type": "sig",
                    "keyHash": cluster.g_address.get_payment_vkey_hash(
                        payment_vkey_file=dst_addr1.vkey_file
                    ),
                },
                *script_top,
                {
                    "type": type_nested,
                    "scripts": [
                        *script_nested,
                        {
                            "type": "sig",
                            "keyHash": cluster.g_address.get_payment_vkey_hash(
                                payment_vkey_file=dst_addr2.vkey_file
                            ),
                        },
                        {
                            "type": "sig",
                            "keyHash": cluster.g_address.get_payment_vkey_hash(
                                payment_vkey_file=dst_addr3.vkey_file
                            ),
                        },
                    ],
                },
            ],
        }
        with open(multisig_script, "w", encoding="utf-8") as fp_out:
            json.dump(script_content, fp_out, indent=4)

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Fund script address
        tx_output = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_addrs[0].skey_file],
            use_build_cmd=use_build_cmd,
            submit_method=submit_method,
        )

        # Try to send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from_fail",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=amount,
                payment_skey_files=[*payment_skey_files, payment_addrs[0].skey_file],
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=invalid_before,
                invalid_hereafter=invalid_hereafter,
                use_build_cmd=use_build_cmd,
                submit_method=submit_method,
            )
        err_str = str(excinfo.value)
        assert expected_err in err_str, err_str

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=tx_output)


class TestCompatibility:
    """Tests for checking compatibility with previous Tx eras."""

    @pytest.fixture
    def payment_addrs(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.AddressRecord]:
        """Create new payment addresses."""
        addrs = common.get_payment_addrs(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=5,
            fund_idx=[0],
            caching_key=helpers.get_current_line_str(),
        )
        return addrs

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.SHELLEY,
        reason="runs only with Shelley TX",
    )
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    def test_script_v2(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Check that it is not possible to use 'SimpleScriptV2' in Shelley-era Tx."""
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        payment_vkey_files = [p.vkey_file for p in payment_addrs]
        payment_skey_files = [p.skey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=100,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        # Create script address
        script_address = cluster.g_address.gen_payment_addr(
            addr_name=temp_template, payment_script_file=multisig_script
        )

        # Send funds to script address
        tx_out_to = multisig_tx(
            cluster_obj=cluster,
            temp_template=f"{temp_template}_to",
            payment_address=payment_addrs[0].address,
            dst_address=script_address,
            amount=amount,
            payment_skey_files=[payment_skey_files[0]],
            submit_method=submit_method,
        )

        # Try to send funds from script address
        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_out_to)
        script_utxos = clusterlib.filter_utxos(utxos=out_utxos, address=script_address)
        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            multisig_tx(
                cluster_obj=cluster,
                temp_template=f"{temp_template}_from",
                payment_address=payment_addrs[0].address,
                dst_address=payment_addrs[1].address,
                amount=amount,
                payment_skey_files=payment_skey_files,
                multisig_script=multisig_script,
                script_utxos=script_utxos,
                invalid_before=100,
                invalid_hereafter=150,
                submit_method=submit_method,
            )
        err_str = str(excinfo.value)
        assert (
            "SimpleScriptV2 is not supported" in err_str
            or "Transaction validity lower bound not supported in Shelley" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.SHELLEY,
        reason="runs only with Shelley TX",
    )
    @submit_utils.PARAM_SUBMIT_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_auxiliary_scripts(
        self,
        cluster: clusterlib.ClusterLib,
        payment_addrs: list[clusterlib.AddressRecord],
        submit_method: str,
    ):
        """Check that it is not possible to use auxiliary script in Shelley-era Tx."""
        temp_template = common.get_test_id(cluster)

        payment_vkey_files = [p.vkey_file for p in payment_addrs]

        # Create multisig script
        multisig_script = cluster.g_transaction.build_multisig_script(
            script_name=temp_template,
            script_type_arg=clusterlib.MultiSigTypeArgs.ALL,
            payment_vkey_files=payment_vkey_files,
            slot=10,
            slot_type_arg=clusterlib.MultiSlotTypeArgs.AFTER,
        )

        tx_files = clusterlib.TxFiles(
            signing_key_files=[payment_addrs[0].skey_file],
            auxiliary_script_files=[multisig_script],
        )

        with pytest.raises((clusterlib.CLIError, submit_api.SubmitApiError)) as excinfo:
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=temp_template,
                src_address=payment_addrs[0].address,
                submit_method=submit_method,
                tx_files=tx_files,
            )
        err_str = str(excinfo.value)
        assert (
            "Transaction auxiliary scripts are not supported" in err_str
            or "Auxiliary scripts cannot be used" in err_str  # node <= 1.35.6
        ), err_str
