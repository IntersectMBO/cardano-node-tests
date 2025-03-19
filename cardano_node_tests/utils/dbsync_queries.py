"""SQL queries to db-sync database."""

import contextlib
import decimal
import typing as tp

import psycopg2
import pydantic

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_conn

_CONF_ARBITRARY_T_ALLOWED: pydantic.ConfigDict = {"arbitrary_types_allowed": True}


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class PoolDataDBRow:
    id: int
    hash: memoryview
    view: str
    cert_index: int
    vrf_key_hash: memoryview
    pledge: int
    reward_addr: memoryview
    reward_addr_view: str
    active_epoch_no: int
    meta_id: int | None
    margin: decimal.Decimal
    fixed_cost: int
    deposit: decimal.Decimal | None
    registered_tx_id: int
    metadata_url: str | None
    metadata_hash: memoryview | None
    owner_stake_address_id: int
    owner: memoryview
    ipv4: str | None
    ipv6: str | None
    dns_name: str | None
    port: int | None
    retire_cert_index: int | None
    retire_announced_tx_id: int | None
    retiring_epoch: int | None


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class PoolOffChainDataDBRow:
    id: int
    ticker_name: str
    hash: memoryview
    json: dict
    bytes: memoryview
    pmr_id: int


@pydantic.dataclasses.dataclass(frozen=True)
class PoolOffChainFetchErrorDBRow:
    id: int
    pmr_id: int
    fetch_error: str
    retry_count: int


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class EpochStakeDBRow:
    id: int
    hash: memoryview
    view: str
    amount: int
    epoch_number: int


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class TxDBRow:
    tx_id: int
    tx_hash: memoryview
    block_id: int
    block_index: int
    out_sum: decimal.Decimal
    fee: decimal.Decimal
    deposit: int
    size: int
    invalid_before: decimal.Decimal | None
    invalid_hereafter: decimal.Decimal | None
    treasury_donation: int
    tx_out_id: int | None
    tx_out_tx_id: int | None
    utxo_ix: int | None
    tx_out_addr: str | None
    tx_out_addr_has_script: bool | None
    tx_out_value: decimal.Decimal | None
    tx_out_data_hash: memoryview | None
    tx_out_inline_datum_hash: memoryview | None
    tx_out_reference_script_hash: memoryview | None
    metadata_count: int
    reserve_count: int
    treasury_count: int
    pot_transfer_count: int
    stake_reg_count: int
    stake_dereg_count: int
    stake_deleg_count: int
    withdrawal_count: int
    collateral_count: int
    reference_input_count: int
    collateral_out_count: int
    script_count: int
    redeemer_count: int
    extra_key_witness_count: int
    ma_tx_out_id: int | None
    ma_tx_out_policy: memoryview | None
    ma_tx_out_name: memoryview | None
    ma_tx_out_quantity: decimal.Decimal | None
    ma_tx_mint_id: int | None
    ma_tx_mint_policy: memoryview | None
    ma_tx_mint_name: memoryview | None
    ma_tx_mint_quantity: decimal.Decimal | None


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class MetadataDBRow:
    id: int
    key: decimal.Decimal
    json: tp.Any
    bytes: memoryview
    tx_id: int


@pydantic.dataclasses.dataclass(frozen=True)
class ADAStashDBRow:
    id: int
    addr_view: str
    cert_index: int
    amount: decimal.Decimal
    tx_id: int


@pydantic.dataclasses.dataclass(frozen=True)
class PotTransferDBRow:
    id: int
    cert_index: int
    treasury: decimal.Decimal
    reserves: decimal.Decimal
    tx_id: int


@pydantic.dataclasses.dataclass(frozen=True)
class StakeAddrDBRow:
    id: int
    view: str
    tx_id: int


@pydantic.dataclasses.dataclass(frozen=True)
class StakeDelegDBRow:
    tx_id: int
    active_epoch_no: int | None
    pool_id: str | None
    address: str | None


@pydantic.dataclasses.dataclass(frozen=True)
class WithdrawalDBRow:
    tx_id: int
    address: str
    amount: int


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class TxInDBRow:
    tx_out_id: int
    utxo_ix: int
    address: str
    value: decimal.Decimal
    tx_hash: memoryview
    reference_script_hash: memoryview | None
    reference_script_json: dict | None
    reference_script_bytes: memoryview | None
    reference_script_type: str | None
    ma_tx_out_id: int | None
    ma_tx_out_policy: memoryview | None
    ma_tx_out_name: memoryview | None
    ma_tx_out_quantity: decimal.Decimal | None


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class TxInNoMADBRow:
    tx_out_id: int
    utxo_ix: int
    address: str
    value: decimal.Decimal
    tx_hash: memoryview
    reference_script_hash: memoryview | None
    reference_script_json: dict | None
    reference_script_bytes: memoryview | None
    reference_script_type: str | None


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class CollateralTxOutDBRow:
    tx_out_id: int
    utxo_ix: int
    address: str
    value: decimal.Decimal
    tx_hash: memoryview


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class ScriptDBRow:
    id: int
    tx_id: int
    hash: memoryview
    type: str
    serialised_size: int | None


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class RedeemerDBRow:
    id: int
    tx_id: int
    unit_mem: int
    unit_steps: int
    fee: int
    purpose: str
    script_hash: memoryview
    value: dict


@pydantic.dataclasses.dataclass(frozen=True)
class ADAPotsDBRow:
    id: int
    slot_no: int
    epoch_no: int
    treasury: decimal.Decimal
    reserves: decimal.Decimal
    rewards: decimal.Decimal
    utxo: decimal.Decimal
    deposits_stake: decimal.Decimal
    deposits_drep: decimal.Decimal
    deposits_proposal: decimal.Decimal
    fees: decimal.Decimal
    block_id: int


@pydantic.dataclasses.dataclass(frozen=True)
class RewardDBRow:
    address: str
    type: str
    amount: decimal.Decimal
    earned_epoch: int
    spendable_epoch: int
    pool_id: str | None = ""


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class UTxODBRow:
    tx_hash: memoryview
    utxo_ix: int
    payment_address: str
    stake_address: str | None
    has_script: bool
    value: int
    data_hash: memoryview | None


@pydantic.dataclasses.dataclass(frozen=True)
class BlockDBRow:
    id: int
    epoch_no: int | None
    slot_no: int | None
    epoch_slot_no: int | None
    block_no: int | None
    previous_id: int | None
    tx_count: int | None
    proto_major: int | None
    proto_minor: int | None
    pool_id: str | None


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class DatumDBRow:
    id: int
    datum_hash: memoryview
    tx_id: int
    value: dict
    bytes: memoryview


@pydantic.dataclasses.dataclass(frozen=True)
class SchemaVersionStages:
    one: int
    two: int
    three: int


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class ParamProposalDBRow:
    id: int
    epoch_no: int | None
    key: memoryview | None
    min_fee_a: int | None
    min_fee_b: int | None
    max_block_size: int | None
    max_tx_size: int | None
    max_bh_size: int | None
    key_deposit: int | None
    pool_deposit: int | None
    max_epoch: int | None
    optimal_pool_count: int | None
    influence: float | None
    monetary_expand_rate: float | None
    treasury_growth_rate: float | None
    decentralisation: float | None
    entropy: memoryview | None
    protocol_major: int | None
    protocol_minor: int | None
    min_utxo_value: int | None
    min_pool_cost: int | None
    coins_per_utxo_size: int | None
    cost_model_id: int | None
    price_mem: float | None
    price_step: float | None
    max_tx_ex_mem: int | None
    max_tx_ex_steps: int | None
    max_block_ex_mem: int | None
    max_block_ex_steps: int | None
    max_val_size: int | None
    collateral_percent: int | None
    max_collateral_inputs: int | None
    registered_tx_id: int | None
    pvt_motion_no_confidence: float | None
    pvt_committee_normal: float | None
    pvt_committee_no_confidence: float | None
    pvt_hard_fork_initiation: float | None
    dvt_motion_no_confidence: float | None
    dvt_committee_normal: float | None
    dvt_committee_no_confidence: float | None
    dvt_update_to_constitution: float | None
    dvt_hard_fork_initiation: float | None
    dvt_p_p_network_group: float | None
    dvt_p_p_economic_group: float | None
    dvt_p_p_technical_group: float | None
    dvt_p_p_gov_group: float | None
    dvt_treasury_withdrawal: float | None
    committee_min_size: int | None
    committee_max_term_length: int | None
    gov_action_lifetime: int | None
    gov_action_deposit: int | None
    drep_deposit: int | None
    drep_activity: decimal.Decimal | None
    pvtpp_security_group: float | None
    min_fee_ref_script_cost_per_byte: float | None


@pydantic.dataclasses.dataclass(frozen=True)
class EpochDBRow:
    id: int
    out_sum: int
    fees: int
    tx_count: int
    blk_count: int
    epoch_number: int


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class EpochParamDBRow:
    id: int
    epoch_no: int
    min_fee_a: int
    min_fee_b: int
    max_block_size: int
    max_tx_size: int
    max_bh_size: int
    key_deposit: int
    pool_deposit: int
    max_epoch: int
    optimal_pool_count: int
    influence: float
    monetary_expand_rate: float
    treasury_growth_rate: float
    decentralisation: float
    protocol_major: int
    protocol_minor: int
    min_utxo_value: int
    min_pool_cost: int
    nonce: memoryview
    cost_model_id: int
    price_mem: float
    price_step: float
    max_tx_ex_mem: int
    max_tx_ex_steps: int
    max_block_ex_mem: int
    max_block_ex_steps: int
    max_val_size: int
    collateral_percent: int
    max_collateral_inputs: int
    block_id: int
    extra_entropy: memoryview | None
    coins_per_utxo_size: int
    pvt_motion_no_confidence: float
    pvt_committee_normal: float
    pvt_committee_no_confidence: float
    pvt_hard_fork_initiation: float
    dvt_motion_no_confidence: float
    dvt_committee_normal: float
    dvt_committee_no_confidence: float
    dvt_update_to_constitution: float
    dvt_hard_fork_initiation: float
    dvt_p_p_network_group: float
    dvt_p_p_economic_group: float
    dvt_p_p_technical_group: float
    dvt_p_p_gov_group: float
    dvt_treasury_withdrawal: float
    committee_min_size: int
    committee_max_term_length: int
    gov_action_lifetime: int
    gov_action_deposit: int
    drep_deposit: int
    drep_activity: int
    pvtpp_security_group: float
    min_fee_ref_script_cost_per_byte: float


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class CommitteeRegistrationDBRow:
    id: int
    tx_id: int
    cert_index: int
    cold_key: memoryview
    hot_key: memoryview


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class CommitteeDeregistrationDBRow:
    id: int
    tx_id: int
    cert_index: int
    voting_anchor_id: int
    cold_key: memoryview


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class DrepRegistrationDBRow:
    id: int
    tx_id: int
    cert_index: int
    deposit: int
    drep_hash_id: int
    voting_anchor_id: int | None
    hash_raw: memoryview
    hash_view: str
    has_script: bool


@pydantic.dataclasses.dataclass(frozen=True)
class GovActionProposalDBRow:
    id: int
    tx_id: int
    action_ix: int
    prev_gov_action_proposal: int | None
    deposit: int
    return_address: int
    expiration: int
    voting_anchor_id: int
    type: str
    description: dict
    param_proposal: int
    ratified_epoch: int | None
    enacted_epoch: int | None
    dropped_epoch: int | None
    expired_epoch: int | None


@pydantic.dataclasses.dataclass(frozen=True)
class VotingProcedureDBRow:
    id: int
    voter_role: str
    committee_voter: int | None
    drep_voter: int | None
    pool_voter: int | None
    vote: str


@pydantic.dataclasses.dataclass(frozen=True)
class NewCommitteeInfoDBRow:
    id: int
    tx_id: int
    action_ix: int
    quorum_numerator: int
    quorum_denominator: int


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class NewCommitteeMemberDBRow:
    id: int
    committee_id: int
    committee_hash: memoryview
    expiration_epoch: int


@pydantic.dataclasses.dataclass(frozen=True)
class TreasuryWithdrawalDBRow:
    id: int
    tx_id: int
    action_ix: int
    expiration: int
    ratified_epoch: int | None
    enacted_epoch: int | None
    dropped_epoch: int | None
    expired_epoch: int | None
    addr_view: str
    amount: decimal.Decimal


@pydantic.dataclasses.dataclass(frozen=True)
class OffChainVoteFetchErrorDBRow:
    id: int
    voting_anchor_id: int
    fetch_error: str


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class OffChainVoteDrepDataDBRow:
    id: int
    hash: memoryview
    language: str
    comment: str | None
    json: dict
    bytes: memoryview
    warning: str | None
    is_valid: bool | None
    payment_address: str
    given_name: str
    objectives: str
    motivations: str
    qualifications: str
    image_url: str
    image_hash: str | None


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class OffChainVoteDataDBRow:
    data_id: int
    data_vot_anchor_id: int
    data_hash: memoryview
    data_json: dict
    data_bytes: memoryview
    data_warning: str | None
    data_language: str
    data_comment: str | None
    data_is_valid: bool | None
    auth_name: str | None
    auth_wit_alg: str | None
    auth_pub_key: str | None
    auth_signature: str | None
    auth_warning: str | None
    ext_update_id: int | None
    ext_update_title: str | None
    ext_update_uri: str | None
    gov_act_id: int | None
    gov_act_title: str | None
    gov_act_abstract: str | None
    gov_act_motivation: str | None
    gov_act_rationale: str | None
    ref_id: int | None
    ref_label: str | None
    ref_uri: str | None
    ref_hash_digest: str | None
    ref_hash_alg: str | None
    vot_anchor_url: str
    vot_anchor_data_hash: memoryview
    vot_anchor_type: str
    vot_anchor_block_id: int


@pydantic.dataclasses.dataclass(frozen=True)
class DelegationVoteDBRow:
    id: int
    stake_address_hash_view: str
    drep_hash_view: str


@pydantic.dataclasses.dataclass(frozen=True, config=_CONF_ARBITRARY_T_ALLOWED)
class NewConstitutionInfoDBRow:
    id: int
    script_hash: memoryview | None
    gov_action_type: str
    gap_id: int
    tx_id: int
    action_ix: int


@pydantic.dataclasses.dataclass(frozen=True)
class DrepDistributionDBRow:
    id: int
    hash_id: int
    amount: int
    epoch_no: int
    drep_hash_view: str


@contextlib.contextmanager
def execute(query: str, vars: tp.Sequence = ()) -> tp.Iterator[psycopg2.extensions.cursor]:
    cur = None
    try:
        cur = dbsync_conn.conn().cursor()

        try:
            cur.execute(query, vars)
            conn_alive = True
        except psycopg2.Error:
            conn_alive = False

        if not conn_alive:
            cur = dbsync_conn.reconn().cursor()
            cur.execute(query, vars)

        yield cur
    finally:
        if cur is not None:
            cur.close()


class SchemaVersion:
    """Query and cache db-sync schema version."""

    _stages: tp.ClassVar[SchemaVersionStages | None] = None

    @classmethod
    def stages(cls) -> SchemaVersionStages:
        if cls._stages is not None:
            return cls._stages

        query = (
            "SELECT stage_one, stage_two, stage_three FROM schema_version ORDER BY id DESC LIMIT 1;"
        )

        with execute(query=query) as cur:
            cls._stages = SchemaVersionStages(*cur.fetchone())

        return cls._stages


def query_tx(txhash: str) -> tp.Generator[TxDBRow, None, None]:
    """Query a transaction in db-sync."""
    query = (
        "SELECT"
        " tx.id, tx.hash, tx.block_id, tx.block_index, tx.out_sum, tx.fee, tx.deposit, tx.size,"
        " tx.invalid_before, tx.invalid_hereafter, tx.treasury_donation,"
        " tx_out.id, tx_out.tx_id, tx_out.index, tx_out.address, tx_out.address_has_script,"
        " tx_out.value, tx_out.data_hash, datum.hash, script.hash,"
        " (SELECT COUNT(id) FROM tx_metadata WHERE tx_id=tx.id) AS metadata_count,"
        " (SELECT COUNT(id) FROM reserve WHERE tx_id=tx.id) AS reserve_count,"
        " (SELECT COUNT(id) FROM treasury WHERE tx_id=tx.id) AS treasury_count,"
        " (SELECT COUNT(id) FROM pot_transfer WHERE tx_id=tx.id) AS pot_transfer_count,"
        " (SELECT COUNT(id) FROM stake_registration WHERE tx_id=tx.id) AS reg_count,"
        " (SELECT COUNT(id) FROM stake_deregistration WHERE tx_id=tx.id) AS dereg_count,"
        " (SELECT COUNT(id) FROM delegation WHERE tx_id=tx.id) AS deleg_count,"
        " (SELECT COUNT(id) FROM withdrawal WHERE tx_id=tx.id) AS withdrawal_count,"
        " (SELECT COUNT(id) FROM collateral_tx_in WHERE tx_in_id=tx.id) AS collateral_count,"
        " (SELECT COUNT(id) FROM reference_tx_in WHERE tx_in_id=tx.id) AS reference_input_count,"
        " (SELECT COUNT(id) FROM collateral_tx_out WHERE tx_id=tx.id) AS collateral_out_count,"
        " (SELECT COUNT(id) FROM script WHERE tx_id=tx.id) AS script_count,"
        " (SELECT COUNT(id) FROM redeemer WHERE tx_id=tx.id) AS redeemer_count,"
        " (SELECT COUNT(id) FROM extra_key_witness WHERE tx_id=tx.id) AS extra_key_witness_count,"
        " ma_tx_out.id, join_ma_out.policy, join_ma_out.name, ma_tx_out.quantity,"
        " ma_tx_mint.id, join_ma_mint.policy, join_ma_mint.name, ma_tx_mint.quantity "
        "FROM tx "
        "LEFT JOIN tx_out ON tx.id = tx_out.tx_id "
        "LEFT JOIN ma_tx_out ON tx_out.id = ma_tx_out.tx_out_id "
        "LEFT JOIN ma_tx_mint ON tx.id = ma_tx_mint.tx_id "
        "LEFT JOIN multi_asset join_ma_out ON ma_tx_out.ident = join_ma_out.id "
        "LEFT JOIN multi_asset join_ma_mint ON ma_tx_mint.ident = join_ma_mint.id "
        "LEFT JOIN datum ON tx_out.inline_datum_id = datum.id "
        "LEFT JOIN script ON tx_out.reference_script_id = script.id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield TxDBRow(*result)


def query_tx_ins(txhash: str) -> tp.Generator[TxInDBRow, None, None]:
    """Query transaction txins in db-sync."""
    query = (
        "SELECT"
        " tx_out.id, tx_out.index, tx_out.address, tx_out.value, jtx_out_id.hash,"
        " script.hash, script.json, script.bytes, script.type,"
        " ma_tx_out.id, join_ma_out.policy, join_ma_out.name, ma_tx_out.quantity "
        "FROM tx_in "
        "LEFT JOIN tx_out"
        " ON (tx_out.tx_id = tx_in.tx_out_id AND tx_out.index = tx_in.tx_out_index) "
        "LEFT JOIN tx jtx_in ON jtx_in.id = tx_in.tx_in_id "
        "LEFT JOIN tx jtx_out_id ON jtx_out_id.id = tx_out.tx_id "
        "LEFT JOIN ma_tx_out ON tx_out.id = ma_tx_out.tx_out_id "
        "LEFT JOIN multi_asset join_ma_out ON ma_tx_out.ident = join_ma_out.id "
        "LEFT JOIN script ON script.id = tx_out.reference_script_id "
        "WHERE jtx_in.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield TxInDBRow(*result)


def query_collateral_tx_ins(txhash: str) -> tp.Generator[TxInNoMADBRow, None, None]:
    """Query transaction collateral txins in db-sync."""
    query = (
        "SELECT"
        " tx_out.id, tx_out.index, tx_out.address, tx_out.value, jtx_out_id.hash,"
        " script.hash, script.json, script.bytes, script.type "
        "FROM collateral_tx_in "
        "LEFT JOIN tx_out"
        " ON (tx_out.tx_id = collateral_tx_in.tx_out_id AND"
        "     tx_out.index = collateral_tx_in.tx_out_index) "
        "LEFT JOIN tx jtx_col ON jtx_col.id = collateral_tx_in.tx_in_id "
        "LEFT JOIN tx jtx_out_id ON jtx_out_id.id = tx_out.tx_id "
        "LEFT JOIN script ON script.id = tx_out.reference_script_id "
        "WHERE jtx_col.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield TxInNoMADBRow(*result)


def query_reference_tx_ins(txhash: str) -> tp.Generator[TxInNoMADBRow, None, None]:
    """Query transaction reference txins in db-sync."""
    query = (
        "SELECT "
        " tx_out.id, tx_out.index, tx_out.address, tx_out.value, jtx_out_id.hash,"
        " script.hash, script.json, script.bytes, script.type "
        "FROM reference_tx_in "
        "LEFT JOIN tx_out"
        " ON (tx_out.tx_id = reference_tx_in.tx_out_id AND"
        "     tx_out.index = reference_tx_in.tx_out_index) "
        "LEFT JOIN tx jtx_ref ON jtx_ref.id = reference_tx_in.tx_in_id "
        "LEFT JOIN tx jtx_out_id ON jtx_out_id.id = tx_out.tx_id "
        "LEFT JOIN script ON script.id = tx_out.reference_script_id "
        "WHERE jtx_ref.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield TxInNoMADBRow(*result)


def query_collateral_tx_outs(txhash: str) -> tp.Generator[CollateralTxOutDBRow, None, None]:
    """Query transaction collateral txouts in db-sync."""
    query = (
        "SELECT "
        " collateral_tx_out.id, collateral_tx_out.index, collateral_tx_out.address,"
        " collateral_tx_out.value, jtx_out_id.hash "
        "FROM collateral_tx_out "
        "LEFT JOIN tx jtx_col ON jtx_col.id = collateral_tx_out.tx_id "
        "LEFT JOIN tx jtx_out_id ON jtx_out_id.id = collateral_tx_out.tx_id "
        "WHERE jtx_col.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield CollateralTxOutDBRow(*result)


def query_scripts(txhash: str) -> tp.Generator[ScriptDBRow, None, None]:
    """Query transaction scripts in db-sync."""
    query = (
        "SELECT"
        " script.id, script.tx_id, script.hash, script.type, script.serialised_size "
        "FROM script "
        "LEFT JOIN tx ON tx.id = script.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield ScriptDBRow(*result)


def query_redeemers(txhash: str) -> tp.Generator[RedeemerDBRow, None, None]:
    """Query transaction redeemers in db-sync."""
    query = (
        "SELECT"
        " redeemer.id, redeemer.tx_id, redeemer.unit_mem, redeemer.unit_steps, redeemer.fee,"
        " redeemer.purpose, redeemer.script_hash, redeemer_data.value "
        "FROM redeemer "
        "LEFT JOIN tx ON tx.id = redeemer.tx_id "
        "LEFT JOIN redeemer_data ON redeemer_data.id = redeemer.redeemer_data_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield RedeemerDBRow(*result)


def query_tx_metadata(txhash: str) -> tp.Generator[MetadataDBRow, None, None]:
    """Query transaction metadata in db-sync."""
    query = (
        "SELECT"
        " tx_metadata.id, tx_metadata.key, tx_metadata.json, tx_metadata.bytes,"
        " tx_metadata.tx_id "
        "FROM tx_metadata "
        "INNER JOIN tx ON tx.id = tx_metadata.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield MetadataDBRow(*result)


def query_tx_reserve(txhash: str) -> tp.Generator[ADAStashDBRow, None, None]:
    """Query transaction reserve record in db-sync."""
    query = (
        "SELECT"
        " reserve.id, stake_address.view, reserve.cert_index, reserve.amount, reserve.tx_id "
        "FROM reserve "
        "INNER JOIN stake_address ON reserve.addr_id = stake_address.id "
        "INNER JOIN tx ON tx.id = reserve.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield ADAStashDBRow(*result)


def query_tx_treasury(txhash: str) -> tp.Generator[ADAStashDBRow, None, None]:
    """Query transaction treasury record in db-sync."""
    query = (
        "SELECT"
        " treasury.id, stake_address.view, treasury.cert_index,"
        " treasury.amount, treasury.tx_id "
        "FROM treasury "
        "INNER JOIN stake_address ON treasury.addr_id = stake_address.id "
        "INNER JOIN tx ON tx.id = treasury.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield ADAStashDBRow(*result)


def query_tx_pot_transfers(txhash: str) -> tp.Generator[PotTransferDBRow, None, None]:
    """Query transaction MIR certificate records in db-sync."""
    query = (
        "SELECT"
        " pot_transfer.id, pot_transfer.cert_index, pot_transfer.treasury,"
        " pot_transfer.reserves, pot_transfer.tx_id "
        "FROM pot_transfer "
        "INNER JOIN tx ON tx.id = pot_transfer.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield PotTransferDBRow(*result)


def query_tx_stake_reg(txhash: str) -> tp.Generator[StakeAddrDBRow, None, None]:
    """Query stake registration record in db-sync."""
    query = (
        "SELECT"
        " stake_registration.addr_id, stake_address.view, stake_registration.tx_id "
        "FROM stake_registration "
        "INNER JOIN stake_address ON stake_registration.addr_id = stake_address.id "
        "INNER JOIN tx ON tx.id = stake_registration.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield StakeAddrDBRow(*result)


def query_tx_stake_dereg(txhash: str) -> tp.Generator[StakeAddrDBRow, None, None]:
    """Query stake deregistration record in db-sync."""
    query = (
        "SELECT"
        " stake_deregistration.addr_id, stake_address.view, stake_deregistration.tx_id "
        "FROM stake_deregistration "
        "INNER JOIN stake_address ON stake_deregistration.addr_id = stake_address.id "
        "INNER JOIN tx ON tx.id = stake_deregistration.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield StakeAddrDBRow(*result)


def query_tx_stake_deleg(txhash: str) -> tp.Generator[StakeDelegDBRow, None, None]:
    """Query stake registration record in db-sync."""
    query = (
        "SELECT"
        " tx.id, delegation.active_epoch_no, pool_hash.view AS pool_view,"
        " stake_address.view AS address_view "
        "FROM delegation "
        "INNER JOIN stake_address ON delegation.addr_id = stake_address.id "
        "INNER JOIN tx ON tx.id = delegation.tx_id "
        "INNER JOIN pool_hash ON pool_hash.id = delegation.pool_hash_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield StakeDelegDBRow(*result)


def query_tx_withdrawal(txhash: str) -> tp.Generator[WithdrawalDBRow, None, None]:
    """Query reward withdrawal record in db-sync."""
    query = (
        "SELECT"
        " tx.id, stake_address.view, amount "
        "FROM withdrawal "
        "INNER JOIN stake_address ON withdrawal.addr_id = stake_address.id "
        "INNER JOIN tx ON tx.id = withdrawal.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield WithdrawalDBRow(*result)


def query_ada_pots(
    epoch_from: int = 0, epoch_to: int = 99999999
) -> tp.Generator[ADAPotsDBRow, None, None]:
    """Query ADA pots record in db-sync."""
    query = (
        "SELECT"
        " id, slot_no, epoch_no, treasury, reserves, rewards, utxo, "
        " deposits_stake, deposits_drep, deposits_proposal, fees, block_id "
        "FROM ada_pots "
        "WHERE epoch_no BETWEEN %s AND %s "
        "ORDER BY id;"
    )

    with execute(query=query, vars=(epoch_from, epoch_to)) as cur:
        while (result := cur.fetchone()) is not None:
            yield ADAPotsDBRow(*result)


def query_address_reward(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> tp.Generator[RewardDBRow, None, None]:
    """Query reward records for stake address in db-sync."""
    query = (
        "SELECT"
        " stake_address.view, reward.type, reward.amount, reward.earned_epoch,"
        " reward.spendable_epoch, pool_hash.view AS pool_view "
        "FROM reward "
        "INNER JOIN stake_address ON reward.addr_id = stake_address.id "
        "LEFT JOIN pool_hash ON pool_hash.id = reward.pool_id "
        "WHERE (stake_address.view = %s) AND (reward.spendable_epoch BETWEEN %s AND %s) ;"
    )

    with execute(query=query, vars=(address, epoch_from, epoch_to)) as cur:
        while (result := cur.fetchone()) is not None:
            yield RewardDBRow(*result)


def query_address_reward_rest(
    address: str, epoch_from: int = 0, epoch_to: int = 99999999
) -> tp.Generator[RewardDBRow, None, None]:
    """Query instant reward records for stake address in db-sync."""
    query = (
        "SELECT"
        " stake_address.view, reward_rest.type, reward_rest.amount, "
        " reward_rest.earned_epoch, reward_rest.spendable_epoch "
        "FROM reward_rest "
        "INNER JOIN stake_address ON reward_rest.addr_id = stake_address.id "
        "WHERE (stake_address.view = %s) AND (reward_rest.spendable_epoch BETWEEN %s AND %s) ;"
    )

    with execute(query=query, vars=(address, epoch_from, epoch_to)) as cur:
        while (result := cur.fetchone()) is not None:
            yield RewardDBRow(*result)


def query_utxo(address: str) -> tp.Generator[UTxODBRow, None, None]:
    """Query UTxOs for payment address in db-sync."""
    query = (
        "SELECT"
        " tx.hash, tx_out.index, tx_out.address, stake_address.view AS stake_address,"
        " tx_out.address_has_script, tx_out.value, tx_out.data_hash "
        "FROM tx_out "
        "INNER JOIN tx ON tx_out.tx_id = tx.id "
        "LEFT JOIN stake_address ON tx_out.stake_address_id = stake_address.id "
        "LEFT JOIN tx_in "
        "ON tx_out.tx_id = tx_in.tx_out_id AND tx_out.index = tx_in.tx_out_index "
        "WHERE tx_in.tx_out_id IS NULL "  # Only unspent UTXOs
        "AND tx_out.address = %s "
        "ORDER BY tx_out.id;"
    )

    with execute(query=query, vars=(address,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield UTxODBRow(*result)


def query_pool_data(pool_id_bech32: str) -> tp.Generator[PoolDataDBRow, None, None]:
    """Query pool data record in db-sync."""
    query = (
        "SELECT DISTINCT"
        " pool_hash.id, pool_hash.hash_raw, pool_hash.view,"
        " pool_update.cert_index, pool_update.vrf_key_hash, pool_update.pledge,"
        " join_reward_address.hash_raw, join_reward_address.view,"
        " pool_update.active_epoch_no, pool_update.meta_id,"
        " pool_update.margin, pool_update.fixed_cost, pool_update.deposit,"
        " pool_update.registered_tx_id,"
        " pool_metadata_ref.url AS metadata_url, pool_metadata_ref.hash AS metadata_hash,"
        " pool_owner.addr_id AS owner_stake_address_id,"
        " join_owner_address.hash_raw AS owner,"
        " pool_relay.ipv4, pool_relay.ipv6, pool_relay.dns_name, pool_relay.port,"
        " pool_retire.cert_index AS retire_cert_index,"
        " pool_retire.announced_tx_id AS retire_announced_tx_id, pool_retire.retiring_epoch "
        "FROM pool_hash "
        "INNER JOIN pool_update ON pool_hash.id = pool_update.hash_id "
        "FULL JOIN pool_metadata_ref ON pool_update.meta_id = pool_metadata_ref.id "
        "INNER JOIN pool_owner ON pool_update.id = pool_owner.pool_update_id "
        "FULL JOIN pool_relay ON pool_update.id = pool_relay.update_id "
        "FULL JOIN pool_retire ON pool_hash.id = pool_retire.hash_id "
        "INNER JOIN stake_address join_reward_address ON"
        " pool_update.reward_addr_id = join_reward_address.id "
        "INNER JOIN stake_address join_owner_address ON pool_owner.addr_id = join_owner_address.id "
        "WHERE pool_hash.view = %s ORDER BY registered_tx_id;"
    )

    with execute(query=query, vars=(pool_id_bech32,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield PoolDataDBRow(*result)


def query_off_chain_pool_data(
    pool_id_bech32: str,
) -> tp.Generator[PoolOffChainDataDBRow, None, None]:
    """Query `Off_Chain_Pool_Data` record in db-sync."""
    query = (
        "SELECT"
        " off_chain_pool_data.pool_id, off_chain_pool_data.ticker_name, off_chain_pool_data.hash,"
        " off_chain_pool_data.json, off_chain_pool_data.bytes, off_chain_pool_data.pmr_id "
        "FROM off_chain_pool_data "
        "INNER JOIN pool_hash ON pool_hash.id = off_chain_pool_data.pool_id "
        "WHERE pool_hash.view = %s;"
    )

    with execute(query=query, vars=(pool_id_bech32,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield PoolOffChainDataDBRow(*result)


def query_off_chain_pool_fetch_error(
    pool_id_bech32: str,
) -> tp.Generator[PoolOffChainFetchErrorDBRow, None, None]:
    """Query `Off_Chain_Pool_Fetch_Error` record in db-sync."""
    query = (
        "SELECT"
        " off_chain_pool_fetch_error.pool_id, off_chain_pool_fetch_error.pmr_id,"
        " off_chain_pool_fetch_error.fetch_error, off_chain_pool_fetch_error.retry_count "
        "FROM off_chain_pool_fetch_error "
        "INNER JOIN pool_hash ON pool_hash.id = off_chain_pool_fetch_error.pool_id "
        "WHERE pool_hash.view = %s;"
    )

    with execute(query=query, vars=(pool_id_bech32,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield PoolOffChainFetchErrorDBRow(*result)


def query_epoch_stake(
    pool_id_bech32: str, epoch_number: int
) -> tp.Generator[EpochStakeDBRow, None, None]:
    """Query epoch stake record for a pool in db-sync."""
    query = (
        "SELECT "
        " epoch_stake.id, pool_hash.hash_raw, pool_hash.view, epoch_stake.amount,"
        " epoch_stake.epoch_no "
        "FROM epoch_stake "
        "INNER JOIN pool_hash ON epoch_stake.pool_id = pool_hash.id "
        "WHERE pool_hash.view = %s AND epoch_stake.epoch_no = %s "
        "ORDER BY epoch_stake.epoch_no DESC;"
    )

    with execute(query=query, vars=(pool_id_bech32, epoch_number)) as cur:
        while (result := cur.fetchone()) is not None:
            yield EpochStakeDBRow(*result)


def query_epoch_param(epoch_no: int = 0) -> EpochParamDBRow:
    """Query epoch param record in db-sync."""
    query_var = epoch_no

    query = (
        "SELECT "
        " id, epoch_no, min_fee_a, min_fee_b, max_block_size, max_tx_size, max_bh_size, "
        " key_deposit, pool_deposit, max_epoch, optimal_pool_count, influence, "
        " monetary_expand_rate, treasury_growth_rate, decentralisation, protocol_major, "
        " protocol_minor, min_utxo_value, min_pool_cost, nonce, cost_model_id, price_mem, "
        " price_step, max_tx_ex_mem, max_tx_ex_steps, max_block_ex_mem, max_block_ex_steps, "
        " max_val_size, collateral_percent, max_collateral_inputs, block_id, extra_entropy, "
        " coins_per_utxo_size, pvt_motion_no_confidence, pvt_committee_normal, "
        " pvt_committee_no_confidence, pvt_hard_fork_initiation, dvt_motion_no_confidence, "
        " dvt_committee_normal, dvt_committee_no_confidence, dvt_update_to_constitution, "
        " dvt_hard_fork_initiation, dvt_p_p_network_group, dvt_p_p_economic_group, "
        " dvt_p_p_technical_group, dvt_p_p_gov_group, dvt_treasury_withdrawal, committee_min_size, "
        " committee_max_term_length, gov_action_lifetime, gov_action_deposit, drep_deposit, "
        " drep_activity, pvtpp_security_group, min_fee_ref_script_cost_per_byte "
        " FROM epoch_param "
        " WHERE epoch_no =  %s "
    )

    with execute(query=query, vars=(query_var,)) as cur:
        results = cur.fetchone()
        return EpochParamDBRow(*results)


def query_blocks(
    pool_id_bech32: str = "", epoch_from: int = 0, epoch_to: int = 99999999
) -> tp.Generator[BlockDBRow, None, None]:
    """Query block records in db-sync."""
    if pool_id_bech32:
        pool_query = "(pool_hash.view = %s) AND"
        query_vars: tuple = (pool_id_bech32, epoch_from, epoch_to)
    else:
        pool_query = ""
        query_vars = (epoch_from, epoch_to)

    query = (
        "SELECT"
        " block.id, block.epoch_no, block.slot_no, block.epoch_slot_no, block.block_no,"
        " block.previous_id, block.tx_count, block.proto_major, block.proto_minor,"
        " pool_hash.view "
        "FROM block "
        "INNER JOIN slot_leader ON slot_leader.id = block.slot_leader_id "
        "LEFT JOIN pool_hash ON pool_hash.id = slot_leader.pool_hash_id "
        f"WHERE {pool_query} (epoch_no BETWEEN %s AND %s) "
        "ORDER BY block.id;"
    )

    with execute(query=query, vars=query_vars) as cur:
        while (result := cur.fetchone()) is not None:
            yield BlockDBRow(*result)


def query_table_names() -> list[str]:
    """Query table names in db-sync."""
    query = (
        "SELECT tablename "
        "FROM pg_catalog.pg_tables "
        "WHERE schemaname != 'pg_catalog' AND schemaname != 'information_schema' "
        "ORDER BY tablename ASC;"
    )

    with execute(query=query) as cur:
        results: list[tuple[str]] = cur.fetchall()
        table_names = [r[0] for r in results]
        return table_names


def query_datum(datum_hash: str) -> tp.Generator[DatumDBRow, None, None]:
    """Query datum record in db-sync."""
    query = "SELECT id, hash, tx_id, value, bytes FROM datum WHERE hash = %s;"

    with execute(query=query, vars=(rf"\x{datum_hash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield DatumDBRow(*result)


def query_cost_model(model_id: int = -1, epoch_no: int = -1) -> dict[str, dict[str, tp.Any]]:
    """Query cost model record in db-sync.

    If `model_id` is specified, query the cost model that corresponds to the given id.
    If `epoch_no` is specified, query the cost model used in the given epoch.
    Otherwise query the latest cost model.
    """
    query_var: int | str

    if model_id != -1:
        subquery = "WHERE cm.id = %s "
        query_var = model_id
    elif epoch_no != -1:
        subquery = (
            "INNER JOIN epoch_param ON epoch_param.cost_model_id = cm.id "
            "WHERE epoch_param.epoch_no = %s "
        )
        query_var = epoch_no
    else:
        subquery = ""
        query_var = ""

    query = f"SELECT * FROM cost_model AS cm {subquery} ORDER BY cm.id DESC LIMIT 1"

    with execute(query=query, vars=(query_var,)) as cur:
        results = cur.fetchone()
        cost_model: dict[str, dict[str, tp.Any]] = results[1] if results else {}
        return cost_model


def query_param_proposal(txhash: str = "") -> ParamProposalDBRow:
    """Query param proposal record in db-sync.

    If txhash is not provided the query will return the last record available.
    """
    if txhash:
        hash_query = "WHERE tx.hash = %s "
        query_var: str = rf"\x{txhash}"
    else:
        hash_query = ""
        query_var = ""

    query = (
        "SELECT"
        " p.id, p.epoch_no, p.key, p.min_fee_a, p.min_fee_b, p.max_block_size,"
        " p.max_tx_size, p.max_bh_size, p.key_deposit, p.pool_deposit, p.max_epoch,"
        " p.optimal_pool_count, p.influence, p.monetary_expand_rate, p.treasury_growth_rate,"
        " p.decentralisation, p.entropy, p.protocol_major, p.protocol_minor, p.min_utxo_value,"
        " p.min_pool_cost, p.coins_per_utxo_size, p.cost_model_id, p.price_mem, p.price_step,"
        " p.max_tx_ex_mem, p.max_tx_ex_steps, p.max_block_ex_mem, p.max_block_ex_steps,"
        " p.max_val_size, p.collateral_percent, p.max_collateral_inputs, p.registered_tx_id,"
        " p.pvt_motion_no_confidence, p.pvt_committee_normal, p.pvt_committee_no_confidence,"
        " p.pvt_hard_fork_initiation, p.dvt_motion_no_confidence, p.dvt_committee_normal,"
        " p.dvt_committee_no_confidence, p.dvt_update_to_constitution, p.dvt_hard_fork_initiation,"
        " p.dvt_p_p_network_group, p.dvt_p_p_economic_group, p.dvt_p_p_technical_group,"
        " p.dvt_p_p_gov_group, p.dvt_treasury_withdrawal, p.committee_min_size,"
        " p.committee_max_term_length, p.gov_action_lifetime, p.gov_action_deposit,"
        " p.drep_deposit, p.drep_activity, p.pvtpp_security_group,"
        " p.min_fee_ref_script_cost_per_byte "
        "FROM param_proposal AS p "
        "INNER JOIN tx ON tx.id = p.registered_tx_id "
        f"{hash_query}"
        "ORDER BY ID DESC LIMIT 1"
    )

    with execute(query=query, vars=(query_var,)) as cur:
        results = cur.fetchone()
        return ParamProposalDBRow(*results)


def query_extra_key_witness(txhash: str) -> tp.Generator[memoryview, None, None]:
    """Query extra key witness records in db-sync."""
    query = (
        "SELECT extra_key_witness.hash "
        "FROM extra_key_witness "
        "INNER JOIN tx ON tx.id = extra_key_witness.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield result[0]


def query_epoch(
    epoch_from: int = 0, epoch_to: int = 99999999
) -> tp.Generator[EpochDBRow, None, None]:
    """Query epoch records in db-sync."""
    query_vars = (epoch_from, epoch_to)

    query = (
        "SELECT"
        " epoch.id, epoch.out_sum, epoch.fees, epoch.tx_count, epoch.blk_count, epoch.no "
        "FROM epoch "
        "WHERE (no BETWEEN %s AND %s);"
    )

    with execute(query=query, vars=query_vars) as cur:
        while (result := cur.fetchone()) is not None:
            yield EpochDBRow(*result)


def query_committee_registration(
    cold_key: str,
) -> tp.Generator[CommitteeRegistrationDBRow, None, None]:
    """Query committee registration in db-sync."""
    query = (
        "SELECT cr.id, cr.tx_id, cr.cert_index, chc.raw, chh.raw "
        "FROM committee_registration AS cr "
        "INNER JOIN committee_hash AS chh ON chh.id = cr.hot_key_id "
        "INNER JOIN committee_hash AS chc ON chc.id = cr.cold_key_id "
        "WHERE chc.raw = %s;"
    )

    with execute(query=query, vars=(rf"\x{cold_key}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield CommitteeRegistrationDBRow(*result)


def query_committee_deregistration(
    cold_key: str,
) -> tp.Generator[CommitteeDeregistrationDBRow, None, None]:
    """Query committee registration in db-sync."""
    query = (
        "SELECT cd.id, cd.tx_id, cd.cert_index, cd.voting_anchor_id, committee_hash.raw "
        "FROM committee_de_registration AS cd "
        "INNER JOIN committee_hash ON committee_hash.id = cd.cold_key_id "
        "WHERE committee_hash.raw = %s;"
    )

    with execute(query=query, vars=(rf"\x{cold_key}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield CommitteeDeregistrationDBRow(*result)


def query_drep_registration(
    drep_hash: str, drep_deposit: int = 2000000
) -> tp.Generator[DrepRegistrationDBRow, None, None]:
    """Query drep registration in db-sync."""
    query = (
        "SELECT"
        " dr.id, dr.tx_id, dr.cert_index, dr.deposit, "
        " dr.drep_hash_id, dr.voting_anchor_id, "
        " dh.raw, dh.view, dh.has_script "
        "FROM drep_registration AS dr "
        "INNER JOIN drep_hash dh ON dh.id = dr.drep_hash_id "
        "WHERE dh.raw = %s "
        "AND dr.deposit = %s "
        "ORDER BY dr.tx_id;"
    )

    with execute(query=query, vars=(rf"\x{drep_hash}", drep_deposit)) as cur:
        while (result := cur.fetchone()) is not None:
            yield DrepRegistrationDBRow(*result)


def query_gov_action_proposal(
    txhash: str = "", type: str = ""
) -> tp.Generator[GovActionProposalDBRow, None, None]:
    """Query gov_action_proposal table in db-sync.

    If type is provided txhash will be ignored.
    """
    if type:
        gap_query = "gap.type = %s"
        query_var: str = type
    else:
        gap_query = "tx.hash = %s"
        query_var = rf"\x{txhash}"

    query = (
        "SELECT"
        " gap.id, gap.tx_id, gap.index, gap.prev_gov_action_proposal, gap.deposit,"
        " gap.return_address, gap.expiration, gap.voting_anchor_id, gap.type, gap.description,"
        " gap.param_proposal, gap.ratified_epoch, gap.enacted_epoch, gap.dropped_epoch,"
        " gap.expired_epoch "
        "FROM gov_action_proposal AS gap "
        "INNER JOIN tx ON tx.id = gap.tx_id "
        f"WHERE {gap_query};"
    )

    with execute(query=query, vars=(query_var,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield GovActionProposalDBRow(*result)


def query_voting_procedure(txhash: str) -> tp.Generator[VotingProcedureDBRow, None, None]:
    """Query voting_procedure table in db-sync."""
    query = (
        "SELECT"
        " vp.id, vp.voter_role, vp.committee_voter, vp.drep_voter, vp.pool_voter, vp.vote "
        "FROM voting_procedure AS vp "
        "INNER JOIN tx ON tx.id = vp.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield VotingProcedureDBRow(*result)


def query_new_committee_info(txhash: str) -> tp.Generator[NewCommitteeInfoDBRow, None, None]:
    """Query new committee proposed in db-sync."""
    query = (
        "SELECT"
        " committee.id, gap.tx_id, gap.index, committee.quorum_numerator,"
        " committee.quorum_denominator "
        "FROM committee "
        "INNER JOIN gov_action_proposal AS gap ON gap.id = committee.gov_action_proposal_id "
        "INNER JOIN tx ON tx.id = gap.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield NewCommitteeInfoDBRow(*result)


def query_committee_members(committee_id: int) -> tp.Generator[NewCommitteeMemberDBRow, None, None]:
    """Query committee members in db-sync."""
    query = (
        "SELECT"
        " cm.id, cm.committee_id, ch.raw, cm.expiration_epoch "
        "FROM committee_member AS cm "
        "INNER JOIN committee_hash AS ch ON ch.id = cm.committee_hash_id "
        "WHERE cm.committee_id = %s;"
    )

    with execute(query=query, vars=(committee_id,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield NewCommitteeMemberDBRow(*result)


def query_treasury_withdrawal(txhash: str) -> tp.Generator[TreasuryWithdrawalDBRow, None, None]:
    """Query treasury_withdrawal table in db-sync."""
    query = (
        "SELECT"
        " gap.id, gap.tx_id, gap.index, gap.expiration, gap.ratified_epoch, gap.enacted_epoch, "
        " gap.dropped_epoch, gap.expired_epoch, "
        " sa.view AS addr_view, tw.amount "
        "FROM gov_action_proposal AS gap "
        "INNER JOIN treasury_withdrawal AS tw ON tw.gov_action_proposal_id = gap.id "
        "INNER JOIN stake_address AS sa ON tw.stake_address_id = sa.id "
        "INNER JOIN tx ON tx.id = gap.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield TreasuryWithdrawalDBRow(*result)


def query_off_chain_vote_data(data_hash: str) -> tp.Generator[OffChainVoteDataDBRow, None, None]:
    """Query the off chain vote data in db-sync."""
    query = (
        "SELECT"
        " data.id, data.voting_anchor_id, data.hash, data.json, data.bytes, "
        "data.warning, data.language, data.comment, data.is_valid, "
        "auth.name, auth.witness_algorithm, auth.public_key, auth.signature, auth.warning, "
        "updt.id, updt.title, updt.uri, "
        "gov.id, gov.title, gov.abstract, gov.motivation, gov.rationale, "
        "ref.id, ref.label, ref.uri, ref.hash_digest, ref.hash_algorithm, "
        "va.url, va.data_hash, va.type, va.block_id "
        "FROM off_chain_vote_data data "
        "LEFT JOIN off_chain_vote_author auth ON data.id = auth.off_chain_vote_data_id "
        "LEFT JOIN off_chain_vote_external_update updt ON data.id = updt.off_chain_vote_data_id "
        "LEFT JOIN off_chain_vote_gov_action_data gov ON data.id = gov.off_chain_vote_data_id "
        "LEFT JOIN off_chain_vote_reference ref ON data.id = ref.off_chain_vote_data_id "
        "LEFT JOIN voting_anchor va ON data.voting_anchor_id = va.id "
        "WHERE data.hash = %s "
        "ORDER BY va.id, ref.id, auth.id, updt.id;"
    )

    with execute(query=query, vars=(rf"\x{data_hash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield OffChainVoteDataDBRow(*result)


def query_off_chain_vote_fetch_error(
    voting_anchor_id: int,
) -> tp.Generator[OffChainVoteFetchErrorDBRow, None, None]:
    """Query off_chain_vote_fetch_error table in db-sync."""
    query = (
        "SELECT"
        " id, voting_anchor_id, fetch_error "
        "FROM off_chain_vote_fetch_error "
        "WHERE off_chain_vote_fetch_error.voting_anchor_id = %s;"
    )

    with execute(query=query, vars=(voting_anchor_id,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield OffChainVoteFetchErrorDBRow(*result)


def query_off_chain_vote_drep_data(
    voting_anchor_id: int,
) -> tp.Generator[OffChainVoteDrepDataDBRow, None, None]:
    """Query off_chain_vote_drep_data table in db-sync."""
    query = (
        "SELECT"
        " vd.id, vd.hash, vd.language, vd.comment, vd.json, vd.bytes, vd.warning, vd.is_valid,"
        " drep.payment_address, drep.given_name, drep.objectives, drep.motivations,"
        " drep.qualifications, drep.image_url, drep.image_hash "
        "FROM off_chain_vote_drep_data AS drep "
        "INNER JOIN off_chain_vote_data AS vd ON vd.id = drep.off_chain_vote_data_id "
        "WHERE vd.voting_anchor_id = %s;"
    )

    with execute(query=query, vars=(voting_anchor_id,)) as cur:
        while (result := cur.fetchone()) is not None:
            yield OffChainVoteDrepDataDBRow(*result)


def query_db_size() -> int:
    """Query database size in MB in db-sync."""
    instance_num = cluster_nodes.get_instance_num()
    query = f"SELECT pg_database_size('{configuration.DBSYNC_DB}{instance_num}')/1024/1024;"

    with execute(query=query) as cur:
        result = cur.fetchone()
        return int(result[0])


def query_delegation_vote(txhash: str) -> tp.Generator[DelegationVoteDBRow, None, None]:
    """Query delegation_vote table in db-sync."""
    query = (
        "SELECT"
        " delegation_vote.id, stake_address.view, drep_hash.view "
        "FROM delegation_vote "
        "INNER JOIN stake_address ON stake_address.id = delegation_vote.addr_id "
        "INNER JOIN drep_hash ON drep_hash.id = delegation_vote.drep_hash_id "
        "INNER JOIN tx ON tx.id = delegation_vote.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield DelegationVoteDBRow(*result)


def query_new_constitution(txhash: str) -> tp.Generator[NewConstitutionInfoDBRow, None, None]:
    """Query new constitution proposed in db-sync."""
    query = (
        "SELECT"
        " constitution.id, constitution.script_hash, gap.type,"
        " gap.id, gap.tx_id, gap.index "
        "FROM constitution "
        "INNER JOIN gov_action_proposal AS gap ON gap.id = constitution.gov_action_proposal_id "
        "INNER JOIN tx ON tx.id = gap.tx_id "
        "WHERE tx.hash = %s;"
    )

    with execute(query=query, vars=(rf"\x{txhash}",)) as cur:
        while (result := cur.fetchone()) is not None:
            yield NewConstitutionInfoDBRow(*result)


def query_drep_distr(
    drep_hash: str, epoch_no: int
) -> tp.Generator[DrepDistributionDBRow, None, None]:
    """Query drep voting power in db-sync."""
    query = (
        "SELECT"
        " drep_distr.id, drep_distr.hash_id, drep_distr.amount, drep_distr.epoch_no,"
        " drep_hash.view "
        "FROM drep_distr "
        "INNER JOIN drep_hash ON drep_hash.id = drep_distr.hash_id "
        "WHERE drep_hash.view = %s AND drep_distr.epoch_no = %s "
    )

    with execute(query=query, vars=(drep_hash, epoch_no)) as cur:
        while (result := cur.fetchone()) is not None:
            yield DrepDistributionDBRow(*result)


def delete_reserved_pool_tickers() -> int:
    """Delete all records from reserved_pool_ticker and return the number of affected rows."""
    query = "DELETE FROM reserved_pool_ticker"

    with execute(query=query) as cur:
        affected_rows = cur.rowcount or 0
        dbsync_conn.conn().commit()
        return affected_rows
