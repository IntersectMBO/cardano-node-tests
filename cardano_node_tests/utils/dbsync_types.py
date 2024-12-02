"""Types used in db-sync related functions."""

import dataclasses
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import dbsync_queries


@dataclasses.dataclass(frozen=True, order=True)
class MetadataRecord:
    key: int
    json: tp.Any
    bytes: memoryview


@dataclasses.dataclass(frozen=True, order=True)
class ADAStashRecord:
    address: str
    cert_index: int
    amount: int


@dataclasses.dataclass(frozen=True, order=True)
class PotTransferRecord:
    treasury: int
    reserves: int


@dataclasses.dataclass(frozen=True, order=True)
class DelegationRecord:
    address: str
    pool_id: str
    active_epoch_no: int


@dataclasses.dataclass(frozen=True, order=True)
class RewardEpochRecord:
    amount: int
    earned_epoch: int
    spendable_epoch: int
    type: str
    pool_id: str


@dataclasses.dataclass(frozen=True, order=True)
class RewardRecord:
    address: str
    rewards: list[RewardEpochRecord]
    reward_sum: int

    def __bool__(self) -> bool:
        return self.reward_sum > 0


@dataclasses.dataclass(frozen=True, order=True)
class UTxORecord:
    utxo_hash: str
    utxo_ix: int
    amount: int
    address: str
    coin: str = clusterlib.DEFAULT_COIN
    decoded_coin: str = ""
    datum_hash: str = ""
    inline_datum_hash: str = ""
    inline_datum: str | dict | None = None
    reference_script: dict | None = None
    reference_script_hash: str = ""


@dataclasses.dataclass(frozen=True, order=True)
class GetUTxORecord:
    utxo_hash: str
    utxo_ix: int
    has_script: bool
    amount: int
    data_hash: str


@dataclasses.dataclass(frozen=True, order=True)
class PaymentAddrRecord:
    payment_address: str
    stake_address: str | None
    amount_sum: int
    utxos: list[GetUTxORecord]

    def __bool__(self) -> bool:
        return self.amount_sum > 0


@dataclasses.dataclass(frozen=True, order=True)
class PoolDataRecord:
    # pylint: disable-next=invalid-name
    id: int
    hash: str
    view: str
    cert_index: int
    vrf_key_hash: str
    pledge: int
    reward_addr: str
    active_epoch_no: int
    meta_id: int | None
    margin: float
    fixed_cost: int
    registered_tx_id: int
    metadata_url: str
    metadata_hash: str
    owners: list[str]
    relays: list[dict[str, dict[str, tp.Any]]]
    retire_cert_index: int | None
    retire_announced_tx_id: int | None
    retiring_epoch: int | None


@dataclasses.dataclass(frozen=True, order=True)
class ScriptRecord:
    hash: str
    type: str
    serialised_size: int


@dataclasses.dataclass(frozen=True, order=True)
class RedeemerRecord:
    unit_mem: int
    unit_steps: int
    fee: int
    purpose: str
    script_hash: str
    value: dict


@dataclasses.dataclass(frozen=True, order=True)
class TxRecord:
    # pylint: disable=too-many-instance-attributes
    tx_id: int
    tx_hash: str
    block_id: int
    block_index: int
    out_sum: int
    fee: int
    deposit: int
    size: int
    invalid_before: int | None
    invalid_hereafter: int | None
    treasury_donation: int
    txins: list[UTxORecord]
    txouts: list[UTxORecord]
    mint: list[UTxORecord]
    collaterals: list[UTxORecord]
    collateral_outputs: list[clusterlib.UTXOData]
    reference_inputs: list[UTxORecord]
    scripts: list[ScriptRecord]
    redeemers: list[RedeemerRecord]
    metadata: list[MetadataRecord]
    reserve: list[ADAStashRecord]
    treasury: list[ADAStashRecord]
    pot_transfers: list[PotTransferRecord]
    stake_registration: list[str]
    stake_deregistration: list[str]
    stake_delegation: list[DelegationRecord]
    withdrawals: list[clusterlib.TxOut]
    extra_key_witness: list[str]

    def _convert_metadata(self) -> dict:
        """Convert list of `MetadataRecord`s to metadata dictionary."""
        metadata = {int(r.key): r.json for r in self.metadata}
        return metadata


@dataclasses.dataclass(frozen=True, order=True)
class TxPrelimRecord:
    utxo_out: list[UTxORecord]
    ma_utxo_out: list[UTxORecord]
    mint_utxo_out: list[UTxORecord]
    last_row: dbsync_queries.TxDBRow


@dataclasses.dataclass(frozen=True, order=True)
class CommitteeRegistrationRecord:
    # pylint: disable-next=invalid-name
    id: int
    tx_id: int
    cert_index: int
    cold_key: str
    hot_key: str


@dataclasses.dataclass(frozen=True, order=True)
class CommitteeDeregistrationRecord:
    # pylint: disable-next=invalid-name
    id: int
    tx_id: int
    cert_index: int
    voting_anchor_id: int
    cold_key: str


@dataclasses.dataclass(frozen=True, order=True)
class DrepRegistrationRecord:
    # pylint: disable-next=invalid-name
    id: int
    tx_id: int
    cert_index: int
    deposit: int
    drep_hash_id: int
    voting_anchor_id: int | None
    hash_hex: str
    hash_bech32: str
    has_script: bool


@dataclasses.dataclass(frozen=True)
class OffChainVoteDataRecord:
    id: int
    vot_anchor_id: int
    hash: str
    json: dict
    bytes: str
    warning: str | None
    language: str
    comment: str | None
    is_valid: bool | None
    authors: list[dict[str, tp.Any]]
    references: list[dict[str, tp.Any]]
    gov_action_data: dict[str, tp.Any]
    external_updates: list[dict[str, tp.Any]]
    voting_anchor: dict[str, tp.Any]
