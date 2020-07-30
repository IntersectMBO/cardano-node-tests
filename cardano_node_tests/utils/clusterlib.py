"""Wrapper for cardano-cli."""
import functools
import json
import logging
import subprocess
import time
from pathlib import Path
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Tuple
from typing import Union

from .types import FileType
from .types import OptionalFiles
from .types import UnpackableSequence

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
    delegation: Optional[str]
    reward_account_balance: Optional[int]


class UTXORec(NamedTuple):
    utxo_hash: str
    utxo_ix: str
    amount: int
    address: str


class TxIn(NamedTuple):
    utxo_hash: str
    utxo_ix: str
    amount: int


class TxOut(NamedTuple):
    address: str
    amount: int


class TxRawData(NamedTuple):
    txins: List[TxIn]
    txouts: List[TxOut]
    outfile: Path
    fee: int
    ttl: int


class PoolCreationArtifacts(NamedTuple):
    stake_pool_id: str
    kes_key_pair: KeyPair
    vrf_key_pair: KeyPair
    cold_key_pair_and_counter: ColdKeyPair
    pool_reg_cert_file: Path
    tx_raw_data: TxRawData


class PoolOwner(NamedTuple):
    addr: str
    stake_addr: str
    addr_key_pair: KeyPair
    stake_key_pair: KeyPair


class PoolData(NamedTuple):
    pool_name: str
    pool_pledge: int
    pool_cost: int
    pool_margin: float
    pool_metadata_url: str = ""
    pool_metadata_hash: str = ""
    pool_relay_dns: str = ""
    pool_relay_port: int = 0


class TxFiles(NamedTuple):
    certificate_files: OptionalFiles = ()
    proposal_files: OptionalFiles = ()
    metadata_json_files: OptionalFiles = ()
    metadata_cbor_files: OptionalFiles = ()
    withdrawal_files: OptionalFiles = ()
    signing_key_files: OptionalFiles = ()


class CLIError(Exception):
    pass


class ClusterLib:
    """Cluster Lib."""

    # pylint: disable=too-many-public-methods

    def __init__(self, state_dir: Union[str, Path]):
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.genesis_json = self.state_dir / "shelley" / "genesis.json"
        self.genesis_utxo_vkey = self.state_dir / "shelley" / "genesis-utxo.vkey"
        self.genesis_utxo_skey = self.state_dir / "shelley" / "genesis-utxo.skey"
        self.genesis_vkey = self.state_dir / "shelley" / "genesis-keys" / "genesis1.vkey"
        self.delegate_skey = self.state_dir / "shelley" / "delegate-keys" / "delegate1.skey"
        self.pparams_file = self.state_dir / "pparams.json"

        self._check_state_dir()

        with open(self.genesis_json) as in_json:
            self.genesis = json.load(in_json)

        self.network_magic = self.genesis["networkMagic"]
        self.slot_length = self.genesis["slotLength"]
        self.epoch_length = self.genesis["epochLength"]
        self.slots_per_kes_period = self.genesis["slotsPerKESPeriod"]
        self.max_kes_evolutions = self.genesis["maxKESEvolutions"]

        self.genesis_utxo_addr = self.gen_genesis_addr(vkey_file=self.genesis_utxo_vkey)

    def _check_state_dir(self):
        """Check that all files expected by `__init__` are present."""
        if not self.state_dir.exists():
            raise CLIError(f"The state dir `{self.state_dir}` doesn't exist.")

        for file_name in (
            self.genesis_json,
            self.genesis_utxo_vkey,
            self.genesis_utxo_skey,
            self.genesis_vkey,
            self.delegate_skey,
        ):
            if not file_name.exists():
                raise CLIError(f"The file `{file_name}` doesn't exist.")

    @staticmethod
    def _check_outfiles(*out_files):
        """Check that the expected output files were created."""
        for out_file in out_files:
            out_file = Path(out_file).expanduser()
            if not out_file.exists():
                raise CLIError(f"The expected file `{out_file}` doesn't exist.")

    @staticmethod
    def cli(cli_args) -> CLIOut:
        """Run the `cardano-cli` command."""
        cmd = ["cardano-cli", "shelley", *cli_args]
        cmd_str = " ".join(cmd)

        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        LOGGER.debug("Running `%s`", cmd_str)

        stdout, stderr = p.communicate()
        if p.returncode != 0:
            raise CLIError(
                f"An error occurred running a CLI command `{cmd_str}`: {stderr.decode()}"
            )

        return CLIOut(stdout or b"", stderr or b"")

    @staticmethod
    def _prepend_flag(flag: str, contents: UnpackableSequence) -> list:
        """Prepend flag to every item of the sequence.

        >>> ClusterLib._prepend_flag("--foo", [1, 2, 3])
        ['--foo', '1', '--foo', '2', '--foo', '3']
        """
        return sum(([flag, str(x)] for x in contents), [])

    def query_cli(self, cli_args: UnpackableSequence) -> str:
        """Run the `cardano-cli query` command."""
        stdout = self.cli(
            ["query", *cli_args, "--testnet-magic", str(self.network_magic), "--shelley-mode"]
        ).stdout
        stdout_dec = stdout.decode("utf-8") if stdout else ""
        return stdout_dec

    def refresh_pparams_file(self):
        """Refresh protocol parameters file."""
        self.query_cli(["protocol-parameters", "--out-file", str(self.pparams_file)])

    def get_utxo(self, address: str) -> dict:
        """Return UTXO info for payment address."""
        utxo = json.loads(
            self.query_cli(["utxo", "--address", address, "--out-file", "/dev/stdout"])
        )
        return utxo

    def get_tip(self) -> dict:
        """Return current tip - last block successfully applied to the ledger."""
        return json.loads(self.query_cli(["tip"]))

    def gen_genesis_addr(self, vkey_file: FileType, *args: UnpackableSequence) -> str:
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
        self,
        payment_vkey_file: FileType,
        *args: UnpackableSequence,
        stake_vkey_file: Optional[FileType] = None,
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

    def gen_stake_addr(self, stake_vkey_file: FileType, *args: UnpackableSequence) -> str:
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

    def gen_payment_key_pair(self, destination_dir: FileType, key_name: str) -> KeyPair:
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

    def gen_stake_key_pair(self, destination_dir: FileType, key_name: str) -> KeyPair:
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
        self, destination_dir: FileType, name: str, stake_vkey_file: Optional[FileType] = None
    ) -> AddressRecord:
        """Generate payment address and key pair."""
        key_pair = self.gen_payment_key_pair(destination_dir=destination_dir, key_name=name)
        addr = self.gen_payment_addr(
            payment_vkey_file=key_pair.vkey_file, stake_vkey_file=stake_vkey_file
        )
        return AddressRecord(
            address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file
        )

    def gen_stake_addr_and_keys(self, destination_dir: FileType, name: str) -> AddressRecord:
        """Generate stake address and key pair."""
        key_pair = self.gen_stake_key_pair(destination_dir=destination_dir, key_name=name)
        addr = self.gen_stake_addr(stake_vkey_file=key_pair.vkey_file)
        return AddressRecord(
            address=addr, vkey_file=key_pair.vkey_file, skey_file=key_pair.skey_file
        )

    def gen_kes_key_pair(self, destination_dir: FileType, node_name: str) -> KeyPair:
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

    def gen_vrf_key_pair(self, destination_dir: FileType, node_name: str) -> KeyPair:
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
        self, destination_dir: FileType, node_name: str
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
        destination_dir: FileType,
        node_name: str,
        node_kes_vkey_file: FileType,
        node_cold_skey_file: FileType,
        node_cold_counter_file: FileType,
    ) -> Path:
        """Generate node operational certificate.

        This certificate is used when starting the node and not submitted through a transaction.
        """
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{node_name}.opcert"
        current_kes_period = self.get_current_kes_period()
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
                str(current_kes_period),
                "--out-file",
                str(out_file),
            ]
        )

        self._check_outfiles(out_file)
        return out_file

    def gen_stake_addr_registration_cert(
        self, destination_dir: FileType, addr_name: str, stake_vkey_file: FileType
    ) -> Path:
        """Generate stake address registration certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake.reg.cert"
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

    def gen_stake_addr_delegation_cert(
        self,
        destination_dir: FileType,
        addr_name: str,
        stake_vkey_file: FileType,
        node_cold_vkey_file: FileType,
    ) -> Path:
        """Generate stake address delegation certificate."""
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake.deleg.cert"
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
        destination_dir: FileType,
        pool_data: PoolData,
        node_vrf_vkey_file: FileType,
        node_cold_vkey_file: FileType,
        owner_stake_vkey_files: List[FileType],
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
        self, destination_dir: FileType, pool_name: str, cold_vkey_file: FileType, epoch: int,
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

    def get_stake_addr_info(self, stake_addr: str) -> StakeAddrInfo:
        """Return info about stake pool address."""
        output_json = json.loads(self.query_cli(["stake-address-info", "--address", stake_addr]))
        addr_hash = list(output_json)[0]
        address_rec = output_json[addr_hash]
        delegation = address_rec.get("delegation")
        reward_account_balance = address_rec.get("rewardAccountBalance")
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
        return int(self.get_last_block_slot_no() / self.epoch_length)

    def get_address_balance(self, address: str) -> int:
        """Return total balance of an address (sum of all UTXO balances)."""
        available_utxos = self.get_utxo(address) or {}
        address_balance = functools.reduce(
            lambda x, y: x + y["amount"], available_utxos.values(), 0
        )
        return int(address_balance)

    def get_utxo_with_highest_amount(self, address: str) -> UTXORec:
        """Return data for UTXO with highest amount."""
        utxo = self.get_utxo(address=address)
        highest_amount_rec = max(utxo.items(), key=lambda x: x[1].get("amount", 0))
        utxo_hash, utxo_ix = highest_amount_rec[0].split("#")
        return UTXORec(
            utxo_hash=utxo_hash,
            utxo_ix=utxo_ix,
            amount=highest_amount_rec[1]["amount"],
            address=highest_amount_rec[1]["address"],
        )

    def calculate_tx_ttl(self) -> int:
        """Calculate ttl for a transaction."""
        return self.get_last_block_slot_no() + 1000

    def get_current_kes_period(self) -> int:
        """Return current KES period."""
        return int(self.get_last_block_block_no() / self.slots_per_kes_period)

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

        return deposit

    def get_tx_ins_outs(
        self,
        src_address: str,
        tx_files: TxFiles,
        txins: Optional[List[TxIn]] = None,
        txouts: Optional[List[TxOut]] = None,
        fee: int = 0,
        deposit: Optional[int] = None,
    ) -> Tuple[list, list]:
        """Return list of transaction's inputs and outputs."""
        txins_copy = list(txins) if txins else []
        txouts_copy = list(txouts) if txouts else []
        max_address = None

        if not txins_copy:
            utxo = self.get_utxo(address=src_address)
            for k, v in utxo.items():
                txin = k.split("#")
                txin = TxIn(txin[0], txin[1], v["amount"])
                txins_copy.append(txin)

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
            txouts_copy.append(TxOut((max_address or src_address), change))

        if not txins_copy:
            LOGGER.error("Cannot build transaction, empty `txins`.")
        if not txouts_copy:
            LOGGER.error("Cannot build transaction, empty `txouts`.")

        return txins_copy, txouts_copy

    def build_raw_tx_bare(
        self,
        out_file: FileType,
        txins: List[TxIn],
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

        return TxRawData(txins=txins, txouts=txouts, outfile=out_file, fee=fee, ttl=ttl)

    def build_raw_tx(
        self,
        out_file: FileType,
        src_address: str,
        txins: Optional[List[TxIn]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        fee: int = 0,
        ttl: Optional[int] = None,
        deposit: Optional[int] = None,
    ) -> TxRawData:
        """Figure out all the missing data and build raw transaction."""
        out_file = Path(out_file)
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
        dst_addresses: Optional[List[str]] = None,
        txins: Optional[List[TxIn]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        ttl: Optional[int] = None,
    ) -> int:
        """Build "dummy" transaction and calculate it's fee."""
        tx_files = tx_files or TxFiles()
        out_file = Path("tx.body_estimate")

        if dst_addresses and txouts:
            LOGGER.warning(
                "The value of `dst_addresses` is ignored when value for `txouts` is available"
            )

        txouts_filled = txouts or [TxOut(address=r, amount=1) for r in (dst_addresses or ())]

        tx_raw_data = self.build_raw_tx(
            out_file,
            src_address=src_address,
            txins=txins,
            txouts=txouts_filled,
            tx_files=tx_files,
            fee=0,
            ttl=ttl,
            deposit=0,
        )

        fee = self.estimate_fee(
            out_file,
            txin_count=len(tx_raw_data.txins),
            txout_count=len(tx_raw_data.txouts),
            witness_count=len(tx_files.signing_key_files),
        )

        return fee

    def sign_tx(
        self,
        tx_body_file: FileType = "tx.body",
        out_file: FileType = "tx.signed",
        signing_key_files: OptionalFiles = (),
    ):
        """Sign transaction."""
        key_args = self._prepend_flag("--signing-key-file", signing_key_files)
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
                *key_args,
            ]
        )

        self._check_outfiles(out_file)

    def submit_tx(self, tx_file: FileType = "tx.signed"):
        """Submit transaction."""
        self.cli(
            [
                "transaction",
                "submit",
                "--testnet-magic",
                str(self.network_magic),
                "--tx-file",
                str(tx_file),
                "--shelley-mode",
            ]
        )

    def send_tx(
        self,
        src_address: str,
        txins: Optional[List[TxIn]] = None,
        txouts: Optional[List[TxOut]] = None,
        tx_files: Optional[TxFiles] = None,
        fee: Optional[int] = None,
        ttl: Optional[int] = None,
        deposit: Optional[int] = None,
    ) -> TxRawData:
        """Build, Sign and Send transaction to chain."""
        tx_files = tx_files or TxFiles()

        if fee is None:
            fee = self.calculate_tx_fee(
                src_address=src_address, txins=txins, txouts=txouts, tx_files=tx_files, ttl=ttl,
            )

        tx_raw_data = self.build_raw_tx(
            out_file="tx.body",
            src_address=src_address,
            txins=txins,
            txouts=txouts,
            tx_files=tx_files,
            fee=fee,
            ttl=ttl,
            deposit=deposit,
        )
        self.sign_tx(
            tx_body_file="tx.body",
            out_file="tx.signed",
            signing_key_files=tx_files.signing_key_files,
        )
        self.submit_tx(tx_file="tx.signed")

        return tx_raw_data

    def submit_update_proposal(self, cli_args: UnpackableSequence, epoch: int) -> TxRawData:
        """Submit update proposal."""
        out_file = Path("update.proposal")
        self.cli(
            [
                "governance",
                "create-update-proposal",
                *cli_args,
                "--out-file",
                str(out_file),
                "--epoch",
                str(epoch),
                "--genesis-verification-key-file",
                str(self.genesis_vkey),
            ]
        )

        self._check_outfiles(out_file)

        return self.send_tx(
            src_address=self.genesis_utxo_addr,
            tx_files=TxFiles(
                proposal_files=[out_file],
                signing_key_files=[self.delegate_skey, self.genesis_utxo_skey],
            ),
        )

    def send_funds(
        self,
        src_address: str,
        destinations: List[TxOut],
        tx_files: Optional[TxFiles] = None,
        fee: Optional[int] = None,
        ttl: Optional[int] = None,
        deposit: Optional[int] = None,
    ) -> TxRawData:
        """Send funds - convenience function for `send_tx`."""
        return self.send_tx(
            src_address=src_address,
            txouts=destinations,
            tx_files=tx_files,
            ttl=ttl,
            fee=fee,
            deposit=deposit,
        )

    def wait_for_new_tip(self, new_blocks: int = 1):
        """Wait for new block(s) to be created."""
        LOGGER.debug(f"Waiting for {new_blocks} new block(s) to be created.")
        timeout_no_of_slots = 200 * new_blocks
        last_block_slot_no = self.get_last_block_slot_no()
        initial_slot_no = last_block_slot_no
        expected_slot_no = initial_slot_no + new_blocks

        LOGGER.debug(f"Initial tip: {initial_slot_no}")
        for __ in range(timeout_no_of_slots):
            time.sleep(self.slot_length)
            last_block_slot_no = self.get_last_block_slot_no()
            if last_block_slot_no >= expected_slot_no:
                break
        else:
            raise CLIError(
                f"Timeout waiting for {timeout_no_of_slots} sec for {new_blocks} slot(s)."
            )

        LOGGER.debug(f"New block was created; slot number: {last_block_slot_no}")

    def wait_for_new_epoch(self, new_epochs: int = 1):
        """Wait for new epoch(s)."""
        last_block_slot_no = self.get_last_block_slot_no()
        last_block_epoch = self.get_last_block_epoch()
        LOGGER.debug(
            f"Current epoch: {last_block_epoch}; Waiting the beginning of epoch: "
            "{last_block_epoch + new_epochs}"
        )

        timeout_no_of_epochs = new_epochs + 1
        expected_epoch_no = last_block_epoch + new_epochs

        for __ in range(timeout_no_of_epochs):
            sleep_slots = (last_block_epoch + 1) * self.epoch_length - last_block_slot_no
            sleep_time = int(sleep_slots * self.slot_length) + 1
            time.sleep(sleep_time)
            last_block_slot_no = self.get_last_block_slot_no()
            last_block_epoch = self.get_last_block_epoch()
            if last_block_epoch >= expected_epoch_no:
                break
        else:
            raise CLIError(
                f"Waited for {timeout_no_of_epochs} epochs and expected epoch is not present"
            )

        LOGGER.debug(f"Expected epoch started; epoch number: {last_block_epoch}")

    def register_stake_pool(
        self,
        destination_dir: FileType,
        pool_data: PoolData,
        pool_owner: PoolOwner,
        node_vrf_vkey_file: FileType,
        node_cold_key_pair: ColdKeyPair,
        deposit: Optional[int] = None,
    ) -> Tuple[Path, TxRawData]:
        """Register stake pool."""
        pool_reg_cert_file = self.gen_pool_registration_cert(
            destination_dir=destination_dir,
            pool_data=pool_data,
            node_vrf_vkey_file=node_vrf_vkey_file,
            node_cold_vkey_file=node_cold_key_pair.vkey_file,
            owner_stake_vkey_files=[pool_owner.stake_key_pair.vkey_file],
        )

        # submit the pool registration certificate through a tx
        tx_files = TxFiles(
            certificate_files=[pool_reg_cert_file],
            signing_key_files=[
                pool_owner.addr_key_pair.skey_file,
                pool_owner.stake_key_pair.skey_file,
                node_cold_key_pair.skey_file,
            ],
        )

        tx_raw_data = self.send_tx(src_address=pool_owner.addr, tx_files=tx_files, deposit=deposit)
        self.wait_for_new_tip(new_blocks=2)

        return pool_reg_cert_file, tx_raw_data

    def deregister_stake_pool(
        self,
        destination_dir: FileType,
        pool_owner: PoolOwner,
        node_cold_key_pair: ColdKeyPair,
        epoch: int,
        pool_name: str,
    ) -> Tuple[Path, TxRawData]:
        """De-register stake pool."""
        LOGGER.debug(
            f"Deregistering stake pool starting with epoch: {epoch}; "
            f"Current epoch is: {self.get_last_block_epoch()}"
        )
        pool_dereg_cert_file = self.gen_pool_deregistration_cert(
            destination_dir=destination_dir,
            pool_name=pool_name,
            cold_vkey_file=node_cold_key_pair.vkey_file,
            epoch=epoch,
        )

        # submit the pool deregistration certificate through a tx
        tx_files = TxFiles(
            certificate_files=[pool_dereg_cert_file],
            signing_key_files=[
                pool_owner.addr_key_pair.skey_file,
                pool_owner.stake_key_pair.skey_file,
                node_cold_key_pair.skey_file,
            ],
        )

        tx_raw_data = self.send_tx(src_address=pool_owner.addr, tx_files=tx_files)
        self.wait_for_new_tip(new_blocks=2)

        return pool_dereg_cert_file, tx_raw_data

    def create_stake_pool(
        self, destination_dir: FileType, pool_data: PoolData, pool_owner: PoolOwner,
    ) -> PoolCreationArtifacts:
        """Create and register stake pool."""
        # create the KES key pair
        node_kes = self.gen_kes_key_pair(
            destination_dir=destination_dir, node_name=pool_data.pool_name
        )
        LOGGER.debug(f"KES keys created - {node_kes.vkey_file}; {node_kes.skey_file}")

        # create the VRF key pair
        node_vrf = self.gen_vrf_key_pair(
            destination_dir=destination_dir, node_name=pool_data.pool_name
        )
        LOGGER.debug(f"VRF keys created - {node_vrf.vkey_file}; {node_vrf.skey_file}")

        # create the cold key pair and node operational certificate counter
        node_cold = self.gen_cold_key_pair_and_counter(
            destination_dir=destination_dir, node_name=pool_data.pool_name
        )
        LOGGER.debug(
            "Cold keys created and counter created - "
            f"{node_cold.vkey_file}; {node_cold.skey_file}; {node_cold.counter_file}"
        )

        pool_reg_cert_file, tx_raw_data = self.register_stake_pool(
            destination_dir=destination_dir,
            pool_data=pool_data,
            pool_owner=pool_owner,
            node_vrf_vkey_file=node_vrf.vkey_file,
            node_cold_key_pair=node_cold,
        )

        return PoolCreationArtifacts(
            stake_pool_id=self.get_stake_pool_id(node_cold.vkey_file),
            kes_key_pair=node_kes,
            vrf_key_pair=node_vrf,
            cold_key_pair_and_counter=node_cold,
            pool_reg_cert_file=pool_reg_cert_file,
            tx_raw_data=tx_raw_data,
        )
