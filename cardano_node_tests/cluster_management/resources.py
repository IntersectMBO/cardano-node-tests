from cardano_node_tests.utils import configuration


class Resources:
    """Resources that can be used for `lock_resources` or `use_resources`."""

    # Whole cluster instance - this resource is used by every test.
    # It can be locked, so only single test will run.
    CLUSTER = "cluster"
    POOL1 = "node-pool1"
    POOL2 = "node-pool2"
    POOL3 = "node-pool3"
    ALL_POOLS = tuple(f"node-pool{i}" for i in range(1, configuration.NUM_POOLS + 1))
    # reserve one pool for all tests where the pool will stop producing blocks
    POOL_FOR_OFFLINE = POOL2
    RESERVES = "reserves"
    TREASURY = "treasury"
    PERF = "performance"
