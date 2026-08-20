"""Unit tests for `cardano_node_tests.utils.clusterlib_utils`.

The tests must not depend on project-specific binaries (`cardano-cli`, ...) being present.
"""

import json
import pathlib as pl

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
