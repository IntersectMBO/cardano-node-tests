"""Types used in db-sync related functions."""
from typing import Any
from typing import Dict
from typing import List
from typing import NamedTuple
from typing import Optional

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import dbsync_queries


class MetadataRecord(NamedTuple):
    key: int
    json: Any
    bytes: memoryview


class ADAStashRecord(NamedTuple):
    address: str
    cert_index: int
    amount: int


class PotTransferRecord(NamedTuple):
    treasury: int
    reserves: int


class DelegationRecord(NamedTuple):
    address: str
    pool_id: str
    active_epoch_no: int


class RewardEpochRecord(NamedTuple):
    amount: int
    earned_epoch: int
    spendable_epoch: int
    type: str
    pool_id: str


class RewardRecord(NamedTuple):
    address: str
    rewards: List[RewardEpochRecord]
    reward_sum: int

    def __bool__(self) -> bool:
        return self.reward_sum > 0


class UTxORecord(NamedTuple):
    utxo_hash: str
    utxo_ix: int
    amount: int
    address: str
    coin: str = clusterlib.DEFAULT_COIN
    decoded_coin: str = ""
    datum_hash: str = ""
    inline_datum_hash: str = ""
    reference_script_hash: str = ""


class GetUTxORecord(NamedTuple):
    utxo_hash: str
    utxo_ix: int
    has_script: bool
    amount: int
    data_hash: str


class PaymentAddrRecord(NamedTuple):
    payment_address: str
    stake_address: Optional[str]
    amount_sum: int
    utxos: List[GetUTxORecord]

    def __bool__(self) -> bool:
        return self.amount_sum > 0


class PoolDataRecord(NamedTuple):
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
    owners: List[str]
    relays: List[Dict[str, Dict[str, Any]]]
    retire_cert_index: int
    retire_announced_tx_id: int
    retiring_epoch: int


class ScriptRecord(NamedTuple):
    hash: str
    type: str
    serialised_size: int


class RedeemerRecord(NamedTuple):
    unit_mem: int
    unit_steps: int
    fee: int
    purpose: str
    script_hash: str
    value: dict


class ExtraKeyWitnessRecord(NamedTuple):
    tx_hash: str
    witness_hash: str


class TxRecord(NamedTuple):
    tx_id: int
    tx_hash: str
    block_id: int
    block_index: int
    out_sum: int
    fee: int
    deposit: int
    size: int
    invalid_before: Optional[int]
    invalid_hereafter: Optional[int]
    txins: List[clusterlib.UTXOData]
    txouts: List[UTxORecord]
    mint: List[UTxORecord]
    collaterals: List[clusterlib.UTXOData]
    collateral_outputs: List[clusterlib.UTXOData]
    reference_inputs: List[clusterlib.UTXOData]
    scripts: List[ScriptRecord]
    redeemers: List[RedeemerRecord]
    metadata: List[MetadataRecord]
    reserve: List[ADAStashRecord]
    treasury: List[ADAStashRecord]
    pot_transfers: List[PotTransferRecord]
    stake_registration: List[str]
    stake_deregistration: List[str]
    stake_delegation: List[DelegationRecord]
    withdrawals: List[clusterlib.TxOut]
    extra_key_witness: List[ExtraKeyWitnessRecord]

    def _convert_metadata(self) -> dict:
        """Convert list of `MetadataRecord`s to metadata dictionary."""
        metadata = {int(r.key): r.json for r in self.metadata}
        return metadata


class TxPrelimRecord(NamedTuple):
    utxo_out: List[UTxORecord]
    ma_utxo_out: List[UTxORecord]
    mint_utxo_out: List[UTxORecord]
    last_row: dbsync_queries.TxDBRow
