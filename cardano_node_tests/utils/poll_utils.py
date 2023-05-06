"""Functions for working with SPO polls."""
import json
import logging
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Tuple

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


class PollFiles(NamedTuple):
    poll: Path
    metadata: Path


def create_poll(
    cluster_obj: clusterlib.ClusterLib, question: str, answers: List[str], name_template: str
) -> PollFiles:
    """Create a poll and return the poll and metadata files."""
    poll_file = f"{name_template}_poll.json"
    metadata_file = f"{name_template}_poll_metadata.json"

    cli_out = cluster_obj.cli(
        [
            "governance",
            "create-poll",
            "--question",
            question,
            *helpers.prepend_flag("--answer", answers),
            "--out-file",
            poll_file,
        ]
    )

    stderr_out = cli_out.stderr.decode("utf-8")
    if "Poll created successfully" not in stderr_out:
        raise clusterlib.CLIError(f"Unexpected output from `governance create-poll`: {stderr_out}")

    with open(metadata_file, "w", encoding="utf-8") as fp_out:
        json.dump(json.loads(cli_out.stdout.rstrip().decode("utf-8")), fp_out)

    return PollFiles(poll=Path(poll_file), metadata=Path(metadata_file))


def answer_poll(
    cluster_obj: clusterlib.ClusterLib, poll_file: Path, answer: int, name_template: str
) -> Path:
    """Answer a poll and return the answer file."""
    answer_file = Path(f"{name_template}_poll_answer.json")

    cli_out = cluster_obj.cli(
        [
            "governance",
            "answer-poll",
            "--poll-file",
            str(poll_file),
            "--answer",
            str(answer),
        ]
    )

    stderr_out = cli_out.stderr.decode("utf-8")
    if "Poll answer created successfully" not in stderr_out:
        raise clusterlib.CLIError(f"Unexpected output from `governance answer-poll`: {stderr_out}")

    with open(answer_file, "w", encoding="utf-8") as fp_out:
        json.dump(json.loads(cli_out.stdout.rstrip().decode("utf-8")), fp_out)

    return answer_file


def verify_poll(
    cluster_obj: clusterlib.ClusterLib, poll_file: Path, tx_signed: Path
) -> Tuple[str, ...]:
    """Verify an answer to the poll."""
    # TODO: Node 8.0.0-rc1 uses the old `--signed-tx-file` argument.
    # Can be removed if 8.0.0 is released with the new `--tx-file` argument,
    # as there is no other release that uses the old argument.
    verify_poll_tx_arg = (
        "--tx-file"
        if clusterlib_utils.cli_has(command="governance verify-poll --tx-file")
        else "--signed-tx-file"
    )

    cli_out = cluster_obj.cli(
        [
            "governance",
            "verify-poll",
            "--poll-file",
            str(poll_file),
            verify_poll_tx_arg,
            str(tx_signed),
        ]
    )

    stderr_out = cli_out.stderr.decode("utf-8")
    if "Found valid poll answer" not in stderr_out:
        raise clusterlib.CLIError(f"Unexpected output from `governance verify-poll`: {stderr_out}")

    signers = json.loads(cli_out.stdout.decode("utf-8"))
    return tuple(signers)
