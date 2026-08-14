"""File-based locking.

`FileLock` is a real lock that works regardless of how pytest is invoked - use it when
coordination is needed even outside pytest-xdist, e.g. across pytest invocations.
`FileLockIfXdist` degrades to a no-op when not running with pytest-xdist workers.
"""

import contextlib
import logging
import typing as tp

from filelock import FileLock
from filelock import Timeout

from cardano_node_tests.utils import configuration

# Suppress messages from filelock
logging.getLogger("filelock").setLevel(logging.WARNING)

# Use dummy locking if not executing with multiple workers.
# When running with multiple workers, operations with shared resources (like faucet addresses)
# need to be locked to single worker (otherwise e.g. balances would not check).
FileLockIfXdist: tp.Any = FileLock if configuration.IS_XDIST else contextlib.nullcontext

__all__ = ["FileLock", "FileLockIfXdist", "Timeout"]
