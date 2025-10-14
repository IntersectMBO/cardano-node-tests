"""Functionality for selecting a cluster instance that has required resources available.

Resources can be requested by name (string).

It is also possible to use filters. A filter is a class that gets a list of all unavailable
resources and returns a list of resources that should be used. An example is `OneOf`, which returns
one usable resource from a given list of resources. The unavailable resources passed to the filter
include resources that are unavailable because they are locked, and also resources that were
already selected by preceding filters in the same request.

It is possible to use multiple `OneOf` filters in a single request. For example, using `OneOf`
filter with the same set of resources twice will result in selecting two different resources from
that set.
"""

import random
import typing as tp


class BaseFilter:
    """Base class for resource filters."""

    def __init__(self, resources: tp.Iterable[str]) -> None:
        if isinstance(resources, str):
            msg = "`resources` cannot be a string"
            raise TypeError(msg)
        self.resources = resources

    def filter(self, unavailable: tp.Iterable[str], **kwargs: tp.Any) -> list[str]:
        """Filter resources."""
        raise NotImplementedError

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.resources})"


class OneOf(BaseFilter):
    """Filter that returns one usable resource out of list of resources."""

    def filter(
        self,
        unavailable: tp.Iterable[str],
        **kwargs: tp.Any,  # noqa: ARG002
    ) -> list[str]:
        if isinstance(unavailable, str):
            msg = "`unavailable` cannot be a string"
            raise TypeError(msg)

        usable = [r for r in self.resources if r not in unavailable]
        if not usable:
            return []

        return [random.choice(usable)]


ResourcesType = tp.Iterable[str | BaseFilter]


def get_resources(
    resources: ResourcesType,
    unavailable: tp.Iterable[str],
) -> list[str]:
    """Get resources that can be used or locked."""
    # The "named resources", i.e. resources specified by string, are always mandatory.
    # If any of these is not available, the selection cannot continue.
    named_resources = [r for r in resources if isinstance(r, str)]

    res_used = [res for res in named_resources if res in unavailable]
    if res_used:
        return []

    # Execute filters on resources that are still available after satisfying the "named resources".
    # If any of the filters returns empty list, the selection cannot continue.
    already_unavailable = {*unavailable, *named_resources}
    resources_w_filter = [r for r in resources if not isinstance(r, str)]
    selected_resources: list[str] = []
    for res_filter in resources_w_filter:
        filtered = res_filter.filter(unavailable=[*already_unavailable, *selected_resources])
        if not filtered:
            return []
        selected_resources.extend(filtered)

    return list({*named_resources, *selected_resources})
