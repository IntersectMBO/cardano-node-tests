import re
import typing as tp

from cardano_node_tests.utils import configuration


class Resources:
    """Resources that can be used for `lock_resources` or `use_resources`."""

    # Whole cluster instance - this resource is used by every test.
    # It can be locked, so only single test will run.
    CLUSTER: tp.Final[str] = "cluster"
    POOL1: tp.Final[str] = "node-pool1"
    POOL2: tp.Final[str] = "node-pool2"
    POOL3: tp.Final[str] = "node-pool3"
    ALL_POOLS: tp.Final[tp.Tuple[str, ...]] = tuple(
        f"node-pool{i}" for i in range(1, configuration.NUM_POOLS + 1)
    )
    # Reserve one pool for all tests where the pool will stop producing blocks
    POOL_FOR_OFFLINE: tp.Final[str] = POOL2
    RESERVES: tp.Final[str] = "reserves"
    TREASURY: tp.Final[str] = "treasury"
    PERF: tp.Final[str] = "performance"
    DREPS: tp.Final[str] = "dreps"
    COMMITTEE: tp.Final[str] = "committee"


_SANITIZE_RE = re.compile("[^a-zA-Z0-9_-]+")


def sanitize_res_name(s: str) -> str:
    """Sanitize resource name to expected format."""
    sanitized = _SANITIZE_RE.sub("_", s).strip()[0:20]
    return sanitized


def get_unsanitized(ls: tp.Iterable[str]) -> tp.List[str]:
    """Return unsanitized resource names from the list."""
    return [s for s in ls if s != sanitize_res_name(s)]
