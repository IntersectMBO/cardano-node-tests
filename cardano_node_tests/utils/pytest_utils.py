import logging
import os
import re
from pathlib import Path
from typing import NamedTuple

LOGGER = logging.getLogger(__name__)


class PytestTest(NamedTuple):
    test_function: str
    test_file: Path
    full: str
    test_class: str = ""
    test_params: str = ""
    stage: str = ""

    def __bool__(self) -> bool:
        return bool(self.test_function)


def get_current_test() -> PytestTest:
    """Get components (test file, test name, etc.) of current pytest test."""
    curr_test = os.environ.get("PYTEST_CURRENT_TEST") or ""
    if not curr_test:
        return PytestTest(test_function="", test_file=Path("/nonexistent"), full="")

    reg = re.search(
        r"(^.*/test_\w+\.py)(?:::)?(Test\w+)?::(test_\w+)(\[.+\])? *\(?(\w+)?", curr_test
    )
    if not reg:
        raise AssertionError(f"Failed to match '{curr_test}'")

    return PytestTest(
        test_function=reg.group(3),
        test_file=Path(reg.group(1)),
        full=curr_test,
        test_class=reg.group(2) or "",
        test_params=reg.group(4) or "",
        stage=reg.group(5) or "",
    )
