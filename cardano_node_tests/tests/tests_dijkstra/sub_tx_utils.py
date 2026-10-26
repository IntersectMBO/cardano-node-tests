"""Small assembly helpers for independently authorized payment children."""

import dataclasses
import json
import pathlib as pl

from cardano_clusterlib import clusterlib


@dataclasses.dataclass(frozen=True)
class SignedChild:
    """Keep independently signed child artifacts and predicted output references."""

    output: clusterlib.SubTxRawOutput
    signed_file: pl.Path
    txid: str


def build_child(
    *,
    cluster: clusterlib.ClusterLib,
    name: str,
    txins: list[clusterlib.UTXOData],
    txouts: list[clusterlib.TxOut],
    signing_keys: list[pl.Path],
    reference_inputs: list[clusterlib.UTXOData] | None = None,
) -> SignedChild:
    """Build and independently sign an explicitly balanced or unbalanced child."""
    output = cluster.g_sub_transaction.build_raw_tx_bare(
        out_file=f"{name}.body",
        txins=txins,
        txouts=txouts,
        readonly_reference_txins=reference_inputs or [],
    )
    signed = cluster.g_sub_transaction.sign_tx(
        sub_tx_file=output.out_file, tx_name=name, signing_key_files=signing_keys
    )
    return SignedChild(
        output=output,
        signed_file=signed,
        txid=cluster.g_sub_transaction.get_txid(signed_sub_tx_file=signed),
    )


def build_parent(
    *,
    cluster: clusterlib.ClusterLib,
    name: str,
    sponsor: clusterlib.AddressRecord,
    txins: list[clusterlib.UTXOData],
    txouts: list[clusterlib.TxOut],
    children: list[SignedChild],
    fee_lovelace: int = 500_000,
    reference_inputs: list[clusterlib.UTXOData] | None = None,
    signing_keys: list[pl.Path] | None = None,
) -> tuple[clusterlib.TxRawOutput, pl.Path]:
    """Assemble file-only children without automatically repairing batch balance."""
    output = cluster.g_transaction.build_raw_tx(
        src_address=sponsor.address,
        tx_name=name,
        txins=txins,
        txouts=txouts,
        readonly_reference_txins=reference_inputs or [],
        fee=fee_lovelace,
        sub_transaction_files=[c.signed_file for c in children],
    )
    signed = cluster.g_transaction.sign_tx(
        tx_body_file=output.out_file,
        tx_name=name,
        signing_key_files=signing_keys if signing_keys is not None else [sponsor.skey_file],
    )
    parent_cbor = json.loads(signed.read_text(encoding="utf-8"))["cborHex"]
    for child in children:
        assert json.loads(child.signed_file.read_text(encoding="utf-8"))["cborHex"] in parent_cbor
    return output, signed


def check_child_outputs(*, cluster: clusterlib.ClusterLib, child: SignedChild) -> None:
    """Check exact originating child output references, values, and addresses."""
    actual = cluster.g_query.get_utxo(
        txin=[f"{child.txid}#{ix}" for ix in range(child.output.txouts_count)]
    )
    assert sorted((u.address, u.amount, u.coin) for u in actual) == sorted(
        (o.address, o.amount, o.coin) for o in child.output.txouts
    )
