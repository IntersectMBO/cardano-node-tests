"""Functionality for getting a cluster instance that has required resources available."""

import random
import typing as tp


class BaseFilter:
    """Base class for resource filters."""

    def __init__(self, resources: tp.Iterable[str]):
        assert not isinstance(resources, str), "`resources` can't be single string"
        self.resources = resources

    def filter(self, unavailable: tp.Iterable[str], **kwargs: tp.Any) -> tp.List[str]:
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
    ) -> tp.List[str]:
        assert not isinstance(unavailable, str), "`unavailable` can't be single string"

        usable = [r for r in self.resources if r not in unavailable]
        if not usable:
            return []

        return [random.choice(usable)]


ResourcesType = tp.Iterable[tp.Union[str, BaseFilter]]


def get_resources(
    resources: ResourcesType,
    unavailable: tp.Iterable[str],
) -> tp.List[str]:
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
    selected_resources: tp.List[str] = []
    for res_filter in resources_w_filter:
        filtered = res_filter.filter(unavailable=[*already_unavailable, *selected_resources])
        if not filtered:
            return []
        selected_resources.extend(filtered)

    return list({*named_resources, *selected_resources})
