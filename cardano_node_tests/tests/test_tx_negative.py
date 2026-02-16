"""Negative tests for transactions.

Tests like duplicated transaction, sending funds to wrong addresses, wrong fee, wrong ttl.
"""

import dataclasses
import logging
import pathlib as pl
import re
import time
import typing as tp

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import issues
from cardano_node_tests.tests import tx_common
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import logfiles
from cardano_node_tests.utils import tx_view
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)


class TestNegative:
    """Transaction tests that are expected to fail."""

    @pytest.fixture(scope="class")
    def skip_on_last_era(self) -> None:
        last_known_era_name = VERSIONS.MAP[VERSIONS.LAST_KNOWN_PROTOCOL_VERSION]
        if VERSIONS.cluster_era_name == last_known_era_name:
            pytest.skip(
                f"doesn't run with the latest cluster era ({VERSIONS.cluster_era_name})",
            )

    @pytest.fixture(scope="class")
    def skip_unknown_last_era(self) -> None:
        last_known_era_name = VERSIONS.MAP[VERSIONS.LAST_KNOWN_PROTOCOL_VERSION]
        if not clusterlib_utils.cli_has(last_known_era_name):
            pytest.skip(f"`{last_known_era_name} transaction build-raw` command is not available")

    @pytest.fixture
    def cluster_wrong_tx_era(
        self,
        skip_on_last_era: None,  # noqa: ARG002
        skip_unknown_last_era: None,  # noqa: ARG002
        cluster: clusterlib.ClusterLib,  # noqa: ARG002
    ) -> clusterlib.ClusterLib:
        # The `cluster` argument (representing the `cluster` fixture) needs to be present
        # in order to have an actual cluster instance assigned at the time this fixture
        # is executed
        return cluster_nodes.get_cluster_type().get_cluster_obj(
            command_era=VERSIONS.MAP[VERSIONS.cluster_era + 1]
        )

    @pytest.fixture
    def pool_users(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> list[clusterlib.PoolUser]:
        """Create pool users."""
        created_users = common.get_pool_users(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            num=3,
            caching_key=helpers.get_current_line_str(),
        )
        return created_users

    def _send_funds_to_invalid_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        temp_template: str,
        build_method: str,
    ):
        """Send funds from payment address to invalid address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        txouts = [clusterlib.TxOut(address=addr, amount=1_000_000)]

        # It should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            if build_method == clusterlib_utils.BuildMethods.BUILD:
                cluster_obj.g_transaction.build_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name=f"{temp_template}_to_invalid",
                    txouts=txouts,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
                cluster_obj.g_transaction.build_raw_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name=f"{temp_template}_to_invalid",
                    txouts=txouts,
                    tx_files=tx_files,
                    fee=0,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
                cluster_obj.g_transaction.build_estimate_tx(
                    src_address=pool_users[0].payment.address,
                    tx_name=f"{temp_template}_to_invalid",
                    txouts=txouts,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                msg = f"Unsupported build method: {build_method}"
                raise ValueError(msg)
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            # TODO: better match
            assert "invalid address" in exc_value or "An error occurred" in exc_value, exc_value

    def _send_funds_from_invalid_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        temp_template: str,
        build_method: str,
    ):
        """Send funds from invalid payment address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        txouts = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        # It should NOT be possible to build a transaction using an invalid address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            if build_method == clusterlib_utils.BuildMethods.BUILD:
                cluster_obj.g_transaction.build_tx(
                    src_address=addr,
                    tx_name=f"{temp_template}_from_invalid",
                    txouts=txouts,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
                cluster_obj.g_transaction.build_raw_tx(
                    src_address=addr,
                    tx_name=f"{temp_template}_from_invalid",
                    txouts=txouts,
                    tx_files=tx_files,
                    fee=0,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
                cluster_obj.g_transaction.build_estimate_tx(
                    src_address=addr,
                    tx_name=f"{temp_template}_from_invalid",
                    txouts=txouts,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                msg = f"Unsupported build method: {build_method}"
                raise ValueError(msg)
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "invalid address" in exc_value, exc_value

    def _send_funds_invalid_change_address(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        temp_template: str,
    ):
        """Send funds with invalid change address."""
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        txouts = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        # It should NOT be possible to build a transaction using an invalid change address
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.build_tx(
                src_address=pool_users[0].payment.address,
                tx_name=f"{temp_template}_invalid_change",
                txouts=txouts,
                change_address=addr,
                tx_files=tx_files,
                fee_buffer=1_000_000,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "invalid address" in exc_value, exc_value

    def _send_funds_with_invalid_utxo(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        utxo: clusterlib.UTXOData,
        temp_template: str,
        build_method: str,
    ) -> str:
        """Send funds with invalid UTxO."""
        src_addr = pool_users[0].payment
        tx_files = clusterlib.TxFiles(signing_key_files=[src_addr.skey_file])
        txouts = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_000_000)]

        with pytest.raises(clusterlib.CLIError) as excinfo:
            if build_method == clusterlib_utils.BuildMethods.BUILD:
                cluster_obj.g_transaction.build_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=txouts,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
                cluster_obj.g_transaction.send_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=txouts,
                    tx_files=tx_files,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
                cluster_obj.g_transaction.build_estimate_tx(
                    src_address=src_addr.address,
                    tx_name=temp_template,
                    txins=[utxo],
                    txouts=txouts,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                )
            else:
                msg = f"Unsupported build method: {build_method}"
                raise ValueError(msg)
        return str(excinfo.value)

    def _submit_wrong_validity(
        self,
        cluster_obj: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        temp_template: str,
        invalid_before: int | None = None,
        invalid_hereafter: int | None = None,
        build_method: str = clusterlib_utils.BuildMethods.BUILD_RAW,
    ) -> tuple[int | None, str, clusterlib.TxRawOutput | None]:
        """Try to build and submit a transaction with wrong validity interval."""
        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        txouts = [clusterlib.TxOut(address=dst_address, amount=2_000_000)]

        exc_str = ""
        slot_no = tx_output = None

        try:
            if build_method == clusterlib_utils.BuildMethods.BUILD:
                tx_output = cluster_obj.g_transaction.build_tx(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=txouts,
                    tx_files=tx_files,
                    invalid_before=invalid_before,
                    invalid_hereafter=invalid_hereafter,
                    fee_buffer=1_000_000,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
                tx_output = cluster_obj.g_transaction.build_raw_tx(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=txouts,
                    tx_files=tx_files,
                    fee=1_000_000,
                    invalid_before=invalid_before,
                    invalid_hereafter=invalid_hereafter,
                )
            elif build_method == clusterlib_utils.BuildMethods.BUILD_EST:
                tx_output = cluster_obj.g_transaction.build_estimate_tx(
                    src_address=src_address,
                    tx_name=temp_template,
                    txouts=txouts,
                    tx_files=tx_files,
                    fee_buffer=1_000_000,
                    invalid_before=invalid_before,
                    invalid_hereafter=invalid_hereafter,
                )
            else:
                msg = f"Unsupported build method: {build_method}"
                raise ValueError(msg)
        except clusterlib.CLIError as exc:
            exc_str = str(exc)
            if "SLOT must not" not in exc_str:
                raise
            return slot_no, exc_str, tx_output

        # Prior to node 1.36.0, it was possible to build a transaction with invalid interval,
        # but it was not possible to submit it.

        out_file_signed = cluster_obj.g_transaction.sign_tx(
            tx_body_file=tx_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # It should NOT be possible to submit a transaction with negative ttl
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_obj.g_transaction.submit_tx_bare(out_file_signed)
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "ExpiredUTxO" in exc_value or "OutsideValidityIntervalUTxO" in exc_value, (
                exc_value
            )

        slot_no_search = re.search(r"ValidityInterval .*SJust \(SlotNo ([0-9]*)", exc_value)
        if slot_no_search is None:
            err = "Cannot find SlotNo in CLI error output"
            raise RuntimeError(err)
        slot_no = int(slot_no_search.group(1))

        return slot_no, exc_value, tx_output

    def _get_validity_range(
        self, cluster_obj: clusterlib.ClusterLib, tx_body_file: pl.Path
    ) -> tuple[int | None, int | None]:
        """Get validity range from a transaction body."""
        tx_loaded = tx_view.load_tx_view(cluster_obj=cluster_obj, tx_body_file=tx_body_file)

        validity_range = tx_loaded.get("validity range") or {}

        loaded_invalid_before = validity_range.get("lower bound")
        loaded_invalid_hereafter = validity_range.get("upper bound") or validity_range.get(
            "time to live"
        )

        return loaded_invalid_before, loaded_invalid_hereafter

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_past_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to send transaction with TTL (time-to-live) in the past.

        Expect failure.

        Uses parametrized build method (build, build-raw, or build-estimate).

        * Set invalid_hereafter to current slot - 1 (already expired)
        * Attempt to build or submit transaction with past TTL
        * Check that transaction fails with ExpiredUTxO or OutsideValidityIntervalUTxO error
        """
        temp_template = common.get_test_id(cluster)
        self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_hereafter=cluster.g_query.get_slot_no() - 1,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_before_negative_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to send transaction with negative invalid_before causing integer overflow.

        Expect failure.

        Uses parametrized build method (build, build-raw, or build-estimate).
        Negative values overflow to MAX_UINT64, which exceeds valid range.

        * Set invalid_before to -5 (overflows to MAX_UINT64 - 5)
        * Attempt to build or submit transaction with overflowed value
        * Check that transaction fails or builds with clamped value
        * Verify validity range in transaction view output
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

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
            build_method=build_method,
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
            issues.node_4863.finish_test()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_before_positive_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to send transaction with invalid_before exceeding MAX_UINT64 causing overflow.

        Expect failure.

        Uses parametrized build method (build, build-raw, or build-estimate).
        Values > MAX_UINT64 overflow back to valid range.

        * Set invalid_before to MAX_UINT64 + (MAX_INT64 + 5) which overflows
        * Attempt to build or submit transaction with overflowed value
        * Check that transaction fails or builds with clamped value
        * Verify validity range in transaction view output
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

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
            build_method=build_method,
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
            issues.node_4863.finish_test()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_before_too_high(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to send transaction with invalid_before exceeding MAX_INT64.

        Expect failure.

        Uses parametrized build method (build, build-raw, or build-estimate).
        Valid values must be <= MAX_INT64.

        * Set invalid_before to MAX_INT64 + 5 (exceeds valid range)
        * Attempt to build or submit transaction with too-high value
        * Check that transaction fails with OutsideValidityIntervalUTxO error
        """
        __: tp.Any  # mypy workaround
        temp_template = common.get_test_id(cluster)

        # Valid values are <= `common.MAX_INT64`
        before_value = common.MAX_INT64 + 5

        __, err_str, *__ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=before_value,
            build_method=build_method,
        )

        assert err_str, "Expected error but none was raised"
        with common.allow_unstable_error_messages():
            assert "OutsideValidityIntervalUTxO" in err_str, err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.ALLEGRA,
        reason="runs only with Allegra+ TX",
    )
    @hypothesis.given(before_value=st.integers(min_value=1, max_value=common.MAX_INT64))
    @hypothesis.example(before_value=1)
    @hypothesis.example(before_value=common.MAX_INT64)
    @common.hypothesis_settings(max_examples=200)
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_pbt_before_negative_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        before_value: int,
        build_method: str,
    ):
        """Try to send transaction with negative invalid_before (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis with values 1 to MAX_INT64.
        Negative values cause integer overflow.

        * Set invalid_before to negative of parametrized value
        * Attempt to build or submit transaction with negative value
        * Check that transaction fails or slot number is positive after overflow
        """
        temp_template = f"{common.get_test_id(cluster)}_{before_value}_{common.unique_time_str()}"

        slot_no, err_str, __ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=-before_value,
            build_method=build_method,
        )

        # In node versions >= 1.36.0 we are checking error from
        # `cardano-cli transaction build/build-raw`
        if "SLOT must not be less than" in err_str:
            return

        # In node versions < 1.36.0 we were checking error from `cardano-cli transaction submit`
        assert slot_no is not None

        # We cannot XFAIL in PBT, so we'll pass on the xfail condition and re-test using
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
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_pbt_before_positive_overflow(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        before_value: int,
        build_method: str,
    ):
        """Try to send transaction with invalid_before > MAX_UINT64 (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis with values (MAX_INT64+1) to MAX_UINT64.
        Values > MAX_UINT64 cause integer overflow.

        * Set invalid_before to MAX_UINT64 + parametrized value (causes overflow)
        * Attempt to build or submit transaction with overflowed value
        * Check that transaction fails or verifies overflow behavior
        """
        temp_template = f"{common.get_test_id(cluster)}_{before_value}_{common.unique_time_str()}"

        over_before_value = common.MAX_UINT64 + before_value
        slot_no, err_str, __ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=over_before_value,
            build_method=build_method,
        )

        # In node versions >= 1.36.0 we are checking error from
        # `cardano-cli transaction build/build-raw`
        if "SLOT must not greater than" in err_str:
            return

        # In node versions < 1.36.0 we were checking error from `cardano-cli transaction submit`
        assert slot_no is not None

        # We cannot XFAIL in PBT, so we'll pass on the xfail condition and re-test using
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
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_pbt_before_too_high(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        before_value: int,
        build_method: str,
    ):
        """Try to send transaction with invalid_before > MAX_INT64 (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis with values (MAX_INT64+1) to MAX_UINT64.
        Valid values must be <= MAX_INT64.

        * Set invalid_before to parametrized value exceeding MAX_INT64
        * Attempt to build or submit transaction with too-high value
        * Check that transaction fails with OutsideValidityIntervalUTxO error
        * Verify slot number matches expected value
        """
        temp_template = f"{common.get_test_id(cluster)}_{before_value}_{common.unique_time_str()}"

        slot_no, err_str, __ = self._submit_wrong_validity(
            cluster_obj=cluster,
            pool_users=pool_users,
            temp_template=temp_template,
            invalid_before=before_value,
            build_method=build_method,
        )

        assert err_str, "Expected error but none was raised"
        with common.allow_unstable_error_messages():
            assert "OutsideValidityIntervalUTxO" in err_str, err_str

        assert slot_no == before_value, f"SlotNo: {slot_no}, `before_value`: {before_value}"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_duplicated_tx(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to submit an identical transaction twice.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * Build and sign transaction sending 2 ADA from source to destination address
        * Submit transaction successfully the first time
        * Check expected balances for both source and destination addresses
        * Attempt to submit the exact same signed transaction again
        * Check that re-submission fails with inputs already spent error
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # Build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        # Submit a transaction for the first time
        cluster.g_transaction.submit_tx(tx_file=out_file_signed, txins=tx_raw_output.txins)

        out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_raw_output)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos, address=src_address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_raw_output.txins) - tx_raw_output.fee - amount
        ), f"Incorrect balance for source address `{src_address}`"
        assert clusterlib.filter_utxos(utxos=out_utxos, address=dst_address)[0].amount == amount, (
            f"Incorrect balance for destination address `{dst_address}`"
        )

        # It should NOT be possible to submit a transaction twice
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.submit_tx_bare(out_file_signed)
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "All inputs are spent" in exc_value  # In cardano-node >= 10.6.0
                or "(ValueNotConservedUTxO" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_wrong_network_magic(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to submit transaction with incorrect network magic number.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * Build and sign transaction sending 2 ADA from source to destination address
        * Add ignore rules for expected handshake errors in logs
        * Attempt to submit transaction with network magic + 100
        * Check that submission fails with HandshakeError
        """
        temp_template = common.get_test_id(cluster)
        amount = 2_000_000

        src_address = pool_users[0].payment.address
        dst_address = pool_users[1].payment.address

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        txouts = [clusterlib.TxOut(address=dst_address, amount=amount)]

        # Build and sign a transaction
        fee = cluster.g_transaction.calculate_tx_fee(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
        )
        tx_raw_output = cluster.g_transaction.build_raw_tx(
            src_address=src_address,
            tx_name=temp_template,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
        )
        out_file_signed = cluster.g_transaction.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            signing_key_files=tx_files.signing_key_files,
            tx_name=temp_template,
        )

        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="HandshakeError",
            ignore_file_id="wrong_magic",
            # Ignore errors for next 20 seconds
            skip_after=time.time() + 20,
        )
        logfiles.add_ignore_rule(
            files_glob="*.stdout",
            regex="NodeToClientVersionData",
            ignore_file_id="wrong_magic",
            # Ignore errors for next 20 seconds
            skip_after=time.time() + 20,
        )

        # It should NOT be possible to submit a transaction with incorrect network magic
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
                [
                    "transaction",
                    "submit",
                    "--testnet-magic",
                    str(cluster.network_magic + 100),
                    "--tx-file",
                    str(out_file_signed),
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "HandshakeError" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_wrong_signing_key(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to send transaction signed with incorrect signing key.

        Expect failure.

        Uses `cardano-cli transaction send` command.

        * Prepare transaction from pool_users[0] to pool_users[1] with 1.5 ADA
        * Sign transaction with pool_users[1] signing key instead of pool_users[0]
        * Attempt to build and submit transaction with wrong signing key
        * Check that transaction fails with MissingVKeyWitnessesUTXOW error
        """
        temp_template = common.get_test_id(cluster)

        # Use wrong signing key
        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[1].payment.skey_file])
        txouts = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_500_000)]

        # It should NOT be possible to submit a transaction with wrong signing key
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_transaction.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "MissingVKeyWitnessesUTXOW" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_wrong_tx_era(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_wrong_tx_era: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to send transaction using TX era higher than network era.

        Expect failure.

        Uses `cardano-cli transaction send` command with future era.

        * Prepare transaction from pool_users[0] to pool_users[1] with 1.5 ADA
        * Use cluster instance configured for era > current network era
        * Attempt to submit transaction built for future era
        * Check that transaction fails with era mismatch error
        """
        temp_template = common.get_test_id(cluster)

        tx_files = clusterlib.TxFiles(signing_key_files=[pool_users[0].payment.skey_file])
        txouts = [clusterlib.TxOut(address=pool_users[1].payment.address, amount=1_500_000)]

        # It should NOT be possible to submit a transaction when TX era > network era
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_wrong_tx_era.g_transaction.send_tx(
                src_address=pool_users[0].payment.address,
                tx_name=temp_template,
                txouts=txouts,
                tx_files=tx_files,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "The era of the node and the tx do not match" in exc_value
                or "HardForkEncoderDisabledEra" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_to_reward_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to send funds from payment address to stake (reward) address.

        Expect failure.

        Uses parametrized build method (build, build-raw, or build-estimate).

        * Prepare transaction with stake address as destination (invalid)
        * Attempt to build transaction with 1 ADA to stake address
        * Check that transaction building fails with invalid address error
        """
        temp_template = common.get_test_id(cluster)

        addr = pool_users[0].stake.address
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_to_utxo_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to send funds from payment address to UTxO hash (not an address).

        Expect failure.

        Uses parametrized build method (build, build-raw, or build-estimate).

        * Get UTxO hash from payment address query
        * Prepare transaction with UTxO hash as destination (invalid)
        * Attempt to build transaction with 1 ADA to UTxO hash
        * Check that transaction building fails with invalid address error
        """
        temp_template = common.get_test_id(cluster)

        dst_addr = pool_users[1].payment.address
        utxo_addr = cluster.g_query.get_utxo(address=dst_addr)[0].utxo_hash
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=utxo_addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD
    @hypothesis.given(addr=st.text(alphabet=common.ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_to_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        build_method: str,
    ):
        """Try to send funds to randomly generated invalid address (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis to generate random 98-char addresses.

        * Generate random address string with valid characters but invalid format
        * Attempt to build transaction with 1 ADA to invalid address
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=common.ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_to_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        build_method: str,
    ):
        """Try to send funds to address with invalid length (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis to generate random 50-250 char addresses.

        * Generate random address string with incorrect length
        * Attempt to build transaction with 1 ADA to wrong-length address
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_to_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        build_method: str,
    ):
        """Try to send funds to address with invalid characters (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis to generate random 98-char strings.

        * Generate random address string with invalid characters
        * Attempt to build transaction with 1 ADA to address with bad characters
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_to_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=common.ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_from_invalid_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        build_method: str,
    ):
        """Try to send funds from randomly generated invalid address (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis to generate random 98-char addresses.

        * Generate random address string with valid characters but invalid format
        * Attempt to build transaction from invalid source address to valid destination
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(addr=st.text(alphabet=common.ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_from_invalid_length_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        build_method: str,
    ):
        """Try to send funds from address with invalid length (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis to generate random 50-250 char addresses.

        * Generate random address string with incorrect length
        * Attempt to build transaction from wrong-length source address to valid destination
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(
        addr=st.text(alphabet=st.characters(blacklist_categories=["C"]), min_size=98, max_size=98)
    )
    @common.hypothesis_settings(300)
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_send_funds_from_invalid_chars_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
        build_method: str,
    ):
        """Try to send funds from address with invalid characters (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis to generate random 98-char strings.

        * Generate random address string with invalid characters
        * Attempt to build transaction from address with bad characters to valid destination
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_from_invalid_address(
            cluster_obj=cluster,
            pool_users=pool_users,
            addr=addr,
            temp_template=temp_template,
            build_method=build_method,
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=common.ADDR_ALPHABET, min_size=98, max_size=98))
    @common.hypothesis_settings(300)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_send_funds_invalid_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using invalid change address (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.
        Uses hypothesis to generate random 98-char addresses.

        * Generate random address string with valid characters but invalid format
        * Attempt to build transaction with invalid change address
        * Check that transaction building fails with invalid address error
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
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_send_funds_invalid_chars_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using change address with invalid characters (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.
        Uses hypothesis to generate random 98-char strings.

        * Generate random address string with invalid characters
        * Attempt to build transaction with change address containing bad characters
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(addr=st.text(alphabet=common.ADDR_ALPHABET, min_size=50, max_size=250))
    @common.hypothesis_settings(300)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_send_funds_invalid_length_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        addr: str,
    ):
        """Try to send funds using change address with invalid length (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.
        Uses hypothesis to generate random 50-250 char addresses.

        * Generate random address string with incorrect length
        * Attempt to build transaction with wrong-length change address
        * Check that transaction building fails with invalid address error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        addr = f"addr_test1{addr}"
        self._send_funds_invalid_change_address(
            cluster_obj=cluster, pool_users=pool_users, addr=addr, temp_template=temp_template
        )

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_nonexistent_utxo_ix(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to use nonexistent UTxO transaction index (TxIx) as input.

        Expect failure.

        Uses parametrized build method (build or build-raw, not build-estimate).

        * Get valid UTxO from payment address
        * Modify UTxO index to nonexistent value (5)
        * Attempt to build or submit transaction using invalid UTxO index
        * Check that transaction fails with empty UTxO or BadInputsUTxO error
        """
        temp_template = common.get_test_id(cluster)

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = dataclasses.replace(utxo, utxo_ix=5)
        err_str = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            build_method=build_method,
        )
        assert err_str, "Expected error but none was raised"

        if build_method in (
            clusterlib_utils.BuildMethods.BUILD,
            clusterlib_utils.BuildMethods.BUILD_EST,
        ):
            with common.allow_unstable_error_messages():
                assert (
                    "The UTxO is empty" in err_str
                    or "The following tx input(s) were not present in the UTxO" in err_str
                ), err_str
        elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
            with common.allow_unstable_error_messages():
                assert (
                    "All inputs are spent" in err_str  # In cardano-node >= 10.6.0
                    or "BadInputsUTxO" in err_str
                ), err_str
        else:
            msg = f"Unsupported build method: {build_method}"
            raise ValueError(msg)

    @allure.link(helpers.get_vcs_link())
    @common.PARAM_BUILD_METHOD_NO_EST
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_nonexistent_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        build_method: str,
    ):
        """Try to use nonexistent UTxO hash as transaction input.

        Expect failure.

        Uses parametrized build method (build or build-raw, not build-estimate).

        * Get valid UTxO from payment address
        * Modify last 4 characters of UTxO hash to create nonexistent hash
        * Attempt to build or submit transaction using invalid UTxO hash
        * Check that transaction fails with empty UTxO or BadInputsUTxO error
        """
        temp_template = common.get_test_id(cluster)

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        new_hash = f"{utxo.utxo_hash[:-4]}fd42"
        utxo_copy = dataclasses.replace(utxo, utxo_hash=new_hash)
        err_str = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            build_method=build_method,
        )
        assert err_str, "Expected error but none was raised"

        if build_method in (
            clusterlib_utils.BuildMethods.BUILD,
            clusterlib_utils.BuildMethods.BUILD_EST,
        ):
            with common.allow_unstable_error_messages():
                assert (
                    "The UTxO is empty" in err_str
                    or "The following tx input(s) were not present in the UTxO" in err_str
                ), err_str
        elif build_method == clusterlib_utils.BuildMethods.BUILD_RAW:
            with common.allow_unstable_error_messages():
                assert (
                    "All inputs are spent" in err_str  # In cardano-node >= 10.6.0
                    or "BadInputsUTxO" in err_str
                ), err_str
        else:
            msg = f"Unsupported build method: {build_method}"
            raise ValueError(msg)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(utxo_hash=st.text(alphabet=common.ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings(300)
    @common.PARAM_BUILD_METHOD
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_invalid_length_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        utxo_hash: str,
        build_method: str,
    ):
        """Try to use UTxO hash with invalid length as input (property-based test).

        Expect failure.

        Uses parametrized build method and hypothesis to generate random 10-550 char hashes.

        * Generate random UTxO hash string with incorrect length
        * Attempt to build or submit transaction using wrong-length UTxO hash
        * Check that transaction fails with deserialization or format error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = dataclasses.replace(utxo, utxo_hash=utxo_hash)
        err_str = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            build_method=build_method,
        )

        assert err_str, "Expected error but none was raised"
        with common.allow_unstable_error_messages():
            assert (
                "Failed to deserialise" in err_str  # With node 10.5.0+
                or "Incorrect transaction id format" in err_str
                or "Failed reading" in err_str
                or "expecting transaction id (hexadecimal)" in err_str
            ), err_str

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @hypothesis.given(utxo_hash=st.text(alphabet=common.ADDR_ALPHABET, min_size=10, max_size=550))
    @common.hypothesis_settings(300)
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_invalid_length_utxo_hash(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
        utxo_hash: str,
    ):
        """Try to use UTxO hash with invalid length as input (property-based test).

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.
        Uses hypothesis to generate random 10-550 char hashes.

        * Generate random UTxO hash string with incorrect length
        * Attempt to build transaction using wrong-length UTxO hash
        * Check that transaction building fails with deserialization or format error
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"

        utxo = cluster.g_query.get_utxo(address=pool_users[0].payment.address)[0]
        utxo_copy = dataclasses.replace(utxo, utxo_hash=utxo_hash)
        err_str = self._send_funds_with_invalid_utxo(
            cluster_obj=cluster,
            pool_users=pool_users,
            utxo=utxo_copy,
            temp_template=temp_template,
            build_method=clusterlib_utils.BuildMethods.BUILD,
        )

        assert err_str, "Expected error but none was raised"
        with common.allow_unstable_error_messages():
            assert (
                "Failed to deserialise" in err_str  # With node 10.5.0+
                or "Incorrect transaction id format" in err_str
                or "Failed reading" in err_str
                or "expecting transaction id (hexadecimal)" in err_str
            ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_missing_fee(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to build transaction with missing `--fee` parameter.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * Prepare transaction inputs and outputs
        * Attempt to build transaction using `build-raw` without --fee parameter
        * Check that transaction building fails with missing fee error
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

        err_str = ""
        try:
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
        except clusterlib.CLIError as exc:
            err_str = str(exc)
        else:
            if VERSIONS.node < version.parse("8.12.0"):
                issues.cli_768.finish_test()
            else:
                issues.cli_796.finish_test()

        assert err_str, "Expected error but none was raised"

        if "Transaction _ fee not supported in" in err_str:
            issues.node_4591.finish_test()

        with common.allow_unstable_error_messages():
            assert (
                "fee must be specified" in err_str
                or "Implicit transaction fee not supported" in err_str
                or "Missing: --fee LOVELACE" in err_str  # node >= 8.12.0
            ), err_str

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.SHELLEY or VERSIONS.node >= version.parse("8.7.0"),
        reason="runs only with Shelley TX on node < 8.7.0",
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_missing_ttl(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to build Shelley era transaction with missing `--ttl` parameter.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.
        Only runs with Shelley TX era on node < 8.7.0.

        * Prepare transaction inputs and outputs
        * Attempt to build Shelley transaction without --invalid-hereafter parameter
        * Check that transaction building fails with missing TTL error
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
                ]
            )
        exc_value = str(excinfo.value)
        if "Transaction validity upper bound not supported" in exc_value:
            issues.node_4591.finish_test()

        with common.allow_unstable_error_messages():
            assert (
                "TTL must be specified" in exc_value
                or "Transaction validity upper bound must be specified" in exc_value
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_missing_tx_in(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to build transaction with missing `--tx-in` parameter.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.

        * Prepare transaction outputs only (no inputs)
        * Attempt to build transaction using `build-raw` without --tx-in parameter
        * Check that transaction building fails with missing tx-in error
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
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert re.search(r"Missing: *\(--tx-in TX[_-]IN", exc_value), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.skipif(
        VERSIONS.transaction_era != VERSIONS.SHELLEY,
        reason="runs only with Shelley TX",
    )
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_lower_bound_not_supported(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to build Shelley era transaction with unsupported `--invalid-before`.

        Expect failure.

        Uses `cardano-cli transaction build-raw` command.
        Only runs with Shelley TX era (validity lower bound not supported).

        * Prepare transaction from source to destination with 2 ADA
        * Attempt to build transaction with invalid_before=10 (unsupported in Shelley)
        * Check that transaction building fails with lower bound not supported error
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
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert (
                "validity lower bound not supported" in exc_value
                or "validity lower bound cannot be used" in exc_value  # node <= 1.35.6
            ), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_missing_tx_in(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to build transaction with missing `--tx-in` parameter.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Prepare transaction outputs only (no inputs)
        * Attempt to build transaction using `build` without --tx-in parameter
        * Check that transaction building fails with missing tx-in error
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
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert re.search(r"Missing: *\(--tx-in TX[_-]IN", exc_value), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_missing_change_address(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to build transaction with missing `--change-address` parameter.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Prepare transaction inputs and outputs
        * Attempt to build transaction using `build` without --change-address parameter
        * Check that transaction building fails with missing change address error
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
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert re.search(r"Missing:.* --change-address ADDRESS", exc_value), exc_value

    @allure.link(helpers.get_vcs_link())
    @common.SKIPIF_BUILD_UNUSABLE
    @pytest.mark.smoke
    @pytest.mark.testnets
    def test_build_multiple_change_addresses(
        self,
        cluster: clusterlib.ClusterLib,
        pool_users: list[clusterlib.PoolUser],
    ):
        """Try to build transaction with multiple `--change-address` parameters.

        Expect failure.

        Uses `cardano-cli transaction build` command for building the transactions.

        * Prepare transaction inputs and outputs
        * Attempt to build transaction with two different --change-address parameters
        * Check that transaction building fails with invalid option error
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
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert re.search(r"Invalid option.*--change-address", exc_value), exc_value
