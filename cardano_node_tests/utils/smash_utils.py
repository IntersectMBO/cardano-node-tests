import dataclasses
import datetime
import logging
import os
import shutil
import typing as tp

import requests
from requests import auth as rauth

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import http_client

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, order=True)
class PoolMetadata:
    name: str
    description: str
    ticker: str
    homepage: str


@dataclasses.dataclass(frozen=True, order=True)
class PoolData:
    pool_id: str


@dataclasses.dataclass(frozen=True, order=True)
class PoolTicker:
    name: str


@dataclasses.dataclass(frozen=True, order=True)
class PoolError:
    cause: str
    pool_hash: str
    pool_id: str
    retry_count: int
    time: str
    utc_time: str


class SmashClient:
    """Utility class for interacting with SMASH via REST API."""

    def __init__(self, instance_num: int) -> None:
        self.instance_num = instance_num
        self.port = (
            cluster_nodes.get_cluster_type()
            .cluster_scripts.get_instance_ports(self.instance_num)
            .smash
        )
        self.base_url = f"http://localhost:{self.port}"
        self.auth = self._get_auth()

    def _get_auth(self) -> rauth.HTTPBasicAuth | None:
        """Get Basic Auth credentials if configured."""
        admin = os.environ.get("SMASH_ADMIN", "admin")
        password = os.environ.get("SMASH_PASSWORD", "password")
        return rauth.HTTPBasicAuth(admin, password) if admin and password else None

    def get_pool_metadata(self, pool_id: str, pool_meta_hash: str) -> PoolMetadata:
        """Fetch stake pool metadata from SMASH, returning a `PoolMetadata`."""
        url = f"{self.base_url}/api/v1/metadata/{pool_id}/{pool_meta_hash}"
        response = http_client.get_session().get(url, auth=self.auth)
        response.raise_for_status()
        data = response.json()
        return PoolMetadata(
            name=data["name"],
            description=data["description"],
            ticker=data["ticker"],
            homepage=data["homepage"],
        )

    def delist_pool(self, pool_id: str) -> PoolData:
        """Delist a stake pool, returning PoolData on success or a RequestException on failure."""
        url = f"{self.base_url}/api/v1/delist"
        response = http_client.get_session().patch(url, json={"poolId": pool_id}, auth=self.auth)
        response.raise_for_status()
        data = response.json()
        return PoolData(pool_id=data["poolId"])

    def enlist_pool(self, pool_id: str) -> PoolData:
        """Enlist a stake pool, returning PoolData on success or a RequestException on failure."""
        url = f"{self.base_url}/api/v1/enlist"
        response = http_client.get_session().patch(url, json={"poolId": pool_id}, auth=self.auth)
        response.raise_for_status()
        data = response.json()
        return PoolData(pool_id=data["poolId"])

    def reserve_ticker(self, ticker_name: str, pool_hash: str) -> PoolTicker:
        """Reserve a ticker for a stake pool."""
        url = f"{self.base_url}/api/v1/tickers/{ticker_name}"
        response = http_client.get_session().post(url, json={"poolId": pool_hash}, auth=self.auth)
        response.raise_for_status()
        data = response.json()
        return PoolTicker(name=data["name"])

    def get_pool_errors(self, pool_id: str, from_date: str | None = None) -> list[PoolError]:
        """Fetch errors for a specific stake pool."""
        url = f"{self.base_url}/api/v1/errors/{pool_id}"
        params = {"fromDate": from_date} if from_date else None
        response = http_client.get_session().get(url, params=params, auth=self.auth)
        response.raise_for_status()
        data = response.json()
        pool_errors = [
            PoolError(
                cause=pool_err["cause"],
                pool_hash=pool_err["poolHash"],
                pool_id=pool_err["poolId"],
                retry_count=pool_err["retryCount"],
                time=pool_err["time"],
                utc_time=pool_err["utcTime"],
            )
            for pool_err in data
        ]
        return pool_errors

    def get_retired_pools(self) -> list[PoolData]:
        """Fetch list of retired pools."""
        url = f"{self.base_url}/api/v1/retired"
        response = requests.get(url, auth=self.auth)
        response.raise_for_status()
        data = response.json()
        retired_pools = [PoolData(pool_id=ret_pool["poolId"]) for ret_pool in data]
        return retired_pools


class SmashManager:
    instances: tp.ClassVar[dict[int, SmashClient]] = {}

    @classmethod
    def get_smash_instance(cls) -> SmashClient:
        """Get a singleton instance of `SmashClient` for the given cluster instance."""
        instance_num = cluster_nodes.get_instance_num()
        if instance_num not in cls.instances:
            cls.instances[instance_num] = SmashClient(instance_num)
        return cls.instances[instance_num]


def is_smash_running() -> bool:
    """Check if `cardano-smash-server` service is running."""
    if not shutil.which("cardano-smash-server"):
        return False
    return cluster_nodes.services_status(service_names=["smash"])[0].status == "RUNNING"


def get_client() -> SmashClient | None:
    """Get and cache the SMASH client."""
    if not is_smash_running():
        return None
    return SmashManager.get_smash_instance()


def check_smash_pool_errors(pool_id: str, pool_metadata_hash: str) -> list[PoolError] | None:
    """Check if pool errors are correctly reported in SMASH."""
    if not is_smash_running():
        return None
    smash = SmashManager.get_smash_instance()

    # Test pool errors endpoint for a date set in the future
    utc_now = datetime.datetime.now(tz=datetime.timezone.utc)
    future_date = (utc_now + datetime.timedelta(days=365 * 5)).strftime("%d.%m.%Y")
    smash_pool_errors_future = smash.get_pool_errors(pool_id=pool_id, from_date=future_date)
    assert smash_pool_errors_future == []

    # Test pool errors endpoint with current datetime
    dbsync_pool_data_records = list(dbsync_queries.query_pool_data(pool_id))
    dbsync_pool_errors = list(dbsync_queries.query_off_chain_pool_fetch_error(pool_id))
    smash_pool_errors = smash.get_pool_errors(pool_id=pool_id)

    assert dbsync_pool_data_records, "No pool data found in dbsync."
    assert dbsync_pool_errors, "No pool errors found in dbsync."
    assert smash_pool_errors, "No pool data found in smash."

    dbsync_pool_data = dbsync_pool_data_records.pop()
    dbsync_pool_error = dbsync_pool_errors.pop()
    smash_pool_error = smash_pool_errors.pop()

    assert dbsync_pool_error.fetch_error == smash_pool_error.cause
    assert dbsync_pool_data.hash.hex() == smash_pool_error.pool_id
    assert pool_metadata_hash == smash_pool_error.pool_hash
    assert isinstance(smash_pool_error.retry_count, int) and smash_pool_error.retry_count >= 0

    try:
        # Parse the time string and check if it has a valid format
        error_time_utc = datetime.datetime.strptime(
            smash_pool_error.time, "%d.%m.%Y. %H:%M:%S"
        ).replace(tzinfo=datetime.timezone.utc)

        time_diff = datetime.timedelta(minutes=20)
        assert error_time_utc <= utc_now, "Error time must not be in the future."
        assert error_time_utc >= (utc_now - time_diff), (
            f"Error time must be within the last {time_diff}."
        )

    except ValueError as err:
        err_msg = "time is not a valid date in the format 'DD.MM.YYYY. HH:MM:SS'."
        raise AssertionError(err_msg) from err

    return smash_pool_errors


def check_smash_pool_retired(pool_id: str) -> list[PoolData] | None:
    """Check if pool is correctly reported as retired in SMASH."""
    if not is_smash_running():
        return None
    smash = SmashManager.get_smash_instance()

    dbsync_pool_data = list(dbsync_queries.query_pool_data(pool_id))
    retired_pools = smash.get_retired_pools()

    assert dbsync_pool_data, "No pool data found in dbsync."
    assert retired_pools, "No retired pools found."

    dbsync_pool = dbsync_pool_data.pop()
    pool_id_hash = dbsync_pool.hash.hex()
    assert any(pool.pool_id == pool_id_hash for pool in retired_pools), (
        "Pool ID not found in retired pools"
    )

    return retired_pools
