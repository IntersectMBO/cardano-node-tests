import functools
import logging
import pathlib as pl
import time

from cardano_node_tests.utils import temptools


@functools.cache
def get_framework_log_path() -> pl.Path:
    return temptools.get_pytest_worker_tmp() / "framework.log"


@functools.cache
def framework_logger() -> logging.Logger:
    """Get logger for the `framework.log` file.

    The logger is configured per worker. It can be used for logging (and later reporting) events
    like a failure to start a cluster instance.
    """

    class UTCFormatter(logging.Formatter):
        converter = time.gmtime  # type: ignore[assignment]

    formatter = UTCFormatter("%(asctime)s %(levelname)s %(message)s")
    handler = logging.FileHandler(get_framework_log_path())
    handler.setFormatter(formatter)

    logger = logging.getLogger("framework")
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    return logger
