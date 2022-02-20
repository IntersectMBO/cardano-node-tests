import os

from blockfrost import BlockFrostApi
from datetime import datetime


def get_blockfrost_health():
    api = BlockFrostApi(project_id=os.environ["BLOCKFROST_API_KEY"])
    health = api.health(return_type='json')
    return health['is_healthy']


def get_tx_count_per_epoch_from_blockfrost(epoch_no):
    print(f"Getting the tx_count_per_epoch_from_blockfrost")
    api = BlockFrostApi(project_id=os.environ["BLOCKFROST_API_KEY"])
    if get_blockfrost_health():
        return api.epoch(number=epoch_no).tx_count
    else:
        print(f"!!! ERROR blockfrost_health is {get_blockfrost_health()}")


def get_current_epoch_no_from_blockfrost():
    print(f"Getting the current_epoch_no_from_blockfrost")
    api = BlockFrostApi(project_id=os.environ["BLOCKFROST_API_KEY"])
    if get_blockfrost_health():
        return api.epoch_latest().epoch
    else:
        print(f"!!! ERROR blockfrost_health is {get_blockfrost_health()}")


def get_epoch_start_datetime_from_blockfrost(epoch_no):
    print(f"Getting the epoch_start_datetime_from_blockfrost")
    api = BlockFrostApi(project_id=os.environ["BLOCKFROST_API_KEY"])
    if get_blockfrost_health():
        return datetime.utcfromtimestamp(api.epoch(number=epoch_no).start_time).strftime("%Y-%m-%dT%H:%M:%SZ")
    else:
        print(f"!!! ERROR blockfrost_health is {get_blockfrost_health()}")
