"""Tests for deriving keys from a mnemonic sentence."""

import dataclasses
import logging
import pathlib as pl
import shutil
import stat
import typing as tp

import allure
import hypothesis
import hypothesis.strategies as st
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import helpers

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent / "data" / "mnemonic_golden"

# pyrefly: ignore  # no-matching-overload
KEY_TYPES = tuple(clusterlib.KeyType)
KEY_TYPE_IDS = tuple(k.value.replace("-", "_") for k in KEY_TYPES)
# pyrefly: ignore  # no-matching-overload
OUT_FORMATS = tuple(clusterlib.OutputFormat)
OUT_FORMAT_IDS = tuple(k.value.replace("-", "_") for k in OUT_FORMATS)

# A small embedded list of *valid* BIP39 words (subset).
# Enough to build syntactically plausible phrases without importing anything.
_VALID_BIP39_SUBSET: tuple[str, ...] = (
    "abandon",
    "ability",
    "able",
    "about",
    "above",
    "absent",
    "absorb",
    "abstract",
    "absurd",
    "abuse",
    "access",
    "accident",
    "account",
    "accuse",
    "achieve",
    "acid",
    "acoustic",
    "acquire",
    "across",
    "act",
    "action",
    "actor",
    "actress",
    "actual",
    "adapt",
    "add",
    "addict",
    "address",
    "adjust",
    "admit",
    "adult",
    "advance",
)

_ALLOWED_COUNTS: frozenset[int] = frozenset({12, 15, 18, 21, 24})


# Case model
@dataclasses.dataclass(frozen=True)
class BadMnemonicCase:
    """A single non-compliant mnemonic-file test case."""

    name: str
    # When content is None, the path will be a *special* case (e.g., directory/symlink).
    content_bytes: bytes | None
    # How to materialize the path on disk.
    kind: tp.Literal[
        "file",
        "empty_file",
        "no_read_perm",
        "directory",
        "broken_symlink",
        "symlink_loop",
    ]


# Primitive strategies

ascii_word = st.text(
    alphabet=st.characters(min_codepoint=0x61, max_codepoint=0x7A),  # a-z
    min_size=3,
    max_size=10,
).filter(lambda s: s not in _VALID_BIP39_SUBSET)

weird_sep = st.sampled_from([" \u00a0 ", ",", ", ", ";"])

bad_token = st.one_of(
    ascii_word,  # Unknown word
    st.text(min_size=1, max_size=8).filter(
        lambda s: any(not c.isalpha() for c in s)
    ),  # Punctuation/digits
    st.sampled_from(["Über", "naïve", "résumé", "café"]),  # Diacritics / non-ASCII
    st.sampled_from(["🚀", "🔥", "🙂"]),  # Emoji
)

valid_token = st.sampled_from(_VALID_BIP39_SUBSET)


# Helpers to compose lines / encodings


@st.composite
def word_sequence(draw: st.DrawFn, *, count: int, ensure_invalid: bool) -> list[str]:
    """Build a list of tokens of a specific count.

    If ensure_invalid=True, inject at least one invalid token.
    """
    if ensure_invalid:
        # At least one invalid token, others may be valid or invalid.
        invalid_ix = draw(st.integers(min_value=0, max_value=max(0, count - 1)))
        tokens: list[str] = []
        for i in range(count):
            tok = draw(bad_token if i == invalid_ix else st.one_of(valid_token, bad_token))
            tokens.append(tok)
        return tokens

    # All valid tokens (from the subset); used for checksum-invalid cases downstream.
    return draw(st.lists(valid_token, min_size=count, max_size=count))


@st.composite
def join_with_weirdness(draw: st.DrawFn, *, tokens: tp.Sequence[str]) -> str:
    """Join tokens with odd/mixed separators and optional leading/trailing clutter."""
    # Draw separators (one fewer than tokens)
    seps = draw(st.lists(weird_sep, min_size=len(tokens) - 1, max_size=len(tokens) - 1))
    # Interleave tokens + seps
    core = "".join(a + b for a, b in zip(tokens, [*seps, ""]))
    # Draw optional prefix/suffix
    prefix = draw(st.sampled_from(["", " ", "\n", "\t", "\ufeff"]))  # Include BOM as char
    suffix = draw(st.sampled_from(["", " ", "\n", "\t", "\u200b"]))  # Zero-width space
    return prefix + core + suffix


def maybe_reencode(text: str) -> st.SearchStrategy[bytes]:
    """Return bytes in various encodings and with potential binary junk."""
    return st.one_of(
        st.just(text.encode("utf-8")),
        st.just(("\ufeff" + text).encode("utf-8")),  # UTF-8 with BOM
        st.just(text.encode("utf-16")),  # BOM included by default
        st.just(text.encode("utf-32")),  # BOM included by default
        # Inject NULs or random bytes around:
        st.binary(min_size=0, max_size=8).map(lambda b: b + text.encode("utf-8") + b"\x00"),
    )


# High-level bad file strategies


@st.composite
def wrong_count_files(draw: st.DrawFn) -> BadMnemonicCase:
    """Wrong number of words (not in {12, 15, 18, 21, 24})."""
    # Zero handled by empty_file elsewhere
    count = draw(st.integers(min_value=1, max_value=48).filter(lambda n: n not in _ALLOWED_COUNTS))
    tokens = draw(word_sequence(count=count, ensure_invalid=False))
    line = draw(join_with_weirdness(tokens=tokens))
    content = draw(maybe_reencode(line))
    return BadMnemonicCase(
        name=f"wrong_count_{count}",
        content_bytes=content,
        kind="file",
    )


@st.composite
def unknown_word_files(draw: st.DrawFn) -> BadMnemonicCase:
    """Produce allowed word count, but at least one token is not a valid BIP39 word."""
    count = draw(st.sampled_from(sorted(_ALLOWED_COUNTS)))
    tokens = draw(word_sequence(count=count, ensure_invalid=True))
    line = draw(join_with_weirdness(tokens=tokens))
    content = draw(maybe_reencode(line))
    return BadMnemonicCase(
        name=f"unknown_word_{count}",
        content_bytes=content,
        kind="file",
    )


@st.composite
def separator_mess_files(draw: st.DrawFn) -> BadMnemonicCase:
    """Only separators/newlines/tabs/comma issues (often two+ separators between words)."""
    count = draw(st.sampled_from(sorted(_ALLOWED_COUNTS)))
    tokens = draw(word_sequence(count=count, ensure_invalid=False))
    # Force messy separators (multiple mixed separators)
    weird_line = draw(join_with_weirdness(tokens=tokens))
    content = draw(maybe_reencode(weird_line))
    return BadMnemonicCase(
        name=f"separator_mess_{count}",
        content_bytes=content,
        kind="file",
    )


@st.composite
def encoding_garbage_files(draw: st.DrawFn) -> BadMnemonicCase:
    """Binary junk, odd encodings, NULs."""
    count = draw(st.sampled_from(sorted(_ALLOWED_COUNTS)))
    tokens = draw(word_sequence(count=count, ensure_invalid=False))
    line = draw(join_with_weirdness(tokens=tokens))
    content = draw(maybe_reencode(line))
    # Strengthen likelihood of binary by maybe wrapping with extra random bytes:
    blob = draw(st.binary(min_size=0, max_size=32))
    return BadMnemonicCase(
        name=f"encoding_garbage_{count}",
        content_bytes=blob + content + blob,
        kind="file",
    )


def empty_file_case() -> st.SearchStrategy[BadMnemonicCase]:
    return st.just(BadMnemonicCase(name="empty_file", content_bytes=b"", kind="empty_file"))


def no_read_perm_case() -> st.SearchStrategy[BadMnemonicCase]:
    return st.just(
        BadMnemonicCase(name="no_read_perm", content_bytes=b"abandon " * 12, kind="no_read_perm")
    )


def directory_case() -> st.SearchStrategy[BadMnemonicCase]:
    return st.just(
        BadMnemonicCase(name="directory_instead_of_file", content_bytes=None, kind="directory")
    )


def broken_symlink_case() -> st.SearchStrategy[BadMnemonicCase]:
    return st.just(
        BadMnemonicCase(name="broken_symlink", content_bytes=None, kind="broken_symlink")
    )


def symlink_loop_case() -> st.SearchStrategy[BadMnemonicCase]:
    return st.just(BadMnemonicCase(name="symlink_loop", content_bytes=None, kind="symlink_loop"))


# Master strategy: mix categories with reasonable weights
invalid_mnemonic_files: st.SearchStrategy[BadMnemonicCase] = st.one_of(
    wrong_count_files(),
    unknown_word_files(),
    separator_mess_files(),
    encoding_garbage_files(),
    empty_file_case(),
    no_read_perm_case(),
    directory_case(),
    broken_symlink_case(),
    symlink_loop_case(),
)

# Materialization helper


def _materialize_case(tmp_dir: pl.Path, case: BadMnemonicCase) -> pl.Path:
    """Create the on-disk artifact described by `case` and return its path."""
    path = tmp_dir / f"{case.name}.txt"
    match case.kind:
        case "file":
            assert case.content_bytes is not None
            path.write_bytes(case.content_bytes)
        case "empty_file":
            path.write_bytes(b"")
        case "no_read_perm":
            assert case.content_bytes is not None
            path.write_bytes(case.content_bytes)
            # Remove read permissions for owner/group/others.
            path.chmod(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)
        case "directory":
            path.mkdir(parents=False, exist_ok=False)
        case "broken_symlink":
            target = tmp_dir / "does_not_exist.txt"
            path.symlink_to(target)
        case "symlink_loop":
            a = tmp_dir / "loop_a"
            b = tmp_dir / "loop_b"
            a.symlink_to(b)
            b.symlink_to(a)
            # Return one end of the loop.
            path = a
        case _:
            err = f"Unhandled kind: {case.kind}"
            raise ValueError(err)
    return path


@common.SKIPIF_WRONG_ERA
class TestMnemonic:
    """Tests for mnemonic sentence."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("size", _ALLOWED_COUNTS)
    @pytest.mark.parametrize("out", ("file", "stdout"))
    @pytest.mark.parametrize("key_type", KEY_TYPES, ids=KEY_TYPE_IDS)
    @pytest.mark.parametrize("out_format", OUT_FORMATS, ids=OUT_FORMAT_IDS)
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
    @pytest.mark.parametrize("size", _ALLOWED_COUNTS)
    @pytest.mark.parametrize("key_type", KEY_TYPES, ids=KEY_TYPE_IDS)
    @pytest.mark.parametrize("out_format", OUT_FORMATS, ids=OUT_FORMAT_IDS)
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

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("key_type", KEY_TYPES, ids=KEY_TYPE_IDS)
    @common.hypothesis_settings(max_examples=300)
    @hypothesis.given(account_number=st.integers(min_value=0, max_value=2**31 - 1))
    @hypothesis.example(account_number=0)
    @hypothesis.example(account_number=2**31 - 1)
    def test_derive_account_key_number_property(
        self,
        cluster: clusterlib.ClusterLib,
        key_type: clusterlib.KeyType,
        account_number: int,
    ) -> None:
        """Test that `derive-from-mnemonic` accepts any valid account_number in [0, 2^31-1].

        For payment/stake keys, pass the same value as key_number, otherwise omit key_number.
        """
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        mnemonic_file = DATA_DIR / "gold_[0-bech32-payment-24]_mnemonic"

        key_number = (
            account_number
            if key_type in (clusterlib.KeyType.PAYMENT, clusterlib.KeyType.STAKE)
            else None
        )

        key_file = cluster.g_key.derive_from_mnemonic(
            key_name=f"{temp_template}_derived",
            key_type=key_type,
            mnemonic_file=mnemonic_file,
            account_number=account_number,
            key_number=key_number,
            out_format=clusterlib.OutputFormat.BECH32,
        )
        assert key_file.exists()


@common.SKIPIF_WRONG_ERA
class TestNegativeMnemonic:
    """Tests with invalid arguments."""

    @pytest.fixture
    def tmp_case_path(self) -> tp.Generator[pl.Path, None, None]:
        d = pl.Path(f"reject_mnemonics_{clusterlib.get_rand_str()}").resolve()
        d.mkdir(exist_ok=True)
        yield d
        shutil.rmtree(d)

    @allure.link(helpers.get_vcs_link())
    @hypothesis.given(size=st.integers().filter(lambda n: n not in _ALLOWED_COUNTS))
    @common.hypothesis_settings(max_examples=1_000)
    @pytest.mark.smoke
    def test_gen_invalid_size(
        self,
        cluster: clusterlib.ClusterLib,
        size: int,
    ):
        """Test generating a mnemonic with an invalid size."""
        common.get_test_id(cluster)
        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_key.gen_mnemonic(size=size)  # type: ignore
        err_value = str(excinfo.value)
        assert "Invalid mnemonic size" in err_value

    @common.hypothesis_settings(max_examples=500)
    @hypothesis.given(bad_case=invalid_mnemonic_files)
    def test_rejects_noncompliant_mnemonics(
        self,
        cluster: clusterlib.ClusterLib,
        bad_case: BadMnemonicCase,
        tmp_case_path: pl.Path,
    ) -> None:
        """Test that the CLI wrapper rejects malformed mnemonic files."""
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        target = _materialize_case(tmp_dir=tmp_case_path, case=bad_case)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_key.derive_from_mnemonic(
                key_name=temp_template,
                key_type=clusterlib.KeyType.DREP,
                mnemonic_file=target,
                account_number=0,
            )
        err_value = str(excinfo.value)
        assert (
            "Error reading mnemonic file" in err_value
            or "Error converting the mnemonic into a key" in err_value
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("key_type", KEY_TYPES, ids=KEY_TYPE_IDS)
    @common.hypothesis_settings(max_examples=300)
    @hypothesis.given(
        bad_value=st.one_of(
            # Integers outside the valid range
            st.integers(max_value=-1),
            st.integers(min_value=2**31),
            # Non-integer types
            st.floats(allow_nan=True, allow_infinity=True),
            st.text(
                alphabet=st.characters(blacklist_categories=["C", "Nd"]), min_size=1, max_size=5
            ),
            st.sampled_from([3.14, object(), [], {}]),
        )
    )
    @hypothesis.example(bad_value=-1)
    @hypothesis.example(bad_value=2**31)
    def test_reject_invalid_account_or_key_number(
        self,
        cluster: clusterlib.ClusterLib,
        key_type: clusterlib.KeyType,
        bad_value: object,
    ) -> None:
        """Property: derive_from_mnemonic rejects out-of-range or non-int account/key numbers."""
        temp_template = f"{common.get_test_id(cluster)}_{common.unique_time_str()}"
        mnemonic_file = DATA_DIR / "gold_[0-bech32-payment-24]_mnemonic"

        kwargs: dict[str, tp.Any] = {
            "key_name": f"{temp_template}_derived",
            "key_type": key_type,
            "mnemonic_file": mnemonic_file,
            "account_number": bad_value,
        }

        if key_type in (clusterlib.KeyType.PAYMENT, clusterlib.KeyType.STAKE):
            kwargs["key_number"] = bad_value

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_key.derive_from_mnemonic(**kwargs)
        err_value = str(excinfo.value)
        assert "unexpected" in err_value or "Error converting the mnemonic into a key" in err_value
