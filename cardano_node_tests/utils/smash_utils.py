import logging
import typing as tp
from dataclasses import dataclass
from functools import lru_cache

import requests
from requests.auth import HTTPBasicAuth

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, order=True)
class PoolMetadata:
    name: str
    description: str
    ticker: str
    homepage: str


@dataclass(frozen=True, order=True)
class PoolData:
    pool_id: str


@dataclass(frozen=True, order=True)
class CodeError:
    code: str
    description: str

@dataclass(frozen=True, order=True)
class DbInsertError:
    code: str
    description: str


class SmashClient:
    """Utility class for interacting with SMASH via REST API."""

    def __init__(self, instance_num: int) -> None:
        self.instance_num = instance_num
        self.port = configuration.SMASH_BASE_PORT + instance_num
        self.base_url = f"http://localhost:{self.port}"
        self.auth = self._get_auth()

    def _get_auth(self) -> tp.Optional[HTTPBasicAuth]:
        """Get Basic Auth credentials if configured."""
        admin = getattr(configuration, "SMASH_ADMIN", None)
        password = getattr(configuration, "SMASH_PASSWORD", None)
        return HTTPBasicAuth(admin, password) if admin and password else None

    @lru_cache(maxsize=128)
    def get_pool_metadata(self, pool_id: str, pool_meta_hash: str) -> tp.Optional[PoolMetadata]:
        """Fetch stake pool metadata from SMASH (with caching), returning a `PoolMetadata` dataclass."""
        url = f"{self.base_url}/api/v1/metadata/{pool_id}/{pool_meta_hash}"
        try:
            response = requests.get(url, auth=self.auth)
            response.raise_for_status()
            data = response.json()

            # Ensure required fields exist before creating the dataclass
            if not all(key in data for key in ("name", "description", "ticker", "homepage")):
                return None

            return PoolMetadata(
                name=data["name"],
                description=data["description"],
                ticker=data["ticker"],
                homepage=data["homepage"]
            )

        except requests.exceptions.RequestException as e:
            print(f"Error fetching metadata: {e}")
            return e

    def delist_pool(self, pool_id: str) -> bool:
        """Delist a stake pool."""
        url = f"{self.base_url}/api/v1/delist"
        response = requests.patch(url, json={"poolId": pool_id}, auth=self.auth)
        response.raise_for_status()
        data = response.json()
        return PoolData(pool_id=data["poolId"])

    def enlist_pool(self, pool_id: str) -> bool:
        """Enlist a stake pool."""
        url = f"{self.base_url}/api/v1/enlist"
        try:
            response = requests.patch(url, json={"poolId": pool_id}, auth=self.auth)
            response.raise_for_status()
            data = response.json()
            return PoolData(pool_id=data["poolId"])
        except requests.exceptions.RequestException as err:
            LOGGER.warning(f"Failed to enlist pool {pool_id}: {err}")
            return False

    def reserve_ticker(self, ticker_name: str, pool_hash: str) -> bool:
        """Reserve a ticker for a stake pool."""
        url = f"{self.base_url}/api/v1/tickers/{ticker_name}"
        try:
            response = requests.post(url, json={"poolHash": pool_hash}, auth=self.auth)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as err:
            LOGGER.warning(f"Failed to reserve ticker {ticker_name}: {err}")
            return False

    def get_pool_errors(self, pool_id: str, from_date: tp.Optional[str] = None) -> tp.Optional[list]:
        """Fetch errors for a specific stake pool."""
        url = f"{self.base_url}/api/v1/errors/{pool_id}"
        params = {"fromDate": from_date} if from_date else None
        try:
            response = requests.get(url, params=params, auth=self.auth)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as err:
            LOGGER.warning(f"Failed to fetch errors for pool {pool_id}: {err}")
            return None

    def get_retired_pools(self) -> tp.Optional[list]:
        """Fetch list of retired pools."""
        url = f"{self.base_url}/api/v1/retired"
        try:
            response = requests.get(url, auth=self.auth)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as err:
            LOGGER.warning(f"Failed to fetch retired pools: {err}")
            return None


class SmashManager:

    instances: tp.ClassVar[dict[int, SmashClient]] = {}

    @classmethod
    def get_smash_instance(cls) -> SmashClient:
        """Return a singleton instance of `SmashClient` for the given cluster instance."""
        instance_num = cluster_nodes.get_instance_num()
        if instance_num not in cls.instances:
            cls.instances[instance_num] = SmashClient(instance_num)
        return cls.instances[instance_num]
    

def get_client() -> SmashClient:
    """Global access to the SMASH client singleton."""
    return SmashManager.get_smash_instance()