"""Functionality for KES key used in multiple tests modules."""
import datetime
import logging
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set

import requests
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes

LOGGER = logging.getLogger(__name__)


# valid scenarios when we are testing the kes-period-info cli command
class KesScenarios:
    ALL_VALID = "all_valid"
    ALL_INVALID = "all_invalid"
    INVALID_COUNTERS = "invalid_counters"
    INVALID_KES_PERIOD = "invalid_kes_period"


def check_kes_period_info_result(  # noqa: C901
    cluster_obj: clusterlib.ClusterLib,
    kes_output: Dict[str, Any],
    expected_scenario: str,
    check_id: str,
    expected_start_kes: Optional[int] = None,
    pool_num: Optional[int] = None,
) -> List[str]:
    """Check output `kes-period-info` command.

    When `pool_num` is specified, prometheus metrics are checked.
    """
    # pylint: disable=too-many-branches
    output_scenario = "unknown"
    errors = []

    # get command metrics
    command_metrics: Dict[str, Any] = kes_output["metrics"] or {}

    # check kes metrics with values in genesis
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

    if command_metrics["qKesKesKeyExpiry"] is None:
        errors.append(f"The kes expiration date is `null` in check '{check_id}'")
    else:
        expected_expiration_date = (
            datetime.datetime.now()
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

    # get prometheus metrics
    prometheus_metrics: Dict[str, Any] = {}
    if pool_num and expected_scenario in (
        KesScenarios.ALL_VALID,
        KesScenarios.INVALID_COUNTERS,
    ):
        instance_ports = cluster_nodes.get_cluster_type().cluster_scripts.get_instance_ports(
            cluster_nodes.get_instance_num()
        )
        prometheus_port = instance_ports.node_ports[pool_num].prometheus
        response = requests.get(f"http://localhost:{prometheus_port}/metrics", timeout=10)

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

    # check kes metrics with expected values
    expected_metrics: Dict[str, Any] = {
        "qKesCurrentKesPeriod": cluster_obj.g_query.get_kes_period(),
    }
    if expected_start_kes is not None:
        expected_metrics["qKesStartKesInterval"] = expected_start_kes
        expected_metrics["qKesEndKesInterval"] = expected_start_kes + cluster_obj.max_kes_evolutions

    for metric, value in expected_metrics.items():
        if value != command_metrics[metric]:
            errors.append(
                f"The metric '{metric}' in check '{check_id}': "
                f"{value} vs {command_metrics[metric]}"
            )

        if prometheus_metrics and value != int(prometheus_metrics[metric]):
            errors.append(
                f"The prometheus metric '{metric}' in check '{check_id}': "
                f"{value} vs {prometheus_metrics[metric]}"
            )

    # in eras > Alonzo, the on-disk counter must be equal or +1 to the on-chain counter
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

    # check if output scenario matches expected scenario
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
    # TODO: tests for cardano node issue #4114
    elif (
        kes_output.get("valid_counters")
        and kes_output.get("valid_kes_period")
        and not valid_counter_metrics
        and valid_kes_period_metrics
    ):
        output_scenario = "issue_4114"

    if expected_scenario != output_scenario:
        errors.append(
            f"Unexpected scenario in check '{check_id}': "
            f"'{expected_scenario}' vs '{output_scenario}'"
        )

    return errors


def get_xfails(errors: List[str]) -> Set[str]:
    """Get xfail error strings."""
    xfails = set()

    for error in errors:
        if not error:
            continue
        if "issue_4114" in error:
            xfails.add("See cardano-node issue #4114")
            continue
        if "expiration date is `null`" in error:
            xfails.add("See cardano-node issue #4396")
            continue
        # if here, there are other failures than the expected ones
        return set()

    return xfails
