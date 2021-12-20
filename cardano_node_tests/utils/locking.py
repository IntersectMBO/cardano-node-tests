import contextlib
import logging
from typing import Any

from cardano_node_tests.utils import configuration


# Use dummy locking if not executing with multiple workers.
# When running with multiple workers, operations with shared resources (like faucet addresses)
# need to be locked to single worker (otherwise e.g. balances would not check).
if configuration.IS_XDIST:
    from filelock import FileLock

    # suppress messages from filelock
    logging.getLogger("filelock").setLevel(logging.WARNING)

    FileLockIfXdist: Any = FileLock
else:
    FileLockIfXdist = contextlib.nullcontext
