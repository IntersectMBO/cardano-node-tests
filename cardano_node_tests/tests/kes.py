"""Functionality for KES key used in multiple tests modules."""

import datetime
import logging
import typing as tp

import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import issues
from cardano_node_tests.utils import blockers
from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import http_client

LOGGER = logging.getLogger(__name__)


# Valid scenarios when we are testing the kes-period-info cli command
class KesScenarios:
    ALL_VALID = "all_valid"
    ALL_INVALID = "all_invalid"
    INVALID_COUNTERS = "invalid_counters"
    INVALID_KES_PERIOD = "invalid_kes_period"


def check_kes_period_info_result(  # noqa: C901
    cluster_obj: clusterlib.ClusterLib,
    kes_output: dict[str, tp.Any],
    expected_scenario: str,
    check_id: str,
    expected_start_kes: int | None = None,
    pool_num: int | None = None,
) -> list[str]:
    """Check output `kes-period-info` command.

    When `pool_num` is specified, prometheus metrics are checked.
    """
    output_scenario = "unknown"
    errors = []

    # Get command metrics
    command_metrics: dict[str, tp.Any] = kes_output["metrics"] or {}

    # Check kes metrics with values in genesis
    if command_metrics["qKesMaxKESEvolutions"] != cluster_obj.max_kes_evolutions:
        errors.append(
            f"The max kes evolution in check '{check_id}': "
            f"{command_metrics['qKesMaxKESEvolutions']} vs {cluster_obj.max_kes_evolutions}"
        )
    if command_metrics["qKesSlotsPerKesPeriod"] != cluster_obj.slots_per_kes_period:
        errors.append(
            f"The slots per kes period in check '{check_id}': "
            f"{command_metrics['qKesSlotsPerKesPeriod']} vs "
            f"{cluster_obj.slots_per_kes_period}"
        )

    if command_metrics["qKesKesKeyExpiry"] is None and expected_scenario not in [
        KesScenarios.ALL_INVALID,
        KesScenarios.INVALID_KES_PERIOD,
    ]:
        errors.append(f"The kes expiration date is `null` in check '{check_id}' -> issue #4396?")
    elif command_metrics["qKesKesKeyExpiry"]:
        expected_expiration_date = (
            datetime.datetime.now(tz=datetime.UTC)
            + datetime.timedelta(
                seconds=command_metrics["qKesRemainingSlotsInKesPeriod"] * cluster_obj.slot_length
            )
        ).strftime("%Y-%m-%d")

        command_expiration_date = command_metrics["qKesKesKeyExpiry"][:10]

        if command_expiration_date != expected_expiration_date:
            errors.append(
                f"The kes expiration date in check '{check_id}': "
                f"{command_expiration_date} vs {expected_expiration_date}"
            )

    # Get prometheus metrics
    prometheus_metrics: dict[str, tp.Any] = {}
    if pool_num and expected_scenario in (
        KesScenarios.ALL_VALID,
        KesScenarios.INVALID_COUNTERS,
    ):
        instance_ports = cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            instance_num=cluster_nodes.get_instance_num()
        )
        prometheus_port = instance_ports.node_ports[pool_num].prometheus
        response = http_client.get_session().get(
            f"http://localhost:{prometheus_port}/metrics", timeout=10
        )

        _prometheus_metrics_raw = [m.split() for m in response.text.strip().split("\n")]
        prometheus_metrics_raw = {m[0]: m[-1] for m in _prometheus_metrics_raw}

        prometheus_metrics = {
            "qKesStartKesInterval": prometheus_metrics_raw[
                "cardano_node_metrics_operationalCertificateStartKESPeriod_int"
            ],
            "qKesEndKesInterval": prometheus_metrics_raw[
                "cardano_node_metrics_operationalCertificateExpiryKESPeriod_int"
            ],
            "qKesCurrentKesPeriod": prometheus_metrics_raw[
                "cardano_node_metrics_currentKESPeriod_int"
            ],
        }

    # Check kes metrics with expected values
    expected_metrics: dict[str, tp.Any] = {
        "qKesCurrentKesPeriod": cluster_obj.g_query.get_kes_period(),
    }
    if expected_start_kes is not None:
        expected_metrics["qKesStartKesInterval"] = expected_start_kes
        expected_metrics["qKesEndKesInterval"] = expected_start_kes + cluster_obj.max_kes_evolutions

    for metric, value in expected_metrics.items():
        if value != command_metrics[metric]:
            errors.append(
                f"The metric '{metric}' in check '{check_id}': {value} vs {command_metrics[metric]}"
            )

        if prometheus_metrics and value != int(prometheus_metrics[metric]):
            errors.append(
                f"The prometheus metric '{metric}' in check '{check_id}': "
                f"{value} vs {prometheus_metrics[metric]}"
            )

    # In eras > Alonzo, the on-disk counter must be equal or +1 to the on-chain counter
    valid_counter_metrics = (
        0
        <= command_metrics["qKesOnDiskOperationalCertificateNumber"]
        - command_metrics["qKesNodeStateOperationalCertificateNumber"]
        <= 1
    )

    valid_kes_period_metrics = (
        command_metrics["qKesStartKesInterval"]
        <= command_metrics["qKesCurrentKesPeriod"]
        <= command_metrics["qKesEndKesInterval"]
    )

    # Check if output scenario matches expected scenario
    if (
        kes_output.get("valid_counters")
        and kes_output.get("valid_kes_period")
        and valid_counter_metrics
        and valid_kes_period_metrics
    ):
        output_scenario = KesScenarios.ALL_VALID
    elif (
        not kes_output.get("valid_counters")
        and not kes_output.get("valid_kes_period")
        and not valid_counter_metrics
        and not valid_kes_period_metrics
    ):
        output_scenario = KesScenarios.ALL_INVALID
    elif (
        not kes_output.get("valid_counters")
        and kes_output.get("valid_kes_period")
        and not valid_counter_metrics
        and valid_kes_period_metrics
    ):
        output_scenario = KesScenarios.INVALID_COUNTERS
    elif (
        kes_output.get("valid_counters")
        and not kes_output.get("valid_kes_period")
        and valid_counter_metrics
        and not valid_kes_period_metrics
    ):
        output_scenario = KesScenarios.INVALID_KES_PERIOD
    # Check node issue #4114
    elif (
        kes_output.get("valid_counters")
        and kes_output.get("valid_kes_period")
        and not valid_counter_metrics
        and valid_kes_period_metrics
    ):
        # Skip the expected scenario check below, just report the corresponding node issue
        output_scenario = expected_scenario
        errors.append(
            f"Undetected invalid counter and certificate in check '{check_id}' -> issue #4114?"
        )

    if expected_scenario != output_scenario:
        errors.append(
            f"Unexpected scenario in check '{check_id}': "
            f"'{expected_scenario}' vs '{output_scenario}'"
        )

    return errors


def get_xfails(errors: list[str]) -> list[blockers.GH]:
    """Get xfail issues.

    Either all errors can Xfail, or none of them can. There can be only one outcome of a test,
    so if there are errors that can't be Xfailed, the test must fail.
    """
    xfails = []

    for error in errors:
        if not error:
            continue
        if "issue #4114" in error and issues.node_4114.is_blocked():
            xfails.append(issues.node_4114)
            continue
        if "issue #4396" in error and issues.node_4396.is_blocked():
            xfails.append(issues.node_4396)
            continue
        # If here, there are other failures than the expected ones
        return []

    return xfails


def finish_on_errors(errors: list[str]) -> None:
    """Fail or Xfail the test if there are errors."""
    if not errors:
        return

    xfails = get_xfails(errors=errors)
    if xfails:
        blockers.finish_test(issues=xfails)
    else:
        err_joined = "\n".join(e for e in errors if e)
        pytest.fail(f"Failed checks on `kes-period-info` command:\n{err_joined}")
