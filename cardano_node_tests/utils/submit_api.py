"""Utilities for `cardano-submit-api` REST service."""

import binascii
import dataclasses
import json
import logging
import pathlib as pl
import random
import shutil
import time

import requests
from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import custom_clusterlib
from cardano_node_tests.utils import http_client

LOGGER = logging.getLogger(__name__)


class SubmitApiError(Exception):
    pass


@dataclasses.dataclass(frozen=True)
class SubmitApiOut:
    txid: str
    response: requests.Response


def is_running() -> bool:
    """Check if `cardano-submit-api` REST service is running."""
    if not shutil.which("cardano-submit-api"):
        return False
    # TODO: `--metrics-port` is not available in older cardano-node releases, see node issue #4280
    # If the metrics port is not available, we can start the `cardano-submit-api` only on the first
    # cluster instance.
    return cluster_nodes.services_status(service_names=["submit_api"])[0].status == "RUNNING"


def tx2cbor(*, tx_file: clusterlib.FileType, destination_dir: clusterlib.FileType = ".") -> pl.Path:
    """Convert signed Tx to binary CBOR."""
    tx_file = pl.Path(tx_file)
    out_file = pl.Path(destination_dir).expanduser() / f"{tx_file.name}.cbor"

    with open(tx_file, encoding="utf-8") as in_fp:
        tx_loaded = json.load(in_fp)

    cbor_bin = binascii.unhexlify(tx_loaded["cborHex"])

    with open(out_file, "wb") as out_fp:
        out_fp.write(cbor_bin)

    return out_file


def post_cbor(*, cbor_file: clusterlib.FileType, url: str) -> requests.Response:
    """Post binary CBOR representation of Tx to `cardano-submit-api` service on `url`."""
    headers = {"Content-Type": "application/cbor"}
    with open(cbor_file, "rb") as in_fp:
        cbor_binary = in_fp.read()

    i = 0
    for i in range(1, 6):
        if i > 1:
            LOGGER.warning("Resubmitting transaction to submit-api.")

        try:
            response = http_client.get_session().post(
                url, headers=headers, data=cbor_binary, timeout=60
            )
        except requests.exceptions.ReadTimeout:
            pass
        else:
            break

        time.sleep(random.random())
    else:
        err = f"Failed to submit the tx after {i} attempts."
        raise SubmitApiError(err)

    return response


def submit_tx_bare(*, tx_file: clusterlib.FileType) -> SubmitApiOut:
    """Submit a signed Tx using `cardano-submit-api` service."""
    cbor_file = tx2cbor(tx_file=tx_file)

    submit_api_port = (
        cluster_nodes.get_cluster_type()
        .cluster_scripts.get_instance_ports(instance_num=cluster_nodes.get_instance_num())
        .submit_api
    )

    url = f"http://localhost:{submit_api_port}/api/submit/tx"

    response = post_cbor(cbor_file=cbor_file, url=url)
    if not response:
        msg = (
            f"Failed to submit the tx.\n"
            f"  status: {response.status_code}\n"
            f"  reason: {response.reason}\n"
            f"  error: {response.text}"
        )
        raise SubmitApiError(msg)

    out = SubmitApiOut(txid=response.json(), response=response)

    return out


def submit_tx(
    *,
    cluster_obj: clusterlib.ClusterLib,
    tx_file: clusterlib.FileType,
    txins: list[clusterlib.UTXOData],
    wait_blocks: int = 2,
) -> str:
    """Submit a transaction, resubmit if the transaction didn't make it to the chain.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        tx_file: A path to signed transaction file.
        txins: An iterable of `clusterlib.UTXOData`, specifying input UTxOs.
        wait_blocks: A number of new blocks to wait for (default = 2).
    """
    txid = ""
    err = None
    for r in range(20):
        err = None

        if r == 0:
            txid = submit_tx_bare(tx_file=tx_file).txid
        else:
            if not txid:
                msg = "The TxId is not known."
                raise SubmitApiError(msg)
            LOGGER.warning(f"Resubmitting transaction '{txid}' (from '{tx_file}').")
            try:
                submit_tx_bare(tx_file=tx_file)
            except SubmitApiError as exc:
                # Check if resubmitting failed because an input UTxO was already spent
                exc_str = str(exc)
                inputs_spent = (
                    '(ConwayMempoolFailure "All inputs are spent.'
                    in exc_str  # In cardano-node >= 10.6.0
                    or "(BadInputsUTxO" in exc_str
                )
                if not inputs_spent:
                    raise
                err = exc
                # If here, the TX is likely still in mempool and we need to wait

        cluster_obj.wait_for_new_block(wait_blocks)

        # Check that one of the input UTxOs can no longer be queried in order to verify
        # the TX was successfully submitted to the chain (that the TX is no longer in mempool).
        # An input is spent when its combination of hash and ix is not found in the list
        # of current UTxOs.
        # TODO: check that the transaction is 1-block deep (can't be done in CLI alone)
        utxo_data = cluster_obj.g_query.get_utxo(utxo=txins[0])
        if not utxo_data:
            break
    else:
        if err is not None:
            # Submitting the TX raised an exception as if the input was already
            # spent, but it was either not the case, or the TX is still in mempool.
            msg = f"Failed to resubmit the transaction '{txid}' (from '{tx_file}')."
            raise SubmitApiError(msg) from err

        msg = f"Transaction '{txid}' didn't make it to the chain (from '{tx_file}')."
        raise SubmitApiError(msg)

    # Create a `.submitted` status file when the Tx was successfully submitted
    custom_clusterlib.create_submitted_file(tx_file=tx_file)

    return txid
