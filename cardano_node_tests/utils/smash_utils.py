import os
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests
from requests import Response

from cardano_node_tests.utils import configuration

SESSION = None
BASE_URL = "http://localhost:3100/api/v1/"
STATUS = urljoin(BASE_URL, "status")
METADATA = urljoin(BASE_URL, "metadata/")
DELIST = urljoin(BASE_URL, "delist")
TICKER = urljoin(BASE_URL, "tickers/")
WHITELIST = urljoin(BASE_URL, "enlist")
ERRORS = urljoin(BASE_URL, "errors/")
RETIRED = urljoin(BASE_URL, "retired")
POLICIES = urljoin(BASE_URL, "policies")


def init_smash_session() -> None:
    node_socket_path = Path(os.environ["CARDANO_NODE_SOCKET_PATH"])
    root_dir, _ = os.path.split(node_socket_path)
    admins_file = Path(root_dir) / "admins.txt"
    with open(admins_file) as f:
        credentials_line = f.readlines()
        credentials = credentials_line[0].split(",")
    username = credentials[0]
    password = credentials[1].strip()

    global SESSION
    SESSION = requests.Session()
    SESSION.auth = (username, password)
    SESSION.headers.update({"Content-Type": "application/json"})


init_smash_session()


def get_status() -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None
    return SESSION.get(STATUS)


def fetch_metadata(pool_id: str, pool_metadata: str) -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None
    metadata_url = urljoin(METADATA, f"{pool_id}/{pool_metadata}")
    return SESSION.get(metadata_url)


def delist_pool(pool_id: str) -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None

    return SESSION.patch(DELIST, json={"poolId": pool_id})


def reserve_ticker(pool_id: str, ticker_name: str) -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None
    reserve_ticker_url = urljoin(TICKER, ticker_name)
    return SESSION.post(reserve_ticker_url, json={"poolId": pool_id})


def whitelist_pool(pool_id: str) -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None
    return SESSION.patch(WHITELIST, json={"poolId": pool_id})


def get_errors(pool_id: str) -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None
    errors_url = urljoin(ERRORS, pool_id)
    return SESSION.get(errors_url)


def get_retired_pools() -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None
    return SESSION.get(RETIRED)


def fetch_policies(smash_url: str = "https://smash.cardano-mainnet.iohk.io") -> Optional[Response]:
    if not configuration.HAS_SMASH:
        return None
    return SESSION.post(POLICIES, json={"smashURL": smash_url})
