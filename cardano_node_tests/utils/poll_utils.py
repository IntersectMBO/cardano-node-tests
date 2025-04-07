"""Functions for working with SPO polls."""

import dataclasses
import json
import logging
import pathlib as pl

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True, order=True)
class PollFiles:
    poll: pl.Path
    metadata: pl.Path


def create_poll(
    cluster_obj: clusterlib.ClusterLib, question: str, answers: list[str], name_template: str
) -> PollFiles:
    """Create a poll and return the poll and metadata files."""
    poll_file = f"{name_template}_poll.json"
    metadata_file = f"{name_template}_poll_metadata.json"

    cli_out = cluster_obj.cli(
        [
            "cardano-cli",
            "compatible",
            "babbage",
            "governance",
            "create-poll",
            "--question",
            question,
            *helpers.prepend_flag("--answer", answers),
            "--out-file",
            poll_file,
        ],
        add_default_args=False,
    )

    stderr_out = cli_out.stderr.decode("utf-8")
    if "Poll created successfully" not in stderr_out:
        msg = f"Unexpected output from `governance create-poll`: {stderr_out}"
        raise clusterlib.CLIError(msg)

    with open(metadata_file, "w", encoding="utf-8") as fp_out:
        json.dump(json.loads(cli_out.stdout.rstrip().decode("utf-8")), fp_out)

    return PollFiles(poll=pl.Path(poll_file), metadata=pl.Path(metadata_file))


def answer_poll(
    cluster_obj: clusterlib.ClusterLib, poll_file: pl.Path, answer: int, name_template: str
) -> pl.Path:
    """Answer a poll and return the answer file."""
    answer_file = pl.Path(f"{name_template}_poll_answer.json")

    cli_out = cluster_obj.cli(
        [
            "cardano-cli",
            "compatible",
            "babbage",
            "governance",
            "answer-poll",
            "--poll-file",
            str(poll_file),
            "--answer",
            str(answer),
        ],
        add_default_args=False,
    )

    stderr_out = cli_out.stderr.decode("utf-8")
    if "Poll answer created successfully" not in stderr_out:
        msg = f"Unexpected output from `governance answer-poll`: {stderr_out}"
        raise clusterlib.CLIError(msg)

    with open(answer_file, "w", encoding="utf-8") as fp_out:
        json.dump(json.loads(cli_out.stdout.rstrip().decode("utf-8")), fp_out)

    return answer_file


def verify_poll(
    cluster_obj: clusterlib.ClusterLib, poll_file: pl.Path, tx_signed: pl.Path
) -> tuple[str, ...]:
    """Verify an answer to the poll."""
    cli_out = cluster_obj.cli(
        [
            "cardano-cli",
            "compatible",
            "babbage",
            "governance",
            "verify-poll",
            "--poll-file",
            str(poll_file),
            "--tx-file",
            str(tx_signed),
        ],
        add_default_args=False,
    )

    stderr_out = cli_out.stderr.decode("utf-8")
    if "Found valid poll answer" not in stderr_out:
        msg = f"Unexpected output from `governance verify-poll`: {stderr_out}"
        raise clusterlib.CLIError(msg)

    signers = json.loads(cli_out.stdout.decode("utf-8"))
    return tuple(signers)
