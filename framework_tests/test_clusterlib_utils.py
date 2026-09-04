"""Unit tests for `cardano_node_tests.utils.clusterlib_utils`.

The tests must not depend on project-specific binaries (`cardano-cli`, ...) being present.
"""

import json
import pathlib as pl
import typing as tp

import cbor2
import pytest

from cardano_node_tests.utils import clusterlib_utils

KEY_HASH1 = "9e1156acae8bd72bc1815d0be9fcb64e2d50e61f4204c45b901dad6b"
KEY_HASH2 = "7c2086ea4ebaa880c6e6c70604c0deb37ffbaa0567aec0bea8564055"


def write_script(*, script: dict, dest_dir: pl.Path) -> pl.Path:
    """Write a script into a file and return its path."""
    script_file = dest_dir / "script.json"
    with open(script_file, "w", encoding="utf-8") as fp_out:
        json.dump(script, fp_out, indent=4)
    return script_file


def write_tx_body(*, aux_data: tp.Any, dest_dir: pl.Path) -> pl.Path:
    """Write a Tx body file with the given auxiliary data and return its path."""
    # A Tx body is a 4-element array - body, witnesses, validity flag and auxiliary data
    cbor_body = cbor2.dumps([{}, {}, True, aux_data])
    body_file = dest_dir / "tx.body"
    with open(body_file, "w", encoding="utf-8") as fp_out:
        json.dump(
            {"type": "Unwitnessed Tx ConwayEra", "description": "", "cborHex": cbor_body.hex()},
            fp_out,
        )
    return body_file


class TestLoadTxMetadata:
    """Tests for `load_tx_metadata`.

    The metadata map is stored under CBOR tag 259, whose content `cbor2` decodes into
    immutable containers. The loaded metadata must still be mutable and JSON serializable.
    """

    def test_metadata(self, tmp_path: pl.Path):
        """Load metadata that nests a map and a list."""
        metadata = {1: "foo", 2: [1, 2, {3: "bar"}]}
        body_file = write_tx_body(aux_data=cbor2.CBORTag(259, {0: metadata}), dest_dir=tmp_path)

        loaded = clusterlib_utils.load_tx_metadata(tx_body_file=body_file)

        assert loaded.metadata == metadata
        assert isinstance(loaded.metadata, dict)
        assert isinstance(loaded.metadata[2], list)
        assert isinstance(loaded.metadata[2][2], dict)
        # The metadata must be JSON serializable, so keys can be converted to strings
        assert json.loads(json.dumps(loaded.metadata)) == {"1": "foo", "2": [1, 2, {"3": "bar"}]}

    def test_metadata_with_set(self, tmp_path: pl.Path):
        """Load metadata that nests a set, which is converted to a list."""
        body_file = write_tx_body(aux_data=cbor2.CBORTag(259, {0: {1: {"foo"}}}), dest_dir=tmp_path)

        loaded = clusterlib_utils.load_tx_metadata(tx_body_file=body_file)

        assert loaded.metadata == {1: ["foo"]}
        assert json.dumps(loaded.metadata)

    def test_no_metadata(self, tmp_path: pl.Path):
        """Load metadata from a Tx body that has no auxiliary data."""
        body_file = write_tx_body(aux_data=None, dest_dir=tmp_path)

        loaded = clusterlib_utils.load_tx_metadata(tx_body_file=body_file)

        assert loaded.metadata == {}
        assert loaded.aux_data == []


class TestGetReferenceScriptSize:
    """Tests for `get_reference_script_size`.

    The expected sizes are the sizes of the scripts as serialized by the ledger. The `sig` and
    `any` sizes were confirmed against `FeeTooSmallUTxO` ledger errors on the Preview testnet,
    where `minFeeRefScriptCostPerByte` is 15: the fee was short by exactly 32 * 15 for the `sig`
    script and by 167 * 15 for the `any` script below.
    """

    def test_sig(self, tmp_path: pl.Path):
        """Get size of a `sig` script."""
        script_file = write_script(script={"keyHash": KEY_HASH1, "type": "sig"}, dest_dir=tmp_path)
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 32

    def test_any_with_slot(self, tmp_path: pl.Path):
        """Get size of an `any` script that nests `sig` scripts and a slot condition."""
        script_file = write_script(
            script={
                "scripts": [
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"slot": 100, "type": "after"},
                ],
                "type": "any",
            },
            dest_dir=tmp_path,
        )
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 167

    def test_all(self, tmp_path: pl.Path):
        """Get size of an `all` script."""
        script_file = write_script(
            script={
                "scripts": [
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                ],
                "type": "all",
            },
            dest_dir=tmp_path,
        )
        # 2 bytes for the outer array and tag, 1 byte for the inner array, 2 * 32 bytes for the
        # nested `sig` scripts
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 67

    def test_at_least(self, tmp_path: pl.Path):
        """Get size of an `atLeast` script."""
        script_file = write_script(
            script={
                "required": 2,
                "scripts": [
                    {"keyHash": KEY_HASH1, "type": "sig"},
                    {"keyHash": KEY_HASH2, "type": "sig"},
                ],
                "type": "atLeast",
            },
            dest_dir=tmp_path,
        )
        # One more byte than the `all` script above, for the `required` value
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 68

    def test_before(self, tmp_path: pl.Path):
        """Get size of a `before` script."""
        script_file = write_script(script={"slot": 100, "type": "before"}, dest_dir=tmp_path)
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == 4

    def test_plutus(self, tmp_path: pl.Path):
        """Get size of a Plutus script, which is the size of the bare script."""
        plutus_bytes = b"\x01\x02\x03\x04\x05"
        script_file = write_script(
            script={
                "type": "PlutusScriptV3",
                "description": "",
                "cborHex": cbor2.dumps(plutus_bytes).hex(),
            },
            dest_dir=tmp_path,
        )
        assert clusterlib_utils.get_reference_script_size(script_file=script_file) == len(
            plutus_bytes
        )

    def test_unsupported_type(self, tmp_path: pl.Path):
        """Fail on an unknown simple script type."""
        script_file = write_script(script={"type": "unknown"}, dest_dir=tmp_path)
        with pytest.raises(ValueError, match="Unsupported simple script type: unknown"):
            clusterlib_utils.get_reference_script_size(script_file=script_file)
