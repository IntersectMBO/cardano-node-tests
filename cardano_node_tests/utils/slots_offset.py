import json
from datetime import datetime
from datetime import timezone
from pathlib import Path


def _datetime2timestamp(datetime_str: str) -> int:
    """Convert UTC datetime string to timestamp."""
    converted_time = datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%SZ")
    timestamp = converted_time.replace(tzinfo=timezone.utc).timestamp()
    return int(timestamp)


def get_slots_offset(genesis_byron: Path, genesis_shelley: Path, shelley_start: str = "") -> int:
    """Get offset of slots from Byron configuration vs current era configuration.

    When no value for `shelley_start` is provided, assume it's local cluster where Shelley
    era starts after a single Byron epoch.
    """
    with open(genesis_byron, encoding="utf-8") as in_json:
        byron_dict = json.load(in_json)
    with open(genesis_shelley, encoding="utf-8") as in_json:
        shelley_dict = json.load(in_json)

    slot_duration_byron_msec = int(byron_dict["blockVersionData"]["slotDuration"])
    slot_duration_byron = float(slot_duration_byron_msec / 1000)
    slot_duration_shelley = float(shelley_dict["slotLength"])

    if shelley_start:
        start_timestamp: int = byron_dict["startTime"]
        testnet_timestamp = _datetime2timestamp(shelley_start)
        offset_sec = testnet_timestamp - start_timestamp

        # assume that epoch length is the same for Byron and Shelley epochs
        slots_in_byron = int(offset_sec / slot_duration_byron)
        slots_in_shelley = int(offset_sec / slot_duration_shelley)
    else:
        slots_per_epoch_shelley = int(shelley_dict["epochLength"])
        byron_k = int(byron_dict["protocolConsts"]["k"])

        byron_epoch_sec = int(byron_k * 10 * slot_duration_byron)
        shelley_epoch_sec = int(slots_per_epoch_shelley * slot_duration_shelley)

        if (slot_duration_byron == slot_duration_shelley) and (
            byron_epoch_sec == shelley_epoch_sec
        ):
            return 0

        # assume that Shelley era starts at epoch 1, i.e. after a single Byron epoch
        slots_in_byron = int(byron_epoch_sec / slot_duration_byron)
        slots_in_shelley = int(shelley_epoch_sec / slot_duration_shelley)

    offset = slots_in_shelley - slots_in_byron

    return offset
