import json
from datetime import datetime
from datetime import timezone
from pathlib import Path


def _datetime2timestamp(datetime_str: str) -> int:
    """Convert UTC datetime string to timestamp."""
    converted_time = datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%SZ")
    timestamp = converted_time.replace(tzinfo=timezone.utc).timestamp()
    return int(timestamp)


def get_slots_offset(genesis_byron: Path, genesis_shelley: Path, shelley_start: str) -> int:
    """Get offset of slots from Byron configuration vs current era configuration."""
    with open(genesis_byron, encoding="utf-8") as in_json:
        byron_dict = json.load(in_json)
    with open(genesis_shelley, encoding="utf-8") as in_json:
        shelley_dict = json.load(in_json)

    start_timestamp: int = byron_dict["startTime"]
    testnet_timestamp = _datetime2timestamp(shelley_start)
    offset_sec = testnet_timestamp - start_timestamp

    slot_duration_byron_msec = int(byron_dict["blockVersionData"]["slotDuration"])
    slot_duration_byron = float(slot_duration_byron_msec / 1000)
    slot_duration_shelley = float(shelley_dict["slotLength"])

    # assume that epoch length is the same for byron and shelley epochs
    slots_in_byron = int(offset_sec / slot_duration_byron)
    slots_in_shelley = int(offset_sec / slot_duration_shelley)
    offset = slots_in_shelley - slots_in_byron

    return offset
