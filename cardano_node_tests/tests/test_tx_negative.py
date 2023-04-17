"""Negative tests for transactions.

Tests like duplicated transaction, sending funds to wrong addresses, wrong fee, wrong ttl.
"""
import logging
import re
import string
import time
from pathlib import Path
from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

ADDR_ALPHABET = list(f"{string.ascii_lowercase}{string.digits}")


@pytest.mark.testnets
@pytest.mark.smoke
class TestNegative:
    """Transaction tests that are expected to fail."""

    # pylint: disable=too-many-public-methods

    @pytest.fixture(scope="class")
    def skip_on_last_era(self) -> None:
        if VERSIONS.cluster_era == VERSIONS.LAST_KNOWN_ERA:
            pytest.skip(
                f"doesn't run with the latest cluster era ({VERSIONS.cluster_era_name})",
            )

    @pytest.fixture(scope="class")
    def skip_unknown_last_era(self) -> None:
        last_known_era_name = VERSIONS.MAP[VERSIONS.LAST_KNOWN_ERA]
        if not clusterlib_utils.cli_has(f"transaction build-raw --{last_known_era_name}-era"):
            pytest.skip(
                f"`transaction build-raw --{last_known_era_name}-era` command is not available"
            )

    @pytest.fixture
    def cluster_wrong_tx_era(
        self,
        skip_on_last_era: None,  # noqa: ARG002
        skip_unknown_last_era: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,  # noqa: ARG002
    ) -> clusterlib.ClusterLib:
        # pylint: disable=unused-argument
        # the `cluster` argument (representing the `cluster` fixture) needs to be present
        # in order to have an actual cluster instance assigned at the time this fixture
        # is executed
        return cluster_nodes.get_cluster_type().get_cluster_obj(
            tx_era=VERSIONS.MAP[VERSIONS.cluster_era + 1]
        )

    @pytest.fixture
    def pool_users(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> List[clusterlib.PoolUser]:
        """Create pool users."""
        with cluster_manager.cache_fixture() as fixture_cache:
            if fixture_cache.value:
                return fixture_cache.value  # type: ignore

            created_users = clusterlib_utils.create_pool_users(
                cluster_obj=cluster,
                name_template=f"test_negative_ci{cluster_manager.cluster_instance_num}",
                no_of_addr=3,
            )
            fixture_cache.value = created_users

        # fund source addresses
        clusterlib_utils.fund_from_faucet(
            *created_users,
            cluster_obj=cluster,
            faucet_data=cluster_manager.cache.addrs_data["user1"],
        )

        return created_users

    def _send_funds_to_invalid_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
        temp_template: str,
        use_build_cmd=False,
    ):
        """Send funds from payment address to invalid address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=addr, amount=1_000_000)]

        # it should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster_obj.g_transaction.build_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name=f"{temp_template}_to_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.g_transaction.build_raw_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name=f"{temp_template}_to_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee=0,
                )
        exc_val = str(excinfo.value)
        # TODO: better match
        assert "invalid address" in exc_val or "An error occurred" in exc_val, exc_val

    def _send_funds_from_invalid_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
        temp_template: str,
        use_build_cmd=False,
    ):
        """Send funds from invalid payment address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        # it should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster_obj.g_transaction.build_tx(
                    src_address=addr,
                    tx_name=f"{temp_template}_from_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.g_transaction.build_raw_tx(
                    src_address=addr,
                    tx_name=f"{temp_template}_from_invalid",
                    txouts=destinations,
                    tx_files=tx_files,
                    fee=0,
                )
        assert "invalid address" in str(excinfo.value)

    def _send_funds_invalid_change_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
        temp_template: str,
    ):
        """Send funds with invalid change address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        # it should NOT be possible to build a transaction using an invalid change address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.build_tx(
                src_address=pool_users[0].payment.address,
                tx_name=f"{temp_template}_invalid_change",
                txouts=destinations,
                change_address=addr,
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        assert "invalid address" in str(excinfo.value)

    def _send_funds_with_invalid_utxo(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo: clusterlib.UTXOData,
        temp_template: str,
        use_build_cmd=False,
    ) -> str:
        """Send funds with invalid UTxO."""
        src_addr = pool_users[0].payment
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if use_build_cmd:
                cluster_obj.g_transaction.build_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=destinations,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                cluster_obj.g_transaction.send_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=destinations,
                    tx_files=tx_files,
                )
        return str(excinfo.value)

    def _submit_wrong_validity(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        temp_template: str,
        invalid_before: Optional[int] = None,
        invalid_hereafter: Optional[int] = None,
        use_build_cmd=False,
    ) -> Tuple[Optional[int], str, Optional[clusterlib.TxRawOutput]]:
        """Try to build and submit a transaction with wrong validity interval."""
        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]

        exc_val = ""
        slot_no = tx_output = None

        try:
            if use_build_cmd:
                tx_output = cluster_obj.g_transaction.build_tx(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=destinations,
                    tx_files=tx_files,
                    invalid_before=invalid_before,
                    invalid_hereafter=invalid_hereafter,
                    fee_buffer=1_000_000,
                )
            else:
                tx_output = cluster_obj.g_transaction.build_raw_tx(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=destinations,
                    tx_files=tx_files,
                    fee=1_000_000,
                    invalid_before=invalid_before,
                    invalid_hereafter=invalid_hereafter,
                )
        except clusterlib.CLIError as exc:
            exc_val = str(exc)
            if "SLOT must not" not in exc_val:
                raise
            return slot_no, exc_val, tx_output

        # Prior to node 1.36.0, it was possible to build a transaction with invalid interval,
        # but it was not possible to submit it.

        out_file_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should NOT be possible to submit a transaction with negative ttl
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.submit_tx_bare(out_file_signed)
        exc_val = str(excinfo.value)

        assert "ExpiredUTxO" in exc_val or "OutsideValidityIntervalUTxO" in exc_val, exc_val

        slot_no = int(
            re.search(r"ValidityInterval .*SJust \(SlotNo ([0-9]*)", exc_val).group(  # type: ignore
                1
            )
        )

        return slot_no, exc_val, tx_output

    def _get_validity_range(
        self, cluster_obj: clusterlib.ClusterLib, tx_body_file: Path
    ) -> Tuple[Optional[int], Optional[int]]:
        """Get validity range from a transaction body."""
        tx_loaded = tx_view.load_tx_view(cluster_obj=cluster_obj, tx_body_file=tx_body_file)

        validity_range = tx_loaded.get("validity range") or {}

        loaded_invalid_before = validity_range.get("lower bound")
        loaded_invalid_hereafter = validity_range.get("upper bound") or validity_range.get(
            "time to live"
        )

        return loaded_invalid_before, loaded_invalid_hereafter

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_past_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send a transaction with ttl in the past.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"
        self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_hereafter=cluster.g_query.get_slot_no() - 1,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @common.PARAM_USE_BUILD_CMD
    def test_before_negative_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send a transaction with negative `invalid_before` and check for int overflow.

        Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        # Negative values overflow to positive `common.MAX_UINT64`.
        # Valid values are <= `common.MAX_INT64`.
        # So in order for submit to fail, we must overflow by value < `common.MAX_INT64`.
        # E.g. '-5' will become `common.MAX_UINT64 - 5`.
        before_value = -5

        slot_no, err_str, tx_output = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=before_value,
            use_build_cmd=use_build_cmd,
        )

        # In node versions >= 1.36.0 we are checking error from
        # `cardano-cli transaction build/build-raw`
        if "SLOT must not be less than" in err_str:
            return

        # In node versions < 1.36.0 we were checking error from `cardano-cli transaction submit`
        assert slot_no is not None
        assert tx_output is not None

        invalid_before, __ = self._get_validity_range(
            cluster_obj=cluster, tx_body_file=tx_output.out_file
        )
        assert invalid_before == slot_no, f"SlotNo: {slot_no}, `invalid_before`: {invalid_before}"

        if slot_no > 0:
            pytest.xfail("UINT64 overflow, see node issue #4863")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @common.PARAM_USE_BUILD_CMD
    def test_before_positive_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send a transaction with `invalid_before` > `MAX_UINT64`.

        Check for int overflow. Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        # Valid values are <= `common.MAX_INT64`, and overflow happens for
        # values > `common.MAX_UINT64`.
        # So in order for submit to fail, we must overflow by value > `common.MAX_INT64`.
        # E.g. `common.MAX_UINT64 + common.MAX_INT64 + 5` will become `common.MAX_INT64 + 5 - 1`.
        before_value = common.MAX_INT64 + 5
        over_before_value = common.MAX_UINT64 + before_value

        slot_no, err_str, tx_output = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=over_before_value,
            use_build_cmd=use_build_cmd,
        )

        # In node versions >= 1.36.0 we are checking error from
        # `cardano-cli transaction build/build-raw`
        if "SLOT must not greater than" in err_str:
            return

        # In node versions < 1.36.0 we were checking error from `cardano-cli transaction submit`
        assert slot_no is not None
        assert tx_output is not None

        invalid_before, __ = self._get_validity_range(
            cluster_obj=cluster, tx_body_file=tx_output.out_file
        )
        assert invalid_before == slot_no, f"SlotNo: {slot_no}, `invalid_before`: {invalid_before}"

        if slot_no == before_value - 1:
            pytest.xfail("UINT64 overflow, see node issue #4863")

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @common.PARAM_USE_BUILD_CMD
    def test_before_too_high(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send a transaction with `invalid_before` > `MAX_INT64`.

        Expect failure.
        """
        __: Any  # mypy workaround
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        # valid values are <= `common.MAX_INT64`
        before_value = common.MAX_INT64 + 5

        __, err_str, *__ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=before_value,
            use_build_cmd=use_build_cmd,
        )

        assert "(OutsideValidityIntervalUTxO" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @hypothesis.given(before_value=st.integers(min_value=1, max_value=common.MAX_INT64))
    @hypothesis.example(before_value=1)
    @hypothesis.example(before_value=common.MAX_INT64)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_USE_BUILD_CMD
    def test_pbt_before_negative_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        before_value: int,
        use_build_cmd: bool,
    ):
        """Try to send a transaction with negative `invalid_before` and check for int overflow.

        Expect failure.
        """
        temp_template = (
            f"{common.get_test_id(cluster)}_{before_value}_{use_build_cmd}_"
            f"{common.unique_time_str()}"
        )

        slot_no, err_str, __ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=-before_value,
            use_build_cmd=use_build_cmd,
        )

        # In node versions >= 1.36.0 we are checking error from
        # `cardano-cli transaction build/build-raw`
        if "SLOT must not be less than" in err_str:
            return

        # In node versions < 1.36.0 we were checking error from `cardano-cli transaction submit`
        assert slot_no is not None

        # we cannot XFAIL in PBT, so we'll pass on the xfail condition and re-test using
        # a regular test `test_before_negative_overflow`
        assert slot_no > 0, f"SlotNo: {slot_no}, `before_value`: {before_value}"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @hypothesis.given(
        before_value=st.integers(min_value=common.MAX_INT64 + 1, max_value=common.MAX_UINT64)
    )
    @hypothesis.example(before_value=common.MAX_INT64 + 1)
    @hypothesis.example(before_value=common.MAX_UINT64)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_USE_BUILD_CMD
    def test_pbt_before_positive_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        before_value: int,
        use_build_cmd: bool,
    ):
        """Try to send a transaction with `invalid_before` > `MAX_UINT64`.

        Check for int overflow. Expect failure.
        """
        temp_template = (
            f"{common.get_test_id(cluster)}_{before_value}_{use_build_cmd}_"
            f"{common.unique_time_str()}"
        )

        over_before_value = common.MAX_UINT64 + before_value
        slot_no, err_str, __ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=over_before_value,
            use_build_cmd=use_build_cmd,
        )

        # In node versions >= 1.36.0 we are checking error from
        # `cardano-cli transaction build/build-raw`
        if "SLOT must not greater than" in err_str:
            return

        # In node versions < 1.36.0 we were checking error from `cardano-cli transaction submit`
        assert slot_no is not None

        # we cannot XFAIL in PBT, so we'll pass on the xfail condition and re-test using
        # a regular test `test_before_positive_overflow`
        assert slot_no == before_value - 1, f"SlotNo: {slot_no}, `before_value`: {before_value}"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @hypothesis.given(
        before_value=st.integers(min_value=common.MAX_INT64 + 1, max_value=common.MAX_UINT64)
    )
    @hypothesis.example(before_value=common.MAX_INT64 + 1)
    @hypothesis.example(before_value=common.MAX_UINT64)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_USE_BUILD_CMD
    def test_pbt_before_too_high(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        before_value: int,
        use_build_cmd: bool,
    ):
        """Try to send a transaction with `invalid_before` > `MAX_INT64`.

        Expect failure.
        """
        temp_template = (
            f"{common.get_test_id(cluster)}_{before_value}_{use_build_cmd}_"
            f"{common.unique_time_str()}"
        )

        slot_no, err_str, __ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=before_value,
            use_build_cmd=use_build_cmd,
        )

        assert "(OutsideValidityIntervalUTxO" in err_str, err_str
        assert slot_no == before_value, f"SlotNo: {slot_no}, `before_value`: {before_value}"

    @allure.link(helpers.get_vcs_link())
    def test_duplicated_tx(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send an identical transaction twice.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # submit a transaction for the first time
        cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount
        ), f"Incorrect balance for destination address `{dst_address}`"

        # it should NOT be possible to submit a transaction twice
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(out_file_signed)
        assert "ValueNotConservedUTxO" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_wrong_network_magic(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        worker_id: str,
    ):
        """Try to submit a TX with wrong network magic.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=destinations,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # it should NOT be possible to submit a transaction with incorrect network magic
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="HandshakeError",
            ignore_file_id=worker_id,
        )
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="NodeToClientVersionData",
            ignore_file_id=worker_id,
        )
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "submit",
                    "--testnet-magic",
                    str(cluster.network_magic + 100),
                    "--tx-file",
                    str(out_file_signed),
                    f"--{cluster.protocol}-mode",
                ]
            )
        assert "HandshakeError" in str(excinfo.value)

        # wait a bit so there's some time for error messages to appear in log file
        time.sleep(1 if cluster.network_magic == configuration.NETWORK_MAGIC_LOCAL else 5)

    @allure.link(helpers.get_vcs_link())
    def test_wrong_signing_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send a transaction signed with wrong signing key.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        # use wrong signing key
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[1].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_500_000)]

        # it should NOT be possible to submit a transaction with wrong signing key
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
        assert "MissingVKeyWitnessesUTXOW" in str(excinfo.value)

    @allure.link(helpers.get_vcs_link())
    def test_wrong_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_wrong_tx_era: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to send a transaction using TX era > network (cluster) era.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        destinations = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_500_000)]

        # it should NOT be possible to submit a transaction when TX era > network era
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_wrong_tx_era.g_transaction.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=destinations,
                tx_files=tx_files,
            )
        err_str = str(excinfo.value)
        assert (
            "The era of the node and the tx do not match" in err_str
            or "HardForkEncoderDisabledEra" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_send_funds_to_reward_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send funds from payment address to stake address.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        addr = pool_users[0].stake.address
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_send_funds_to_utxo_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to send funds from payment address to UTxO address.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        dst_addr = pool_users[1].payment.address
        utxo_addr = cluster.g_query.get_utxo(address=dst_addr)[0].utxo_hash
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=utxo_addr,
            temp_template=temp_template,
            use_build_cmd=use_build_cmd,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_send_funds_to_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to non-existent address (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_build_send_funds_to_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to non-existent address (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            use_build_cmd=True,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_send_funds_to_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid length.

        Expect failure. Property-based test.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_build_send_funds_to_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid length.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure. Property-based test.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            use_build_cmd=True,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_send_funds_to_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid characters.

        Expect failure. Property-based test.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_build_send_funds_to_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from payment address to address with invalid characters.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure. Property-based test.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            use_build_cmd=True,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_send_funds_from_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from invalid address (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_build_send_funds_from_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from non-existent address (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            use_build_cmd=True,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_send_funds_from_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid length (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_build_send_funds_from_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid length (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            use_build_cmd=True,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_send_funds_from_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid characters (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_build_send_funds_from_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds from address with invalid characters (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            use_build_cmd=True,
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    def test_build_send_funds_invalid_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using invalid change address (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    def test_build_send_funds_invalid_chars_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using change address with invalid characters (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    def test_build_send_funds_invalid_length_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using change address with invalid length (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_nonexistent_utxo_ix(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to use nonexistent UTxO TxIx as an input.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_ix=5)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            use_build_cmd=use_build_cmd,
        )
        if use_build_cmd:
            assert (
                "The UTxO is empty" in err
                # in 1.35.3 and older
                or "The following tx input(s) were not present in the UTxO" in err
            ), err
        else:
            assert "BadInputsUTxO" in err, err

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_USE_BUILD_CMD
    def test_nonexistent_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        use_build_cmd: bool,
    ):
        """Try to use nonexistent UTxO hash as an input.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{use_build_cmd}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        new_hash = f"{utxo.utxo_hash[:-4]}fd42"
        utxo_copy = utxo._replace(utxo_hash=new_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            use_build_cmd=use_build_cmd,
        )
        if use_build_cmd:
            assert (
                "The UTxO is empty" in err
                # in 1.35.3 and older
                or "The following tx input(s) were not present in the UTxO" in err
            ), err
        else:
            assert "BadInputsUTxO" in err, err

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(utxo_hash=st.text(alphabet=ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings(300)
    def test_invalid_length_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo_hash: str,
    ):
        """Try to use invalid UTxO hash as an input (property-based test).

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_hash=utxo_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster, pool_users=pool_users, utxo=utxo_copy, temp_template=temp_template
        )
        assert (
            "Incorrect transaction id format" in err
            or "Failed reading" in err
            or "expecting transaction id (hexadecimal)" in err
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(utxo_hash=st.text(alphabet=ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings(300)
    def test_build_invalid_length_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
        utxo_hash: str,
    ):
        """Try to use invalid UTxO hash as an input (property-based test).

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = utxo._replace(utxo_hash=utxo_hash)
        err = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            use_build_cmd=True,
        )
        assert (
            "Incorrect transaction id format" in err
            or "Failed reading" in err
            or "expecting transaction id (hexadecimal)" in err
        )

    @allure.link(helpers.get_vcs_link())
    def test_missing_fee(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--fee` parameter.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--invalid-hereafter",
                    str(tx_raw_output.invalid_hereafter),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                ]
            )
        err_str = str(excinfo.value)

        if "Transaction _ fee not supported in" in err_str:
            pytest.xfail("See node issue #4591 - Transaction _ fee not supported")

        assert (
            "fee must be specified" in err_str
            or "Implicit transaction fee not supported" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.SHELLEY,
        reason="runs only with Shelley TX",
    )
    def test_missing_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a Shelley era TX with a missing `--ttl` (`--invalid-hereafter`) parameter.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--fee",
                    str(tx_raw_output.fee),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        err_str = str(excinfo.value)

        if "Transaction validity upper bound not supported" in err_str:
            pytest.xfail("See node issue #4591 - Transaction validity upper bound not supported")

        assert (
            "TTL must be specified" in err_str
            or "Transaction validity upper bound must be specified" in err_str
        ), err_str

    @allure.link(helpers.get_vcs_link())
    def test_missing_tx_in(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--tx-in` parameter.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
        )
        __, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build-raw",
                    "--invalid-hereafter",
                    str(tx_raw_output.invalid_hereafter),
                    "--fee",
                    str(tx_raw_output.fee),
                    "--out-file",
                    str(tx_raw_output.out_file),
                    *helpers.prepend_flag("--tx-out", txouts),
                ]
            )
        assert re.search(r"Missing: *\(--tx-in TX-IN", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.SHELLEY,
        reason="runs only with Shelley TX",
    )
    def test_lower_bound_not_supported(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a Shelley era TX with an `--invalid-before` argument.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        src_addr = pool_users[0].payment
        dst_addr = pool_users[1].payment

        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts = [clusterlib.TxOut(address=dst_addr.address, amount=2_000_000)]

        # TODO: add test version that uses `cardano-cli transaction build` command once node
        # issue #4286 is fixed
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.build_raw_tx(
                src_address=src_addr.address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
                fee=1_000,
                invalid_before=10,  # the unsupported argument
            )
        err_str = str(excinfo.value)
        assert (
            "validity lower bound not supported" in err_str
            or "validity lower bound cannot be used" in err_str  # node <= 1.35.6
        ), err_str

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_missing_tx_in(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--tx-in` parameter.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        __, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--change-address",
                    str(pool_users[0].payment.address),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        assert re.search(r"Missing: *\(--tx-in TX-IN", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_missing_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with a missing `--change-address` parameter.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        assert re.search(r"Missing:.* --change-address ADDRESS", str(excinfo.value))

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    def test_build_multiple_change_addresses(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: List[clusterlib.PoolUser],
    ):
        """Try to build a transaction with multiple `--change-address` parameters.

        Uses `cardano-cli transaction build` command for building the transactions.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        tx_raw_output = tx_common.get_raw_tx_values(
            cluster_obj=cluster,
            tx_name=temp_template,
            src_record=pool_users[0].payment,
            dst_record=pool_users[1].payment,
            for_build_command=True,
        )
        txins, txouts = tx_common.get_txins_txouts(
            txins=tx_raw_output.txins, txouts=tx_raw_output.txouts
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "build",
                    "--out-file",
                    str(tx_raw_output.out_file),
                    "--testnet-magic",
                    str(cluster.network_magic),
                    *helpers.prepend_flag("--tx-in", txins),
                    *helpers.prepend_flag("--tx-out", txouts),
                    *helpers.prepend_flag(
                        "--change-address",
                        [pool_users[0].payment.address, pool_users[2].payment.address],
                    ),
                    *cluster.g_transaction.tx_era_arg,
                ]
            )
        assert re.search(r"Invalid option.*--change-address", str(excinfo.value))
