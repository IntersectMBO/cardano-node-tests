"""Utilities for `cardano-submit-api` REST service."""
import binascii
import json
import shutil
from pathlib import Path
from typing import NamedTuple

import requests

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.types import FileType


class SubmitApiError(Exception):
    pass


class SubmitApiOut(NamedTuple):
    txid: str
    response: requests.Response


@helpers.callonce
def has_submit_api() -> bool:
    """Check if `cardano-submit-api` REST service is available.

    Assumes that if available, the service is available on all local cluster instances.
    """
    submit_api_port = (
        cluster_nodes.get_cluster_type()
        .cluster_scripts.get_instance_ports(cluster_nodes.get_instance_num())
        .submit_api
    )
    if shutil.which("cardano-submit-api") and helpers.is_port_open(
        host="127.0.0.1", port=submit_api_port
    ):
        return True
    return False


def tx2cbor(tx_file: FileType, destination_dir: FileType = ".") -> Path:
    """Convert signed Tx to binary CBOR."""
    tx_file = Path(tx_file)
    out_file = Path(destination_dir).expanduser() / f"{tx_file.name}.cbor"

    with open(tx_file, encoding="utf-8") as in_fp:
        tx_loaded = json.load(in_fp)

    cbor_bin = binascii.unhexlify(tx_loaded["cborHex"])

    with open(out_file, "wb") as out_fp:
        out_fp.write(cbor_bin)

    return out_file


def post_cbor(cbor_file: FileType, url: str) -> requests.Response:
    """Post binary CBOR representation of Tx to `cardano-submit-api` service on `url`."""
    headers = {"Content-Type": "application/cbor"}
    with open(cbor_file, "rb") as in_fp:
        cbor_binary = in_fp.read()
        response = requests.post(url, headers=headers, data=cbor_binary)
    return response


def submit_tx(tx_file: FileType) -> SubmitApiOut:
    """Submit a signed Tx using `cardano-submit-api` service."""
    cbor_file = tx2cbor(tx_file=tx_file)

    submit_api_port = (
        cluster_nodes.get_cluster_type()
        .cluster_scripts.get_instance_ports(cluster_nodes.get_instance_num())
        .submit_api
    )

    url = f"http://localhost:{submit_api_port}/api/submit/tx"

    response = post_cbor(cbor_file=cbor_file, url=url)
    if not response:
        raise SubmitApiError(
            f"Failed to submit the tx.\n"
            f"  status: {response.status_code}\n"
            f"  reason: {response.reason}\n"
            f"  error: {response.text}"
        )

    out = SubmitApiOut(txid=response.json(), response=response)

    return out
