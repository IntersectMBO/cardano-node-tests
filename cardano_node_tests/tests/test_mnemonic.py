"""Tests for deriving keys from a mnemonic sentence."""

import logging
import pathlib as pl
import typing as tp

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent / "data" / "mnemonic_golden"


@common.SKIPIF_WRONG_ERA
class TestMnemonic:
    """Tests for mnemonic sentence."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("size", (12, 15, 18, 21, 24))
    @pytest.mark.parametrize("out", ("file", "stdout"))
    @pytest.mark.parametrize(
        "key_type",
        # pyrefly: ignore  # no-matching-overload
        iter(clusterlib.KeyType),
        # pyrefly: ignore  # no-matching-overload
        ids=(k.value.replace("-", "_") for k in iter(clusterlib.KeyType)),
    )
    @pytest.mark.parametrize(
        "out_format",
        # pyrefly: ignore  # no-matching-overload
        iter(clusterlib.OutputFormat),
        # pyrefly: ignore  # no-matching-overload
        ids=(k.value.replace("-", "_") for k in iter(clusterlib.OutputFormat)),
    )
    @pytest.mark.parametrize("path_num", (0, 2**31 - 1))
    @pytest.mark.smoke
    def test_gen_and_deriv(
        self,
        cluster: clusterlib.ClusterLib,
        size: tp.Literal[12, 15, 18, 21, 24],
        out: str,
        key_type: clusterlib.KeyType,
        out_format: clusterlib.OutputFormat,
        path_num: int,
    ):
        """Test `generate-mnemonic` and `derive-from-mnemonic`."""
        temp_template = common.get_test_id(cluster)
        mnemonic_file = pl.Path(f"{temp_template}_mnemonic")

        if out == "stdout":
            words = cluster.g_key.gen_mnemonic(size=size)
            mnemonic_file.write_text(" ".join(words))
        else:
            words = cluster.g_key.gen_mnemonic(size=size, out_file=mnemonic_file)
        assert len(words) == size

        key_number = (
            path_num
            if (key_type in (clusterlib.KeyType.PAYMENT, clusterlib.KeyType.STAKE))
            else None
        )
        key_file = cluster.g_key.derive_from_mnemonic(
            key_name=f"{temp_template}_derived",
            key_type=key_type,
            mnemonic_file=mnemonic_file,
            account_number=path_num,
            key_number=key_number,
            out_format=out_format,
        )
        assert key_file.exists()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("size", (12, 15, 18, 21, 24))
    @pytest.mark.parametrize(
        "key_type",
        # pyrefly: ignore  # no-matching-overload
        iter(clusterlib.KeyType),
        # pyrefly: ignore  # no-matching-overload
        ids=(k.value.replace("-", "_") for k in iter(clusterlib.KeyType)),
    )
    @pytest.mark.parametrize(
        "out_format",
        # pyrefly: ignore  # no-matching-overload
        iter(clusterlib.OutputFormat),
        # pyrefly: ignore  # no-matching-overload
        ids=(k.value.replace("-", "_") for k in iter(clusterlib.OutputFormat)),
    )
    @pytest.mark.parametrize("path_num", (0, 2**31 - 1))
    @pytest.mark.smoke
    def test_golden_deriv(
        self,
        cluster: clusterlib.ClusterLib,
        size: tp.Literal[12, 15, 18, 21, 24],
        key_type: clusterlib.KeyType,
        out_format: clusterlib.OutputFormat,
        path_num: int,
    ):
        """Test `derive-from-mnemonic` using golden files."""
        temp_template = common.get_test_id(cluster)

        stem = (
            f"gold_[{path_num}-{out_format.value.replace('-', '_')}-"
            f"{key_type.value.replace('-', '_')}-{size}]"
        )
        mnemonic_file = DATA_DIR / f"{stem}_mnemonic"
        golden_key_file = DATA_DIR / f"{stem}_derived.skey"

        assert mnemonic_file.exists()
        assert golden_key_file.exists()

        key_number = (
            path_num
            if (key_type in (clusterlib.KeyType.PAYMENT, clusterlib.KeyType.STAKE))
            else None
        )
        key_file = cluster.g_key.derive_from_mnemonic(
            key_name=f"{temp_template}_derived",
            key_type=key_type,
            mnemonic_file=mnemonic_file,
            account_number=path_num,
            key_number=key_number,
            out_format=out_format,
        )
        assert key_file.exists()

        assert helpers.checksum(filename=key_file) == helpers.checksum(filename=golden_key_file)
