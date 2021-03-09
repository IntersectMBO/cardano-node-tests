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
from typing import Set
from typing import Tuple
from typing import Union

from cardano_node_tests.utils.types import FileType
from cardano_node_tests.utils.types import FileTypeList
from cardano_node_tests.utils.types import OptionalFiles
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)

DEFAULT_COIN = "lovelace"
MAINNET_MAGIC = 764824073


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
# list of `UTXOData`s, empty list, or empty tuple
OptionalUTXOData = Union[List[UTXOData], Tuple[()]]


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


class GenesisKeys(NamedTuple):
    genesis_utxo_vkey: Path
    genesis_utxo_skey: Path
    genesis_vkeys: List[Path]
    delegate_skeys: List[Path]


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
    """Return random ASCII lowercase string."""
    if length < 1:
        return ""
    return "".join(random.choice(string.ascii_lowercase) for i in range(length))


def read_address_from_file(addr_file: FileType) -> str:
    """Read address stored in file."""
    with open(Path(addr_file).expanduser()) as in_file:
        return in_file.read().strip()


class ClusterLib:
    """Methods for working with cardano cluster using `cardano-cli`..

    Attributes:
        state_dir: A directory with cluster state files (keys, config files, logs, ...).
        protocol: A cluster protocol - full cardano mode by default.
        era: An era the cluster is using (Shelley, Allegra, Mary, ...).
        tx_era: An era used for transactions, by default same as `era`.
        slots_offset: Difference in slots between cluster's start era and current era
            (e.g. Byron->Mary)
    """

    # pylint: disable=too-many-public-methods

    def __init__(
        self,
        state_dir: FileType,
        protocol: str = Protocols.CARDANO,
        era: str = Eras.MARY,
        tx_era: str = "",
        slots_offset: int = 0,
    ):
        self.cli_coverage: dict = {}
        self._rand_str = get_rand_str(4)

        self.state_dir = Path(state_dir).expanduser().resolve()
        self.genesis_json = self.state_dir / "shelley" / "genesis.json"
        self.pparams_file = self.state_dir / f"pparams-{self._rand_str}.json"
        self._check_state_dir()

        with open(self.genesis_json) as in_json:
            self.genesis = json.load(in_json)

        self.slot_length = self.genesis["slotLength"]
        self.epoch_length = self.genesis["epochLength"]
        self.epoch_length_sec = self.epoch_length * self.slot_length
        self.slots_per_kes_period = self.genesis["slotsPerKESPeriod"]
        self.max_kes_evolutions = self.genesis["maxKESEvolutions"]
        self.slots_offset = slots_offset

        self.network_magic = self.genesis["networkMagic"]
        if self.network_magic == MAINNET_MAGIC:
            self.magic_args = ["--mainnet"]
        else:
            self.magic_args = ["--testnet-magic", str(self.network_magic)]

        self.ttl_length = 1000

        self.era = era
        self.era_arg = [f"--{self.era}-era"] if self.era else []
        self.tx_era = tx_era or self.era
        self.tx_era_arg = [f"--{self.tx_era}-era"] if self.tx_era else []

        self.protocol = protocol
        self._check_protocol()

        self._genesis_keys: Optional[GenesisKeys] = None
        self._genesis_utxo_addr: str = ""

    def _check_state_dir(self) -> None:
        """Check that all files expected by `__init__` are present."""
        if not self.state_dir.exists():
            raise CLIError(f"The state dir `{self.state_dir}` doesn't exist.")
        if not self.genesis_json.exists():
            raise CLIError(f"The genesis JSON file `{self.genesis_json}` doesn't exist.")

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

    @property
    def genesis_keys(self) -> GenesisKeys:
        """Return tuple with genesis-related keys."""
        if self._genesis_keys:
            return self._genesis_keys

        genesis_utxo_vkey = self.state_dir / "shelley" / "genesis-utxo.vkey"
        genesis_utxo_skey = self.state_dir / "shelley" / "genesis-utxo.skey"
        genesis_vkeys = list(self.state_dir.glob("shelley/genesis-keys/genesis?.vkey"))
        delegate_skeys = list(self.state_dir.glob("shelley/delegate-keys/delegate?.skey"))

        if not genesis_vkeys:
            raise CLIError("The genesis verification keys don't exist.")
        if not delegate_skeys:
            raise CLIError("The delegation signing keys don't exist.")

        for file_name in (
            genesis_utxo_vkey,
            genesis_utxo_skey,
        ):
            if not file_name.exists():
                raise CLIError(f"The file `{file_name}` doesn't exist.")

        genesis_keys = GenesisKeys(
            genesis_utxo_vkey=genesis_utxo_skey,
            genesis_utxo_skey=genesis_utxo_skey,
            genesis_vkeys=genesis_vkeys,
            delegate_skeys=delegate_skeys,
        )

        self._genesis_keys = genesis_keys

        return genesis_keys

    @property
    def genesis_utxo_addr(self) -> str:
        """Produce a genesis UTxO address."""
        if self._genesis_utxo_addr:
            return self._genesis_utxo_addr

        self._genesis_utxo_addr = self.gen_genesis_addr(
            addr_name=f"genesis-{self._rand_str}",
            vkey_file=self.genesis_keys.genesis_utxo_vkey,
            destination_dir=self.state_dir,
        )

        return self._genesis_utxo_addr

    def _check_outfiles(self, *out_files: FileType) -> None:
        """Check that the expected output files were created.

        Args:
            *out_files: Variable length list of expected output files.
        """
        for out_file in out_files:
            out_file = Path(out_file).expanduser()
            if not out_file.exists():
                raise CLIError(f"The expected file `{out_file}` doesn't exist.")

    def record_cli_coverage(self, cli_args: List[str]) -> None:
        """Record coverage info for CLI commands.

        Args:
            cli_args: A list of command and it's arguments.
        """
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
        """Run a command.

        Args:
            cli_args: A list consisting of command and it's arguments.

        Returns:
            CLIOut: A tuple containing command stdout and stderr.
        """
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
        """Run the `cardano-cli` command.

        Args:
            cli_args: A list of arguments for cardano-cli.

        Returns:
            CLIOut: A tuple containing command stdout and stderr.
        """
        cmd = ["cardano-cli", *cli_args]
        self.record_cli_coverage(cmd)
        return self.cli_base(cmd)

    def _prepend_flag(self, flag: str, contents: UnpackableSequence) -> List[str]:
        """Prepend flag to every item of the sequence.

        Args:
            flag: A flag to prepend to every item of the `contents`.
            contents: A list (iterable) of content to be prepended.

        Returns:
            List[str]: A list of flag followed by content, see below.

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
                *self.magic_args,
                f"--{self.protocol}-mode",
            ]
        ).stdout
        stdout_dec = stdout.decode("utf-8") if stdout else ""
        return stdout_dec

    def refresh_pparams_file(self) -> None:
        """Refresh protocol parameters file."""
        self.query_cli(["protocol-parameters", *self.era_arg, "--out-file", str(self.pparams_file)])

    def get_utxo(self, address: str, coins: UnpackableSequence = ()) -> List[UTXOData]:
        """Return UTxO info for payment address.

        Args:
            address: A payment address.
            coins: A list (iterable) of coin names (asset IDs).

        Returns:
            List[UTXOData]: A list of UTxO data.
        """
        utxo_dict = json.loads(
            self.query_cli(
                ["utxo", "--address", address, *self.era_arg, "--out-file", "/dev/stdout"]
            )
        )

        utxo = []
        for utxo_rec, utxo_data in utxo_dict.items():
            utxo_hash, utxo_ix = utxo_rec.split("#")
            # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/2460
            if "address" not in utxo_data:
                addr = next(iter(utxo_data))  # first key
                addr_data = next(iter(utxo_data.values()))  # first value
            else:
                addr = utxo_data["address"]
                addr_data = utxo_data["value"]
            for policyid, coin_data in addr_data.items():
                if policyid == DEFAULT_COIN:
                    utxo.append(
                        UTXOData(
                            utxo_hash=utxo_hash,
                            utxo_ix=utxo_ix,
                            amount=coin_data,
                            address=addr,
                            coin=DEFAULT_COIN,
                        )
                    )
                    continue
                for asset_name, amount in coin_data.items():
                    utxo.append(
                        UTXOData(
                            utxo_hash=utxo_hash,
                            utxo_ix=utxo_ix,
                            amount=amount,
                            address=addr,
                            coin=f"{policyid}.{asset_name}",
                        )
                    )

        if coins:
            filtered_utxo = [u for u in utxo if u.coin in coins]
            return filtered_utxo

        return utxo

    def get_tip(self) -> dict:
        """Return current tip - last block successfully applied to the ledger."""
        tip: dict = json.loads(self.query_cli(["tip"]))
        return tip

    def gen_genesis_addr(
        self, addr_name: str, vkey_file: FileType, destination_dir: FileType = "."
    ) -> str:
        """Generate the address for an initial UTxO based on the verification key.

        Args:
            addr_name: A name of genesis address.
            vkey_file: A path to corresponding vkey file.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            str: A generated genesis address.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_genesis.addr"

        self.cli(
            [
                "genesis",
                "initial-addr",
                *self.magic_args,
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
        """Generate a payment address, with optional delegation to a stake address.

        Args:
            addr_name: A name of payment address.
            payment_vkey_file: A path to corresponding vkey file.
            stake_vkey_file: A path to corresponding stake vkey file (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            str: A generated payment address.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}.addr"

        cli_args = ["--payment-verification-key-file", str(payment_vkey_file)]
        if stake_vkey_file:
            cli_args.extend(["--stake-verification-key-file", str(stake_vkey_file)])

        self.cli(
            [
                "address",
                "build",
                *self.magic_args,
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
        """Generate a stake address.

        Args:
            addr_name: A name of payment address.
            stake_vkey_file: A path to corresponding stake vkey file.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            str: A generated stake address.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake.addr"

        self.cli(
            [
                "stake-address",
                "build",
                "--stake-verification-key-file",
                str(stake_vkey_file),
                *self.magic_args,
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return read_address_from_file(out_file)

    def gen_script_addr(
        self, addr_name: str, script_file: FileType, destination_dir: FileType = "."
    ) -> str:
        """Generate a script address.

        Args:
            addr_name: A name of payment address.
            script_file: A path to corresponding script file.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            str: A generated script address.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_script.addr"

        self.cli(
            [
                "address",
                "build-script",
                "--script-file",
                str(script_file),
                *self.magic_args,
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return read_address_from_file(out_file)

    def gen_payment_key_pair(self, key_name: str, destination_dir: FileType = ".") -> KeyPair:
        """Generate an address key pair.

        Args:
            key_name: A name of the key pair.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            KeyPair: A tuple containing the key pair.
        """
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
        """Generate a stake address key pair.

        Args:
            key_name: A name of the key pair.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            KeyPair: A tuple containing the key pair.
        """
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
        """Generate payment address and key pair.

        Args:
            name: A name of the address and key pair.
            stake_vkey_file: A path to corresponding stake vkey file (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            AddressRecord: A tuple containing the address and key pair.
        """
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
        """Generate stake address and key pair.

        Args:
            name: A name of the address and key pair.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            AddressRecord: A tuple containing the address and key pair.
        """
        key_pair = self.gen_stake_key_pair(key_name=name, destination_dir=destination_dir)
        addr = self.gen_stake_addr(
            addr_name=name, stake_vkey_file=key_pair.vkey_file, destination_dir=destination_dir
        )

        return AddressRecord(
            address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file
        )

    def gen_kes_key_pair(self, node_name: str, destination_dir: FileType = ".") -> KeyPair:
        """Generate a key pair for a node KES operational key.

        Args:
            node_name: A name of the node the key pair is generated for.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            KeyPair: A tuple containing the key pair.
        """
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
        """Generate a key pair for a node VRF operational key.

        Args:
            node_name: A name of the node the key pair is generated for.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            KeyPair: A tuple containing the key pair.
        """
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
        """Generate a key pair for operator's offline key and a new certificate issue counter.

        Args:
            node_name: A name of the node the key pair and the counter is generated for.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            ColdKeyPair: A tuple containing the key pair and the counter.
        """
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
        kes_vkey_file: FileType,
        cold_skey_file: FileType,
        cold_counter_file: FileType,
        kes_period: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> Path:
        """Generate a node operational certificate.

        This certificate is used when starting the node and not submitted through a transaction.

        Args:
            node_name: A name of the node the certificate is generated for.
            kes_vkey_file: A path to pool KES vkey file.
            cold_skey_file: A path to pool cold skey file.
            cold_counter_file: A path to pool cold counter file.
            kes_period: A start KES period. The current KES period is used when not specified.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the generated certificate.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{node_name}.opcert"
        kes_period = kes_period if kes_period is not None else self.get_last_block_kes_period()
        self.cli(
            [
                "node",
                "issue-op-cert",
                "--kes-verification-key-file",
                str(kes_vkey_file),
                "--cold-signing-key-file",
                str(cold_skey_file),
                "--operational-certificate-issue-counter",
                str(cold_counter_file),
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
        """Generate a stake address registration certificate.

        Args:
            addr_name: A name of stake address.
            stake_vkey_file: A path to corresponding stake vkey file.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the generated certificate.
        """
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
        """Generate a stake address deregistration certificate.

        Args:
            addr_name: A name of stake address.
            stake_vkey_file: A path to corresponding stake vkey file.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the generated certificate.
        """
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
        """Generate a stake address delegation certificate.

        Args:
            addr_name: A name of stake address.
            stake_vkey_file: A path to corresponding stake vkey file.
            cold_vkey_file: A path to pool cold vkey file (optional).
            stake_pool_id: An ID of the stake pool (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the generated certificate.
        """
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
        """Generate the hash of pool metadata.

        Args:
            pool_metadata_file: A path to the pool metadata file.

        Returns:
            str: A metadata hash.
        """
        return (
            self.cli(
                ["stake-pool", "metadata-hash", "--pool-metadata-file", str(pool_metadata_file)]
            )
            .stdout.rstrip()
            .decode("ascii")
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
        """Generate a stake pool registration certificate.

        Args:
            pool_data: A `PoolData` tuple cointaining info about the stake pool.
            vrf_vkey_file: A path to node VRF vkey file.
            cold_vkey_file: A path to pool cold vkey file.
            owner_stake_vkey_files: A list of paths to pool owner stake vkey files.
            reward_account_vkey_file: A path to pool reward acount vkey file (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the generated certificate.
        """
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
                else str(list(owner_stake_vkey_files)[0]),
                *self._prepend_flag(
                    "--pool-owner-stake-verification-key-file", owner_stake_vkey_files
                ),
                *self.magic_args,
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
        """Generate a stake pool deregistration certificate.

        Args:
            pool_name: A name of the stake pool.
            cold_vkey_file: A path to pool cold vkey file.
            epoch: An epoch where the pool will be deregistered.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the generated certificate.
        """
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
        """Return the hash of an address key.

        Args:
            payment_vkey_file: A path to payment vkey file.

        Returns:
            str: A generated hash.
        """
        return (
            self.cli(
                ["address", "key-hash", "--payment-verification-key-file", str(payment_vkey_file)]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

    def get_ledger_state(self) -> dict:
        """Return the current ledger state info."""
        ledger_state: dict = json.loads(self.query_cli(["ledger-state", *self.era_arg]))
        return ledger_state

    def save_ledger_state(
        self,
        state_name: str,
        destination_dir: FileType = ".",
    ) -> Path:
        """Save ledger state to file.

        Args:
            state_name: A name of the ledger state (can be epoch number, etc.).
            destination_dir: A path to directory for storing the state JSON file (optional).

        Returns:
            Path: A path to the generated state JSON file.
        """
        json_file = Path(destination_dir) / f"{state_name}_ledger_state.json"
        # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/2461
        # self.query_cli(["ledger-state", *self.era_arg, "--out-file", str(json_file)])
        ledger_state = self.get_ledger_state()
        with open(json_file, "w") as fp_out:
            json.dump(ledger_state, fp_out, indent=4)
        return json_file

    def get_protocol_state(self) -> dict:
        """Return the current protocol state info."""
        protocol_state: dict = json.loads(self.query_cli(["protocol-state", *self.era_arg]))
        return protocol_state

    def get_protocol_params(self) -> dict:
        """Return the current protocol parameters."""
        self.refresh_pparams_file()
        with open(self.pparams_file) as in_json:
            pparams: dict = json.load(in_json)
        return pparams

    def get_registered_stake_pools_ledger_state(self) -> dict:
        """Return ledger state info for registered stake pools."""
        registered_pools_details: dict = self.get_ledger_state()["stateBefore"]["esLState"][
            "delegationState"
        ]["pstate"]["pParams pState"]
        return registered_pools_details

    def get_stake_pool_id(self, cold_vkey_file: FileType) -> str:
        """Return pool ID from the offline key.

        Args:
            cold_vkey_file: A path to pool cold vkey file.

        Returns:
            str: A pool ID.
        """
        pool_id = (
            self.cli(["stake-pool", "id", "--cold-verification-key-file", str(cold_vkey_file)])
            .stdout.strip()
            .decode("utf-8")
        )
        return pool_id

    def get_stake_addr_info(self, stake_addr: str) -> StakeAddrInfo:
        """Return the current delegations and reward accounts filtered by stake address.

        Args:
            stake_addr: A stake address string.

        Returns:
            StakeAddrInfo: A tuple containing stake address info.
        """
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

    def get_address_deposit(self) -> int:
        """Return stake address deposit amount."""
        pparams = self.get_protocol_params()
        return pparams.get("stakeAddressDeposit") or 0

    def get_pool_deposit(self) -> int:
        """Return stake pool deposit amount."""
        pparams = self.get_protocol_params()
        return pparams.get("stakePoolDeposit") or 0

    def get_stake_distribution(self) -> dict:
        """Return current aggregated stake distribution per stake pool."""
        # stake pool values are displayed starting with line 2 from the command output
        result = self.query_cli(["stake-distribution", *self.era_arg]).splitlines()[2:]
        stake_distribution = {}
        for pool in result:
            pool_id, *__, stake = pool.split(" ")
            stake_distribution[pool_id] = stake
        return stake_distribution

    def get_last_block_slot_no(self) -> int:
        """Return slot number of last block that was successfully applied to the ledger."""
        return int(self.get_tip()["slot"])

    def get_last_block_block_no(self) -> int:
        """Return block number of last block that was successfully applied to the ledger."""
        return int(self.get_tip()["block"])

    def get_last_block_epoch(self) -> int:
        """Return epoch of last block that was successfully applied to the ledger."""
        return int((self.get_last_block_slot_no() + self.slots_offset) // self.epoch_length)

    def get_address_balance(self, address: str, coin: str = DEFAULT_COIN) -> int:
        """Get total balance of an address (sum of all UTxO balances).

        Args:
            address: A payment address string.

        Returns:
            int: A total balance.
        """
        utxo = self.get_utxo(address, coins=[coin])
        address_balance = functools.reduce(lambda x, y: x + y.amount, utxo, 0)
        return int(address_balance)

    def get_utxo_with_highest_amount(self, address: str, coin: str = DEFAULT_COIN) -> UTXOData:
        """Return data for UTxO with highest amount.

        Args:
            address: A payment address string.

        Returns:
            UTXOData: An UTxO record with the highest amount.
        """
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
        """Get the transaction identifier trom transaction body.

        Args:
            tx_body_file: A path to the transaction body file.

        Returns:
            str: A transaction ID.
        """
        return (
            self.cli(["transaction", "txid", "--tx-body-file", str(tx_body_file)])
            .stdout.rstrip()
            .decode("ascii")
        )

    def get_tx_deposit(self, tx_files: TxFiles) -> int:
        """Get deposit amount for a transaction (based on certificates used for the TX).

        Args:
            tx_files: A `TxFiles` tuple containing files needed for the transaction.

        Returns:
            int: A total deposit amount needed for the transaction.
        """
        if not tx_files.certificate_files:
            return 0

        pparams = self.get_protocol_params()
        key_deposit = pparams.get("stakeAddressDeposit") or 0
        pool_deposit = pparams.get("stakePoolDeposit") or 0

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

    def _organize_tx_ins_outs_by_coin(
        self, tx_list: Union[List[UTXOData], List[TxOut], Tuple[()]]
    ) -> Dict[str, list]:
        """Organize transaction inputs or outputs by coin type."""
        db: Dict[str, list] = {}
        for rec in tx_list:
            if rec.coin not in db:
                db[rec.coin] = []
            db[rec.coin].append(rec)
        return db

    def _organize_utxos_by_id(self, tx_list: List[UTXOData]) -> Dict[str, List[UTXOData]]:
        """Organize UTxOs by ID (hash#ix)."""
        db: Dict[str, List[UTXOData]] = {}
        for rec in tx_list:
            utxo_id = f"{rec.utxo_hash}#{rec.utxo_ix}"
            if utxo_id not in db:
                db[utxo_id] = []
            db[utxo_id].append(rec)
        return db

    def _get_utxos_with_coins(self, src_address: str, coins: Set[str]) -> List[UTXOData]:
        """Get all UTxOs that contain any of the required coins (`coins`)."""
        txins_all = self.get_utxo(src_address)
        txins_by_id = self._organize_utxos_by_id(txins_all)

        txins = []
        seen_ids = set()
        for rec in txins_all:
            utxo_id = f"{rec.utxo_hash}#{rec.utxo_ix}"
            if rec.coin in coins and utxo_id not in seen_ids:
                seen_ids.add(utxo_id)
                txins.extend(txins_by_id[utxo_id])

        return txins

    def _collect_utxos_amount(self, utxos: List[UTXOData], amount: int) -> List[UTXOData]:
        """Collect UTxOs so their total combined amount >= `amount`."""
        collected_utxos: List[UTXOData] = []
        collected_amount = 0
        for utxo in utxos:
            if collected_amount >= amount:
                break
            collected_utxos.append(utxo)
            collected_amount += utxo.amount

        return collected_utxos

    def _select_utxos(
        self,
        tx_files: TxFiles,
        txins_db: Dict[str, List[UTXOData]],
        txouts_passed_db: Dict[str, List[TxOut]],
        txouts_mint_db: Dict[str, List[TxOut]],
        fee: int,
        deposit: Optional[int],
        withdrawals: OptionalTxOuts,
    ) -> Set[str]:
        """Select UTxOs that can satisfy all outputs, deposits and fee.

        Return IDs of selected UTxOs.
        """
        utxo_ids: Set[str] = set()

        # iterate over coins both in txins and txouts
        for coin in set(txins_db).union(txouts_passed_db).union(txouts_mint_db):
            coin_txins = txins_db.get(coin) or []
            coin_txouts = txouts_passed_db.get(coin) or []

            # the value "-1" means all available funds
            max_index = [idx for idx, val in enumerate(coin_txouts) if val.amount == -1]
            if max_index:
                utxo_ids.update(f"{rec.utxo_hash}#{rec.utxo_ix}" for rec in coin_txins)
                continue

            total_output_amount = functools.reduce(lambda x, y: x + y.amount, coin_txouts, 0)

            if coin == DEFAULT_COIN:
                tx_deposit = self.get_tx_deposit(tx_files=tx_files) if deposit is None else deposit
                tx_fee = fee if fee > 0 else 0
                funds_needed = total_output_amount + tx_fee + tx_deposit
                total_withdrawals_amount = functools.reduce(
                    lambda x, y: x + y.amount, withdrawals, 0
                )
                input_funds_needed = funds_needed - total_withdrawals_amount
            else:
                coin_txouts_minted = txouts_mint_db.get(coin) or []
                total_minted_amount = functools.reduce(
                    lambda x, y: x + y.amount, coin_txouts_minted, 0
                )
                input_funds_needed = total_output_amount - total_minted_amount

            filtered_coin_utxos = self._collect_utxos_amount(
                utxos=coin_txins, amount=input_funds_needed
            )
            utxo_ids.update(f"{rec.utxo_hash}#{rec.utxo_ix}" for rec in filtered_coin_utxos)

        return utxo_ids

    def _balance_txouts(
        self,
        src_address: str,
        tx_files: TxFiles,
        txins_db: Dict[str, List[UTXOData]],
        txouts_passed_db: Dict[str, List[TxOut]],
        txouts_mint_db: Dict[str, List[TxOut]],
        fee: int,
        deposit: Optional[int],
        withdrawals: OptionalTxOuts,
    ) -> List[TxOut]:
        """Ballance the transaction by adding change output for each coin."""
        txouts_result: List[TxOut] = []

        # iterate over coins both in txins and txouts
        for coin in set(txins_db).union(txouts_passed_db).union(txouts_mint_db):
            max_address = None
            coin_txins = txins_db.get(coin) or []
            coin_txouts = txouts_passed_db.get(coin) or []

            # the value "-1" means all available funds
            max_index = [idx for idx, val in enumerate(coin_txouts) if val.amount == -1]
            if len(max_index) > 1:
                raise CLIError("Cannot send all remaining funds to more than one address.")
            if max_index:
                max_address = coin_txouts.pop(max_index[0]).address

            total_input_amount = functools.reduce(lambda x, y: x + y.amount, coin_txins, 0)
            total_output_amount = functools.reduce(lambda x, y: x + y.amount, coin_txouts, 0)

            if coin == DEFAULT_COIN:
                tx_deposit = self.get_tx_deposit(tx_files=tx_files) if deposit is None else deposit
                tx_fee = fee if fee > 0 else 0
                funds_needed = total_output_amount + tx_fee + tx_deposit
                total_withdrawals_amount = functools.reduce(
                    lambda x, y: x + y.amount, withdrawals, 0
                )
                change = total_input_amount + total_withdrawals_amount - funds_needed
                if change < 0:
                    LOGGER.error(
                        "Not enough funds to make the transaction - "
                        f"available: {total_input_amount}; needed {funds_needed}"
                    )
            else:
                coin_txouts_minted = txouts_mint_db.get(coin) or []
                total_minted_amount = functools.reduce(
                    lambda x, y: x + y.amount, coin_txouts_minted, 0
                )
                change = total_input_amount + total_minted_amount - total_output_amount

            if change > 0:
                coin_txouts.append(
                    TxOut(address=(max_address or src_address), amount=change, coin=coin)
                )

            txouts_result.extend(coin_txouts)

        return txouts_result

    def get_tx_ins_outs(
        self,
        src_address: str,
        tx_files: TxFiles,
        txins: OptionalUTXOData = (),
        txouts: OptionalTxOuts = (),
        fee: int = 0,
        deposit: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        mint: OptionalTxOuts = (),
    ) -> Tuple[list, list]:
        """Return list of transaction's inputs and outputs.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_files: A `TxFiles` tuple containing files needed for the transaction.
            txins: An interable of `UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            fee: A fee amount (optional).
            deposit: A deposit amount needed by the transaction (optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            mint: A list (iterable) of `TxOuts`, specifying minted tokens (optional).

        Returns:
            Tuple[list, list]: A tuple of list of transaction inputs and list of transaction
                outputs.
        """
        txouts_passed_db: Dict[str, List[TxOut]] = self._organize_tx_ins_outs_by_coin(txouts)
        txouts_mint_db: Dict[str, List[TxOut]] = self._organize_tx_ins_outs_by_coin(mint)
        outcoins_all = {DEFAULT_COIN, *txouts_mint_db.keys(), *txouts_passed_db.keys()}
        outcoins_passed = [DEFAULT_COIN, *txouts_passed_db.keys()]

        txins_all = list(txins) or self._get_utxos_with_coins(
            src_address=src_address, coins=outcoins_all
        )
        txins_db_all: Dict[str, List[UTXOData]] = self._organize_tx_ins_outs_by_coin(txins_all)

        if not txins_all:
            LOGGER.error("No input UTxO.")
        elif not all(c in txins_db_all for c in outcoins_passed):
            LOGGER.error("Not all output coins are present in input UTxO.")

        if txins:
            # don't touch txins that were passed to the function
            txins_filtered = txins_all
            txins_db_filtered = txins_db_all
        else:
            # select only UTxOs that are needed to satisfy all outputs, deposits and fee
            selected_utxo_ids = self._select_utxos(
                tx_files=tx_files,
                txins_db=txins_db_all,
                txouts_passed_db=txouts_passed_db,
                txouts_mint_db=txouts_mint_db,
                fee=fee,
                deposit=deposit,
                withdrawals=withdrawals,
            )
            txins_by_id: Dict[str, List[UTXOData]] = self._organize_utxos_by_id(txins_all)
            _txins_filtered = [
                utxo for uid, utxo in txins_by_id.items() if uid in selected_utxo_ids
            ]

            if _txins_filtered:
                txins_filtered = list(itertools.chain.from_iterable(_txins_filtered))
            else:
                # there's always a txin needed, if only for the fee
                txins_filtered = [txins_all[0]] if txins_all else []

            txins_db_filtered = self._organize_tx_ins_outs_by_coin(txins_filtered)

        # balance the transaction
        txouts_balanced = self._balance_txouts(
            src_address=src_address,
            tx_files=tx_files,
            txins_db=txins_db_filtered,
            txouts_passed_db=txouts_passed_db,
            txouts_mint_db=txouts_mint_db,
            fee=fee,
            deposit=deposit,
            withdrawals=withdrawals,
        )

        # filter out negative token amounts (tokens burning)
        txouts_balanced = [r for r in txouts_balanced if r.amount > 0]

        if not txins_filtered:
            LOGGER.error("Cannot build transaction, empty `txins`.")
        if not txouts_balanced:
            LOGGER.error("Cannot build transaction, empty `txouts`.")

        return txins_filtered, txouts_balanced

    def get_withdrawals(self, withdrawals: List[TxOut]) -> List[TxOut]:
        """Get list of resolved reward withdrawals.

        The `TxOut.amount` can be '-1', meaning all available funds.

        Args:
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).

        Returns:
            List[TxOut]: A list of `TxOuts`, specifying resolved reward withdrawals.
        """
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
        join_txouts: bool = True,
    ) -> TxRawOutput:
        """Build a raw transaction.

        Args:
            out_file: An output file.
            txins: An interable of `UTXOData`, specifying input UTxOs.
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs.
            tx_files: A `TxFiles` tuple containing files needed for the transaction.
            fee: A fee amount.
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            mint: A list (iterable) of `TxOuts`, specifying minted tokens (optional).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).

        Returns:
            TxRawOutput: A tuple with transaction output details.
        """
        # pylint: disable=too-many-arguments
        out_file = Path(out_file)

        if join_txouts:
            # aggregate TX outputs by address
            txouts_by_addr: Dict[str, List[str]] = {}
            for rec in txouts:
                if rec.address not in txouts_by_addr:
                    txouts_by_addr[rec.address] = []
                coin = f" {rec.coin}" if rec.coin and rec.coin != DEFAULT_COIN else ""
                txouts_by_addr[rec.address].append(f"{rec.amount}{coin}")

            # join txouts with the same address
            txout_args: List[str] = []
            for addr, amounts in txouts_by_addr.items():
                amounts_joined = "+".join(amounts)
                txout_args.append(f"{addr}+{amounts_joined}")
        else:
            txout_args = [f"{rec.address}+{rec.amount}" for rec in txouts]

        # filter out duplicate txins
        txins_combined = {f"{x.utxo_hash}#{x.utxo_ix}" for x in txins}

        withdrawals_combined = [f"{x.address}+{x.amount}" for x in withdrawals]

        bound_args = []
        if invalid_before is not None:
            bound_args.extend(["--invalid-before", str(invalid_before)])
        if invalid_hereafter is None:
            # `--ttl` and `--upper-bound` are the same
            bound_args.extend(["--ttl", str(ttl)])
        else:
            bound_args.extend(["--invalid-hereafter", str(invalid_hereafter)])

        mint_records = [f"{m.amount} {m.coin}" for m in mint]
        mint_args = ["--mint", "+".join(mint_records)] if mint_records else []

        self.cli(
            [
                "transaction",
                "build-raw",
                "--fee",
                str(fee),
                "--out-file",
                str(out_file),
                *self._prepend_flag("--tx-in", txins_combined),
                *self._prepend_flag("--tx-out", txout_args),
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
        txins: OptionalUTXOData = (),
        txouts: OptionalTxOuts = (),
        tx_files: Optional[TxFiles] = None,
        fee: int = 0,
        ttl: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        deposit: Optional[int] = None,
        invalid_hereafter: Optional[int] = None,
        invalid_before: Optional[int] = None,
        mint: OptionalTxOuts = (),
        join_txouts: bool = True,
        destination_dir: FileType = ".",
    ) -> TxRawOutput:
        """Figure out all the missing data and build a raw transaction.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            txins: An interable of `UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            tx_files: A `TxFiles` tuple containing files needed for the transaction (optional).
            fee: A fee amount (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            deposit: A deposit amount needed by the transaction (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            mint: A list (iterable) of `TxOuts`, specifying minted tokens (optional).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            TxRawOutput: A tuple with transaction output details.
        """
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
            join_txouts=join_txouts,
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
        """Estimate the minimum fee for a transaction.

        Args:
            txbody_file: A path to file with transaction body.
            txin_count: A number of transaction inputs.
            txout_count: A number of transaction outputs.
            witness_count: A number of witnesses (optional).
            byron_witness_count: A number of Byron witnesses (optional).

        Returns:
            int: An estimated fee.
        """
        self.refresh_pparams_file()
        stdout = self.cli(
            [
                "transaction",
                "calculate-min-fee",
                *self.magic_args,
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
        txins: OptionalUTXOData = (),
        txouts: OptionalTxOuts = (),
        tx_files: Optional[TxFiles] = None,
        ttl: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        mint: OptionalTxOuts = (),
        witness_count_add: int = 0,
        join_txouts: bool = True,
        destination_dir: FileType = ".",
    ) -> int:
        """Build "dummy" transaction and calculate (estimate) it's fee.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            dst_addresses: A list of destination addresses (optional)
            txins: An interable of `UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            tx_files: A `TxFiles` tuple containing files needed for the transaction (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            mint: A list (iterable) of `TxOuts`, specifying minted tokens (optional).
            witness_count_add: A number of witnesses to add - workaround to make the fee
                calculation more precise.
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            int: An estimated fee.
        """
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
            join_txouts=join_txouts,
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
        script_files: OptionalFiles = (),
        destination_dir: FileType = ".",
    ) -> Path:
        """Sign a transaction.

        Args:
            tx_body_file: A path to file with transaction body.
            signing_key_files: A list of paths to signing key files.
            tx_name: A name of the transaction.
            script_files: A list of paths to script files (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to signed transaction file.
        """
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
                *self.magic_args,
                *self._prepend_flag("--signing-key-file", signing_key_files),
                *self._prepend_flag("--script-file", script_files),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def witness_tx(
        self,
        tx_body_file: FileType,
        witness_name: str,
        signing_key_files: OptionalFiles = (),
        script_file: Optional[FileType] = None,
        destination_dir: FileType = ".",
    ) -> Path:
        """Create a transaction witness.

        Args:
            tx_body_file: A path to file with transaction body.
            witness_name: A name of the transaction witness.
            signing_key_files: A list of paths to signing key files (optional).
            script_file: A path to script file (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to transaction witness file.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{witness_name}_tx.witness"

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
                *self.magic_args,
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
        """Assemble a tx body and witness(es) to form a signed transaction.

        Args:
            tx_body_file: A path to file with transaction body.
            witness_files: A list of paths to transaction witness files.
            tx_name: A name of the transaction.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to signed transaction file.
        """
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
        """Submit a transaction.

        Args:
            tx_file: A path to signed transaction file.
        """
        self.cli(
            [
                "transaction",
                "submit",
                *self.magic_args,
                "--tx-file",
                str(tx_file),
                f"--{self.protocol}-mode",
            ]
        )

    def send_tx(
        self,
        src_address: str,
        tx_name: str,
        txins: OptionalUTXOData = (),
        txouts: OptionalTxOuts = (),
        tx_files: Optional[TxFiles] = None,
        fee: Optional[int] = None,
        ttl: Optional[int] = None,
        withdrawals: OptionalTxOuts = (),
        deposit: Optional[int] = None,
        invalid_hereafter: Optional[int] = None,
        invalid_before: Optional[int] = None,
        script_files: OptionalFiles = (),
        join_txouts: bool = True,
        destination_dir: FileType = ".",
    ) -> TxRawOutput:
        """Build, Sign and Send a transaction.

        Args:
            src_address: An address used for fee and inputs (if inputs not specified by `txins`).
            tx_name: A name of the transaction.
            txins: An interable of `UTXOData`, specifying input UTxOs (optional).
            txouts: A list (iterable) of `TxOuts`, specifying transaction outputs (optional).
            tx_files: A `TxFiles` tuple containing files needed for the transaction (optional).
            fee: A fee amount (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            withdrawals: A list (iterable) of `TxOuts`, specifying reward withdrawals (optional).
            deposit: A deposit amount needed by the transaction (optional).
            invalid_hereafter: A last block when the transaction is still valid (optional).
            invalid_before: A first block when the transaction is valid (optional).
            script_files: A list of paths to script files (optional).
            join_txouts: A bool indicating whether to aggregate transaction outputs
                by payment address (True by default).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            TxRawOutput: A tuple with transaction output details.
        """
        # pylint: disable=too-many-arguments
        tx_files = tx_files or TxFiles()

        if fee is None:
            witness_count_add = 0
            if script_files:
                # TODO: workaround for https://github.com/input-output-hk/cardano-node/issues/1892
                witness_count_add += 5 * len(script_files)
            fee = self.calculate_tx_fee(
                src_address=src_address,
                tx_name=tx_name,
                txins=txins,
                txouts=txouts,
                tx_files=tx_files,
                ttl=invalid_hereafter or ttl,
                witness_count_add=witness_count_add,
                join_txouts=join_txouts,
                destination_dir=destination_dir,
            )
            # add 5% to the estimated fee, as the estimation is not precise enough
            fee = int(fee + fee * 0.05)

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
            join_txouts=join_txouts,
            destination_dir=destination_dir,
        )
        tx_signed_file = self.sign_tx(
            tx_body_file=tx_raw_output.out_file,
            tx_name=tx_name,
            signing_key_files=tx_files.signing_key_files,
            script_files=script_files,
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
        """Build a multi-signature script.

        Args:
            script_name: A name of the script.
            script_type_arg: A script type, see `MultiSigTypeArgs`.
            payment_vkey_files: A list of paths to payment vkey files.
            required: A number of required keys for the "atLeast" script type (optional).
            slot: A slot that sets script validity, depending on value of `slot_type_arg`
                (optional).
            slot_type_arg: A slot validity type, see `MultiSlotTypeArgs` (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the script file.
        """
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

        with open(out_file, "w") as fp_out:
            json.dump(script, fp_out, indent=4)

        return out_file

    def get_policyid(
        self,
        script_file: FileType,
    ) -> str:
        """Calculate the PolicyId from the monetary policy script.

        Args:
            script_file: A path to the script file.

        Returns:
            str: A script policyId.
        """
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
        """Create an update proposal.

        Args:
            cli_args: A list (iterable) of CLI arguments.
            epoch: An epoch where the update proposal will take effect.
            tx_name: A name of the transaction.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Path: A path to the update proposal file.
        """
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
                *self._prepend_flag(
                    "--genesis-verification-key-file", self.genesis_keys.genesis_vkeys
                ),
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
        """Submit an update proposal.

        Args:
            cli_args: A list (iterable) of CLI arguments.
            src_address: An address used for fee and inputs.
            src_skey_file: A path to skey file corresponding to the `src_address`.
            tx_name: A name of the transaction.
            epoch: An epoch where the update proposal will take effect (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            TxRawOutput: A tuple with transaction output details.
        """
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
                signing_key_files=[*self.genesis_keys.delegate_skeys, Path(src_skey_file)],
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
        """Send funds - convenience function for `send_tx`.

        Args:
            src_address: An address used for fee and inputs.
            destinations: A list (iterable) of `TxOuts`, specifying transaction outputs.
            tx_name: A name of the transaction.
            tx_files: A `TxFiles` tuple containing files needed for the transaction (optional).
            fee: A fee amount (optional).
            ttl: A last block when the transaction is still valid
                (deprecated in favor of `invalid_hereafter`, optional).
            deposit: A deposit amount needed by the transaction (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            TxRawOutput: A tuple with transaction output details.
        """
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
        """Wait for new block(s) to be created.

        Args:
            new_blocks: A number of new blocks to wait for (optional).
        """
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
        """Wait for new epoch(s).

        Args:
            new_epochs: A number of new epochs to wait for (optional).
            padding_seconds: A number of additional seconds to wait for (optional).
        """
        if new_epochs < 1:
            return

        last_block_epoch = self.get_last_block_epoch()
        expected_epoch_no = last_block_epoch + new_epochs

        LOGGER.debug(
            f"Current epoch: {last_block_epoch}; Waiting for the beginning of epoch: "
            "{expected_epoch_no}"
        )

        # how many seconds to wait until start of the expected epoch
        sleep_slots = (last_block_epoch + new_epochs) * self.epoch_length - (
            self.get_last_block_slot_no() + self.slots_offset
        )
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

    def time_to_next_epoch_start(self) -> float:
        """How many seconds to start of new epoch."""
        slots_to_go = (self.get_last_block_epoch() + 1) * self.epoch_length - (
            self.get_last_block_slot_no() + self.slots_offset
        )
        return float(slots_to_go * self.slot_length)

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
        """Register a stake pool.

        Args:
            pool_data: A `PoolData` tuple cointaining info about the stake pool.
            pool_owners: A list of `PoolUser` structures containing pool user addresses and keys.
            vrf_vkey_file: A path to node VRF vkey file.
            cold_key_pair: A `ColdKeyPair` tuple containing the key pair and the counter.
            tx_name: A name of the transaction.
            reward_account_vkey_file: A path to reward account vkey file (optional).
            deposit: A deposit amount needed by the transaction (optional).
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Tuple[Path, TxRawOutput]: A tuple with pool registration cert file and transaction
                output details.
        """
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
        """Deregister a stake pool.

        Args:
            pool_owners: A list of `PoolUser` structures containing pool user addresses and keys.
            cold_key_pair: A `ColdKeyPair` tuple containing the key pair and the counter.
            epoch: An epoch where the update proposal will take effect (optional).
            pool_name: A name of the stake pool.
            tx_name: A name of the transaction.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            Tuple[Path, TxRawOutput]: A tuple with pool registration cert file and transaction
                output details.
        """
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
        """Create and register a stake pool.

        Args:
            pool_data: A `PoolData` tuple cointaining info about the stake pool.
            pool_owners: A list of `PoolUser` structures containing pool user addresses and keys.
            tx_name: A name of the transaction.
            destination_dir: A path to directory for storing artifacts (optional).

        Returns:
            PoolCreationOutput: A tuple containing pool creation output.
        """
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

    def withdraw_reward(
        self,
        stake_addr_record: AddressRecord,
        dst_addr_record: AddressRecord,
        tx_name: str,
        verify: bool = True,
        destination_dir: FileType = ".",
    ) -> None:
        """Withdraw reward to payment address.

        Args:
            stake_addr_record: An `AddressRecord` tuple for the stake address with reward.
            dst_addr_record: An `AddressRecord` tuple for the destination payment address.
            tx_name: A name of the transaction.
            verify: A bool indicating whether to verify that the reward was transferred correctly.
            destination_dir: A path to directory for storing artifacts (optional).
        """
        dst_address = dst_addr_record.address
        src_init_balance = self.get_address_balance(dst_address)

        tx_files_withdrawal = TxFiles(
            signing_key_files=[dst_addr_record.skey_file, stake_addr_record.skey_file],
        )

        tx_raw_withdrawal_output = self.send_tx(
            src_address=dst_address,
            tx_name=f"{tx_name}_reward_withdrawal",
            tx_files=tx_files_withdrawal,
            withdrawals=[TxOut(address=stake_addr_record.address, amount=-1)],
            destination_dir=destination_dir,
        )
        self.wait_for_new_block(new_blocks=2)

        if not verify:
            return

        # check that reward is 0
        if self.get_stake_addr_info(stake_addr_record.address).reward_account_balance != 0:
            raise AssertionError("Not all rewards were transferred")

        # check that rewards were transferred
        src_reward_balance = self.get_address_balance(dst_address)
        if (
            src_reward_balance
            != src_init_balance
            - tx_raw_withdrawal_output.fee
            + tx_raw_withdrawal_output.withdrawals[0].amount  # type: ignore
        ):
            raise AssertionError(f"Incorrect balance for destination address `{dst_address}`")

    def __repr__(self) -> str:
        return f"<ClusterLib: protocol={self.protocol}, era={self.era}, tx_era={self.tx_era}>"
