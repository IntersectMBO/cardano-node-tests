"""Types used in db-sync related functions."""
import typing as tp

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import dbsync_queries


class MetadataRecord(tp.NamedTuple):
    key: int
    json: tp.Any
    bytes: memoryview


class ADAStashRecord(tp.NamedTuple):
    address: str
    cert_index: int
    amount: int


class PotTransferRecord(tp.NamedTuple):
    treasury: int
    reserves: int


class DelegationRecord(tp.NamedTuple):
    address: str
    pool_id: str
    active_epoch_no: int


class RewardEpochRecord(tp.NamedTuple):
    amount: int
    earned_epoch: int
    spendable_epoch: int
    type: str
    pool_id: str


class RewardRecord(tp.NamedTuple):
    address: str
    rewards: tp.List[RewardEpochRecord]
    reward_sum: int

    def __bool__(self) -> bool:
        return self.reward_sum > 0


class UTxORecord(tp.NamedTuple):
    utxo_hash: str
    utxo_ix: int
    amount: int
    address: str
    coin: str = clusterlib.DEFAULT_COIN
    decoded_coin: str = ""
    datum_hash: str = ""
    inline_datum_hash: str = ""
    inline_datum: tp.Optional[tp.Union[str, dict]] = None
    reference_script: tp.Optional[dict] = None
    reference_script_hash: str = ""


class GetUTxORecord(tp.NamedTuple):
    utxo_hash: str
    utxo_ix: int
    has_script: bool
    amount: int
    data_hash: str


class PaymentAddrRecord(tp.NamedTuple):
    payment_address: str
    stake_address: tp.Optional[str]
    amount_sum: int
    utxos: tp.List[GetUTxORecord]

    def __bool__(self) -> bool:
        return self.amount_sum > 0


class PoolDataRecord(tp.NamedTuple):
    id: int
    hash: str
    view: str
    cert_index: int
    vrf_key_hash: str
    pledge: int
    reward_addr: str
    active_epoch_no: int
    meta_id: int
    margin: float
    fixed_cost: int
    registered_tx_id: int
    metadata_url: str
    metadata_hash: str
    owners: tp.List[str]
    relays: tp.List[tp.Dict[str, tp.Dict[str, tp.Any]]]
    retire_cert_index: int
    retire_announced_tx_id: int
    retiring_epoch: int


class ScriptRecord(tp.NamedTuple):
    hash: str
    type: str
    serialised_size: int


class RedeemerRecord(tp.NamedTuple):
    unit_mem: int
    unit_steps: int
    fee: int
    purpose: str
    script_hash: str
    value: dict


class TxRecord(tp.NamedTuple):
    tx_id: int
    tx_hash: str
    block_id: int
    block_index: int
    out_sum: int
    fee: int
    deposit: int
    size: int
    invalid_before: tp.Optional[int]
    invalid_hereafter: tp.Optional[int]
    txins: tp.List[UTxORecord]
    txouts: tp.List[UTxORecord]
    mint: tp.List[UTxORecord]
    collaterals: tp.List[UTxORecord]
    collateral_outputs: tp.List[clusterlib.UTXOData]
    reference_inputs: tp.List[UTxORecord]
    scripts: tp.List[ScriptRecord]
    redeemers: tp.List[RedeemerRecord]
    metadata: tp.List[MetadataRecord]
    reserve: tp.List[ADAStashRecord]
    treasury: tp.List[ADAStashRecord]
    pot_transfers: tp.List[PotTransferRecord]
    stake_registration: tp.List[str]
    stake_deregistration: tp.List[str]
    stake_delegation: tp.List[DelegationRecord]
    withdrawals: tp.List[clusterlib.TxOut]
    extra_key_witness: tp.List[str]

    def _convert_metadata(self) -> dict:
        """Convert list of `MetadataRecord`s to metadata dictionary."""
        metadata = {int(r.key): r.json for r in self.metadata}
        return metadata


class TxPrelimRecord(tp.NamedTuple):
    utxo_out: tp.List[UTxORecord]
    ma_utxo_out: tp.List[UTxORecord]
    mint_utxo_out: tp.List[UTxORecord]
    last_row: dbsync_queries.TxDBRow
