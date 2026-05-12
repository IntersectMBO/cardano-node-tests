"""Antithesis SDK wrappers.

All functions are no-ops when the ``antithesis`` package is not installed,
so tests that use them run normally outside the Antithesis environment.
Install the package only inside the Antithesis Docker image — do not add it
to pyproject.toml.
"""

import typing as tp

try:
    import antithesis.assertions as _ant

    def always(condition: bool, message: str, details: tp.Mapping[str, tp.Any]) -> None:
        """Assert *condition* is true on every invocation."""
        _ant.always(condition, message, details)

    def sometimes(condition: bool, message: str, details: tp.Mapping[str, tp.Any]) -> None:
        """Assert *condition* is true at least once across all calls."""
        _ant.sometimes(condition, message, details)

    def reachable(message: str, details: tp.Mapping[str, tp.Any]) -> None:
        """Assert this code location is reached at least once."""
        _ant.reachable(message, details)

    def unreachable(message: str, details: tp.Mapping[str, tp.Any]) -> None:
        """Assert this code location is never reached."""
        _ant.unreachable(message, details)

except ImportError:

    def always(condition: bool, message: str, details: tp.Mapping[str, tp.Any]) -> None:  # type: ignore[misc]
        pass

    def sometimes(condition: bool, message: str, details: tp.Mapping[str, tp.Any]) -> None:  # type: ignore[misc]
        pass

    def reachable(message: str, details: tp.Mapping[str, tp.Any]) -> None:  # type: ignore[misc]
        pass

    def unreachable(message: str, details: tp.Mapping[str, tp.Any]) -> None:  # type: ignore[misc]
        pass
