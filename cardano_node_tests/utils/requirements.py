"""Functionality for tracking execution of external requirements."""

import enum
import json
import logging
import pathlib as pl
import typing as tp

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


class GroupsKnown:
    CHANG_US: tp.Final[str] = "chang_us"


class Statuses(enum.Enum):
    success = 1
    failure = 2
    partial_success = 3
    uncovered = 4


class Req:
    """Methods for working with external requirements.

    Attributes:
        id: An identification of the requirement.
        group: A group of the requirement.
        url: An url of the requirement.
    """

    def __init__(
        self,
        id: str,
        group: str = "",
        url: str = "",
        enabled: bool = True,
    ) -> None:
        self.id = str(id)
        self.group = group
        self.url = url
        self.basename = f"req-{helpers.get_rand_str(8)}"
        self.enabled = enabled

    def _get_dest_dir(self) -> pl.Path:
        dest_dir = pl.Path.cwd() / "requirements"
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir

    def success(self) -> bool:
        if not self.enabled:
            return True

        content = {
            "id": self.id,
            "group": self.group,
            "url": self.url,
            "status": Statuses.success.name,
        }
        helpers.write_json(
            out_file=self._get_dest_dir() / f"{self.basename}_success.json", content=content
        )
        return True

    def failure(self) -> bool:
        if not self.enabled:
            return False

        content = {
            "id": self.id,
            "group": self.group,
            "url": self.url,
            "status": Statuses.failure.name,
        }
        helpers.write_json(
            out_file=self._get_dest_dir() / f"{self.basename}_init.json", content=content
        )
        return False

    def start(self, url: str = "") -> "Req":
        if not self.enabled:
            return self

        if url:
            self.url = url
        self.failure()
        return self

    def __repr__(self) -> str:
        return f"<Req: id='{self.id}', group='{self.group}', url='{self.url}'>"


def collect_executed_req(base_dir: pl.Path) -> dict:
    """Collect executed requirements."""
    collected: dict = {}
    for rf in base_dir.glob("**/requirements/req-*.json"):
        with open(rf, encoding="utf-8") as in_fp:
            req_rec = json.load(in_fp)

        group_name = req_rec["group"]
        group_collected: dict = collected.get(group_name) or {}
        if not group_collected:
            collected[group_name] = group_collected

        req_id = req_rec["id"]
        id_collected: dict = group_collected.get(req_id) or {}
        if id_collected and id_collected["status"] == Statuses.success.name:
            continue
        if not id_collected:
            group_collected[req_id] = id_collected
        id_collected["status"] = req_rec["status"]
        id_collected["url"] = req_rec.get("url") or ""

    return collected


def merge_reqs(*reqs: dict[str, dict]) -> dict:
    """Merge requirements."""
    merged: dict[str, dict] = {}
    for report in reqs:
        for gname, greqs in report.items():
            merged_group = merged.get(gname) or {}
            for req_id, req_data in greqs.items():
                merged_rec = merged_group.get(req_id) or {}
                merged_status_key = merged_rec.get("status") or "uncovered"

                merged_status_val = Statuses[merged_status_key].value

                req_status_val = Statuses[req_data["status"]].value

                if not merged_rec or req_status_val < merged_status_val:
                    merged_group[req_id] = req_data
            merged[gname] = merged_group
    return merged


def get_mapped_req(mapping: pl.Path, executed_req: dict) -> dict:  # noqa: C901
    """Get mapped requirements."""
    with open(mapping, encoding="utf-8") as in_fp:
        requirements_mapping = json.load(in_fp)

    errors: dict[str, set[str]] = {}
    for group, reqs in requirements_mapping.items():
        reqs_set = set(reqs.keys())
        executed_group = executed_req.get(group) or {}
        if not executed_group:
            executed_req[group] = executed_group

        group_errors: set[str] = set()
        for req_id, dependencies in reqs.items():
            deps_in_reqs = reqs_set.intersection(dependencies)
            if deps_in_reqs:
                group_errors.update(deps_in_reqs)
            # If there are already any errors, we'll just validate the rest of the mappings
            if errors or group_errors:
                continue

            url = None
            dependencies_success = []
            dependencies_failures = []

            for p_req in dependencies:
                p_status = executed_group.get(p_req, {}).get("status")

                if not url:
                    url = executed_group.get(p_req, {}).get("url")

                if p_status == Statuses.success.name:
                    dependencies_success.append(p_req)
                elif p_status == Statuses.failure.name:
                    dependencies_failures.append(p_req)

            # If any partial requirement failed, the overall outcome would be failed
            if dependencies_failures:
                status = Statuses.failure.name
            # If none partial requirement is covered, the overall outcome would be uncovered
            elif not (dependencies_success or dependencies_failures):
                status = Statuses.uncovered.name
            # If all partial requirements are successful, the overall outcome would be success
            elif len(dependencies_success) == len(dependencies):
                status = Statuses.success.name
            else:
                status = Statuses.partial_success.name

            executed_req[group][req_id] = {"status": status, "url": url}

        if group_errors:
            errors[group] = group_errors

    if errors:
        err_msgs = [
            f"Mapping error in group '{g}': the following dependencies are also top level keys: {d}"
            for g, d in errors.items()
        ]
        err_str = "\n".join(err_msgs)
        raise ValueError(err_str)

    return executed_req
