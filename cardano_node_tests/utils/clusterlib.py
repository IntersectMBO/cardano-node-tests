"""Wrapper for cardano-cli."""
import functools
import itertools
import json
import logging
import os
import random
import string
import subprocess
import time
from pathlib import Path
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Union

from cardano_node_tests.utils.types import FileType
from cardano_node_tests.utils.types import FileTypeList
from cardano_node_tests.utils.types import OptionalFiles
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)

DEFAULT_COIN = "lovelace"


class CLIOut(NamedTuple):
    stdout: bytes
    stderr: bytes


class KeyPair(NamedTuple):
    vkey_file: Path
    skey_file: Path


class ColdKeyPair(NamedTuple):
    vkey_file: Path
    skey_file: Path
    counter_file: Path


class AddressRecord(NamedTuple):
    address: str
    vkey_file: Path
    skey_file: Path


class StakeAddrInfo(NamedTuple):
    address: str
    delegation: str
    reward_account_balance: int

    def __bool__(self) -> bool:
        return bool(self.address)


class UTXOData(NamedTuple):
    utxo_hash: str
    utxo_ix: str
    amount: int
    address: Optional[str] = None
    coin: str = DEFAULT_COIN


class TxOut(NamedTuple):
    address: str
    amount: int
    coin: str = DEFAULT_COIN


# list of `TxOut`s, empty list, or empty tuple
OptionalTxOuts = Union[List[TxOut], Tuple[()]]


class TxFiles(NamedTuple):
    certificate_files: OptionalFiles = ()
    proposal_files: OptionalFiles = ()
    metadata_json_files: OptionalFiles = ()
    metadata_cbor_files: OptionalFiles = ()
    script_files: OptionalFiles = ()
    signing_key_files: OptionalFiles = ()


class PoolUser(NamedTuple):
    payment: AddressRecord
    stake: AddressRecord


class PoolData(NamedTuple):
    pool_name: str
    pool_pledge: int
    pool_cost: int
    pool_margin: float
    pool_metadata_url: str = ""
    pool_metadata_hash: str = ""
    pool_relay_dns: str = ""
    pool_relay_ipv4: str = ""
    pool_relay_port: int = 0


class TxRawOutput(NamedTuple):
    txins: List[UTXOData]
    txouts: List[TxOut]
    tx_files: TxFiles
    out_file: Path
    fee: int
    ttl: int
    withdrawals: OptionalTxOuts


class PoolCreationOutput(NamedTuple):
    stake_pool_id: str
    vrf_key_pair: KeyPair
    cold_key_pair: ColdKeyPair
    pool_reg_cert_file: Path
    pool_data: PoolData
    pool_owners: List[PoolUser]
    tx_raw_output: TxRawOutput
    kes_key_pair: Optional[KeyPair] = None


class Protocols:
    CARDANO = "cardano"
    SHELLEY = "shelley"


class Eras:
    SHELLEY = "shelley"
    ALLEGRA = "allegra"
    MARY = "mary"


class MultiSigTypeArgs:
    ALL = "all"
    ANY = "any"
    AT_LEAST = "atLeast"


class MultiSlotTypeArgs:
    BEFORE = "before"
    AFTER = "after"


class CLIError(Exception):
    pass


def get_rand_str(length: int = 8) -> str:
    """Return random string."""
    if length < 1:
        return ""
    return "".join(random.choice(string.ascii_lowercase) for i in range(length))


def read_address_from_file(addr_file: FileType) -> str:
    """Read address stored in file."""
    with open(Path(addr_file).expanduser()) as in_file:
        return in_file.read().strip()


class ClusterLib:
    """Cluster Lib."""

    # pylint: disable=too-many-public-methods

    def __init__(
        self,
        state_dir: FileType,
        protocol: str = Protocols.SHELLEY,
        era: str = "",
        tx_era: str = "",
    ):
        self.cli_coverage: dict = {}

        _rand_str = get_rand_str(4)

        self.state_dir = Path(state_dir).expanduser().resolve()
        self.genesis_json = self.state_dir / "shelley" / "genesis.json"
        self.genesis_utxo_vkey = self.state_dir / "shelley" / "genesis-utxo.vkey"
        self.genesis_utxo_skey = self.state_dir / "shelley" / "genesis-utxo.skey"
        self.genesis_vkeys = list(self.state_dir.glob("shelley/genesis-keys/genesis?.vkey"))
        self.delegate_skeys = list(self.state_dir.glob("shelley/delegate-keys/delegate?.skey"))
        self.pparams_file = self.state_dir / f"pparams-{_rand_str}.json"
        self._check_state_dir()

        with open(self.genesis_json) as in_json:
            self.genesis = json.load(in_json)

        self.network_magic = self.genesis["networkMagic"]
        self.slot_length = self.genesis["slotLength"]
        self.epoch_length = self.genesis["epochLength"]
        self.epoch_length_sec = self.epoch_length * self.slot_length
        self.slots_per_kes_period = self.genesis["slotsPerKESPeriod"]
        self.max_kes_evolutions = self.genesis["maxKESEvolutions"]

        self.ttl_length = 1000
        self.genesis_utxo_addr = self.gen_genesis_addr(
            addr_name=f"genesis-{_rand_str}",
            vkey_file=self.genesis_utxo_vkey,
            destination_dir=self.state_dir,
        )

        self.era = era
        self.era_arg = [f"--{self.era}-era"] if self.era else []
        self.tx_era = tx_era or self.era
        self.tx_era_arg = [f"--{self.tx_era}-era"] if self.tx_era else []

        self.protocol = protocol
        self._check_protocol()

    def _check_state_dir(self) -> None:
        """Check that all files expected by `__init__` are present."""
        if not self.state_dir.exists():
            raise CLIError(f"The state dir `{self.state_dir}` doesn't exist.")
        if not self.genesis_vkeys:
            raise CLIError("The genesis verification keys don't exist.")
        if not self.delegate_skeys:
            raise CLIError("The delegation signing keys don't exist.")

        for file_name in (
            self.genesis_json,
            self.genesis_utxo_vkey,
            self.genesis_utxo_skey,
        ):
            if not file_name.exists():
                raise CLIError(f"The file `{file_name}` doesn't exist.")

    def _check_protocol(self) -> None:
        """Check that the cluster is running with the expected protocol."""
        try:
            self.refresh_pparams_file()
        except CLIError as exc:
            if "SingleEraInfo" not in str(exc):
                raise
            raise CLIError(
                f"The cluster is running with protocol different from '{self.protocol}'."
            ) from exc

    def _check_outfiles(self, *out_files: FileType) -> None:
        """Check that the expected output files were created."""
        for out_file in out_files:
            out_file = Path(out_file).expanduser()
            if not out_file.exists():
                raise CLIError(f"The expected file `{out_file}` doesn't exist.")

    def record_cli_coverage(self, cli_args: List[str]) -> None:
        """Record CLI coverage info."""
        parent_dict = self.cli_coverage
        prev_arg = ""
        for arg in cli_args:
            # if the current argument is a parameter to an option, skip it
            if prev_arg.startswith("--") and not arg.startswith("--"):
                continue
            prev_arg = arg

            cur_dict = parent_dict.get(arg)
            # initialize record if it doesn't exist yet
            if not cur_dict:
                parent_dict[arg] = {"_count": 0}
                cur_dict = parent_dict[arg]

            # increment count
            cur_dict["_count"] += 1

            # set new parent dict
            if not arg.startswith("--"):
                parent_dict = cur_dict

    def cli_base(self, cli_args: List[str]) -> CLIOut:
        """Run command."""
        cmd_str = " ".join(cli_args)
        LOGGER.debug("Running `%s`", cmd_str)

        # re-run the command when running into
        # Network.Socket.connect: <socket: X>: resource exhausted (Resource temporarily unavailable)
        for __ in range(3):
            p = subprocess.Popen(cli_args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            stdout, stderr = p.communicate()

            if p.returncode == 0:
                break

            stderr_dec = stderr.decode()
            err_msg = (
                f"An error occurred running a CLI command `{cmd_str}` on path "
                f"`{os.getcwd()}`: {stderr_dec}"
            )
            if "resource exhausted" in stderr_dec:
                LOGGER.error(err_msg)
                time.sleep(0.4)
                continue
            raise CLIError(err_msg)
        else:
            raise CLIError(err_msg)

        return CLIOut(stdout or b"", stderr or b"")

    def cli(self, cli_args: List[str]) -> CLIOut:
        """Run the `cardano-cli` command."""
        cmd = ["cardano-cli", *cli_args]
        self.record_cli_coverage(cmd)
        return self.cli_base(cmd)

    def _prepend_flag(self, flag: str, contents: UnpackableSequence) -> List[str]:
        """Prepend flag to every item of the sequence.

        >>> ClusterLib._prepend_flag(None, "--foo", [1, 2, 3])
        ['--foo', '1', '--foo', '2', '--foo', '3']
        """
        return list(itertools.chain.from_iterable([flag, str(x)] for x in contents))

    def query_cli(self, cli_args: UnpackableSequence) -> str:
        """Run the `cardano-cli query` command."""
        stdout = self.cli(
            [
                "query",
                *cli_args,
                "--testnet-magic",
                str(self.network_magic),
                f"--{self.protocol}-mode",
            ]
        ).stdout
        stdout_dec = stdout.decode("utf-8") if stdout else ""
        return stdout_dec

    def refresh_pparams_file(self) -> None:
        """Refresh protocol parameters file."""
        self.query_cli(["protocol-parameters", *self.era_arg, "--out-file", str(self.pparams_file)])

    def get_utxo(self, address: str, coins: UnpackableSequence = ()) -> List[UTXOData]:
        """Return UTXO info for payment address."""
        utxo_dict = json.loads(
            self.query_cli(
                ["utxo", "--address", address, *self.era_arg, "--out-file", "/dev/stdout"]
            )
        )

        def _get_tokens_utxo(data: list) -> List[Tuple[int, str]]:
            tokens_db = []
            for policyid_rec in data:
                policyid = policyid_rec[0]
                for coin_rec in policyid_rec[1]:
                    coin = coin_rec[0]
                    coin = f".{coin}" if coin else ""
                    amount = coin_rec[1]
                    tokens_db.append((amount, f"{policyid}{coin}"))
            return tokens_db

        utxo = []
        for utxo_rec, utxo_data in utxo_dict.items():
            utxo_hash, utxo_ix = utxo_rec.split("#")
            amount = utxo_data["amount"]

            # check if there's native tokens info available
            if not isinstance(amount, int):
                amount, tokens = amount[0], amount[1]
                native_tokens = _get_tokens_utxo(tokens)
                for token in native_tokens:
                    utxo.append(
                        UTXOData(
                            utxo_hash=utxo_hash,
                            utxo_ix=utxo_ix,
                            amount=token[0],
                            address=utxo_data["address"],
                            coin=token[1],
                        )
                    )

            utxo.append(
                UTXOData(
                    utxo_hash=utxo_hash,
                    utxo_ix=utxo_ix,
                    amount=amount,
                    address=utxo_data["address"],
                    coin=DEFAULT_COIN,
                )
            )

        if coins:
            filtered_utxo = [u for u in utxo if u.coin in coins]
            return filtered_utxo

        return utxo

    def get_tip(self) -> dict:
        """Return current tip - last block successfully applied to the ledger."""
        return json.loads(self.query_cli(["tip"]))  # type: ignore

    def gen_genesis_addr(
        self, addr_name: str, vkey_file: FileType, destination_dir: FileType = "."
    ) -> str:
        """Generate genesis address."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_genesis.addr"

        self.cli(
            [
                "genesis",
                "initial-addr",
                "--testnet-magic",
                str(self.network_magic),
                "--verification-key-file",
                str(vkey_file),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return read_address_from_file(out_file)

    def gen_payment_addr(
        self,
        addr_name: str,
        payment_vkey_file: FileType,
        stake_vkey_file: Optional[FileType] = None,
        destination_dir: FileType = ".",
    ) -> str:
        """Generate payment address."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}.addr"

        cli_args = ["--payment-verification-key-file", str(payment_vkey_file)]
        if stake_vkey_file:
            cli_args.extend(["--stake-verification-key-file", str(stake_vkey_file)])

        self.cli(
            [
                "address",
                "build",
                "--testnet-magic",
                str(self.network_magic),
                *cli_args,
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return read_address_from_file(out_file)

    def gen_stake_addr(
        self, addr_name: str, stake_vkey_file: FileType, destination_dir: FileType = "."
    ) -> str:
        """Generate stake address."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake.addr"

        self.cli(
            [
                "stake-address",
                "build",
                "--stake-verification-key-file",
                str(stake_vkey_file),
                "--testnet-magic",
                str(self.network_magic),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return read_address_from_file(out_file)

    def gen_script_addr(
        self, addr_name: str, script_file: FileType, destination_dir: FileType = "."
    ) -> str:
        """Generate multi-signature address."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_script.addr"

        self.cli(
            [
                "address",
                "build-script",
                "--script-file",
                str(script_file),
                "--testnet-magic",
                str(self.network_magic),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return read_address_from_file(out_file)

    def gen_payment_key_pair(self, key_name: str, destination_dir: FileType = ".") -> KeyPair:
        """Generate payment key pair."""
        destination_dir = Path(destination_dir).expanduser()
        vkey = destination_dir / f"{key_name}.vkey"
        skey = destination_dir / f"{key_name}.skey"
        self.cli(
            [
                "address",
                "key-gen",
                "--verification-key-file",
                str(vkey),
                "--signing-key-file",
                str(skey),
            ]
        )

        self._check_outfiles(vkey, skey)
        return KeyPair(vkey, skey)

    def gen_stake_key_pair(self, key_name: str, destination_dir: FileType = ".") -> KeyPair:
        """Generate stake key pair."""
        destination_dir = Path(destination_dir).expanduser()
        vkey = destination_dir / f"{key_name}_stake.vkey"
        skey = destination_dir / f"{key_name}_stake.skey"
        self.cli(
            [
                "stake-address",
                "key-gen",
                "--verification-key-file",
                str(vkey),
                "--signing-key-file",
                str(skey),
            ]
        )

        self._check_outfiles(vkey, skey)
        return KeyPair(vkey, skey)

    def gen_payment_addr_and_keys(
        self, name: str, stake_vkey_file: Optional[FileType] = None, destination_dir: FileType = "."
    ) -> AddressRecord:
        """Generate payment address and key pair."""
        key_pair = self.gen_payment_key_pair(key_name=name, destination_dir=destination_dir)
        addr = self.gen_payment_addr(
            addr_name=name,
            payment_vkey_file=key_pair.vkey_file,
            stake_vkey_file=stake_vkey_file,
            destination_dir=destination_dir,
        )

        return AddressRecord(
            address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file
        )

    def gen_stake_addr_and_keys(self, name: str, destination_dir: FileType = ".") -> AddressRecord:
        """Generate stake address and key pair."""
        key_pair = self.gen_stake_key_pair(key_name=name, destination_dir=destination_dir)
        addr = self.gen_stake_addr(
            addr_name=name, stake_vkey_file=key_pair.vkey_file, destination_dir=destination_dir
        )

        return AddressRecord(
            address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file
        )

    def gen_kes_key_pair(self, node_name: str, destination_dir: FileType = ".") -> KeyPair:
        """Generate KES key pair."""
        destination_dir = Path(destination_dir).expanduser()
        vkey = destination_dir / f"{node_name}_kes.vkey"
        skey = destination_dir / f"{node_name}_kes.skey"
        self.cli(
            [
                "node",
                "key-gen-KES",
                "--verification-key-file",
                str(vkey),
                "--signing-key-file",
                str(skey),
            ]
        )

        self._check_outfiles(vkey, skey)
        return KeyPair(vkey, skey)

    def gen_vrf_key_pair(self, node_name: str, destination_dir: FileType = ".") -> KeyPair:
        """Generate VRF key pair."""
        destination_dir = Path(destination_dir).expanduser()
        vkey = destination_dir / f"{node_name}_vrf.vkey"
        skey = destination_dir / f"{node_name}_vrf.skey"
        self.cli(
            [
                "node",
                "key-gen-VRF",
                "--verification-key-file",
                str(vkey),
                "--signing-key-file",
                str(skey),
            ]
        )

        self._check_outfiles(vkey, skey)
        return KeyPair(vkey, skey)

    def gen_cold_key_pair_and_counter(
        self, node_name: str, destination_dir: FileType = "."
    ) -> ColdKeyPair:
        """Generate cold key pair and counter."""
        destination_dir = Path(destination_dir).expanduser()
        vkey = destination_dir / f"{node_name}_cold.vkey"
        skey = destination_dir / f"{node_name}_cold.skey"
        counter = destination_dir / f"{node_name}_cold.counter"
        self.cli(
            [
                "node",
                "key-gen",
                "--verification-key-file",
                str(vkey),
                "--signing-key-file",
                str(skey),
                "--operational-certificate-issue-counter",
                str(counter),
            ]
        )

        self._check_outfiles(vkey, skey, counter)
        return ColdKeyPair(vkey, skey, counter)

    def gen_node_operational_cert(
        self,
        node_name: str,
        node_kes_vkey_file: FileType,
        node_cold_skey_file: FileType,
        node_cold_counter_file: FileType,
        kes_period: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> Path:
        """Generate node operational certificate.

        This certificate is used when starting the node and not submitted through a transaction.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{node_name}.opcert"
        kes_period = kes_period if kes_period is not None else self.get_last_block_kes_period()
        self.cli(
            [
                "node",
                "issue-op-cert",
                "--kes-verification-key-file",
                str(node_kes_vkey_file),
                "--cold-signing-key-file",
                str(node_cold_skey_file),
                "--operational-certificate-issue-counter",
                str(node_cold_counter_file),
                "--kes-period",
                str(kes_period),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def gen_stake_addr_registration_cert(
        self, addr_name: str, stake_vkey_file: FileType, destination_dir: FileType = "."
    ) -> Path:
        """Generate stake address registration certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake_reg.cert"
        self.cli(
            [
                "stake-address",
                "registration-certificate",
                "--stake-verification-key-file",
                str(stake_vkey_file),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def gen_stake_addr_deregistration_cert(
        self, addr_name: str, stake_vkey_file: FileType, destination_dir: FileType = "."
    ) -> Path:
        """Generate stake address deregistration certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake_dereg.cert"
        self.cli(
            [
                "stake-address",
                "deregistration-certificate",
                "--stake-verification-key-file",
                str(stake_vkey_file),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def gen_stake_addr_delegation_cert(
        self,
        addr_name: str,
        stake_vkey_file: FileType,
        cold_vkey_file: Optional[FileType] = None,
        stake_pool_id: Optional[str] = None,
        destination_dir: FileType = ".",
    ) -> Path:
        """Generate stake address delegation certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake_deleg.cert"

        if cold_vkey_file:
            pool_args = [
                "--cold-verification-key-file",
                str(cold_vkey_file),
            ]
        elif stake_pool_id:
            pool_args = [
                "--stake-pool-id",
                str(stake_pool_id),
            ]
        else:
            raise CLIError("Either `--cold-verification-key-file` or `--stake-pool-id` is needed.")

        self.cli(
            [
                "stake-address",
                "delegation-certificate",
                "--stake-verification-key-file",
                str(stake_vkey_file),
                *pool_args,
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def gen_pool_metadata_hash(self, pool_metadata_file: FileType) -> str:
        """Generate hash of pool metadata."""
        return (
            self.cli(
                ["stake-pool", "metadata-hash", "--pool-metadata-file", str(pool_metadata_file)]
            )
            .stdout.rstrip()
            .decode("utf-8")
        )

    def gen_pool_registration_cert(
        self,
        pool_data: PoolData,
        vrf_vkey_file: FileType,
        cold_vkey_file: FileType,
        owner_stake_vkey_files: FileTypeList,
        reward_account_vkey_file: Optional[FileType] = None,
        destination_dir: FileType = ".",
    ) -> Path:
        """Generate pool registration certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{pool_data.pool_name}_pool_reg.cert"

        metadata_cmd = []
        if pool_data.pool_metadata_url and pool_data.pool_metadata_hash:
            metadata_cmd = [
                "--metadata-url",
                str(pool_data.pool_metadata_url),
                "--metadata-hash",
                str(pool_data.pool_metadata_hash),
            ]

        relay_cmd = []
        if pool_data.pool_relay_dns:
            relay_cmd.extend(["--single-host-pool-relay", pool_data.pool_relay_dns])
        if pool_data.pool_relay_ipv4:
            relay_cmd.extend(["--pool-relay-ipv4", pool_data.pool_relay_ipv4])
        if pool_data.pool_relay_port:
            relay_cmd.extend(["--pool-relay-port", str(pool_data.pool_relay_port)])

        self.cli(
            [
                "stake-pool",
                "registration-certificate",
                "--pool-pledge",
                str(pool_data.pool_pledge),
                "--pool-cost",
                str(pool_data.pool_cost),
                "--pool-margin",
                str(pool_data.pool_margin),
                "--vrf-verification-key-file",
                str(vrf_vkey_file),
                "--cold-verification-key-file",
                str(cold_vkey_file),
                "--pool-reward-account-verification-key-file",
                str(reward_account_vkey_file)
                if reward_account_vkey_file
                else str(owner_stake_vkey_files[0]),
                *self._prepend_flag(
                    "--pool-owner-stake-verification-key-file", owner_stake_vkey_files
                ),
                "--testnet-magic",
                str(self.network_magic),
                "--out-file",
                str(out_file),
                *metadata_cmd,
                *relay_cmd,
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def gen_pool_deregistration_cert(
        self, pool_name: str, cold_vkey_file: FileType, epoch: int, destination_dir: FileType = "."
    ) -> Path:
        """Generate pool deregistration certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{pool_name}_pool_dereg.cert"
        self.cli(
            [
                "stake-pool",
                "deregistration-certificate",
                "--cold-verification-key-file",
                str(cold_vkey_file),
                "--epoch",
                str(epoch),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def get_payment_vkey_hash(
        self,
        payment_vkey_file: FileType,
    ) -> str:
        """Return payment vkey hash."""
        return (
            self.cli(
                ["address", "key-hash", "--payment-verification-key-file", str(payment_vkey_file)]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

    def get_ledger_state(self) -> dict:
        """Return ledger state info."""
        return json.loads(self.query_cli(["ledger-state", *self.era_arg]))  # type: ignore

    def get_protocol_state(self) -> dict:
        """Return protocol state info."""
        return json.loads(self.query_cli(["protocol-state", *self.era_arg]))  # type: ignore

    def get_protocol_params(self) -> dict:
        """Return protocol parameters info."""
        self.refresh_pparams_file()
        with open(self.pparams_file) as in_json:
            return json.load(in_json)  # type: ignore

    def get_registered_stake_pools_ledger_state(self) -> dict:
        """Return ledger state info for registered stake pools."""
        registered_pools_details = self.get_ledger_state()["nesEs"]["esLState"]["_delegationState"][
            "_pstate"
        ]["_pParams"]
        return registered_pools_details  # type: ignore

    def get_stake_pool_id(self, pool_cold_vkey_file: FileType) -> str:
        """Return ID of stake pool."""
        pool_id = (
            self.cli(["stake-pool", "id", "--cold-verification-key-file", str(pool_cold_vkey_file)])
            .stdout.strip()
            .decode("utf-8")
        )
        return pool_id

    def get_stake_addr_info(self, stake_addr: str) -> StakeAddrInfo:
        """Return info about stake pool address."""
        output_json = json.loads(
            self.query_cli(["stake-address-info", *self.era_arg, "--address", stake_addr])
        )
        if not output_json:
            return StakeAddrInfo(address="", delegation="", reward_account_balance=0)

        address_rec = list(output_json)[0]
        address = address_rec.get("address") or ""
        delegation = address_rec.get("delegation") or ""
        reward_account_balance = address_rec.get("rewardAccountBalance") or 0
        return StakeAddrInfo(
            address=address,
            delegation=delegation,
            reward_account_balance=reward_account_balance,
        )

    def get_key_deposit(self) -> int:
        """Return key deposit amount."""
        return int(self.get_protocol_params()["keyDeposit"])

    def get_pool_deposit(self) -> int:
        """Return pool deposit amount."""
        return int(self.get_protocol_params()["poolDeposit"])

    def get_stake_distribution(self) -> dict:
        """Return info about stake distribution per stake pool."""
        # stake pool values are displayed starting with line 2 from the command output
        result = self.query_cli(["stake-distribution", *self.era_arg]).splitlines()[2:]
        stake_distribution = {}
        for pool in result:
            pool_id, *__, stake = pool.split(" ")
            stake_distribution[pool_id] = stake
        return stake_distribution

    def get_last_block_slot_no(self) -> int:
        """Return slot number of last block that was successfully applied to the ledger."""
        return int(self.get_tip()["slotNo"])

    def get_last_block_block_no(self) -> int:
        """Return block number of last block that was successfully applied to the ledger."""
        return int(self.get_tip()["blockNo"])

    def get_last_block_epoch(self) -> int:
        """Return epoch of last block that was successfully applied to the ledger."""
        return int(self.get_last_block_slot_no() // self.epoch_length)

    def get_address_balance(self, address: str, coin: str = DEFAULT_COIN) -> int:
        """Return total balance of an address (sum of all UTXO balances)."""
        utxo = self.get_utxo(address, coins=[coin])
        address_balance = functools.reduce(lambda x, y: x + y.amount, utxo, 0)
        return int(address_balance)

    def get_utxo_with_highest_amount(self, address: str, coin: str = DEFAULT_COIN) -> UTXOData:
        """Return data for UTXO with highest amount."""
        utxo = self.get_utxo(address, coins=[coin])
        highest_amount_rec = max(utxo, key=lambda x: x.amount)
        return highest_amount_rec

    def calculate_tx_ttl(self) -> int:
        """Calculate ttl for a transaction."""
        return self.get_last_block_slot_no() + self.ttl_length

    def get_last_block_kes_period(self) -> int:
        """Return last block KES period."""
        return int(self.get_last_block_slot_no() // self.slots_per_kes_period)

    def get_txid(self, tx_body_file: FileType) -> str:
        """Get txid trom transaction body."""
        return (
            self.cli(["transaction", "txid", "--tx-body-file", str(tx_body_file)])
            .stdout.rstrip()
            .decode("ascii")
        )

    def get_tx_deposit(self, tx_files: TxFiles) -> int:
        """Return deposit amount for a transaction (based on certificates used for the TX)."""
        if not tx_files.certificate_files:
            return 0

        pparams = self.get_protocol_params()
        key_deposit = pparams["keyDeposit"]
        pool_deposit = pparams["poolDeposit"]

        deposit = 0
        for cert in tx_files.certificate_files:
            with open(cert) as in_json:
                content = json.load(in_json)
            description = content.get("description", "")
            if "Stake Address Registration" in description:
                deposit += key_deposit
            elif "Stake Pool Registration" in description:
                deposit += pool_deposit
            elif "Stake Address Deregistration" in description:
                deposit -= key_deposit

        return deposit

    def _organize_tx_ins_outs(self, tx_list: Union[List[UTXOData], List[TxOut]]) -> Dict[str, list]:
        """Organize transaction inputs or outputs by coin type."""
        db: Dict[str, list] = {}
        for rec in tx_list:
            if rec.coin not in db:
                db[rec.coin] = []
            db[rec.coin].append(rec)
        return db

    def get_tx_ins_outs(  # noqa: C901
        self,
        src_address: str,
        tx_files: TxFiles,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        fee: int = 0,
        deposit: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        mint: OptionalTxOuts = (),
    ) -> Tuple[list, list]:
        """Return list of transaction's inputs and outputs."""
        # pylint: disable=too-many-branches
        withdrawals_copy = list(withdrawals) if withdrawals else []

        txouts_copy = list(txouts) if txouts else []
        txouts_db: Dict[str, List[TxOut]] = self._organize_tx_ins_outs(txouts_copy)
        outcoins = [DEFAULT_COIN, *txouts_db.keys()]

        txins_copy = list(txins) if txins else self.get_utxo(src_address, coins=outcoins)
        txins_db: Dict[str, List[UTXOData]] = self._organize_tx_ins_outs(txins_copy)

        if not all(c in txins_db for c in outcoins):
            LOGGER.error("Not all output coins are present in input UTxO.")

        txins_result: List[UTXOData] = []
        txouts_result: List[TxOut] = []

        for coin in txins_db:
            max_address = None
            coin_txins = txins_db[coin]
            coin_txouts = txouts_db.get(coin) or []

            # the value "-1" means all available funds
            max_index = [idx for idx, val in enumerate(coin_txouts) if val.amount == -1]
            if len(max_index) > 1:
                raise CLIError("Cannot send all remaining funds to more than one address.")
            if max_index:
                max_address = coin_txouts.pop(max_index[0]).address

            total_input_amount = functools.reduce(lambda x, y: x + y.amount, coin_txins, 0)
            total_output_amount = functools.reduce(lambda x, y: x + y.amount, coin_txouts, 0)
            total_withdrawals_amount = functools.reduce(
                lambda x, y: x + y.amount, withdrawals_copy, 0
            )

            tx_deposit = self.get_tx_deposit(tx_files=tx_files) if deposit is None else deposit
            funds_needed = total_output_amount + fee + tx_deposit
            change = total_input_amount + total_withdrawals_amount - funds_needed
            if change < 0:
                LOGGER.error(
                    "Not enough funds to make a transaction - "
                    f"available: {total_input_amount}; needed {funds_needed}"
                )
            if change > 0:
                coin_txouts.append(
                    TxOut(address=(max_address or src_address), amount=change, coin=coin)
                )

            txins_result.extend(coin_txins)
            txouts_result.extend(coin_txouts)

        for m in mint:
            if m.amount > 0:
                txouts_result.append(TxOut(address=m.address, amount=m.amount, coin=m.coin))

        if not txins_result:
            LOGGER.error("Cannot build transaction, empty `txins`.")
        if not txouts_result:
            LOGGER.error("Cannot build transaction, empty `txouts`.")

        return txins_result, txouts_result

    def get_withdrawals(self, withdrawals: List[TxOut]) -> List[TxOut]:
        """Return list of withdrawals."""
        resolved_withdrawals = []
        for rec in withdrawals:
            # the amount with value "-1" means all available balance
            if rec[1] == -1:
                balance = self.get_stake_addr_info(rec[0]).reward_account_balance
                resolved_withdrawals.append(TxOut(address=rec[0], amount=balance))
            else:
                resolved_withdrawals.append(rec)

        return resolved_withdrawals

    def build_raw_tx_bare(
        self,
        out_file: FileType,
        txins: List[UTXOData],
        txouts: List[TxOut],
        tx_files: TxFiles,
        fee: int,
        ttl: int,
        withdrawals: OptionalTxOuts = (),
        invalid_hereafter: Optional[int] = None,
        invalid_before: Optional[int] = None,
        mint: OptionalTxOuts = (),
    ) -> TxRawOutput:
        """Build raw transaction."""
        # pylint: disable=too-many-arguments
        out_file = Path(out_file)

        # aggregate TX outputs by address
        txouts_by_addr: Dict[str, List[str]] = {}
        for rec in txouts:
            if rec.address not in txouts_by_addr:
                txouts_by_addr[rec.address] = []
            coin = f" {rec.coin}" if rec.coin and rec.coin != DEFAULT_COIN else ""
            txouts_by_addr[rec.address].append(f"{rec.amount}{coin}")

        txouts_combined: List[str] = []
        for addr, amounts in txouts_by_addr.items():
            amounts_joined = "+".join(amounts)
            txouts_combined.append(f"{addr}+{amounts_joined}")

        txins_combined = [f"{x.utxo_hash}#{x.utxo_ix}" for x in txins]
        withdrawals_combined = [f"{x.address}+{x.amount}" for x in withdrawals]

        bound_args = []
        if invalid_before is not None:
            bound_args.extend(["--invalid-before", str(invalid_before)])
        if invalid_hereafter is None:
            # `--ttl` and `--upper-bound` are the same
            bound_args.extend(["--ttl", str(ttl)])
        else:
            bound_args.extend(["--invalid-hereafter", str(invalid_hereafter)])

        mint_args = []
        for m in mint:
            mint_args.extend(["--mint", f"{m.amount} {m.coin}"])

        self.cli(
            [
                "transaction",
                "build-raw",
                "--fee",
                str(fee),
                "--out-file",
                str(out_file),
                *self._prepend_flag("--tx-in", txins_combined),
                *self._prepend_flag("--tx-out", txouts_combined),
                *self._prepend_flag("--certificate-file", tx_files.certificate_files),
                *self._prepend_flag("--update-proposal-file", tx_files.proposal_files),
                *self._prepend_flag("--metadata-json-file", tx_files.metadata_json_files),
                *self._prepend_flag("--metadata-cbor-file", tx_files.metadata_cbor_files),
                *self._prepend_flag("--script-file", tx_files.script_files),
                *self._prepend_flag("--withdrawal", withdrawals_combined),
                *bound_args,
                *mint_args,
                *self.tx_era_arg,
            ]
        )

        return TxRawOutput(
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
            out_file=out_file,
            fee=fee,
            ttl=ttl,
            withdrawals=withdrawals,
        )

    def build_raw_tx(
        self,
        src_address: str,
        tx_name: str,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        fee: int = 0,
        ttl: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        deposit: Optional[int] = None,
        invalid_hereafter: Optional[int] = None,
        invalid_before: Optional[int] = None,
        mint: OptionalTxOuts = (),
        destination_dir: FileType = ".",
    ) -> TxRawOutput:
        """Figure out all the missing data and build raw transaction."""
        # pylint: disable=too-many-arguments
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.body"
        tx_files = tx_files or TxFiles()
        ttl = ttl or self.calculate_tx_ttl()
        withdrawals = withdrawals and self.get_withdrawals(withdrawals)

        txins_copy, txouts_copy = self.get_tx_ins_outs(
            src_address=src_address,
            tx_files=tx_files,
            txins=txins,
            txouts=txouts,
            fee=fee,
            deposit=deposit,
            withdrawals=withdrawals,
            mint=mint,
        )

        tx_raw_output = self.build_raw_tx_bare(
            out_file=out_file,
            txins=txins_copy,
            txouts=txouts_copy,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
            withdrawals=withdrawals,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            mint=mint,
        )

        self._check_outfiles(out_file)
        return tx_raw_output

    def estimate_fee(
        self,
        txbody_file: FileType,
        txin_count: int,
        txout_count: int,
        witness_count: int = 1,
        byron_witness_count: int = 0,
    ) -> int:
        """Estimate fee of a transaction."""
        self.refresh_pparams_file()
        stdout = self.cli(
            [
                "transaction",
                "calculate-min-fee",
                "--testnet-magic",
                str(self.network_magic),
                "--protocol-params-file",
                str(self.pparams_file),
                "--tx-in-count",
                str(txin_count),
                "--tx-out-count",
                str(txout_count),
                "--byron-witness-count",
                str(byron_witness_count),
                "--witness-count",
                str(witness_count),
                "--tx-body-file",
                str(txbody_file),
            ]
        ).stdout
        fee, *__ = stdout.decode().split(" ")
        return int(fee)

    def calculate_tx_fee(
        self,
        src_address: str,
        tx_name: str,
        dst_addresses: Optional[List[str]] = None,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        ttl: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        mint: OptionalTxOuts = (),
        witness_count_add: int = 0,
        destination_dir: FileType = ".",
    ) -> int:
        """Build "dummy" transaction and calculate it's fee."""
        # pylint: disable=too-many-arguments
        tx_files = tx_files or TxFiles()
        tx_name = f"{tx_name}_estimate"

        if dst_addresses and txouts:
            LOGGER.warning(
                "The value of `dst_addresses` is ignored when value for `txouts` is available"
            )

        txouts_filled = txouts or [TxOut(address=r, amount=1) for r in (dst_addresses or ())]

        tx_raw_output = self.build_raw_tx(
            src_address=src_address,
            tx_name=tx_name,
            txins=txins,
            txouts=txouts_filled,
            tx_files=tx_files,
            fee=0,
            ttl=ttl,
            withdrawals=withdrawals,
            deposit=0,
            mint=mint,
            destination_dir=destination_dir,
        )

        fee = self.estimate_fee(
            txbody_file=tx_raw_output.out_file,
            txin_count=len(tx_raw_output.txins),
            txout_count=len(tx_raw_output.txouts),
            witness_count=len(tx_files.signing_key_files) + witness_count_add,
        )

        return fee

    def sign_tx(
        self,
        tx_body_file: FileType,
        signing_key_files: OptionalFiles,
        tx_name: str,
        destination_dir: FileType = ".",
    ) -> Path:
        """Sign transaction."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.signed"

        self.cli(
            [
                "transaction",
                "sign",
                "--tx-body-file",
                str(tx_body_file),
                "--out-file",
                str(out_file),
                "--testnet-magic",
                str(self.network_magic),
                *self._prepend_flag("--signing-key-file", signing_key_files),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def witness_tx(
        self,
        tx_body_file: FileType,
        tx_name: str,
        signing_key_files: OptionalFiles = (),
        script_file: Optional[FileType] = None,
        destination_dir: FileType = ".",
    ) -> Path:
        """Witness transaction."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.witness"

        cli_args = []
        if script_file:
            cli_args = ["--script-file", str(script_file)]

        self.cli(
            [
                "transaction",
                "witness",
                "--tx-body-file",
                str(tx_body_file),
                "--out-file",
                str(out_file),
                "--testnet-magic",
                str(self.network_magic),
                *self._prepend_flag("--signing-key-file", signing_key_files),
                *cli_args,
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def assemble_tx(
        self,
        tx_body_file: FileType,
        witness_files: OptionalFiles,
        tx_name: str,
        destination_dir: FileType = ".",
    ) -> Path:
        """Assemble transaction from TX body and a set of witnesses."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.witnessed"

        self.cli(
            [
                "transaction",
                "assemble",
                "--tx-body-file",
                str(tx_body_file),
                "--out-file",
                str(out_file),
                *self._prepend_flag("--witness-file", witness_files),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def submit_tx(self, tx_file: FileType) -> None:
        """Submit transaction."""
        self.cli(
            [
                "transaction",
                "submit",
                "--testnet-magic",
                str(self.network_magic),
                "--tx-file",
                str(tx_file),
                f"--{self.protocol}-mode",
            ]
        )

    def send_tx(
        self,
        src_address: str,
        tx_name: str,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        fee: Optional[int] = None,
        ttl: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        deposit: Optional[int] = None,
        invalid_hereafter: Optional[int] = None,
        invalid_before: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> TxRawOutput:
        """Build, Sign and Send transaction to chain."""
        # pylint: disable=too-many-arguments
        tx_files = tx_files or TxFiles()

        if fee is None:
            fee = self.calculate_tx_fee(
                src_address=src_address,
                tx_name=tx_name,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                ttl=ttl,
                destination_dir=destination_dir,
            )

        tx_raw_output = self.build_raw_tx(
            src_address=src_address,
            tx_name=tx_name,
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
            withdrawals=withdrawals,
            deposit=deposit,
            invalid_hereafter=invalid_hereafter,
            invalid_before=invalid_before,
            destination_dir=destination_dir,
        )
        tx_signed_file = self.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=tx_name,
            signing_key_files=tx_files.signing_key_files,
            destination_dir=destination_dir,
        )
        self.submit_tx(tx_signed_file)

        return tx_raw_output

    def build_multisig_script(
        self,
        script_name: str,
        script_type_arg: str,
        payment_vkey_files: OptionalFiles,
        required: int = 0,
        slot: int = 0,
        slot_type_arg: str = "",
        destination_dir: FileType = ".",
    ) -> Path:
        """Build multi-signature script."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{script_name}_multisig.script"

        scripts_l: List[dict] = [
            {"keyHash": self.get_payment_vkey_hash(f), "type": "sig"} for f in payment_vkey_files
        ]
        if slot:
            scripts_l.append({"slot": slot, "type": slot_type_arg})

        script: dict = {
            "scripts": scripts_l,
            "type": script_type_arg,
        }

        if script_type_arg == MultiSigTypeArgs.AT_LEAST:
            script["required"] = required

        with open(out_file, "wt") as fp_out:
            json.dump(script, fp_out, indent=4)

        return out_file

    def get_policyid(
        self,
        script_file: FileType,
    ) -> str:
        """Calculate the PolicyId from the monetary policy script."""
        return (
            self.cli(["transaction", "policyid", "--script-file", str(script_file)])
            .stdout.rstrip()
            .decode("utf-8")
        )

    def gen_update_proposal(
        self,
        cli_args: UnpackableSequence,
        epoch: int,
        tx_name: str,
        destination_dir: FileType = ".",
    ) -> Path:
        """Create update proposal."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_update.proposal"

        self.cli(
            [
                "governance",
                "create-update-proposal",
                *cli_args,
                "--out-file",
                str(out_file),
                "--epoch",
                str(epoch),
                *self._prepend_flag("--genesis-verification-key-file", self.genesis_vkeys),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def submit_update_proposal(
        self,
        cli_args: UnpackableSequence,
        src_address: str,
        src_skey_file: FileType,
        tx_name: str,
        epoch: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> TxRawOutput:
        """Submit update proposal."""
        # TODO: assumption is update proposals submitted near beginning of epoch
        epoch = epoch if epoch is not None else self.get_last_block_epoch()

        out_file = self.gen_update_proposal(
            cli_args=cli_args,
            epoch=epoch,
            tx_name=tx_name,
            destination_dir=destination_dir,
        )

        return self.send_tx(
            src_address=src_address,
            tx_name=f"{tx_name}_submit_proposal",
            tx_files=TxFiles(
                proposal_files=[out_file],
                signing_key_files=[*self.delegate_skeys, Path(src_skey_file)],
            ),
            destination_dir=destination_dir,
        )

    def send_funds(
        self,
        src_address: str,
        destinations: List[TxOut],
        tx_name: str,
        tx_files: Optional[TxFiles] = None,
        fee: Optional[int] = None,
        ttl: Optional[int] = None,
        deposit: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> TxRawOutput:
        """Send funds - convenience function for `send_tx`."""
        return self.send_tx(
            src_address=src_address,
            tx_name=tx_name,
            txouts=destinations,
            tx_files=tx_files,
            ttl=ttl,
            fee=fee,
            deposit=deposit,
            destination_dir=destination_dir,
        )

    def wait_for_new_block(self, new_blocks: int = 1) -> None:
        """Wait for new block(s) to be created."""
        if new_blocks < 1:
            return

        LOGGER.debug(f"Waiting for {new_blocks} new block(s) to be created.")
        timeout_no_of_slots = 200 * new_blocks
        initial_block_no = self.get_last_block_block_no()
        expected_block_no = initial_block_no + new_blocks

        LOGGER.debug(f"Initial block no: {initial_block_no}")
        for __ in range(timeout_no_of_slots):
            time.sleep(self.slot_length)
            last_block_block_no = self.get_last_block_block_no()
            if last_block_block_no >= expected_block_no:
                break
        else:
            raise CLIError(
                f"Timeout waiting for {timeout_no_of_slots * self.slot_length} sec for "
                f"{new_blocks} block(s)."
            )

        LOGGER.debug(f"New block(s) were created; block number: {last_block_block_no}")

    def wait_for_new_epoch(self, new_epochs: int = 1, padding_seconds: int = 0) -> None:
        """Wait for new epoch(s)."""
        if new_epochs < 1:
            return

        last_block_epoch = self.get_last_block_epoch()
        expected_epoch_no = last_block_epoch + new_epochs

        LOGGER.debug(
            f"Current epoch: {last_block_epoch}; Waiting for the beginning of epoch: "
            "{expected_epoch_no}"
        )

        # how many seconds to wait until start of the expected epoch
        sleep_slots = (
            last_block_epoch + new_epochs
        ) * self.epoch_length - self.get_last_block_slot_no()
        sleep_time = int(sleep_slots * self.slot_length) + (padding_seconds or 1)

        if sleep_time > 15:
            LOGGER.info(
                f"Waiting for {sleep_time} sec for start of the epoch no {expected_epoch_no}"
            )

        time.sleep(sleep_time)

        wakeup_epoch = self.get_last_block_epoch()
        if wakeup_epoch != expected_epoch_no:
            raise CLIError(
                f"Waited for epoch number {expected_epoch_no} and current epoch is "
                f"number {wakeup_epoch}"
            )

        LOGGER.debug(f"Expected epoch started; epoch number: {wakeup_epoch}")

    def register_stake_pool(
        self,
        pool_data: PoolData,
        pool_owners: List[PoolUser],
        vrf_vkey_file: FileType,
        cold_key_pair: ColdKeyPair,
        tx_name: str,
        reward_account_vkey_file: Optional[FileType] = None,
        deposit: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> Tuple[Path, TxRawOutput]:
        """Register stake pool."""
        tx_name = f"{tx_name}_reg_pool"
        pool_reg_cert_file = self.gen_pool_registration_cert(
            pool_data=pool_data,
            vrf_vkey_file=vrf_vkey_file,
            cold_vkey_file=cold_key_pair.vkey_file,
            owner_stake_vkey_files=[p.stake.vkey_file for p in pool_owners],
            reward_account_vkey_file=reward_account_vkey_file,
            destination_dir=destination_dir,
        )

        # submit the pool registration certificate through a tx
        tx_files = TxFiles(
            certificate_files=[pool_reg_cert_file],
            signing_key_files=[
                *[p.payment.skey_file for p in pool_owners],
                *[p.stake.skey_file for p in pool_owners],
                cold_key_pair.skey_file,
            ],
        )

        tx_raw_output = self.send_tx(
            src_address=pool_owners[0].payment.address,
            tx_name=tx_name,
            tx_files=tx_files,
            deposit=deposit,
            destination_dir=destination_dir,
        )
        self.wait_for_new_block(new_blocks=2)

        return pool_reg_cert_file, tx_raw_output

    def deregister_stake_pool(
        self,
        pool_owners: List[PoolUser],
        cold_key_pair: ColdKeyPair,
        epoch: int,
        pool_name: str,
        tx_name: str,
        destination_dir: FileType = ".",
    ) -> Tuple[Path, TxRawOutput]:
        """Deregister stake pool."""
        tx_name = f"{tx_name}_dereg_pool"
        LOGGER.debug(
            f"Deregistering stake pool starting with epoch: {epoch}; "
            f"Current epoch is: {self.get_last_block_epoch()}"
        )
        pool_dereg_cert_file = self.gen_pool_deregistration_cert(
            pool_name=pool_name,
            cold_vkey_file=cold_key_pair.vkey_file,
            epoch=epoch,
            destination_dir=destination_dir,
        )

        # submit the pool deregistration certificate through a tx
        tx_files = TxFiles(
            certificate_files=[pool_dereg_cert_file],
            signing_key_files=[
                *[p.payment.skey_file for p in pool_owners],
                *[p.stake.skey_file for p in pool_owners],
                cold_key_pair.skey_file,
            ],
        )

        tx_raw_output = self.send_tx(
            src_address=pool_owners[0].payment.address,
            tx_name=tx_name,
            tx_files=tx_files,
            destination_dir=destination_dir,
        )
        self.wait_for_new_block(new_blocks=2)

        return pool_dereg_cert_file, tx_raw_output

    def create_stake_pool(
        self,
        pool_data: PoolData,
        pool_owners: List[PoolUser],
        tx_name: str,
        destination_dir: FileType = ".",
    ) -> PoolCreationOutput:
        """Create and register stake pool."""
        # create the KES key pair
        node_kes = self.gen_kes_key_pair(
            node_name=pool_data.pool_name,
            destination_dir=destination_dir,
        )
        LOGGER.debug(f"KES keys created - {node_kes.vkey_file}; {node_kes.skey_file}")

        # create the VRF key pair
        node_vrf = self.gen_vrf_key_pair(
            node_name=pool_data.pool_name,
            destination_dir=destination_dir,
        )
        LOGGER.debug(f"VRF keys created - {node_vrf.vkey_file}; {node_vrf.skey_file}")

        # create the cold key pair and node operational certificate counter
        node_cold = self.gen_cold_key_pair_and_counter(
            node_name=pool_data.pool_name,
            destination_dir=destination_dir,
        )
        LOGGER.debug(
            "Cold keys created and counter created - "
            f"{node_cold.vkey_file}; {node_cold.skey_file}; {node_cold.counter_file}"
        )

        pool_reg_cert_file, tx_raw_output = self.register_stake_pool(
            pool_data=pool_data,
            pool_owners=pool_owners,
            vrf_vkey_file=node_vrf.vkey_file,
            cold_key_pair=node_cold,
            tx_name=tx_name,
            destination_dir=destination_dir,
        )

        return PoolCreationOutput(
            stake_pool_id=self.get_stake_pool_id(node_cold.vkey_file),
            vrf_key_pair=node_vrf,
            cold_key_pair=node_cold,
            pool_reg_cert_file=pool_reg_cert_file,
            pool_data=pool_data,
            pool_owners=pool_owners,
            tx_raw_output=tx_raw_output,
            kes_key_pair=node_kes,
        )
