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


class Req:
    """Methods for working with external requirements.

    Attributes:
        id: An identification of the requirement.
        group: A group of the requirement.
        url: An url of the requirement.
        destination_dir: A destination directory for output files.
    """

    def __init__(
        self,
        id: str,
        group: str = "",
        url: str = "",
        destination_dir: tp.Optional[pl.Path] = None,
    ) -> None:
        # pylint: disable=invalid-name,redefined-builtin
        self.id = str(id)
        self.group = group
        self.url = url
        self.basename = f"req-{helpers.get_rand_str(8)}"

        self.destination_dir = (
            pl.Path(destination_dir).expanduser() if destination_dir else pl.Path()
        ) / "requirements"
        self.destination_dir.mkdir(parents=True, exist_ok=True)

    def success(self) -> bool:
        content = {"id": self.id, "group": self.group, "url": self.url, "status": Statuses.SUCCESS}
        helpers.write_json(
            out_file=self.destination_dir / f"{self.basename}_success.json", content=content
        )
        return True

    def failure(self) -> bool:
        content = {"id": self.id, "group": self.group, "url": self.url, "status": Statuses.FAILURE}
        helpers.write_json(
            out_file=self.destination_dir / f"{self.basename}_init.json", content=content
        )
        return False

    def start(self, url: str = "") -> bool:
        if url:
            self.url = url
        return self.failure()

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
