import dataclasses
from typing import Dict
from typing import Optional

from cardano_clusterlib import clusterlib


@dataclasses.dataclass
class ClusterManagerCache:
    """Cache for a single cluster instance.

    Here goes only data that makes sense to reuse in multiple tests.
    """

    # single `ClusterLib` instance can be used in multiple tests executed on the same worker
    cluster_obj: Optional[clusterlib.ClusterLib] = None
    # data for initialized cluster instance
    test_data: dict = dataclasses.field(default_factory=dict)
    addrs_data: dict = dataclasses.field(default_factory=dict)
    last_checksum: str = ""


class CacheManager:
    """Set of cache management methods."""

    # every pytest worker has its own cache, i.e. this cache is local to single worker
    cache: Dict[int, ClusterManagerCache] = {}

    @classmethod
    def get_cache(cls) -> Dict[int, ClusterManagerCache]:
        return cls.cache

    @classmethod
    def get_instance_cache(cls, instance_num: int) -> ClusterManagerCache:
        instance_cache = cls.cache.get(instance_num)
        if not instance_cache:
            instance_cache = ClusterManagerCache()
            cls.cache[instance_num] = instance_cache
        return instance_cache
