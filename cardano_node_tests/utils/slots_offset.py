import json
from datetime import datetime
from datetime import timezone
from pathlib import Path


def _datetime2timestamp(datetime_str: str) -> int:
    """Convert UTC datetime string to timestamp."""
    converted_time = datetime.strptime(datetime_str, "%Y-%m-%dT%H:%M:%SZ")
    timestamp = converted_time.replace(tzinfo=timezone.utc).timestamp()
    return int(timestamp)


def get_slots_offset(
    genesis_byron: Path, genesis_shelley: Path, shelley_start: str = "", byron_epochs: int = 1
) -> int:
    """Get offset of slots between Byron and Shelley eras.

    Byron and Shelley eras can have different epoch and slot lengths. The slots offset is needed
    in order to be able to calculate what slot number to expect in certain point of time
    (e.g. epoch boundary).

    When no values for `shelley_start` and `byron_epochs` are provided, assume it's local cluster
    where Shelley era starts after a single Byron epoch.

    Args:
        genesis_byron: Path to Byron genesis file.
        genesis_shelley: Path to Shelley genesis file.
        shelley_start: Time of start of Shelley era.
        byron_epochs: Number of Byron epochs.
    """
    if not (shelley_start or byron_epochs):
        return 0

    with open(genesis_byron, encoding="utf-8") as in_json:
        byron_dict = json.load(in_json)
    with open(genesis_shelley, encoding="utf-8") as in_json:
        shelley_dict = json.load(in_json)

    if shelley_start:
        slot_length_byron_msec = int(byron_dict["blockVersionData"]["slotDuration"])
        # slot lengths in seconds
        slot_length_byron = float(slot_length_byron_msec / 1000)
        slot_length_shelley = float(shelley_dict["slotLength"])

        start_timestamp = int(byron_dict["startTime"])
        shelley_timestamp = _datetime2timestamp(shelley_start)
        timestamp_offset = shelley_timestamp - start_timestamp

        # assume that epoch length is the same for Byron and Shelley epochs
        slots_in_byron = int(timestamp_offset / slot_length_byron)
        slots_in_shelley = int(timestamp_offset / slot_length_shelley)
    else:
        byron_k = int(byron_dict["protocolConsts"]["k"])

        # epoch length can differ between Byron and Shelley epochs
        slots_in_byron = byron_k * 10 * byron_epochs
        slots_in_shelley = int(shelley_dict["epochLength"]) * byron_epochs

    offset = slots_in_shelley - slots_in_byron

    return offset
