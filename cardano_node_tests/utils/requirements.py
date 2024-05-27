"""Functionality for tracking execution of external requirements."""

import json
import logging
import pathlib as pl
import typing as tp

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


class GroupsKnown:
    CHANG_US: tp.Final[str] = "chang_us"


class Statuses:
    SUCCESS: tp.Final[str] = "success"
    FAILURE: tp.Final[str] = "failure"
    UNCOVERED: tp.Final[str] = "uncovered"
    PARTIAL_SUCCESS: tp.Final[str] = "partial_success"


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
    ) -> None:
        # pylint: disable=invalid-name,redefined-builtin
        self.id = str(id)
        self.group = group
        self.url = url
        self.basename = f"req-{helpers.get_rand_str(8)}"

    def _get_dest_dir(self) -> pl.Path:
        dest_dir = pl.Path.cwd() / "requirements"
        dest_dir.mkdir(parents=True, exist_ok=True)
        return dest_dir

    def success(self) -> bool:
        content = {"id": self.id, "group": self.group, "url": self.url, "status": Statuses.SUCCESS}
        helpers.write_json(
            out_file=self._get_dest_dir() / f"{self.basename}_success.json", content=content
        )
        return True

    def failure(self) -> bool:
        content = {"id": self.id, "group": self.group, "url": self.url, "status": Statuses.FAILURE}
        helpers.write_json(
            out_file=self._get_dest_dir() / f"{self.basename}_init.json", content=content
        )
        return False

    def start(self, url: str = "") -> "Req":
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
        group_collected = collected.get(group_name)
        if not group_collected:
            group_collected = {}
            collected[group_name] = group_collected

        req_id = req_rec["id"]
        id_collected = group_collected.get(req_id)
        if id_collected and id_collected["status"] == Statuses.SUCCESS:
            continue
        if not id_collected:
            id_collected = {}
            group_collected[req_id] = id_collected
        id_collected["status"] = req_rec["status"]
        id_collected["url"] = req_rec.get("url") or ""

    return collected


def get_mapped_req(mapping: pl.Path, executed_req: dict) -> dict:
    """Get mapped requirements."""
    with open(mapping, encoding="utf-8") as in_fp:
        requirements_mapping = json.load(in_fp)

    for group, reqs in requirements_mapping.items():
        executed_group = executed_req.get(group) or {}

        for req_id, dependencies in reqs.items():
            url = None
            dependencies_success = []
            dependencies_failures = []

            for p_req in dependencies:
                p_status = executed_group.get(p_req, {}).get("status")

                if not url:
                    url = executed_group.get(p_req, {}).get("url")

                if p_status == Statuses.SUCCESS:
                    dependencies_success.append(p_req)
                elif p_status == Statuses.FAILURE:
                    dependencies_failures.append(p_req)

            # If any partial requirement failed, the overall outcome would be failed
            if dependencies_failures:
                status = Statuses.FAILURE
            # If none partial requirement is covered, the overall outcome would be uncovered
            elif not (dependencies_success or dependencies_failures):
                status = Statuses.UNCOVERED
            # If all partial requirements are successful, the overall outcome would be success
            elif len(dependencies_success) == len(dependencies):
                status = Statuses.SUCCESS
            else:
                status = Statuses.PARTIAL_SUCCESS

            executed_req[group][req_id] = {"status": status, "url": url}

    return executed_req
