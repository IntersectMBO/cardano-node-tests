"""Functionality for KES key used in multiple tests modules."""
import logging

LOGGER = logging.getLogger(__name__)


# valid scenarios when we are testing the kes-period-info cli command
class KesScenarios:
    ALL_VALID = "all_valid"
    ALL_INVALID = "all_invalid"
    INVALID_COUNTERS = "invalid_counters"
    INVALID_KES_PERIOD = "invalid_kes_period"


def check_kes_period_info_result(kes_output: dict, expected_scenario: str):
    """Check output from kes-period-info command."""
    output_scenario = None

    # expected scenario: all_valid
    if kes_output.get("valid_counters") and kes_output.get("valid_kes_period"):
        # check metrics, metrics dict are only returned by the command if everything is valid
        metrics: dict = kes_output["metrics"] or {}
        node_state_counter: int = metrics["qKesNodeStateOperationalCertificateNumber"]
        on_disk_counter: int = metrics["qKesOnDiskOperationalCertificateNumber"]
        start_kes_period: int = metrics["qKesStartKesInterval"]
        end_kes_period: int = metrics["qKesEndKesInterval"]
        current_kes_period: int = metrics["qKesCurrentKesPeriod"]

        # check if the metrics match with the expected scenario
        if (
            node_state_counter <= on_disk_counter
            and start_kes_period <= current_kes_period <= end_kes_period
        ):
            output_scenario = KesScenarios.ALL_VALID
    # expected scenario: all_invalid
    elif not kes_output.get("valid_counters") and not kes_output.get("valid_kes_period"):
        output_scenario = KesScenarios.ALL_INVALID
    # expected scenario: invalid_counter
    elif not kes_output.get("valid_counters") and kes_output.get("valid_kes_period"):
        output_scenario = KesScenarios.INVALID_COUNTERS
    # expected scenario: invalid_kes_period
    elif kes_output.get("valid_counters") and not kes_output.get("valid_kes_period"):
        output_scenario = KesScenarios.INVALID_KES_PERIOD

    assert expected_scenario == output_scenario, (
        "The kes-period-info command is not returning the expected results. "
        f"Expected: '{expected_scenario}'; Returned: '{output_scenario}'\n"
        f"Full output:\n{kes_output}"
    )
