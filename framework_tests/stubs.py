"""Stubs of objects that are shared by multiple unit tests."""

import typing as tp

from _pytest.config import Config

from cardano_node_tests.utils import artifacts


class PytestConfigStub:
    """Minimal stub of pytest `Config` that provides only `getoption`."""

    def __init__(self, cli_coverage_dir: str) -> None:
        self._cli_coverage_dir = cli_coverage_dir

    def getoption(self, name: str) -> str:
        """Return the configured CLI coverage dir."""
        assert name == artifacts.CLI_COVERAGE_ARG
        return self._cli_coverage_dir


class FixtureRequestStub:
    """Minimal stub of pytest `FixtureRequest` that records finalizers."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self.finalizers: list[tp.Callable[[], None]] = []

    def addfinalizer(self, finalizer: tp.Callable[[], None]) -> None:
        """Record a finalizer instead of running it at teardown."""
        self.finalizers.append(finalizer)


class ClusterObjStub:
    """Minimal stub of `ClusterLib` that provides only `cli_coverage` and `command_era`."""

    def __init__(
        self, cli_coverage: dict[str, tp.Any] | None = None, command_era: str = ""
    ) -> None:
        self.cli_coverage: dict[str, tp.Any] = {} if cli_coverage is None else cli_coverage
        self.command_era = command_era
