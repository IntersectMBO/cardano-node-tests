"""Wrapper for cardano-cli."""
import datetime
import enum
import functools
import json
import logging
import random
import string
import subprocess
import time
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple

from cardano_node_tests.utils.types import FileType
from cardano_node_tests.utils.types import OptionalFiles
from cardano_node_tests.utils.types import UnpackableSequence

LOGGER = logging.getLogger(__name__)


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
    addr_hash: str
    delegation: str
    reward_account_balance: int


class UTXOData(NamedTuple):
    utxo_hash: str
    utxo_ix: str
    amount: int
    address: Optional[str] = None


class TxOut(NamedTuple):
    address: str
    amount: int


class TxFiles(NamedTuple):
    certificate_files: OptionalFiles = ()
    proposal_files: OptionalFiles = ()
    metadata_json_files: OptionalFiles = ()
    metadata_cbor_files: OptionalFiles = ()
    withdrawal_files: OptionalFiles = ()
    signing_key_files: OptionalFiles = ()


class TxRawData(NamedTuple):
    txins: List[UTXOData]
    txouts: List[TxOut]
    tx_files: TxFiles
    out_file: Path
    fee: int
    ttl: int


class PoolOwner(NamedTuple):
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
    pool_relay_port: int = 0


class PoolCreationArtifacts(NamedTuple):
    stake_pool_id: str
    vrf_key_pair: KeyPair
    cold_key_pair_and_counter: ColdKeyPair
    pool_reg_cert_file: Path
    pool_data: PoolData
    pool_owners: List[PoolOwner]
    tx_raw_data: TxRawData
    kes_key_pair: Optional[KeyPair] = None


class Protocols(enum.Enum):
    CARDANO = "cardano"
    SHELLEY = "shelley"


class CLIError(Exception):
    pass


def get_rand_str(length: int = 8) -> str:
    """Return random string."""
    if length < 1:
        return ""
    return "".join(random.choice(string.ascii_lowercase) for i in range(length))


def get_timestamped_rand_str(rand_str_length: int = 4) -> str:
    """Return random string prefixed with timestamp.

    >>> len(get_timestamped_rand_str()) == len("200801_002401314_cinf")
    True
    """
    timestamp = datetime.datetime.now().strftime("%y%m%d_%H%M%S%f")[:-3]
    rand_str_component = get_rand_str(rand_str_length)
    rand_str_component = rand_str_component and f"_{rand_str_component}"
    return f"{timestamp}{rand_str_component}"


class ClusterLib:
    """Cluster Lib."""

    # pylint: disable=too-many-public-methods

    def __init__(self, state_dir: FileType, protocol: str = Protocols.SHELLEY.value):
        self.cli_coverage: dict = {}

        self.state_dir = Path(state_dir).expanduser().resolve()
        self.genesis_json = self.state_dir / "shelley" / "genesis.json"
        self.genesis_utxo_vkey = self.state_dir / "shelley" / "genesis-utxo.vkey"
        self.genesis_utxo_skey = self.state_dir / "shelley" / "genesis-utxo.skey"
        self.genesis_vkeys = list(self.state_dir.glob("shelley/genesis-keys/genesis?.vkey"))
        self.delegate_skeys = list(self.state_dir.glob("shelley/delegate-keys/delegate?.skey"))
        self.pparams_file = self.state_dir / "pparams.json"
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
        self.genesis_utxo_addr = self.gen_genesis_addr(vkey_file=self.genesis_utxo_vkey)

        self.protocol = protocol
        self._check_protocol()

    def _check_state_dir(self):
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

    def _check_protocol(self):
        """Check that the cluster is running with the expected protocol."""
        try:
            self.refresh_pparams_file()
        except CLIError as exc:
            if "SingleEraInfo" not in str(exc):
                raise
            raise CLIError(
                f"The cluster is running with protocol different from '{self.protocol}':\n" f"{exc}"
            )

    def _check_outfiles(self, *out_files):
        """Check that the expected output files were created."""
        for out_file in out_files:
            out_file = Path(out_file).expanduser()
            if not out_file.exists():
                raise CLIError(f"The expected file `{out_file}` doesn't exist.")

    def record_cli_coverage(self, cli_args: List[str]):
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

    def cli(self, cli_args) -> CLIOut:
        """Run the `cardano-cli` command."""
        cmd = ["cardano-cli", "shelley", *cli_args]
        self.record_cli_coverage(cmd)
        cmd_str = " ".join(cmd)

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        LOGGER.debug("Running `%s`", cmd_str)

        stdout, stderr = p.communicate()
        if p.returncode != 0:
            raise CLIError(
                f"An error occurred running a CLI command `{cmd_str}`: {stderr.decode()}"
            )

        return CLIOut(stdout or b"", stderr or b"")

    def _prepend_flag(self, flag: str, contents: UnpackableSequence) -> list:
        """Prepend flag to every item of the sequence.

        >>> ClusterLib._prepend_flag(None, "--foo", [1, 2, 3])
        ['--foo', '1', '--foo', '2', '--foo', '3']
        """
        return sum(([flag, str(x)] for x in contents), [])

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

    def refresh_pparams_file(self):
        """Refresh protocol parameters file."""
        self.query_cli(["protocol-parameters", "--out-file", str(self.pparams_file)])

    def get_utxo(self, address: str) -> List[UTXOData]:
        """Return UTXO info for payment address."""
        utxo_dict = json.loads(
            self.query_cli(["utxo", "--address", address, "--out-file", "/dev/stdout"])
        )

        utxo = []
        for utxo_rec, utxo_data in utxo_dict.items():
            utxo_hash, utxo_ix = utxo_rec.split("#")
            utxo.append(
                UTXOData(
                    utxo_hash=utxo_hash,
                    utxo_ix=utxo_ix,
                    amount=utxo_data["amount"],
                    address=utxo_data["address"],
                )
            )
        return utxo

    def get_tip(self) -> dict:
        """Return current tip - last block successfully applied to the ledger."""
        return json.loads(self.query_cli(["tip"]))

    def gen_genesis_addr(self, vkey_file: FileType, *args: str) -> str:
        """Generate genesis address."""
        return (
            self.cli(
                [
                    "genesis",
                    "initial-addr",
                    "--testnet-magic",
                    str(self.network_magic),
                    "--verification-key-file",
                    str(vkey_file),
                    *args,
                ]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

    def gen_payment_addr(
        self, payment_vkey_file: FileType, *args: str, stake_vkey_file: Optional[FileType] = None,
    ) -> str:
        """Generate payment address."""
        cli_args = ["--payment-verification-key-file", str(payment_vkey_file)]
        if stake_vkey_file:
            cli_args.extend(["--stake-verification-key-file", str(stake_vkey_file)])

        return (
            self.cli(
                ["address", "build", "--testnet-magic", str(self.network_magic), *cli_args, *args]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

    def gen_stake_addr(self, stake_vkey_file: FileType, *args: str) -> str:
        """Generate stake address."""
        return (
            self.cli(
                [
                    "stake-address",
                    "build",
                    "--stake-verification-key-file",
                    str(stake_vkey_file),
                    "--testnet-magic",
                    str(self.network_magic),
                    *args,
                ]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

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
            payment_vkey_file=key_pair.vkey_file, stake_vkey_file=stake_vkey_file
        )
        return AddressRecord(
            address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file
        )

    def gen_stake_addr_and_keys(self, name: str, destination_dir: FileType = ".") -> AddressRecord:
        """Generate stake address and key pair."""
        key_pair = self.gen_stake_key_pair(key_name=name, destination_dir=destination_dir,)
        addr = self.gen_stake_addr(stake_vkey_file=key_pair.vkey_file)
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
        destination_dir: FileType = ".",
    ) -> Path:
        """Generate node operational certificate.

        This certificate is used when starting the node and not submitted through a transaction.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{node_name}.opcert"
        last_block_kes_period = self.get_last_block_kes_period()
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
                str(last_block_kes_period),
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
        """Generate stake address de-registration certificate."""
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
        node_cold_vkey_file: FileType,
        destination_dir: FileType = ".",
    ) -> Path:
        """Generate stake address delegation certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake_deleg.cert"
        self.cli(
            [
                "stake-address",
                "delegation-certificate",
                "--stake-verification-key-file",
                str(stake_vkey_file),
                "--cold-verification-key-file",
                str(node_cold_vkey_file),
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
        node_vrf_vkey_file: FileType,
        node_cold_vkey_file: FileType,
        owner_stake_vkey_files: List[FileType],
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
                str(node_vrf_vkey_file),
                "--cold-verification-key-file",
                str(node_cold_vkey_file),
                "--pool-reward-account-verification-key-file",
                str(owner_stake_vkey_files[0]),
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
        """Generate pool de-registration certificate."""
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

    def get_ledger_state(self) -> dict:
        """Return ledger state info."""
        return json.loads(self.query_cli(["ledger-state"]))

    def get_registered_stake_pools_ledger_state(self) -> dict:
        """Return ledger state info for registered stake pools."""
        registered_pools_details = self.get_ledger_state()["esLState"]["_delegationState"][
            "_pstate"
        ]["_pParams"]
        return registered_pools_details

    def get_stake_pool_id(self, pool_cold_vkey_file: FileType) -> str:
        """Return ID of stake pool."""
        pool_id = (
            self.cli(["stake-pool", "id", "--verification-key-file", str(pool_cold_vkey_file)])
            .stdout.strip()
            .decode("utf-8")
        )
        return pool_id

    def delegate_stake_addr(self, stake_addr_skey: FileType, pool_id: str, delegation_fee: int):
        """Delegate stake address to stake pool."""
        cli_args = [
            "stake-address",
            "delegate",
            "--signing-key-file",
            str(stake_addr_skey),
            "--pool-id",
            str(pool_id),
            "--delegation-fee",
            str(delegation_fee),
        ]

        stdout = self.cli(cli_args).stdout
        if stdout and "runStakeAddressCmd" in stdout.decode():
            cmd = " ".join(cli_args)
            raise CLIError(
                f"command not implemented yet;\ncommand: {cmd}\nresult: {stdout.decode()}"
            )

    def get_stake_addr_info(self, stake_addr: str) -> Optional[StakeAddrInfo]:
        """Return info about stake pool address."""
        output_json = json.loads(self.query_cli(["stake-address-info", "--address", stake_addr]))
        if not output_json:
            return None

        addr_hash = list(output_json)[0]
        address_rec = output_json[addr_hash]
        delegation = address_rec.get("delegation") or ""
        reward_account_balance = address_rec.get("rewardAccountBalance") or 0
        return StakeAddrInfo(
            addr_hash=addr_hash,
            delegation=delegation,
            reward_account_balance=reward_account_balance,
        )

    def get_protocol_params(self) -> dict:
        """Return up-to-date protocol parameters."""
        self.refresh_pparams_file()
        with open(self.pparams_file) as in_json:
            return json.load(in_json)

    def get_key_deposit(self) -> int:
        """Return key deposit amount."""
        return int(self.get_protocol_params()["keyDeposit"])

    def get_pool_deposit(self) -> int:
        """Return pool deposit amount."""
        return int(self.get_protocol_params()["poolDeposit"])

    def get_stake_distribution(self) -> dict:
        """Return info about stake distribution per stake pool."""
        # stake pool values are displayed starting with line 2 from the command output
        result = self.query_cli(["stake-distribution"]).splitlines()[2:]
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

    def get_address_balance(self, address: str) -> int:
        """Return total balance of an address (sum of all UTXO balances)."""
        utxo = self.get_utxo(address)
        address_balance = functools.reduce(lambda x, y: x + y.amount, utxo, 0)
        return int(address_balance)

    def get_utxo_with_highest_amount(self, address: str) -> UTXOData:
        """Return data for UTXO with highest amount."""
        utxo = self.get_utxo(address)
        highest_amount_rec = max(utxo, key=lambda x: x.amount)
        return highest_amount_rec

    def calculate_tx_ttl(self) -> int:
        """Calculate ttl for a transaction."""
        return self.get_last_block_slot_no() + self.ttl_length

    def get_last_block_kes_period(self) -> int:
        """Return last block KES period."""
        return int(self.get_last_block_block_no() / self.slots_per_kes_period)

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

    def get_tx_ins_outs(
        self,
        src_address: str,
        tx_files: TxFiles,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        fee: int = 0,
        deposit: Optional[int] = None,
    ) -> Tuple[list, list]:
        """Return list of transaction's inputs and outputs."""
        txins_copy = list(txins) if txins else self.get_utxo(src_address)
        txouts_copy = list(txouts) if txouts else []
        max_address = None

        # the value "-1" means all available funds
        max_index = [idx for idx, val in enumerate(txouts_copy) if val[1] == -1]
        if len(max_index) > 1:
            raise CLIError("Cannot send all remaining funds to more than one address.")
        if max_index:
            max_address = txouts_copy.pop(max_index[0]).address

        total_input_amount = functools.reduce(lambda x, y: x + y[2], txins_copy, 0)
        total_output_amount = functools.reduce(lambda x, y: x + y[1], txouts_copy, 0)

        tx_deposit = self.get_tx_deposit(tx_files=tx_files) if deposit is None else deposit
        funds_needed = total_output_amount + fee + tx_deposit
        change = total_input_amount - funds_needed
        if change < 0:
            LOGGER.error(
                "Not enough funds to make a transaction - "
                f"available: {total_input_amount}; needed {funds_needed}"
            )
        if change > 0:
            txouts_copy.append(TxOut(address=(max_address or src_address), amount=change))

        if not txins_copy:
            LOGGER.error("Cannot build transaction, empty `txins`.")
        if not txouts_copy:
            LOGGER.error("Cannot build transaction, empty `txouts`.")

        return txins_copy, txouts_copy

    def build_raw_tx_bare(
        self,
        out_file: FileType,
        txins: List[UTXOData],
        txouts: List[TxOut],
        tx_files: TxFiles,
        fee: int,
        ttl: int,
    ) -> TxRawData:
        """Build raw transaction."""
        out_file = Path(out_file)
        txins_combined = [f"{x[0]}#{x[1]}" for x in txins]
        txouts_combined = [f"{x[0]}+{x[1]}" for x in txouts]

        self.cli(
            [
                "transaction",
                "build-raw",
                "--ttl",
                str(ttl),
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
                *self._prepend_flag("--withdrawal", tx_files.withdrawal_files),
            ]
        )

        return TxRawData(
            txins=txins, txouts=txouts, tx_files=tx_files, out_file=out_file, fee=fee, ttl=ttl
        )

    def build_raw_tx(
        self,
        src_address: str,
        tx_name: Optional[str] = None,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        fee: int = 0,
        ttl: Optional[int] = None,
        deposit: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> TxRawData:
        """Figure out all the missing data and build raw transaction."""
        tx_name = tx_name or get_timestamped_rand_str()
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{tx_name}_tx.body"
        tx_files = tx_files or TxFiles()
        ttl = ttl or self.calculate_tx_ttl()

        txins_copy, txouts_copy = self.get_tx_ins_outs(
            src_address=src_address,
            tx_files=tx_files,
            txins=txins,
            txouts=txouts,
            fee=fee,
            deposit=deposit,
        )

        tx_raw_data = self.build_raw_tx_bare(
            out_file=out_file,
            txins=txins_copy,
            txouts=txouts_copy,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
        )

        self._check_outfiles(out_file)
        return tx_raw_data

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
        tx_name: Optional[str] = None,
        dst_addresses: Optional[List[str]] = None,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        ttl: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> int:
        """Build "dummy" transaction and calculate it's fee."""
        tx_files = tx_files or TxFiles()
        tx_name = tx_name or get_timestamped_rand_str()
        tx_name = f"{tx_name}_estimate"

        if dst_addresses and txouts:
            LOGGER.warning(
                "The value of `dst_addresses` is ignored when value for `txouts` is available"
            )

        txouts_filled = txouts or [TxOut(address=r, amount=1) for r in (dst_addresses or ())]

        tx_raw_data = self.build_raw_tx(
            src_address=src_address,
            tx_name=tx_name,
            txins=txins,
            txouts=txouts_filled,
            tx_files=tx_files,
            fee=0,
            ttl=ttl,
            deposit=0,
            destination_dir=destination_dir,
        )

        fee = self.estimate_fee(
            txbody_file=tx_raw_data.out_file,
            txin_count=len(tx_raw_data.txins),
            txout_count=len(tx_raw_data.txouts),
            witness_count=len(tx_files.signing_key_files),
        )

        return fee

    def sign_tx(
        self,
        tx_body_file: FileType,
        tx_name: Optional[str] = None,
        signing_key_files: OptionalFiles = (),
        destination_dir: FileType = ".",
    ) -> Path:
        """Sign transaction."""
        tx_name = tx_name or get_timestamped_rand_str()
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

    def submit_tx(self, tx_file: FileType):
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
        tx_name: Optional[str] = None,
        txins: Optional[List[UTXOData]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        fee: Optional[int] = None,
        ttl: Optional[int] = None,
        deposit: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> TxRawData:
        """Build, Sign and Send transaction to chain."""
        tx_files = tx_files or TxFiles()
        tx_name = tx_name or get_timestamped_rand_str()

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

        tx_raw_data = self.build_raw_tx(
            src_address=src_address,
            tx_name=tx_name,
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
            deposit=deposit,
            destination_dir=destination_dir,
        )
        tx_signed_file = self.sign_tx(
            tx_body_file=tx_raw_data.out_file,
            tx_name=tx_name,
            signing_key_files=tx_files.signing_key_files,
            destination_dir=destination_dir,
        )
        self.submit_tx(tx_signed_file)

        return tx_raw_data

    def gen_update_proposal(
        self,
        cli_args: UnpackableSequence,
        epoch: int,
        tx_name: Optional[str] = None,
        destination_dir: FileType = ".",
    ) -> Path:
        """Create update proposal."""
        tx_name = tx_name or get_timestamped_rand_str()
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
        epoch: Optional[int] = None,
        tx_name: Optional[str] = None,
        destination_dir: FileType = ".",
    ) -> TxRawData:
        """Submit update proposal."""
        tx_name = tx_name or get_timestamped_rand_str()
        # TODO: assumption is update proposals submitted near beginning of epoch
        epoch = epoch if epoch is not None else self.get_last_block_epoch()

        out_file = self.gen_update_proposal(
            cli_args=cli_args, tx_name=tx_name, epoch=epoch, destination_dir=destination_dir,
        )

        return self.send_tx(
            src_address=self.genesis_utxo_addr,
            tx_name=tx_name,
            tx_files=TxFiles(
                proposal_files=[out_file],
                signing_key_files=[*self.delegate_skeys, self.genesis_utxo_skey],
            ),
            destination_dir=destination_dir,
        )

    def send_funds(
        self,
        src_address: str,
        destinations: List[TxOut],
        tx_name: Optional[str] = None,
        tx_files: Optional[TxFiles] = None,
        fee: Optional[int] = None,
        ttl: Optional[int] = None,
        deposit: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> TxRawData:
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

    def wait_for_new_block(self, new_blocks: int = 1):
        """Wait for new block(s) to be created."""
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

    def wait_for_new_epoch(self, new_epochs: int = 1):
        """Wait for new epoch(s)."""
        last_block_epoch = self.get_last_block_epoch()
        LOGGER.debug(
            f"Current epoch: {last_block_epoch}; Waiting the beginning of epoch: "
            "{last_block_epoch + new_epochs}"
        )

        expected_epoch_no = last_block_epoch + new_epochs

        # how many seconds to wait until start of the expected epoch
        sleep_slots = (
            last_block_epoch + new_epochs
        ) * self.epoch_length - self.get_last_block_slot_no()
        sleep_time = int(sleep_slots * self.slot_length) + 1
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
        pool_owners: List[PoolOwner],
        node_vrf_vkey_file: FileType,
        node_cold_key_pair: ColdKeyPair,
        pool_reg_cert_file: Optional[FileType] = None,
        tx_name: Optional[str] = None,
        deposit: Optional[int] = None,
        destination_dir: FileType = ".",
    ) -> Tuple[Path, TxRawData]:
        """Register stake pool."""
        pool_reg_cert_file = Path(
            pool_reg_cert_file
            or self.gen_pool_registration_cert(
                pool_data=pool_data,
                node_vrf_vkey_file=node_vrf_vkey_file,
                node_cold_vkey_file=node_cold_key_pair.vkey_file,
                owner_stake_vkey_files=[p.stake.vkey_file for p in pool_owners],
                destination_dir=destination_dir,
            )
        )

        # submit the pool registration certificate through a tx
        tx_files = TxFiles(
            certificate_files=[pool_reg_cert_file],
            signing_key_files=[
                *[p.payment.skey_file for p in pool_owners],
                *[p.stake.skey_file for p in pool_owners],
                node_cold_key_pair.skey_file,
            ],
        )

        tx_raw_data = self.send_tx(
            src_address=pool_owners[0].payment.address,
            tx_name=tx_name,
            tx_files=tx_files,
            deposit=deposit,
            destination_dir=destination_dir,
        )
        self.wait_for_new_block(new_blocks=2)

        return pool_reg_cert_file, tx_raw_data

    def deregister_stake_pool(
        self,
        pool_owners: List[PoolOwner],
        node_cold_key_pair: ColdKeyPair,
        epoch: int,
        pool_name: str,
        tx_name: Optional[str] = None,
        destination_dir: FileType = ".",
    ) -> Tuple[Path, TxRawData]:
        """De-register stake pool."""
        LOGGER.debug(
            f"Deregistering stake pool starting with epoch: {epoch}; "
            f"Current epoch is: {self.get_last_block_epoch()}"
        )
        pool_dereg_cert_file = self.gen_pool_deregistration_cert(
            pool_name=pool_name,
            cold_vkey_file=node_cold_key_pair.vkey_file,
            epoch=epoch,
            destination_dir=destination_dir,
        )

        # submit the pool deregistration certificate through a tx
        tx_files = TxFiles(
            certificate_files=[pool_dereg_cert_file],
            signing_key_files=[
                *[p.payment.skey_file for p in pool_owners],
                *[p.stake.skey_file for p in pool_owners],
                node_cold_key_pair.skey_file,
            ],
        )

        tx_raw_data = self.send_tx(
            src_address=pool_owners[0].payment.address,
            tx_name=tx_name,
            tx_files=tx_files,
            destination_dir=destination_dir,
        )
        self.wait_for_new_block(new_blocks=2)

        return pool_dereg_cert_file, tx_raw_data

    def create_stake_pool(
        self, pool_data: PoolData, pool_owners: List[PoolOwner], destination_dir: FileType = "."
    ) -> PoolCreationArtifacts:
        """Create and register stake pool."""
        # create the KES key pair
        node_kes = self.gen_kes_key_pair(
            node_name=pool_data.pool_name, destination_dir=destination_dir,
        )
        LOGGER.debug(f"KES keys created - {node_kes.vkey_file}; {node_kes.skey_file}")

        # create the VRF key pair
        node_vrf = self.gen_vrf_key_pair(
            node_name=pool_data.pool_name, destination_dir=destination_dir,
        )
        LOGGER.debug(f"VRF keys created - {node_vrf.vkey_file}; {node_vrf.skey_file}")

        # create the cold key pair and node operational certificate counter
        node_cold = self.gen_cold_key_pair_and_counter(
            node_name=pool_data.pool_name, destination_dir=destination_dir,
        )
        LOGGER.debug(
            "Cold keys created and counter created - "
            f"{node_cold.vkey_file}; {node_cold.skey_file}; {node_cold.counter_file}"
        )

        pool_reg_cert_file, tx_raw_data = self.register_stake_pool(
            pool_data=pool_data,
            pool_owners=pool_owners,
            node_vrf_vkey_file=node_vrf.vkey_file,
            node_cold_key_pair=node_cold,
            destination_dir=destination_dir,
        )

        return PoolCreationArtifacts(
            stake_pool_id=self.get_stake_pool_id(node_cold.vkey_file),
            vrf_key_pair=node_vrf,
            cold_key_pair_and_counter=node_cold,
            pool_reg_cert_file=pool_reg_cert_file,
            pool_data=pool_data,
            pool_owners=pool_owners,
            tx_raw_data=tx_raw_data,
            kes_key_pair=node_kes,
        )
