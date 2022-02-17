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
    """Get offset of slots between Byron and Shelley eras.

    Byron and Shelley eras can have different epoch and slot lengths. The slots offset is needed
    in order to be able to calculate what slot number to expect in certain point of time
    (e.g. epoch boundary).

    When no value for `shelley_start` is provided, assume it's local cluster where Shelley
    era starts after a single Byron epoch.
    """
    with open(genesis_byron, encoding="utf-8") as in_json:
        byron_dict = json.load(in_json)
    with open(genesis_shelley, encoding="utf-8") as in_json:
        shelley_dict = json.load(in_json)

    slot_length_byron_msec = int(byron_dict["blockVersionData"]["slotDuration"])
    # slot lengths in seconds
    slot_length_byron = float(slot_length_byron_msec / 1000)
    slot_length_shelley = float(shelley_dict["slotLength"])

    if shelley_start:
        start_timestamp = int(byron_dict["startTime"])
        shelley_timestamp = _datetime2timestamp(shelley_start)
        timestamp_offset = shelley_timestamp - start_timestamp

        # assume that epoch length is the same for Byron and Shelley epochs
        slots_in_byron = int(timestamp_offset / slot_length_byron)
        slots_in_shelley = int(timestamp_offset / slot_length_shelley)
    else:
        byron_k = int(byron_dict["protocolConsts"]["k"])
        byron_epoch_length = int(byron_k * 10 * slot_length_byron)

        slots_per_epoch_shelley = int(shelley_dict["epochLength"])
        shelley_epoch_length = int(slots_per_epoch_shelley * slot_length_shelley)

        # Assume that Shelley era starts at epoch 1, i.e. after a single Byron epoch.
        # Epoch length can differ between Byron and Shelley epochs.
        slots_in_byron = int(byron_epoch_length / slot_length_byron)
        slots_in_shelley = int(shelley_epoch_length / slot_length_shelley)

    offset = slots_in_shelley - slots_in_byron

    return offset
