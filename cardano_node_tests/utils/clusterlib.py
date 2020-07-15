"""Wrapper for cardano-cli."""
import collections
import functools
import json
import logging
import subprocess
from copy import copy
from pathlib import Path

LOGGER = logging.getLogger(__name__)

KeyPair = collections.namedtuple("KeyPair", ("vkey", "skey"))
CLIOut = collections.namedtuple("CLIOut", ("stdout", "stderr"))


class CLIError(Exception):
    pass


class ClusterLib:
    """Cluster Lib."""

    def __init__(self, network_magic, state_dir):
        self.network_magic = network_magic

        self.state_dir = Path(state_dir).expanduser().resolve()
        self.genesis_json = self.state_dir / "keys" / "genesis.json"
        self.genesis_utxo_vkey = self.state_dir / "keys" / "genesis-utxo.vkey"
        self.genesis_utxo_skey = self.state_dir / "keys" / "genesis-utxo.skey"
        self.genesis_vkey = self.state_dir / "keys" / "genesis-keys" / "genesis1.vkey"
        self.delegate_skey = self.state_dir / "keys" / "delegate-keys" / "delegate1.skey"
        self.pparams_file = self.state_dir / "pparams.json"

        self.check_state_dir()

        with open(self.genesis_json) as in_json:
            self.genesis = json.load(in_json)

        self.genesis_utxo_addr = self.get_genesis_addr(self.genesis_utxo_vkey)

        self.pparams = None
        self.refresh_pparams()

        self.slot_length = self.genesis["slotLength"]
        self.epoch_length = self.genesis["epochLength"]
        self.slots_per_kes_period = self.genesis["slotsPerKESPeriod"]
        self.max_kes_evolutions = self.genesis["maxKESEvolutions"]

    def check_state_dir(self):
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
    def check_outfile(out_file):
        out_file = Path(out_file).expanduser()
        if not out_file.exists():
            raise CLIError(f"The expected file `{out_file}` doesn't exist.")

    @staticmethod
    def cli(cli_args):
        p = subprocess.Popen(
            ["cardano-cli", "shelley", *cli_args], stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )
        LOGGER.debug("Running `%s`", " ".join(p.args))

        stdout, stderr = p.communicate()
        if p.returncode != 0:
            raise CLIError(f"An error occurred running a CLI command `{p.args}`: {stderr}")
        return CLIOut(stdout, stderr)

    @staticmethod
    def prepend_flag(flag, contents):
        return sum(([flag, str(x)] for x in contents), [])

    def query_cli(self, cli_args):
        return self.cli(["query", *cli_args, "--testnet-magic", str(self.network_magic)]).stdout

    def refresh_pparams(self):
        self.query_cli(["protocol-parameters", "--out-file", str(self.pparams_file)])
        with open(self.pparams_file) as in_json:
            self.pparams = json.load(in_json)

    def build_raw_tx(
        self,
        out_file,
        change_address=None,
        txins=None,
        txouts=None,
        certificate_files=None,
        proposal_files=None,
        metadata_json_files=None,
        metadata_cbor_files=None,
        withdrawal_files=None,
        fee=0,
        ttl=None,
    ):
        out_file = Path(out_file)
        txins = txins or []
        txouts_copy = copy(txouts) if txouts else []
        ttl = ttl or self.calculate_tx_ttl()

        certificate_files = certificate_files or []
        proposal_files = proposal_files or []
        metadata_json_files = metadata_json_files or []
        metadata_cbor_files = metadata_cbor_files or []
        withdrawal_files = withdrawal_files or []

        if change_address:
            change_balance = self.get_address_balance(change_address)
        else:
            change_address = self.genesis_utxo_addr
            change_balance = 0

        total_input_amount = functools.reduce(lambda x, y: x + y[2], txins, 0)
        total_output_amount = functools.reduce(lambda x, y: x + y[2], txouts_copy, 0)
        change = change_balance + total_input_amount - total_output_amount - fee
        if change > 0:
            txouts_copy.append((change_address, change))

        txins_combined = [f"{x[0]}#{x[1]}" for x in txins]
        txouts_combined = [f"{x[0]}+{x[1]}" for x in txouts_copy]

        txin_args = self.prepend_flag("--tx-in", txins_combined)
        txout_args = self.prepend_flag("--tx-out", txouts_combined)
        cert_args = self.prepend_flag("--certificate-file", certificate_files)
        proposal_args = self.prepend_flag("--update-proposal-file", proposal_files)
        metadata_json_args = self.prepend_flag("--metadata-json-file", metadata_json_files)
        metadata_cbor_args = self.prepend_flag("--metadata-cbor-file", metadata_cbor_files)
        withdrawal_args = self.prepend_flag("--withdrawal", withdrawal_files)

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
                *txin_args,
                *txout_args,
                *cert_args,
                *proposal_args,
                *metadata_json_args,
                *metadata_cbor_args,
                *withdrawal_args,
            ]
        )

        self.check_outfile(out_file)

    def estimate_fee(
        self, txbody_file, txin_count=1, txout_count=1, witness_count=1, byron_witness_count=0
    ):
        self.refresh_pparams()
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
        change_address=None,
        txins=None,
        txouts=None,
        certificate_files=None,
        proposal_files=None,
        metadata_json_files=None,
        metadata_cbor_files=None,
        withdrawal_files=None,
        ttl=None,
        signing_keys=None,
    ):
        signing_keys = signing_keys or []
        out_file = Path("tx.body_estimate")

        self.build_raw_tx(
            out_file,
            change_address=change_address,
            txins=txins,
            txouts=txouts,
            certificate_files=certificate_files,
            proposal_files=proposal_files,
            metadata_json_files=metadata_json_files,
            metadata_cbor_files=metadata_cbor_files,
            withdrawal_files=withdrawal_files,
            fee=0,
            ttl=ttl,
        )

        fee = self.estimate_fee(
            out_file,
            txin_count=len(txins),
            txout_count=len(txouts),
            witness_count=len(signing_keys),
        )

        return fee

    def sign_tx(self, tx_body_file="tx.body", out_file="tx.signed", signing_keys=None):
        signing_keys = signing_keys or []
        key_args = self.prepend_flag("--signing-key-file", signing_keys)
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
        self.check_outfile(out_file)

    def submit_tx(self, tx_file="tx.signed"):
        self.cli(
            [
                "transaction",
                "submit",
                "--testnet-magic",
                str(self.network_magic),
                "--tx-file",
                str(tx_file),
            ]
        )

    def get_payment_address(self, payment_vkey, stake_vkey=None):
        if not payment_vkey:
            raise CLIError("Must set payment key.")

        cli_args = ["--payment-verification-key-file", str(payment_vkey)]
        if stake_vkey:
            cli_args.extend("--stake-verification-key-file", str(stake_vkey))

        return (
            self.cli(["address", "build", "--testnet-magic", str(self.network_magic), *cli_args])
            .stdout.rstrip()
            .decode("ascii")
        )

    def get_genesis_addr(self, vkey_path):
        return (
            self.cli(
                [
                    "genesis",
                    "initial-addr",
                    "--testnet-magic",
                    str(self.network_magic),
                    "--verification-key-file",
                    str(vkey_path),
                ]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

    def get_utxo(self, address):
        utxo = json.loads(
            self.query_cli(["utxo", "--address", address, "--out-file", "/dev/stdout"])
        )
        return utxo

    def get_tip(self):
        return json.loads(self.query_cli(["tip"]))

    def create_payment_key_pair(self, destination_dir, key_name):
        destination_dir = Path(destination_dir).expanduser()
        skey = destination_dir / f"{key_name}.skey"
        vkey = destination_dir / f"{key_name}.vkey"
        self.cli(
            ["address", "key-gen", "--verification-key-file", vkey, "--signing-key-file", skey]
        )
        return KeyPair(vkey, skey)

    def create_stake_key_pair(self, destination_dir, key_name):
        destination_dir = Path(destination_dir).expanduser()
        skey = destination_dir / f"{key_name}.skey"
        vkey = destination_dir / f"{key_name}.vkey"
        self.cli(
            [
                "stake-address",
                "key-gen",
                "--verification-key-file",
                vkey,
                "--signing-key-file",
                skey,
            ]
        )
        return KeyPair(vkey, skey)

    def build_payment_address(self, payment_vkey):
        return (
            self.cli(
                [
                    "address",
                    "build",
                    "--payment-verification-key-file",
                    str(payment_vkey),
                    "--testnet-magic",
                    str(self.network_magic),
                ]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

    def build_stake_address(self, stake_vkey):
        return (
            self.cli(
                [
                    "stake-address",
                    "build",
                    "--stake-verification-key-file",
                    str(stake_vkey),
                    "--testnet-magic",
                    str(self.network_magic),
                ]
            )
            .stdout.rstrip()
            .decode("ascii")
        )

    def delegate_stake_address(self, stake_addr_skey, pool_id, delegation_fee):
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

        stderr = self.cli(cli_args).stderr
        if stderr and "runStakeAddressCmd" in stderr.decode():
            cmd = " ".join(cli_args)
            raise CLIError(f"command not implemented yet;\ncommand: {cmd}\nresult: {stderr}")

    def get_stake_address_info(self, stake_addr):
        output_json = json.loads(self.query_cli(["stake-address-info", "--address", stake_addr]))
        delegation = output_json[stake_addr]["delegation"]
        reward_account_balance = output_json[stake_addr]["rewardAccountBalance"]

        StakeAddrInfo = collections.namedtuple(
            "StakeAddrInfo", ("delegation", "reward_account_balance", "stake_addr_info")
        )
        return StakeAddrInfo(delegation, reward_account_balance, output_json)

    def create_stake_addr_registration_cert(self, destination_dir, stake_addr_vkey, addr_name):
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake.reg.cert"
        self.cli(
            [
                "stake-address",
                "registration-certificate",
                "--stake-verification-key-file",
                str(stake_addr_vkey),
                "--out-file",
                str(out_file),
            ]
        )
        self.check_outfile(out_file)
        return out_file

    def create_stake_addr_delegation_cert(
        self, destination_dir, stake_addr_vkey, node_cold_vkey, addr_name
    ):
        destination_dir = Path(destination_dir).expanduser()
        out_file = destination_dir / f"{addr_name}_stake.deleg.cert"
        self.cli(
            [
                "stake-address",
                "delegation-certificate",
                "--stake-verification-key-file ",
                str(stake_addr_vkey),
                "--cold-verification-key-file ",
                str(node_cold_vkey),
                "--out-file ",
                str(out_file),
            ]
        )

        self.check_outfile(out_file)
        return out_file

    def get_protocol_params(self):
        self.refresh_pparams()
        return self.pparams

    def get_key_deposit(self):
        return self.get_protocol_params()["keyDeposit"]

    def get_pool_deposit(self):
        return self.get_protocol_params()["poolDeposit"]

    def get_stake_distribution(self):
        # stake pool values are displayed starting with line 2 from the command output
        result = self.query_cli(["stake-distribution"]).decode().splitlines()[2:]
        stake_distribution = {}
        for pool in result:
            pool_id, *__, stake = pool.split(" ")
            stake_distribution[pool_id] = stake
        return stake_distribution

    def get_last_block_slot_no(self):
        return int(self.get_tip()["slotNo"])

    def get_last_block_block_no(self):
        return int(self.get_tip()["blockNo"])

    def get_last_block_epoch(self):
        return int(self.get_last_block_slot_no() / self.epoch_length)

    def get_address_balance(self, address):
        available_utxos = self.get_utxo(address) or {}
        address_balance = functools.reduce(
            lambda x, y: x + y["amount"], available_utxos.values(), 0
        )
        return int(address_balance)

    def get_utxo_with_highest_amount(self, address):
        utxo = self.get_utxo(address=address)
        highest_amount_rec = max(utxo.items(), key=lambda x: x[1].get("amount", 0))
        return {highest_amount_rec[0]: highest_amount_rec[1]}

    def calculate_tx_ttl(self):
        current_slot_no = self.get_last_block_slot_no()
        return current_slot_no + 1000

    def send_tx_genesis(
        self, txouts=None, certificate_files=None, signing_keys=None, proposal_files=None,
    ):
        txouts = txouts or []
        certificate_files = certificate_files or []
        signing_keys_copy = copy(signing_keys) if signing_keys else []

        signing_keys_copy.append(str(self.genesis_utxo_skey))

        # TODO: calculate from current tip
        utxo = self.get_utxo(address=self.genesis_utxo_addr)
        txins = []
        for k, v in utxo.items():
            txin = k.split("#")
            txin = (txin[0], txin[1], v["amount"])
            txins.append(txin)

        # Build, Sign and Send TX to chain
        try:
            fee = self.calculate_tx_fee(
                txins=txins,
                txouts=txouts,
                certificate_files=certificate_files,
                proposal_files=proposal_files,
                signing_keys=signing_keys_copy,
            )
            self.build_raw_tx(
                out_file="tx.body",
                txins=txins,
                txouts=txouts,
                certificate_files=certificate_files,
                fee=fee,
                proposal_files=proposal_files,
            )
            self.sign_tx(signing_keys=signing_keys_copy)
            self.submit_tx()
        except CLIError as err:
            raise CLIError(
                f"Sending a genesis transaction failed!\n"
                f"utxo: {utxo}\n"
                f"txins: {txins} txouts: {txouts} signing keys: {signing_keys_copy}\n{err}"
            )

    def submit_update_proposal(self, cli_args, epoch=None):
        out_file = Path("update.proposal")

        self.cli(
            [
                "governance",
                "create-update-proposal",
                *cli_args,
                "--out-file",
                str(out_file),
                "--epoch",
                str(epoch or self.get_last_block_epoch()),
                "--genesis-verification-key-file",
                str(self.genesis_vkey),
            ]
        )
        self.check_outfile(out_file)

        self.send_tx_genesis(
            proposal_files=["update.proposal"], signing_keys=[str(self.delegate_skey)],
        )
