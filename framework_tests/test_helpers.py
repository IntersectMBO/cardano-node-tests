"""Unit tests for `cardano_node_tests.utils.helpers`.

The tests must not depend on project-specific binaries (`bech32`, `cardano-cli`, ...) being
present. Subprocess tests use `sys.executable` or POSIX shell builtins, and tests for helpers
that wrap external tools monkeypatch `run_command`.
"""

import argparse
import hashlib
import inspect
import json
import os
import pathlib as pl
import string
import sys
import typing as tp

import pytest

from cardano_node_tests.utils import helpers


class TestChangeCwd:
    """Tests for `change_cwd`."""

    def test_change_and_restore(self, tmp_path: pl.Path):
        """Change CWD inside the context and restore it afterwards."""
        orig_cwd = pl.Path.cwd()
        with helpers.change_cwd(tmp_path):
            assert pl.Path.cwd() == tmp_path
        assert pl.Path.cwd() == orig_cwd

    def test_restore_on_error(self, tmp_path: pl.Path):
        """Restore CWD even when the block raises."""
        orig_cwd = pl.Path.cwd()
        err = "boom"
        with pytest.raises(RuntimeError), helpers.change_cwd(tmp_path):
            raise RuntimeError(err)
        assert pl.Path.cwd() == orig_cwd


class TestEnviron:
    """Tests for the `environ` context manager."""

    def test_set_and_restore(self, monkeypatch: pytest.MonkeyPatch):
        """Set a new value inside the context and restore the original afterwards."""
        monkeypatch.setenv("HELPERS_TEST_VAR", "orig")
        with helpers.environ({"HELPERS_TEST_VAR": "new"}):
            assert os.environ["HELPERS_TEST_VAR"] == "new"
        assert os.environ["HELPERS_TEST_VAR"] == "orig"

    def test_unset_after_context(self, monkeypatch: pytest.MonkeyPatch):
        """Remove a variable that was not set before entering the context."""
        monkeypatch.delenv("HELPERS_TEST_VAR", raising=False)
        with helpers.environ({"HELPERS_TEST_VAR": "new"}):
            assert os.environ["HELPERS_TEST_VAR"] == "new"
        assert "HELPERS_TEST_VAR" not in os.environ

    def test_var_deleted_inside_context(self, monkeypatch: pytest.MonkeyPatch):
        """Don't fail when a variable that was unset before is deleted inside the context."""
        monkeypatch.delenv("HELPERS_TEST_VAR", raising=False)
        with helpers.environ({"HELPERS_TEST_VAR": "new"}):
            del os.environ["HELPERS_TEST_VAR"]
        assert "HELPERS_TEST_VAR" not in os.environ

    def test_restore_on_error(self, monkeypatch: pytest.MonkeyPatch):
        """Restore the original value even when the block raises."""
        monkeypatch.setenv("HELPERS_TEST_VAR", "orig")
        err = "boom"
        with pytest.raises(RuntimeError), helpers.environ({"HELPERS_TEST_VAR": "new"}):
            raise RuntimeError(err)
        assert os.environ["HELPERS_TEST_VAR"] == "orig"


class TestEnvVarHelpers:
    """Tests for `is_truthy_env_var`, `get_env_int` and `get_env_path`."""

    @pytest.mark.parametrize("value", ("1", "true", "YES", "y", "On", "ENABLED"))
    def test_truthy(self, monkeypatch: pytest.MonkeyPatch, value: str):
        """Report truthy values as True."""
        monkeypatch.setenv("HELPERS_TEST_VAR", value)
        assert helpers.is_truthy_env_var("HELPERS_TEST_VAR") is True

    @pytest.mark.parametrize("value", ("", "0", "false", "no", "off", "bogus"))
    def test_falsy(self, monkeypatch: pytest.MonkeyPatch, value: str):
        """Report falsy or unknown values as False."""
        monkeypatch.setenv("HELPERS_TEST_VAR", value)
        assert helpers.is_truthy_env_var("HELPERS_TEST_VAR") is False

    def test_unset_is_falsy(self, monkeypatch: pytest.MonkeyPatch):
        """Report an unset variable as False."""
        monkeypatch.delenv("HELPERS_TEST_VAR", raising=False)
        assert helpers.is_truthy_env_var("HELPERS_TEST_VAR") is False

    def test_int_value(self, monkeypatch: pytest.MonkeyPatch):
        """Parse a valid integer value."""
        monkeypatch.setenv("HELPERS_TEST_VAR", "42")
        assert helpers.get_env_int("HELPERS_TEST_VAR", default=1) == 42

    @pytest.mark.parametrize("value", (None, ""))
    def test_int_default(self, monkeypatch: pytest.MonkeyPatch, value: str | None):
        """Return the default when the variable is unset or empty."""
        if value is None:
            monkeypatch.delenv("HELPERS_TEST_VAR", raising=False)
        else:
            monkeypatch.setenv("HELPERS_TEST_VAR", value)
        assert helpers.get_env_int("HELPERS_TEST_VAR", default=7) == 7

    def test_int_malformed(self, monkeypatch: pytest.MonkeyPatch):
        """Raise ValueError naming the variable for a malformed value."""
        monkeypatch.setenv("HELPERS_TEST_VAR", "not-a-number")
        with pytest.raises(ValueError, match="HELPERS_TEST_VAR"):
            helpers.get_env_int("HELPERS_TEST_VAR", default=1)

    def test_path_value(self, monkeypatch: pytest.MonkeyPatch, tmp_path: pl.Path):
        """Return a resolved path when the variable is set."""
        monkeypatch.setenv("HELPERS_TEST_VAR", str(tmp_path))
        assert helpers.get_env_path("HELPERS_TEST_VAR") == tmp_path.resolve()

    def test_path_unset(self, monkeypatch: pytest.MonkeyPatch):
        """Return None when the variable is unset."""
        monkeypatch.delenv("HELPERS_TEST_VAR", raising=False)
        assert helpers.get_env_path("HELPERS_TEST_VAR") is None


class TestRunCommand:
    """Tests for `run_command`."""

    def test_list_command(self):
        """Run a command passed as a list and capture stdout."""
        out = helpers.run_command([sys.executable, "-c", "print('hello')"])
        assert out.strip() == b"hello"

    def test_list_command_nonstr_items(self, tmp_path: pl.Path):
        """Accept non-str list items such as Path objects."""
        script = tmp_path / "script.py"
        script.write_text("print('from script')")
        out = helpers.run_command([sys.executable, script])
        assert out.strip() == b"from script"

    def test_str_command_quoted_args(self, tmp_path: pl.Path):
        """Tokenize a string command with shlex so quoted arguments survive."""
        script = tmp_path / "script.py"
        script.write_text("import sys; print(sys.argv[1])")
        out = helpers.run_command(f'{sys.executable} {script} "two words"')
        assert out.strip() == b"two words"

    def test_stdin_data(self):
        """Pass data to stdin of the command."""
        out = helpers.run_command(
            [sys.executable, "-c", "import sys; print(sys.stdin.read().upper(), end='')"],
            stdin_data=b"stdin text",
        )
        assert out == b"STDIN TEXT"

    def test_failure_raises(self):
        """Raise RuntimeError with stderr content when the command fails."""
        with pytest.raises(RuntimeError, match="err output"):
            helpers.run_command(
                [sys.executable, "-c", "import sys; sys.stderr.write('err output'); sys.exit(1)"]
            )

    def test_ignore_fail(self):
        """Don't raise when the command fails and `ignore_fail` is used."""
        out = helpers.run_command(
            [sys.executable, "-c", "print('partial'); import sys; sys.exit(1)"],
            ignore_fail=True,
        )
        assert out.strip() == b"partial"

    def test_merge_stderr(self):
        """Redirect stderr to stdout when `merge_stderr` is used."""
        out = helpers.run_command(
            [sys.executable, "-c", "import sys; sys.stderr.write('on stderr')"],
            merge_stderr=True,
        )
        assert out == b"on stderr"

    def test_workdir(self, tmp_path: pl.Path):
        """Run the command in the given working directory."""
        out = helpers.run_command(
            [sys.executable, "-c", "import os; print(os.getcwd())"], workdir=tmp_path
        )
        assert out.decode().strip() == str(tmp_path)

    def test_shell(self):
        """Pass the command string to the shell untokenized.

        Only shell builtins are used, no external binary is needed.
        """
        out = helpers.run_command("echo $((1 + 2))", shell=True)
        assert out.strip() == b"3"

    def test_failure_merge_stderr(self):
        """Take the error message from stdout when stderr is merged into it."""
        with pytest.raises(RuntimeError, match="on both streams"):
            helpers.run_command(
                [sys.executable, "-c", "import sys; sys.stderr.write('on both streams'); exit(1)"],
                merge_stderr=True,
            )

    def test_missing_executable(self):
        """Raise RuntimeError naming the command when the executable doesn't exist."""
        with pytest.raises(RuntimeError, match=r"Failed to execute.*nonexistent-binary-xyz"):
            helpers.run_command(["nonexistent-binary-xyz", "--arg"])

    def test_missing_workdir(self, tmp_path: pl.Path):
        """Raise RuntimeError when the working directory doesn't exist."""
        with pytest.raises(RuntimeError, match="Failed to execute"):
            helpers.run_command([sys.executable, "-c", "pass"], workdir=tmp_path / "nonexistent")


class TestRunInBash:
    """Tests for `run_in_bash`.

    `run_command` is monkeypatched, the bash binary is not needed.
    """

    def test_invocation(self, monkeypatch: pytest.MonkeyPatch):
        """Run the command string via bash with pipefail enabled."""
        recorded: dict[str, tp.Any] = {}

        def _fake_run_command(command: list, **kwargs: tp.Any) -> bytes:
            recorded["command"] = command
            recorded["workdir"] = kwargs.get("workdir")
            return b""

        monkeypatch.setattr(helpers, "run_command", _fake_run_command)
        helpers.run_in_bash("false | true", workdir="/some/dir")
        assert recorded["command"] == ["bash", "-o", "pipefail", "-c", "false | true"]
        assert recorded["workdir"] == "/some/dir"


class TestBech32:
    """Tests for `decode_bech32` and `encode_bech32`.

    The `bech32` binary is not expected to be present, so the tests only check
    how the tool is invoked.
    """

    def test_decode_invocation(self, monkeypatch: pytest.MonkeyPatch):
        """Invoke the bech32 tool without a shell and pass the input via stdin."""
        recorded: dict[str, tp.Any] = {}

        def _fake_run_command(command: list, **kwargs: tp.Any) -> bytes:
            assert "shell" not in kwargs
            recorded["command"] = command
            recorded["stdin_data"] = kwargs.get("stdin_data")
            return b"00ff\n"

        monkeypatch.setattr(helpers, "run_command", _fake_run_command)
        out = helpers.decode_bech32("addr1'$(injected)")
        assert out == "00ff"
        assert recorded["command"] == ["bech32"]
        assert recorded["stdin_data"] == b"addr1'$(injected)\n"

    def test_encode_invocation(self, monkeypatch: pytest.MonkeyPatch):
        """Pass the prefix as a separate argument and the data via stdin."""
        recorded: dict[str, tp.Any] = {}

        def _fake_run_command(command: list, **kwargs: tp.Any) -> bytes:
            assert "shell" not in kwargs
            recorded["command"] = command
            recorded["stdin_data"] = kwargs.get("stdin_data")
            return b"pool1abc\n"

        monkeypatch.setattr(helpers, "run_command", _fake_run_command)
        out = helpers.encode_bech32(prefix="pool", data="00ff")
        assert out == "pool1abc"
        assert recorded["command"] == ["bech32", "pool"]
        assert recorded["stdin_data"] == b"00ff\n"


class TestRandStr:
    """Tests for `get_rand_str` and `get_timestamped_rand_str`."""

    def test_length_and_charset(self):
        """Return a string of the requested length built from lowercase letters."""
        rand_str = helpers.get_rand_str(20)
        assert len(rand_str) == 20
        assert set(rand_str) <= set(string.ascii_lowercase)

    @pytest.mark.parametrize("length", (0, -5))
    def test_nonpositive_length(self, length: int):
        """Return an empty string for non-positive lengths."""
        assert helpers.get_rand_str(length) == ""

    def test_timestamped(self):
        """Return a timestamp followed by a random suffix."""
        out = helpers.get_timestamped_rand_str(rand_str_length=4)
        assert len(out) == len("200801_002401314_cinf")
        timestamp, _, rand_part = out.rpartition("_")
        assert len(rand_part) == 4
        assert timestamp.replace("_", "").isdigit()

    def test_timestamped_no_rand(self):
        """Return only the timestamp when the random suffix length is 0."""
        out = helpers.get_timestamped_rand_str(rand_str_length=0)
        assert len(out) == len("200801_002401314")
        assert "_" in out


class TestPrependFlag:
    """Tests for `prepend_flag`."""

    def test_prepend(self):
        """Prepend the flag to every item and convert items to str."""
        assert helpers.prepend_flag("--foo", [1, 2, 3]) == [
            "--foo",
            "1",
            "--foo",
            "2",
            "--foo",
            "3",
        ]

    def test_empty(self):
        """Return an empty list for empty contents."""
        assert helpers.prepend_flag("--foo", []) == []


class TestGetCurrentCommit:
    """Tests for `get_current_commit`.

    `run_command` is monkeypatched, git is not executed.
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> tp.Generator[None]:
        """Clear the `get_current_commit` cache so cached fake values don't persist."""
        helpers.get_current_commit.cache_clear()
        yield
        helpers.get_current_commit.cache_clear()

    def test_env_var_precedence(self, monkeypatch: pytest.MonkeyPatch):
        """Use the `GIT_REVISION` value without asking git."""
        monkeypatch.setenv("GIT_REVISION", "f" * 40)
        monkeypatch.setattr(
            helpers, "run_command", lambda *_a, **_kw: pytest.fail("git was executed")
        )
        assert helpers.get_current_commit() == "f" * 40

    def test_empty_env_var_falls_through(self, monkeypatch: pytest.MonkeyPatch):
        """Ask git when `GIT_REVISION` is set but empty."""
        monkeypatch.setenv("GIT_REVISION", "")
        monkeypatch.setattr(helpers, "run_command", lambda *_a, **_kw: b"def456\n")
        assert helpers.get_current_commit() == "def456"

    def test_anchored_to_repo(self, monkeypatch: pytest.MonkeyPatch):
        """Ask git in the directory of the helpers module, not in CWD."""
        monkeypatch.delenv("GIT_REVISION", raising=False)
        recorded: dict[str, tp.Any] = {}

        def _fake_run_command(command: str, **kwargs: tp.Any) -> bytes:
            recorded["command"] = command
            recorded["workdir"] = kwargs.get("workdir")
            return b"abc123\n"

        monkeypatch.setattr(helpers, "run_command", _fake_run_command)
        assert helpers.get_current_commit() == "abc123"
        assert recorded["command"] == "git rev-parse HEAD"
        assert recorded["workdir"] == pl.Path(helpers.__file__).parent


class TestLineStr:
    """Tests for `get_current_line_str`, `get_line_str_from_frame` and `get_vcs_link`."""

    @pytest.fixture(autouse=True)
    def clear_commit_cache(self) -> tp.Generator[None]:
        """Clear the `get_current_commit` cache so a fake `GIT_REVISION` cannot leak."""
        helpers.get_current_commit.cache_clear()
        yield
        helpers.get_current_commit.cache_clear()

    def test_current_line_str(self):
        """Return path and line number of the calling line."""
        frame = inspect.currentframe()
        assert frame is not None
        expected_lineno = frame.f_lineno + 1
        line_str = helpers.get_current_line_str()
        assert line_str == f"{__file__}#L{expected_lineno}"

    def test_vcs_link(self, monkeypatch: pytest.MonkeyPatch):
        """Build a GitHub link pointing to the calling line at the current commit."""
        monkeypatch.setenv("GIT_REVISION", "0" * 40)
        monkeypatch.setattr(
            helpers,
            "get_line_str_from_frame",
            lambda **_kwargs: "/repos/checkout/cardano_node_tests/tests/test_foo.py#L10",
        )
        expected = f"{helpers.GITHUB_URL}/blob/{'0' * 40}/cardano_node_tests/tests/test_foo.py#L10"
        assert helpers.get_vcs_link() == expected

    def test_vcs_link_outside_package(self, monkeypatch: pytest.MonkeyPatch):
        """Raise ValueError when called from a file outside `cardano_node_tests`."""
        monkeypatch.setenv("GIT_REVISION", "0" * 40)
        monkeypatch.setattr(
            helpers, "get_line_str_from_frame", lambda **_kwargs: "/elsewhere/mod.py#L1"
        )
        with pytest.raises(ValueError, match="Couldn't find the repo location"):
            helpers.get_vcs_link()


class TestChecksum:
    """Tests for `checksum`."""

    def test_matches_hashlib(self, tmp_path: pl.Path):
        """Return the blake2b hex digest of the file content."""
        test_file = tmp_path / "data.bin"
        content = b"some test content\n" * 1000
        test_file.write_bytes(content)
        assert helpers.checksum(test_file) == hashlib.blake2b(content).hexdigest()


class TestWriteJson:
    """Tests for `write_json`."""

    def test_roundtrip(self, tmp_path: pl.Path):
        """Write JSON content that can be read back."""
        out_file = tmp_path / "out.json"
        content = {"key": "value", "num": 42, "nested": {"a": [1, 2]}}
        ret = helpers.write_json(out_file=out_file, content=content)
        assert ret == out_file
        assert json.loads(out_file.read_text()) == content


class TestCheckArgs:
    """Tests for `check_dir_arg`, `check_dir_arg_keep` and `check_file_arg`."""

    def test_dir_arg(self, tmp_path: pl.Path):
        """Return the resolved path for an existing dir."""
        assert helpers.check_dir_arg(str(tmp_path)) == tmp_path.resolve()

    def test_dir_arg_empty(self):
        """Return None for an empty value."""
        assert helpers.check_dir_arg("") is None

    def test_dir_arg_missing(self, tmp_path: pl.Path):
        """Raise ArgumentTypeError for a nonexistent dir."""
        with pytest.raises(argparse.ArgumentTypeError, match="doesn't exist"):
            helpers.check_dir_arg(str(tmp_path / "nonexistent"))

    def test_dir_arg_keep_relative(self, tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Keep a relative path unresolved."""
        (tmp_path / "subdir").mkdir()
        monkeypatch.chdir(tmp_path)
        assert helpers.check_dir_arg_keep("subdir") == pl.Path("subdir")

    def test_dir_arg_keep_expands_user(self, tmp_path: pl.Path, monkeypatch: pytest.MonkeyPatch):
        """Expand `~` in the returned path."""
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / "subdir").mkdir()
        assert helpers.check_dir_arg_keep("~/subdir") == tmp_path / "subdir"

    def test_dir_arg_keep_missing(self, tmp_path: pl.Path):
        """Raise ArgumentTypeError naming the right function for a nonexistent dir."""
        with pytest.raises(argparse.ArgumentTypeError, match="check_dir_arg_keep"):
            helpers.check_dir_arg_keep(str(tmp_path / "nonexistent"))

    def test_file_arg(self, tmp_path: pl.Path):
        """Return the resolved path for an existing file."""
        test_file = tmp_path / "file.txt"
        test_file.touch()
        assert helpers.check_file_arg(str(test_file)) == test_file.resolve()

    def test_file_arg_missing(self, tmp_path: pl.Path):
        """Raise ArgumentTypeError for a nonexistent file."""
        with pytest.raises(argparse.ArgumentTypeError, match="doesn't exist"):
            helpers.check_file_arg(str(tmp_path / "nonexistent"))


class TestIsInInterval:
    """Tests for `is_in_interval`."""

    @pytest.mark.parametrize(
        ("num1", "num2", "expected"),
        (
            (100, 100, True),
            (90, 100, True),
            (110, 100, True),
            (89, 100, False),
            (111, 100, False),
            (-95, -100, True),
            (-111, -100, False),
            (0, 0, True),
        ),
    )
    def test_interval(self, num1: float, num2: float, expected: bool):
        """Check interval membership, including negative reference values."""
        assert helpers.is_in_interval(num1, num2) is expected

    def test_custom_frac(self):
        """Respect a custom fraction."""
        assert helpers.is_in_interval(80, 100, frac=0.2) is True
        assert helpers.is_in_interval(79, 100, frac=0.2) is False


class TestToolHas:
    """Tests for `tool_has`.

    `run_command` is monkeypatched, no external tool is executed.
    """

    @pytest.fixture(autouse=True)
    def clear_cache(self) -> tp.Generator[None]:
        """Clear the `tool_has` cache so results of the fake commands don't persist."""
        helpers.tool_has.cache_clear()
        yield
        helpers.tool_has.cache_clear()

    def test_available(self, monkeypatch: pytest.MonkeyPatch):
        """Return True when the command succeeds."""
        monkeypatch.setattr(helpers, "run_command", lambda _c: b"")
        assert helpers.tool_has("sometool subcommand-a") is True

    def test_invalid_subcommand(self, monkeypatch: pytest.MonkeyPatch):
        """Return False when the tool reports an invalid subcommand."""

        def _fail(command: str) -> bytes:
            err = f"An error occurred while running `{command}`: Invalid argument `subcommand-b`"
            raise RuntimeError(err)

        monkeypatch.setattr(helpers, "run_command", _fail)
        assert helpers.tool_has("sometool subcommand-b") is False

    def test_other_error(self, monkeypatch: pytest.MonkeyPatch):
        """Return True when the command fails for other reasons than invalid subcommand."""

        def _fail(command: str) -> bytes:
            err = f"An error occurred while running `{command}`: Missing config file"
            raise RuntimeError(err)

        monkeypatch.setattr(helpers, "run_command", _fail)
        assert helpers.tool_has("sometool subcommand-c") is True

    def test_error_without_colon(self, monkeypatch: pytest.MonkeyPatch):
        """Don't crash on an error message without a colon."""

        def _fail(_command: str) -> bytes:
            err = "all is broken"
            raise RuntimeError(err)

        monkeypatch.setattr(helpers, "run_command", _fail)
        assert helpers.tool_has("sometool subcommand-d") is True


class TestFlatten:
    """Tests for `flatten`."""

    def test_nested(self):
        """Flatten arbitrarily nested iterables."""
        assert list(helpers.flatten([1, [2, [3, [4]]], (5, 6)])) == [1, 2, 3, 4, 5, 6]

    def test_strings_kept_whole(self):
        """Don't split strings and bytes into items."""
        assert list(helpers.flatten(["ab", ["cd", [b"ef"]]])) == ["ab", "cd", b"ef"]

    def test_ltypes(self):
        """Flatten only the iterable types given in `ltypes`."""
        assert list(helpers.flatten([[1, (2, 3)], [4]], ltypes=list)) == [1, (2, 3), 4]

    def test_empty(self):
        """Return nothing for an empty iterable."""
        assert list(helpers.flatten([])) == []


class TestValidateDictValues:
    """Tests for `validate_dict_values`."""

    def test_no_discrepancies(self):
        """Return an empty list when the values match."""
        d1 = {"a": 1, "b": 2}
        d2 = {"a": 1, "b": 2, "c": 3}
        assert helpers.validate_dict_values(d1, d2, keys=["a", "b"]) == []

    def test_discrepancies(self):
        """Report mismatched values for the specified keys."""
        d1 = {"a": 1, "b": 2}
        d2 = {"a": 1, "b": 20}
        errors = helpers.validate_dict_values(d1, d2, keys=["a", "b"])
        assert len(errors) == 1
        assert "'b'" in errors[0]

    def test_missing_key(self):
        """Report a key missing from the actual data as a discrepancy against None."""
        d1 = {"a": 1}
        errors = helpers.validate_dict_values(d1, {}, keys=["a"])
        assert len(errors) == 1
        assert "None" in errors[0]


class TestGetPoolParam:
    """Tests for `get_pool_param`."""

    def test_sps_key_present(self):
        """Return the value stored under the sps-prefixed key."""
        params = {"spsRewardAccount": "acct1"}
        assert helpers.get_pool_param("spsRewardAccount", pool_params=params) == "acct1"

    def test_sps_key_fallback_to_old(self):
        """Fall back to the old key name when the sps key is missing."""
        params = {"rewardAccount": "acct1"}
        assert helpers.get_pool_param("spsRewardAccount", pool_params=params) == "acct1"

    def test_old_key_maps_to_sps(self):
        """Translate a camelCase old key to its sps-prefixed variant."""
        params = {"spsRewardAccount": "acct1"}
        assert helpers.get_pool_param("rewardAccount", pool_params=params) == "acct1"

    def test_old_key_single_word(self):
        """Translate a single-word old key to its sps-prefixed variant."""
        params = {"spsOwners": ["owner1"]}
        assert helpers.get_pool_param("owners", pool_params=params) == ["owner1"]

    def test_missing_key(self):
        """Return None when the key is not present under either name."""
        assert helpers.get_pool_param("pledge", pool_params={}) is None

    @pytest.mark.parametrize("key", ("", "sps"))
    def test_degenerate_keys(self, key: str):
        """Return None instead of raising for degenerate keys."""
        assert helpers.get_pool_param(key, pool_params={"a": 1}) is None


class TestCheckSocketPath:
    """Tests for `check_cardano_node_socket_path`."""

    def test_valid(self, monkeypatch: pytest.MonkeyPatch):
        """Accept a socket path inside a state-cluster dir."""
        monkeypatch.setenv("CARDANO_NODE_SOCKET_PATH", "/tmp/state-cluster0/relay1.socket")
        helpers.check_cardano_node_socket_path()

    def test_unset(self, monkeypatch: pytest.MonkeyPatch):
        """Reject an unset variable."""
        monkeypatch.delenv("CARDANO_NODE_SOCKET_PATH", raising=False)
        with pytest.raises(ValueError, match="is not set"):
            helpers.check_cardano_node_socket_path()

    @pytest.mark.parametrize(
        "socket_path",
        (
            "/tmp/other-dir/relay1.socket",
            "/tmp/state-cluster0/other.socket",
        ),
    )
    def test_invalid(self, monkeypatch: pytest.MonkeyPatch, socket_path: str):
        """Reject socket paths not matching the expected layout."""
        monkeypatch.setenv("CARDANO_NODE_SOCKET_PATH", socket_path)
        with pytest.raises(ValueError, match="not valid"):
            helpers.check_cardano_node_socket_path()
