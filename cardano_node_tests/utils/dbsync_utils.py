"""Functionality for interacting with db-sync."""
import decimal
import functools
import itertools
import logging
import time
from typing import Any
from typing import Dict
from typing import Generator
from typing import List
from typing import NamedTuple
from typing import Optional
from typing import Set
from typing import Tuple

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_conn

LOGGER = logging.getLogger(__name__)


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


class RewardRecord(NamedTuple):
    address: str
    pool_id: str
    rewards: List[RewardEpochRecord]
    reward_sum: int


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
    txouts: List[clusterlib.UTXOData]
    mint: List[clusterlib.UTXOData]
    collaterals: List[clusterlib.UTXOData]
    metadata: List[MetadataRecord]
    reserve: List[ADAStashRecord]
    treasury: List[ADAStashRecord]
    pot_transfers: List[PotTransferRecord]
    stake_registration: List[str]
    stake_deregistration: List[str]
    stake_delegation: List[DelegationRecord]
    withdrawals: List[clusterlib.TxOut]

    def _convert_metadata(self) -> dict:
        """Convert list of `MetadataRecord`s to metadata dictionary."""
        metadata = {int(r.key): r.json for r in self.metadata}
        return metadata


class PoolDataDBRow(NamedTuple):
    id: int
    hash: memoryview
    view: str
    cert_index: int
    vrf_key_hash: memoryview
    pledge: int
    reward_addr: memoryview
    active_epoch_no: int
    meta_id: int
    margin: decimal.Decimal
    fixed_cost: int
    registered_tx_id: int
    metadata_url: str
    metadata_hash: memoryview
    owner_stake_address_id: int
    owner: memoryview
    ipv4: str
    ipv6: str
    dns_name: str
    port: int
    retire_cert_index: int
    retire_announced_tx_id: int
    retiring_epoch: int


class TxDBRow(NamedTuple):
    tx_id: int
    tx_hash: memoryview
    block_id: int
    block_index: int
    out_sum: decimal.Decimal
    fee: decimal.Decimal
    deposit: int
    size: int
    invalid_before: Optional[decimal.Decimal]
    invalid_hereafter: Optional[decimal.Decimal]
    tx_out_id: int
    tx_out_tx_id: int
    utxo_ix: int
    tx_out_addr: str
    tx_out_value: decimal.Decimal
    metadata_count: int
    reserve_count: int
    treasury_count: int
    pot_transfer_count: int
    stake_reg_count: int
    stake_dereg_count: int
    stake_deleg_count: int
    withdrawal_count: int
    collateral_count: int
    ma_tx_out_id: Optional[int]
    ma_tx_out_policy: Optional[memoryview]
    ma_tx_out_name: Optional[memoryview]
    ma_tx_out_quantity: Optional[decimal.Decimal]
    ma_tx_mint_id: Optional[int]
    ma_tx_mint_policy: Optional[memoryview]
    ma_tx_mint_name: Optional[memoryview]
    ma_tx_mint_quantity: Optional[decimal.Decimal]


class TxPrelimRecord(NamedTuple):
    utxo_out: List[clusterlib.UTXOData]
    ma_utxo_out: List[clusterlib.UTXOData]
    mint_utxo_out: List[clusterlib.UTXOData]
    last_row: TxDBRow


class MetadataDBRow(NamedTuple):
    id: int
    key: decimal.Decimal
    json: Any
    bytes: memoryview
    tx_id: int


class ADAStashDBRow(NamedTuple):
    id: int
    addr_view: str
    cert_index: int
    amount: decimal.Decimal
    tx_id: int


class PotTransferDBRow(NamedTuple):
    id: int
    cert_index: int
    treasury: decimal.Decimal
    reserves: decimal.Decimal
    tx_id: int


class StakeAddrDBRow(NamedTuple):
    id: int
    view: str
    tx_id: int


class StakeDelegDBRow(NamedTuple):
    tx_id: int
    active_epoch_no: Optional[int]
    pool_id: Optional[str]
    address: Optional[str]


class WithdrawalDBRow(NamedTuple):
    tx_id: int
    address: str
    amount: int


class TxInDBRow(NamedTuple):
    tx_out_id: int
    utxo_ix: int
    address: str
    value: decimal.Decimal
    tx_hash: memoryview
    ma_tx_out_id: Optional[int]
    ma_tx_out_policy: Optional[memoryview]
    ma_tx_out_name: Optional[memoryview]
    ma_tx_out_quantity: Optional[decimal.Decimal]


class CollateralTxInDBRow(NamedTuple):
    tx_out_id: int
    utxo_ix: int
    address: str
    value: decimal.Decimal
    tx_hash: memoryview


class ADAPotsDBRow(NamedTuple):
    id: int
    slot_no: int
    epoch_no: int
    treasury: decimal.Decimal
    reserves: decimal.Decimal
    rewards: decimal.Decimal
    utxo: decimal.Decimal
    deposits: decimal.Decimal
    fees: decimal.Decimal
    block_id: int


class RewardDBRow(NamedTuple):
    address: str
    type: str
    amount: decimal.Decimal
    earned_epoch: int
    spendable_epoch: int
    pool_id: str


class OrphanedRewardDBRow(NamedTuple):
    address: str
    type: str
    amount: decimal.Decimal
    epoch_no: int
    pool_id: str


class SchemaVersionStages(NamedTuple):
    one: int
    two: int
    three: int


class SchemaVersion:
    """Query and cache db-sync schema version."""

    _stages: Optional[SchemaVersionStages] = None

    @classmethod
    def stages(cls) -> SchemaVersionStages:
        if cls._stages is not None:
            return cls._stages

        with dbsync_conn.DBSync.conn().cursor() as cur:
            cur.execute(
                "SELECT stage_one, stage_two, stage_three "
                "FROM schema_version ORDER BY id DESC LIMIT 1;"
            )

            cls._stages = SchemaVersionStages(*cur.fetchone())

        return cls._stages


def query_tx(txhash: str) -> Generator[TxDBRow, None, None]:
    """Query a transaction in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx.id, tx.hash, tx.block_id, tx.block_index, tx.out_sum, tx.fee, tx.deposit, tx.size,"
            " tx.invalid_before, tx.invalid_hereafter,"
            " tx_out.id, tx_out.tx_id, tx_out.index, tx_out.address, tx_out.value,"
            " (SELECT COUNT(id) FROM tx_metadata WHERE tx_id=tx.id) AS metadata_count,"
            " (SELECT COUNT(id) FROM reserve WHERE tx_id=tx.id) AS reserve_count,"
            " (SELECT COUNT(id) FROM treasury WHERE tx_id=tx.id) AS treasury_count,"
            " (SELECT COUNT(id) FROM pot_transfer WHERE tx_id=tx.id) AS pot_transfer_count,"
            " (SELECT COUNT(id) FROM stake_registration WHERE tx_id=tx.id) AS reg_count,"
            " (SELECT COUNT(id) FROM stake_deregistration WHERE tx_id=tx.id) AS dereg_count,"
            " (SELECT COUNT(id) FROM delegation WHERE tx_id=tx.id) AS deleg_count,"
            " (SELECT COUNT(id) FROM withdrawal WHERE tx_id=tx.id) AS withdrawal_count,"
            " (SELECT COUNT(id) FROM collateral_tx_in WHERE tx_in_id=tx.id) AS collateral_count,"
            " ma_tx_out.id, ma_tx_out.policy, ma_tx_out.name, ma_tx_out.quantity,"
            " ma_tx_mint.id, ma_tx_mint.policy, ma_tx_mint.name, ma_tx_mint.quantity "
            "FROM tx "
            "LEFT JOIN tx_out ON tx.id = tx_out.tx_id "
            "LEFT JOIN ma_tx_out ON tx_out.id = ma_tx_out.tx_out_id "
            "LEFT JOIN ma_tx_mint ON tx.id = ma_tx_mint.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield TxDBRow(*result)


def query_tx_ins(txhash: str) -> Generator[TxInDBRow, None, None]:
    """Query transaction txins in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx_out.id, tx_out.index, tx_out.address, tx_out.value,"
            " (SELECT hash FROM tx WHERE id = tx_out.tx_id) AS tx_hash,"
            " ma_tx_out.id, ma_tx_out.policy, ma_tx_out.name, ma_tx_out.quantity "
            "FROM tx_in "
            "LEFT JOIN tx_out "
            "ON (tx_out.tx_id = tx_in.tx_out_id AND tx_out.index = tx_in.tx_out_index) "
            "LEFT JOIN tx ON tx.id = tx_in.tx_in_id "
            "LEFT JOIN ma_tx_out ON tx_out.id = ma_tx_out.tx_out_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield TxInDBRow(*result)


def query_collateral_tx_ins(txhash: str) -> Generator[CollateralTxInDBRow, None, None]:
    """Query transaction collateral txins in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx_out.id, tx_out.index, tx_out.address, tx_out.value,"
            " (SELECT hash FROM tx WHERE id = tx_out.tx_id) AS tx_hash "
            "FROM collateral_tx_in "
            "LEFT JOIN tx_out "
            "ON (tx_out.tx_id = collateral_tx_in.tx_out_id AND"
            "    tx_out.index = collateral_tx_in.tx_out_index) "
            "LEFT JOIN tx ON tx.id = collateral_tx_in.tx_in_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield CollateralTxInDBRow(*result)


def query_tx_metadata(txhash: str) -> Generator[MetadataDBRow, None, None]:
    """Query transaction metadata in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx_metadata.id, tx_metadata.key, tx_metadata.json, tx_metadata.bytes,"
            " tx_metadata.tx_id "
            "FROM tx_metadata "
            "INNER JOIN tx ON tx.id = tx_metadata.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield MetadataDBRow(*result)


def query_tx_reserve(txhash: str) -> Generator[ADAStashDBRow, None, None]:
    """Query transaction reserve record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " reserve.id, stake_address.view, reserve.cert_index, reserve.amount, reserve.tx_id "
            "FROM reserve "
            "INNER JOIN stake_address ON reserve.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = reserve.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield ADAStashDBRow(*result)


def query_tx_treasury(txhash: str) -> Generator[ADAStashDBRow, None, None]:
    """Query transaction treasury record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " treasury.id, stake_address.view, treasury.cert_index, "
            "treasury.amount, treasury.tx_id "
            "FROM treasury "
            "INNER JOIN stake_address ON treasury.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = treasury.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield ADAStashDBRow(*result)


def query_tx_pot_transfers(txhash: str) -> Generator[PotTransferDBRow, None, None]:
    """Query transaction MIR certificate records in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " pot_transfer.id, pot_transfer.cert_index, pot_transfer.treasury,"
            " pot_transfer.reserves, pot_transfer.tx_id "
            "FROM pot_transfer "
            "INNER JOIN tx ON tx.id = pot_transfer.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield PotTransferDBRow(*result)


def query_tx_stake_reg(txhash: str) -> Generator[StakeAddrDBRow, None, None]:
    """Query stake registration record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " stake_registration.addr_id, stake_address.view, stake_registration.tx_id "
            "FROM stake_registration "
            "INNER JOIN stake_address ON stake_registration.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = stake_registration.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield StakeAddrDBRow(*result)


def query_tx_stake_dereg(txhash: str) -> Generator[StakeAddrDBRow, None, None]:
    """Query stake deregistration record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " stake_deregistration.addr_id, stake_address.view, stake_deregistration.tx_id "
            "FROM stake_deregistration "
            "INNER JOIN stake_address ON stake_deregistration.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = stake_deregistration.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield StakeAddrDBRow(*result)


def query_tx_stake_deleg(txhash: str) -> Generator[StakeDelegDBRow, None, None]:
    """Query stake registration record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx.id, delegation.active_epoch_no, pool_hash.view AS pool_view,"
            " stake_address.view AS address_view "
            "FROM delegation "
            "INNER JOIN stake_address ON delegation.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = delegation.tx_id "
            "INNER JOIN pool_hash ON pool_hash.id = delegation.pool_hash_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield StakeDelegDBRow(*result)


def query_tx_withdrawal(txhash: str) -> Generator[WithdrawalDBRow, None, None]:
    """Query reward withdrawal record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " tx.id, stake_address.view, amount "
            "FROM withdrawal "
            "INNER JOIN stake_address ON withdrawal.addr_id = stake_address.id "
            "INNER JOIN tx ON tx.id = withdrawal.tx_id "
            "WHERE tx.hash = %s;",
            (rf"\x{txhash}",),
        )

        while (result := cur.fetchone()) is not None:
            yield WithdrawalDBRow(*result)


def query_ada_pots(
    epoch_from: int = 0, epoch_to: int = 99999999
) -> Generator[ADAPotsDBRow, None, None]:
    """Query ADA pots record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " id, slot_no, epoch_no, treasury, reserves, rewards, utxo, deposits, fees, block_id "
            "FROM ada_pots "
            "WHERE epoch_no BETWEEN %s AND %s;",
            (epoch_from, epoch_to),
        )

        while (result := cur.fetchone()) is not None:
            yield ADAPotsDBRow(*result)


def query_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Generator[RewardDBRow, None, None]:
    """Query reward records for stake address in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " stake_address.view, reward.type, reward.amount, reward.earned_epoch,"
            " reward.spendable_epoch, pool_hash.view AS pool_view "
            "FROM reward "
            "INNER JOIN stake_address ON reward.addr_id = stake_address.id "
            "INNER JOIN pool_hash ON pool_hash.id = reward.pool_id "
            "WHERE (stake_address.view = %s) AND (reward.spendable_epoch BETWEEN %s AND %s);",
            (address, epoch_from, epoch_to),
        )

        while (result := cur.fetchone()) is not None:
            yield RewardDBRow(*result)


def query_address_orphaned_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Generator[OrphanedRewardDBRow, None, None]:
    """Query orphaned reward records for stake address in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT"
            " stake_address.view, orphaned_reward.type, orphaned_reward.amount,"
            " orphaned_reward.epoch_no, pool_hash.view AS pool_view "
            "FROM orphaned_reward "
            "INNER JOIN stake_address ON orphaned_reward.addr_id = stake_address.id "
            "INNER JOIN pool_hash ON pool_hash.id = orphaned_reward.pool_id "
            "WHERE (stake_address.view = %s) AND (orphaned_reward.epoch_no BETWEEN %s AND %s);",
            (address, epoch_from, epoch_to),
        )

        while (result := cur.fetchone()) is not None:
            yield OrphanedRewardDBRow(*result)


def query_pool_data(pool_id_bech32: str) -> Generator[PoolDataDBRow, None, None]:
    """Query pool data record in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT DISTINCT"
            " pool_hash.id, pool_hash.hash_raw, pool_hash.view,"
            " pool_update.cert_index, pool_update.vrf_key_hash, pool_update.pledge,"
            " pool_update.reward_addr, pool_update.active_epoch_no, pool_update.meta_id,"
            " pool_update.margin, pool_update.fixed_cost, pool_update.registered_tx_id,"
            " pool_metadata_ref.url as metadata_url,pool_metadata_ref.hash AS metadata_hash,"
            " pool_owner.addr_id AS owner_stake_address_id,"
            " stake_address.hash_raw AS owner,"
            " pool_relay.ipv4, pool_relay.ipv6, pool_relay.dns_name, pool_relay.port,"
            " pool_retire.cert_index AS retire_cert_index,"
            " pool_retire.announced_tx_id AS retire_announced_tx_id, pool_retire.retiring_epoch "
            "FROM pool_hash "
            "INNER JOIN pool_update ON pool_hash.id=pool_update.hash_id "
            "FULL JOIN pool_metadata_ref ON pool_update.meta_id=pool_metadata_ref.id "
            "INNER JOIN pool_owner ON pool_hash.id=pool_owner.pool_hash_id "
            "FULL JOIN pool_relay ON pool_update.id=pool_relay.update_id "
            "FULL JOIN pool_retire ON pool_hash.id=pool_retire.hash_id "
            "INNER JOIN stake_address ON pool_owner.addr_id=stake_address.id "
            "WHERE pool_hash.view = %s ORDER BY registered_tx_id;",
            [pool_id_bech32],
        )

        while (result := cur.fetchone()) is not None:
            yield PoolDataDBRow(*result)


def query_table_names() -> List[str]:
    """Query table names in db-sync."""
    with dbsync_conn.DBSync.conn().cursor() as cur:
        cur.execute(
            "SELECT tablename "
            "FROM pg_catalog.pg_tables "
            "WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema' "
            "ORDER BY tablename ASC;"
        )
        results: List[Tuple[str]] = cur.fetchall()
        table_names = [r[0] for r in results]
        return table_names


def get_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Optional[RewardRecord]:
    """Get reward data for stake address from db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward can be spent.
    """
    rewards = []
    for db_row in query_address_reward(address=address, epoch_from=epoch_from, epoch_to=epoch_to):
        rewards.append(
            RewardEpochRecord(
                amount=int(db_row.amount),
                earned_epoch=db_row.earned_epoch,
                spendable_epoch=db_row.spendable_epoch,
            )
        )
    if not rewards:
        return None

    reward_sum = functools.reduce(lambda x, y: x + y.amount, rewards, 0)
    # pylint: disable=undefined-loop-variable
    return RewardRecord(
        address=db_row.address, pool_id=db_row.pool_id, rewards=rewards, reward_sum=reward_sum
    )


def check_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Optional[RewardRecord]:
    """Check reward data for stake address in db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward can be spent.
    """
    reward = get_address_reward(address=address, epoch_from=epoch_from, epoch_to=epoch_to)
    if reward is None:
        return None

    errors = []
    for r in reward.rewards:
        if r.spendable_epoch != r.earned_epoch + 2:
            errors.append(f"{r.earned_epoch} != {r.spendable_epoch} + 2")

    if errors:
        err_str = ", ".join(errors)
        raise AssertionError(f"The 'earned epoch' and 'spendable epoch' don't match: {err_str}")

    return reward


def get_address_orphaned_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> Optional[RewardRecord]:
    """Get data about orphaned rewards for stake address from db-sync.

    The `epoch_from` and `epoch_to` are epochs where the reward was earned, not epochs
    where the reward can be spent.
    """
    rewards = []
    for db_row in query_address_orphaned_reward(
        address=address, epoch_from=epoch_from, epoch_to=epoch_to
    ):
        rewards.append(
            RewardEpochRecord(
                amount=int(db_row.amount),
                earned_epoch=db_row.epoch_no,
                spendable_epoch=-1,
            )
        )
    if not rewards:
        return None

    reward_sum = functools.reduce(lambda x, y: x + y.amount, rewards, 0)
    # pylint: disable=undefined-loop-variable
    return RewardRecord(
        address=db_row.address, pool_id=db_row.pool_id, rewards=rewards, reward_sum=reward_sum
    )


def get_pool_data(pool_id_bech32: str) -> Optional[PoolDataRecord]:
    """Get pool data from db-sync."""
    pools = list(query_pool_data(pool_id_bech32))
    if not pools:
        return None

    known_owners = set()
    single_host_names = []
    single_host_addresses = []

    latest_registered_tx_id = pools[-1].registered_tx_id
    latest_pools = [pool for pool in pools if pool.registered_tx_id == latest_registered_tx_id]

    for pool in latest_pools:
        if pool.owner:
            owner = pool.owner.hex()[2:]
            known_owners.add(owner)
        if pool.dns_name:
            host_name = {"single host name": {"dnsName": pool.dns_name, "port": pool.port}}
            if host_name not in single_host_names:
                single_host_names.append(host_name)
        if pool.ipv4:
            host_address = {
                "single host address": {"IPv4": pool.ipv4, "IPv6": pool.ipv6, "port": pool.port}
            }
            if host_address not in single_host_addresses:
                single_host_addresses.append(host_address)

    # pylint: disable=undefined-loop-variable
    pool_data = PoolDataRecord(
        id=pool.id,
        hash=pool.hash.hex(),
        view=pool.view,
        cert_index=pool.cert_index,
        vrf_key_hash=pool.vrf_key_hash.hex(),
        pledge=int(pool.pledge),
        reward_addr=pool.reward_addr.hex()[2:],
        active_epoch_no=pool.active_epoch_no,
        meta_id=pool.meta_id,
        margin=float(pool.margin),
        fixed_cost=int(pool.fixed_cost),
        registered_tx_id=pool.registered_tx_id,
        metadata_url=pool.metadata_url,
        metadata_hash=pool.metadata_hash.hex(),
        owners=list(known_owners),
        relays=[*single_host_names, *single_host_addresses],
        retire_cert_index=pool.retire_cert_index,
        retire_announced_tx_id=pool.retire_announced_tx_id,
        retiring_epoch=pool.retiring_epoch,
    )

    return pool_data


def get_prelim_tx_record(txhash: str) -> TxPrelimRecord:
    """Get first batch of transaction data from db-sync."""
    utxo_out: List[clusterlib.UTXOData] = []
    seen_tx_out_ids = set()
    ma_utxo_out: List[clusterlib.UTXOData] = []
    seen_ma_tx_out_ids = set()
    mint_utxo_out: List[clusterlib.UTXOData] = []
    seen_ma_tx_mint_ids = set()
    tx_id = -1

    for query_row in query_tx(txhash=txhash):
        if tx_id == -1:
            tx_id = query_row.tx_id
        if tx_id != query_row.tx_id:
            raise AssertionError("Transaction ID differs from the expected ID.")

        # Lovelace outputs
        if query_row.tx_out_id and query_row.tx_out_id not in seen_tx_out_ids:
            seen_tx_out_ids.add(query_row.tx_out_id)
            out_rec = clusterlib.UTXOData(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.tx_out_value),
                address=str(query_row.tx_out_addr),
            )
            utxo_out.append(out_rec)

        # MA outputs
        if query_row.ma_tx_out_id and query_row.ma_tx_out_id not in seen_ma_tx_out_ids:
            seen_ma_tx_out_ids.add(query_row.ma_tx_out_id)
            asset_name = (
                bytearray.fromhex(query_row.ma_tx_out_name.hex()).decode()
                if query_row.ma_tx_out_name
                else None
            )
            policyid = query_row.ma_tx_out_policy.hex() if query_row.ma_tx_out_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            ma_rec = clusterlib.UTXOData(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.ma_tx_out_quantity or 0),
                address=str(query_row.tx_out_addr),
                coin=coin,
            )
            ma_utxo_out.append(ma_rec)

        # MA minting
        if query_row.ma_tx_mint_id and query_row.ma_tx_mint_id not in seen_ma_tx_mint_ids:
            seen_ma_tx_mint_ids.add(query_row.ma_tx_mint_id)
            asset_name = (
                bytearray.fromhex(query_row.ma_tx_mint_name.hex()).decode()
                if query_row.ma_tx_mint_name
                else None
            )
            policyid = query_row.ma_tx_mint_policy.hex() if query_row.ma_tx_mint_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            mint_rec = clusterlib.UTXOData(
                utxo_hash=str(txhash),
                utxo_ix=int(query_row.utxo_ix),
                amount=int(query_row.ma_tx_mint_quantity or 0),
                address="",  # this is available only for MA outputs
                coin=coin,
            )
            mint_utxo_out.append(mint_rec)

    if tx_id == -1:
        raise RuntimeError("No results were returned by the TX SQL query.")

    # pylint: disable=undefined-loop-variable
    txdata = TxPrelimRecord(
        utxo_out=utxo_out,
        ma_utxo_out=ma_utxo_out,
        mint_utxo_out=mint_utxo_out,
        last_row=query_row,
    )

    return txdata


def get_txins(txhash: str) -> List[clusterlib.UTXOData]:
    """Get txins of a transaction from db-sync."""
    txins: List[clusterlib.UTXOData] = []
    seen_txins_out_ids = set()
    seen_txins_ma_ids = set()

    for txins_row in query_tx_ins(txhash=txhash):
        # Lovelace inputs
        if txins_row.tx_out_id and txins_row.tx_out_id not in seen_txins_out_ids:
            seen_txins_out_ids.add(txins_row.tx_out_id)
            txins.append(
                clusterlib.UTXOData(
                    utxo_hash=txins_row.tx_hash.hex(),
                    utxo_ix=int(txins_row.utxo_ix),
                    amount=int(txins_row.value),
                    address=str(txins_row.address),
                )
            )

        # MA inputs
        if txins_row.ma_tx_out_id and txins_row.ma_tx_out_id not in seen_txins_ma_ids:
            seen_txins_ma_ids.add(txins_row.ma_tx_out_id)
            asset_name = (
                bytearray.fromhex(txins_row.ma_tx_out_name.hex()).decode()
                if txins_row.ma_tx_out_name
                else None
            )
            policyid = txins_row.ma_tx_out_policy.hex() if txins_row.ma_tx_out_policy else ""
            coin = f"{policyid}.{asset_name}" if asset_name else policyid
            txins.append(
                clusterlib.UTXOData(
                    utxo_hash=txins_row.tx_hash.hex(),
                    utxo_ix=int(txins_row.utxo_ix),
                    amount=int(txins_row.ma_tx_out_quantity or 0),
                    address=str(txins_row.address),
                    coin=coin,
                )
            )

    return txins


def get_tx_record(txhash: str) -> TxRecord:
    """Get transaction data from db-sync.

    Compile data from multiple SQL queries to get as much information about the TX as possible.
    """
    txdata = get_prelim_tx_record(txhash)
    txins = get_txins(txhash)

    metadata = []
    if txdata.last_row.metadata_count:
        metadata = [
            MetadataRecord(key=int(r.key), json=r.json, bytes=r.bytes)
            for r in query_tx_metadata(txhash=txhash)
        ]

    reserve = []
    if txdata.last_row.reserve_count:
        reserve = [
            ADAStashRecord(
                address=str(r.addr_view), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in query_tx_reserve(txhash=txhash)
        ]

    treasury = []
    if txdata.last_row.treasury_count:
        treasury = [
            ADAStashRecord(
                address=str(r.addr_view), cert_index=int(r.cert_index), amount=int(r.amount)
            )
            for r in query_tx_treasury(txhash=txhash)
        ]

    pot_transfers = []
    if txdata.last_row.pot_transfer_count:
        pot_transfers = [
            PotTransferRecord(treasury=int(r.treasury), reserves=int(r.reserves))
            for r in query_tx_pot_transfers(txhash=txhash)
        ]

    stake_registration = []
    if txdata.last_row.stake_reg_count:
        stake_registration = [r.view for r in query_tx_stake_reg(txhash=txhash)]

    stake_deregistration = []
    if txdata.last_row.stake_dereg_count:
        stake_deregistration = [r.view for r in query_tx_stake_dereg(txhash=txhash)]

    stake_delegation = []
    if txdata.last_row.stake_deleg_count:
        stake_delegation = [
            DelegationRecord(
                address=r.address, pool_id=r.pool_id, active_epoch_no=r.active_epoch_no
            )
            for r in query_tx_stake_deleg(txhash=txhash)
            if (r.address and r.pool_id and r.active_epoch_no)
        ]

    withdrawals = []
    if txdata.last_row.withdrawal_count:
        withdrawals = [
            clusterlib.TxOut(address=r.address, amount=int(r.amount))
            for r in query_tx_withdrawal(txhash=txhash)
        ]

    collaterals = []
    if txdata.last_row.collateral_count:
        collaterals = [
            clusterlib.UTXOData(
                utxo_hash=r.tx_hash.hex(),
                utxo_ix=int(r.utxo_ix),
                amount=int(r.value),
                address=str(r.address),
            )
            for r in query_collateral_tx_ins(txhash=txhash)
        ]

    record = TxRecord(
        tx_id=int(txdata.last_row.tx_id),
        tx_hash=txdata.last_row.tx_hash.hex(),
        block_id=int(txdata.last_row.block_id),
        block_index=int(txdata.last_row.block_index),
        out_sum=int(txdata.last_row.out_sum),
        fee=int(txdata.last_row.fee),
        deposit=int(txdata.last_row.deposit),
        size=int(txdata.last_row.size),
        invalid_before=int(txdata.last_row.invalid_before)
        if txdata.last_row.invalid_before
        else None,
        invalid_hereafter=int(txdata.last_row.invalid_hereafter)
        if txdata.last_row.invalid_hereafter
        else None,
        txins=txins,
        txouts=[*txdata.utxo_out, *txdata.ma_utxo_out],
        mint=txdata.mint_utxo_out,
        collaterals=collaterals,
        metadata=metadata,
        reserve=reserve,
        treasury=treasury,
        pot_transfers=pot_transfers,
        stake_registration=stake_registration,
        stake_deregistration=stake_deregistration,
        stake_delegation=stake_delegation,
        withdrawals=withdrawals,
    )

    return record


def get_tx_record_retry(txhash: str, retry_num: int = 3) -> TxRecord:
    """Retry `get_tx_record` when data is anticipated and are not available yet.

    Under load it might be necessary to wait a bit and retry the query.
    """
    retry_num = retry_num if retry_num >= 0 else 0

    # first try + number of retries
    for r in range(1 + retry_num):
        if r > 0:
            LOGGER.warning(f"Repeating TX SQL query for '{txhash}' for the {r} time.")
            time.sleep(2 + r * r)
        try:
            response = get_tx_record(txhash=txhash)
            break
        except RuntimeError:
            if r == retry_num:
                raise

    return response


def _sum_mint_txouts(txouts: clusterlib.OptionalTxOuts) -> List[clusterlib.TxOut]:
    """Calculate minting amount sum for records with the same token.

    Remove address information - minting tokens doesn't include address, only amount and asset ID,
    i.e. address information is not available in `ma_tx_mint` table.
    MA output is handled in Tx output checks.
    """
    mint_txouts: Dict[str, clusterlib.TxOut] = {}

    for mt in txouts:
        if mt.coin in mint_txouts:
            mt_stored = mint_txouts[mt.coin]
            mint_txouts[mt.coin] = mt_stored._replace(
                address="", amount=mt_stored.amount + mt.amount
            )
        else:
            mint_txouts[mt.coin] = mt._replace(address="")

    return list(mint_txouts.values())


def check_tx(
    cluster_obj: clusterlib.ClusterLib, tx_raw_output: clusterlib.TxRawOutput, retry_num: int = 3
) -> Optional[TxRecord]:
    """Check a transaction in db-sync."""
    if not configuration.HAS_DBSYNC:
        return None

    txhash = cluster_obj.get_txid(tx_body_file=tx_raw_output.out_file)
    response = get_tx_record_retry(txhash=txhash, retry_num=retry_num)

    # we don't have complete info about the transaction when `build` command
    # was used, so we'll skip some of the checks
    if not tx_raw_output.change_address:
        txouts_amount = clusterlib_utils.get_amount(tx_raw_output.txouts)
        assert (
            response.out_sum == txouts_amount
        ), f"Sum of TX amounts doesn't match ({response.out_sum} != {txouts_amount})"

        assert (
            response.fee == tx_raw_output.fee
        ), f"TX fee doesn't match ({response.fee} != {tx_raw_output.fee})"

        len_db_txouts, len_out_txouts = len(response.txouts), len(tx_raw_output.txouts)
        assert (
            len_db_txouts == len_out_txouts
        ), f"Number of TX outputs doesn't match ({len_db_txouts} != {len_out_txouts})"

        # cannot get datum hash from db-sync, we need to strip it from tx data as well
        tx_txouts = sorted(r._replace(datum_hash="") for r in tx_raw_output.txouts)
        db_txouts = sorted(clusterlib_utils.utxodata2txout(r) for r in response.txouts)
        assert tx_txouts == db_txouts, f"TX outputs don't match ({tx_txouts} != {db_txouts})"

    assert response.invalid_before == tx_raw_output.invalid_before, (
        "TX invalid_before doesn't match "
        f"({response.invalid_before} != {tx_raw_output.invalid_before})"
    )
    assert response.invalid_hereafter == tx_raw_output.invalid_hereafter, (
        "TX invalid_hereafter doesn't match "
        f"({response.invalid_hereafter} != {tx_raw_output.invalid_hereafter})"
    )

    combined_txins: Set[clusterlib.UTXOData] = {
        *tx_raw_output.txins,
        *[p.txins[0] for p in tx_raw_output.plutus_txins],
        *[p.txins[0] for p in tx_raw_output.plutus_mint],
    }
    txin_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in combined_txins}
    db_utxos = {f"{r.utxo_hash}#{r.utxo_ix}" for r in response.txins}
    assert (
        txin_utxos == db_utxos
    ), f"Not all TX inputs are present in the db ({txin_utxos} != {db_utxos})"

    tx_mint_txouts = sorted(_sum_mint_txouts(tx_raw_output.mint))
    len_db_mint, len_out_mint = len(response.mint), len(tx_mint_txouts)
    assert (
        len_db_mint == len_out_mint
    ), f"Number of MA minting doesn't match ({len_db_mint} != {len_out_mint})"

    db_mint_txouts = sorted(clusterlib_utils.utxodata2txout(r) for r in response.mint)
    assert (
        tx_mint_txouts == db_mint_txouts
    ), f"MA minting outputs don't match ({tx_mint_txouts} != {db_mint_txouts})"

    len_db_withdrawals = len(response.withdrawals)
    len_out_withdrawals = len(tx_raw_output.withdrawals)
    assert (
        len_db_withdrawals == len_out_withdrawals
    ), f"Number of TX withdrawals doesn't match ({len_db_withdrawals} != {len_out_withdrawals})"

    tx_withdrawals = sorted(tx_raw_output.withdrawals)
    db_withdrawals = sorted(response.withdrawals)
    assert (
        tx_withdrawals == db_withdrawals
    ), f"TX withdrawals don't match ({tx_withdrawals} != {db_withdrawals})"

    tx_collaterals_nested = [
        r.collaterals for r in (*tx_raw_output.plutus_txins, *tx_raw_output.plutus_mint)
    ]
    tx_collaterals = set(itertools.chain.from_iterable(tx_collaterals_nested))
    db_collaterals = set(response.collaterals)
    assert (
        tx_collaterals == db_collaterals
    ), f"TX collaterals don't match ({tx_collaterals} != {db_collaterals})"

    return response
