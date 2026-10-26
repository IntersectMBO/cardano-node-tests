"""Small assembly helpers for independently authorized payment children."""

import dataclasses
import io
import json
import pathlib as pl
import typing as tp

import cbor2
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
    tx_files: clusterlib.TxFiles | None = None,
    treasury_donation: int | None = None,
) -> SignedChild:
    """Build and independently sign an explicitly balanced or unbalanced child."""
    output = cluster.g_sub_transaction.build_raw_tx_bare(
        out_file=f"{name}.body",
        txins=txins,
        txouts=txouts,
        readonly_reference_txins=reference_inputs or [],
        tx_files=tx_files,
        treasury_donation=treasury_donation,
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
    collateral_inputs: list[clusterlib.UTXOData] | None = None,
) -> tuple[clusterlib.TxRawOutput, pl.Path]:
    """Assemble file-only children without automatically repairing batch balance."""
    output = cluster.g_transaction.build_raw_tx(
        src_address=sponsor.address,
        tx_name=name,
        txins=txins,
        txouts=txouts,
        readonly_reference_txins=reference_inputs or [],
        fee=fee_lovelace,
        collateral_txins=collateral_inputs or [],
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


def remove_output_datum_hash(*, tx_file: pl.Path, out_file: pl.Path) -> pl.Path:
    """Remove the first output's hash before signing, preserving all other CBOR bytes.

    Retain the CLI-generated datum witness and integrity hash to exercise its
    supplemental scope. Never rewrite an embedded signed child or accept an
    already key-witnessed transaction as input to this mutation.
    """
    envelope = json.loads(tx_file.read_text(encoding="utf-8"))
    encoded = bytes.fromhex(envelope["cborHex"])
    transaction = cbor2.loads(encoded)
    assert encoded[0] == 0x83 and not transaction[1].get(0)
    stream = io.BytesIO(encoded)
    stream.read(1)
    map_header = stream.read(1)[0]
    assert 0xA0 <= map_header < 0xB8, "Expected a small definite body map"
    decoder = cbor2.CBORDecoder(stream)
    start = end = 0
    outputs = transaction[0][1]
    for _ in range(map_header - 0xA0):
        key = decoder.decode()
        value_start = stream.tell()
        value = decoder.decode()
        if key == 1:
            start, end = value_start, stream.tell()
            outputs = value
    assert start and end
    if isinstance(outputs[0], list):
        assert len(outputs[0]) == 3
        outputs[0] = outputs[0][:2]
    else:
        assert outputs[0][2][0] == 0
        del outputs[0][2]
    changed = encoded[:start] + cbor2.dumps(outputs) + encoded[end:]
    decoded = cbor2.loads(changed)
    assert decoded[1:] == transaction[1:]
    assert {k for k in transaction[0] if transaction[0][k] != decoded[0][k]} == {1}
    out_file.write_text(json.dumps({**envelope, "cborHex": changed.hex()}), encoding="utf-8")
    return out_file


def replace_witnesses(
    *, tx_file: pl.Path, out_file: pl.Path, witnesses: dict[int, tp.Any]
) -> pl.Path:
    """Change unsigned witness supply without re-encoding any transaction body."""
    envelope = json.loads(tx_file.read_text(encoding="utf-8"))
    encoded = bytes.fromhex(envelope["cborHex"])
    stream = io.BytesIO(encoded)
    assert stream.read(1) == b"\x83"
    decoder = cbor2.CBORDecoder(stream)
    decoder.decode()
    start = stream.tell()
    original_witnesses = decoder.decode()
    end = stream.tell()
    assert not original_witnesses.get(0) and not witnesses.get(0)
    changed = encoded[:start] + cbor2.dumps(witnesses) + encoded[end:]
    out_file.write_text(json.dumps({**envelope, "cborHex": changed.hex()}), encoding="utf-8")
    return out_file


def append_body_field(*, tx_file: pl.Path, out_file: pl.Path, key: int, value: tp.Any) -> pl.Path:
    """Append an absent field before signing without re-encoding embedded children."""
    envelope = json.loads(tx_file.read_text(encoding="utf-8"))
    encoded = bytes.fromhex(envelope["cborHex"])
    decoded = cbor2.loads(encoded)
    assert key not in decoded[0] and not decoded[1].get(0)
    stream = io.BytesIO(encoded)
    assert stream.read(1) == b"\x83"
    cbor2.CBORDecoder(stream).decode()
    body_end = stream.tell()
    assert 0xA0 <= encoded[1] < 0xB7
    changed = (
        b"\x83"
        + bytes([encoded[1] + 1])
        + encoded[2:body_end]
        + cbor2.dumps(key)
        + cbor2.dumps(value)
        + encoded[body_end:]
    )
    assert cbor2.loads(changed)[0] == {**decoded[0], key: cbor2.loads(cbor2.dumps(value))}
    out_file.write_text(json.dumps({**envelope, "cborHex": changed.hex()}), encoding="utf-8")
    return out_file
