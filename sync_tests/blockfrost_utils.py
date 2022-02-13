import os

from blockfrost import BlockFrostApi

project_id = os.environ["BLOCKFROST_API_KEY"]


def get_blockfrost_health():
    api = BlockFrostApi(project_id=project_id)
    health = api.health(return_type='json')
    return health['is_healthy']


def get_tx_count_per_epoch_from_blockfrost(epoch_no):
    api = BlockFrostApi(project_id=project_id)
    if get_blockfrost_health():
        return api.epoch(number=epoch_no).tx_count
    else:
        print(f"!!! ERROR blockfrost_health is {get_blockfrost_health()}")


def get_current_epoch_no_from_blockfrost():
    api = BlockFrostApi(project_id=project_id)
    if get_blockfrost_health():
        return api.epoch_latest().epoch
    else:
        print(f"!!! ERROR blockfrost_health is {get_blockfrost_health()}")


def get_epoch_start_datetime_from_blockfrost(epoch_no):
    api = BlockFrostApi(project_id=project_id)
    if get_blockfrost_health():
        return api.epoch(number=epoch_no).start_time
    else:
        print(f"!!! ERROR blockfrost_health is {get_blockfrost_health()}")


print(get_tx_count_per_epoch_from_blockfrost(200))
print(get_current_epoch_no_from_blockfrost())
print(get_epoch_start_datetime_from_blockfrost(200))
