"""Functionality for managing db-sync service."""

import dataclasses
import enum
import logging
import pathlib as pl
import shutil
import typing as tp

import yaml

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import cluster_scripts
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils import locking
from cardano_node_tests.utils import temptools

LOGGER = logging.getLogger(__name__)


class Table(enum.StrEnum):
    """Enum representing all tables in the Cardano db-sync database."""

    ADA_POTS = "ada_pots"
    ADDRESS = "address"
    BLOCK = "block"
    COLLATERAL_TX_IN = "collateral_tx_in"
    COLLATERAL_TX_OUT = "collateral_tx_out"
    COMMITTEE = "committee"
    COMMITTEE_DE_REGISTRATION = "committee_de_registration"
    COMMITTEE_HASH = "committee_hash"
    COMMITTEE_MEMBER = "committee_member"
    COMMITTEE_REGISTRATION = "committee_registration"
    CONSTITUTION = "constitution"
    COST_MODEL = "cost_model"
    DATUM = "datum"
    DELEGATION = "delegation"
    DELEGATION_VOTE = "delegation_vote"
    DELISTED_POOL = "delisted_pool"
    DREP_DISTR = "drep_distr"
    DREP_HASH = "drep_hash"
    DREP_REGISTRATION = "drep_registration"
    EPOCH = "epoch"
    EPOCH_PARAM = "epoch_param"
    EPOCH_STAKE = "epoch_stake"
    EPOCH_STAKE_PROGRESS = "epoch_stake_progress"
    EPOCH_STATE = "epoch_state"
    EPOCH_SYNC_TIME = "epoch_sync_time"
    EVENT_INFO = "event_info"
    EXTRA_KEY_WITNESS = "extra_key_witness"
    EXTRA_MIGRATIONS = "extra_migrations"
    GOV_ACTION_PROPOSAL = "gov_action_proposal"
    MA_TX_MINT = "ma_tx_mint"
    MA_TX_OUT = "ma_tx_out"
    META = "meta"
    MULTI_ASSET = "multi_asset"
    NEW_COMMITTEE = "new_committee"
    OFF_CHAIN_POOL_DATA = "off_chain_pool_data"
    OFF_CHAIN_POOL_FETCH_ERROR = "off_chain_pool_fetch_error"
    OFF_CHAIN_VOTE_AUTHOR = "off_chain_vote_author"
    OFF_CHAIN_VOTE_DATA = "off_chain_vote_data"
    OFF_CHAIN_VOTE_DREP_DATA = "off_chain_vote_drep_data"
    OFF_CHAIN_VOTE_EXTERNAL_UPDATE = "off_chain_vote_external_update"
    OFF_CHAIN_VOTE_FETCH_ERROR = "off_chain_vote_fetch_error"
    OFF_CHAIN_VOTE_GOV_ACTION_DATA = "off_chain_vote_gov_action_data"
    OFF_CHAIN_VOTE_REFERENCE = "off_chain_vote_reference"
    PARAM_PROPOSAL = "param_proposal"
    POOL_HASH = "pool_hash"
    POOL_METADATA_REF = "pool_metadata_ref"
    POOL_OWNER = "pool_owner"
    POOL_RELAY = "pool_relay"
    POOL_RETIRE = "pool_retire"
    POOL_STAT = "pool_stat"
    POOL_UPDATE = "pool_update"
    POT_TRANSFER = "pot_transfer"
    REDEEMER = "redeemer"
    REDEEMER_DATA = "redeemer_data"
    REFERENCE_TX_IN = "reference_tx_in"
    RESERVE = "reserve"
    RESERVED_POOL_TICKER = "reserved_pool_ticker"
    REVERSE_INDEX = "reverse_index"
    REWARD = "reward"
    REWARD_REST = "reward_rest"
    SCHEMA_VERSION = "schema_version"
    SCRIPT = "script"
    SLOT_LEADER = "slot_leader"
    STAKE_ADDRESS = "stake_address"
    STAKE_DEREGISTRATION = "stake_deregistration"
    STAKE_REGISTRATION = "stake_registration"
    TREASURY = "treasury"
    TREASURY_WITHDRAWAL = "treasury_withdrawal"
    TX = "tx"
    TX_CBOR = "tx_cbor"
    TX_IN = "tx_in"
    TX_METADATA = "tx_metadata"
    TX_OUT = "tx_out"
    VOTING_ANCHOR = "voting_anchor"
    VOTING_PROCEDURE = "voting_procedure"


class Column:
    class Tx(enum.StrEnum):
        FEE = "tx.fee"

    class Redeemer(enum.StrEnum):
        SCRIPT_HASH = "redeemer.script_hash"


class SettingState(enum.StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"


class TxOutMode(enum.StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"
    CONSUMED = "consumed"
    PRUNE = "prune"
    BOOTSTRAP = "bootstrap"


class LedgerMode(enum.StrEnum):
    ENABLE = "enable"
    DISABLE = "disable"
    IGNORE = "ignore"


class Preset(enum.StrEnum):
    FULL = "full"
    ONLY_UTXO = "only_utxo"
    ONLY_GOVERNANCE = "only_governance"
    DISABLE_ALL = "disable_all"


@dataclasses.dataclass
class TxOutConfig:
    value: TxOutMode = TxOutMode.ENABLE
    force_tx_in: bool | None = None
    use_address_table: bool | None = None


@dataclasses.dataclass
class ShelleyConfig:
    enable: bool = True


@dataclasses.dataclass
class MultiAssetConfig:
    enable: bool = True


@dataclasses.dataclass
class MetadataConfig:
    enable: bool = True
    keys: list[int] | None = None


@dataclasses.dataclass
class PlutusConfig:
    enable: bool = True


class DBSyncConfigBuilder:
    def __init__(self) -> None:
        self._config = {
            "tx_cbor": SettingState.DISABLE,
            "tx_out": TxOutConfig(),
            "ledger": LedgerMode.ENABLE,
            "shelley": ShelleyConfig(),
            "multi_asset": MultiAssetConfig(),
            "metadata": MetadataConfig(),
            "plutus": PlutusConfig(),
            "governance": SettingState.ENABLE,
            "offchain_pool_data": SettingState.ENABLE,
            "pool_stat": SettingState.ENABLE,
            "remove_jsonb_from_schema": SettingState.DISABLE,
        }
        self._preset_applied = False

    def with_preset(self, preset: Preset) -> tp.Self:
        self._preset_applied = True

        if preset == Preset.FULL:
            self._config.update(
                {
                    "tx_cbor": SettingState.DISABLE,
                    "tx_out": TxOutConfig(value=TxOutMode.ENABLE),
                    "ledger": LedgerMode.ENABLE,
                    "shelley": ShelleyConfig(enable=True),
                    "multi_asset": MultiAssetConfig(enable=True),
                    "metadata": MetadataConfig(enable=True),
                    "plutus": PlutusConfig(enable=True),
                    "governance": SettingState.ENABLE,
                    "offchain_pool_data": SettingState.ENABLE,
                    "pool_stat": SettingState.ENABLE,
                }
            )
        elif preset == Preset.ONLY_UTXO:
            self._config.update(
                {
                    "tx_cbor": SettingState.DISABLE,
                    "tx_out": TxOutConfig(value=TxOutMode.BOOTSTRAP),
                    "ledger": LedgerMode.IGNORE,
                    "shelley": ShelleyConfig(enable=False),
                    "metadata": MetadataConfig(enable=False),
                    "multi_asset": MultiAssetConfig(enable=True),
                    "plutus": PlutusConfig(enable=False),
                    "governance": SettingState.DISABLE,
                    "offchain_pool_data": SettingState.DISABLE,
                    "pool_stat": SettingState.DISABLE,
                }
            )
        elif preset == Preset.ONLY_GOVERNANCE:
            self._config.update(
                {
                    "tx_cbor": SettingState.DISABLE,
                    "tx_out": TxOutConfig(value=TxOutMode.DISABLE),
                    "ledger": LedgerMode.ENABLE,
                    "shelley": ShelleyConfig(enable=False),
                    "multi_asset": MultiAssetConfig(enable=False),
                    "plutus": PlutusConfig(enable=False),
                    "governance": SettingState.ENABLE,
                    "offchain_pool_data": SettingState.DISABLE,
                    "pool_stat": SettingState.ENABLE,
                }
            )
        elif preset == Preset.DISABLE_ALL:
            self._config.update(
                {
                    "tx_cbor": SettingState.DISABLE,
                    "tx_out": TxOutConfig(value=TxOutMode.DISABLE),
                    "ledger": LedgerMode.DISABLE,
                    "shelley": ShelleyConfig(enable=False),
                    "multi_asset": MultiAssetConfig(enable=False),
                    "plutus": PlutusConfig(enable=False),
                    "governance": SettingState.DISABLE,
                    "offchain_pool_data": SettingState.DISABLE,
                    "pool_stat": SettingState.DISABLE,
                }
            )

        return self

    def with_tx_cbor(self, value: SettingState) -> tp.Self:
        if not self._preset_applied:
            self._config["tx_cbor"] = value
        return self

    def with_tx_out(
        self,
        value: TxOutMode,
        force_tx_in: bool | None = None,
        use_address_table: bool | None = None,
    ) -> tp.Self:
        if not self._preset_applied:
            self._config["tx_out"] = TxOutConfig(
                value=value, force_tx_in=force_tx_in, use_address_table=use_address_table
            )
        return self

    def with_ledger(self, value: LedgerMode) -> tp.Self:
        if not self._preset_applied:
            self._config["ledger"] = value
        return self

    def with_shelley(self, enable: bool) -> tp.Self:
        if not self._preset_applied:
            self._config["shelley"] = ShelleyConfig(enable=enable)
        return self

    def with_multi_asset(self, enable: bool) -> tp.Self:
        if not self._preset_applied:
            self._config["multi_asset"] = MultiAssetConfig(enable=enable)
        return self

    def with_metadata(self, enable: bool, keys: list[int] | None = None) -> tp.Self:
        if not self._preset_applied:
            self._config["metadata"] = MetadataConfig(enable=enable, keys=keys)
        return self

    def with_plutus(self, enable: bool) -> tp.Self:
        if not self._preset_applied:
            self._config["plutus"] = PlutusConfig(enable=enable)
        return self

    def with_governance(self, value: SettingState) -> tp.Self:
        if not self._preset_applied:
            self._config["governance"] = value
        return self

    def with_offchain_pool_data(self, value: SettingState) -> tp.Self:
        if not self._preset_applied:
            self._config["offchain_pool_data"] = value
        return self

    def with_pool_stat(self, value: SettingState) -> tp.Self:
        if not self._preset_applied:
            self._config["pool_stat"] = value
        return self

    def with_remove_jsonb_from_schema(self, value: SettingState) -> tp.Self:
        if not self._preset_applied:
            self._config["remove_jsonb_from_schema"] = value
        return self

    def build(self) -> dict[str, tp.Any]:
        tx_out = tp.cast(TxOutConfig, self._config["tx_out"])
        shelley = tp.cast(ShelleyConfig, self._config["shelley"])
        multi_asset = tp.cast(MultiAssetConfig, self._config["multi_asset"])
        metadata = tp.cast(MetadataConfig, self._config["metadata"])
        plutus = tp.cast(PlutusConfig, self._config["plutus"])

        config: dict[str, tp.Any] = {
            "tx_cbor": self._enum_to_value(self._config["tx_cbor"]),
            "tx_out": {
                "value": self._enum_to_value(tx_out.value),
                **(self._optional("force_tx_in", tx_out.force_tx_in)),
                **(self._optional("use_address_table", tx_out.use_address_table)),
            },
            "ledger": self._enum_to_value(self._config["ledger"]),
            "shelley": {"enable": shelley.enable},
            "multi_asset": {"enable": multi_asset.enable},
            "metadata": {
                "enable": metadata.enable,
                **(self._optional("keys", metadata.keys)),
            },
            "plutus": {"enable": plutus.enable},
            "governance": self._enum_to_value(self._config["governance"]),
            "offchain_pool_data": self._enum_to_value(self._config["offchain_pool_data"]),
            "pool_stat": self._enum_to_value(self._config["pool_stat"]),
            "remove_jsonb_from_schema": self._enum_to_value(
                self._config["remove_jsonb_from_schema"]
            ),
        }

        return config

    def _optional(self, key: str, value: tp.Any) -> dict[str, tp.Any]:
        if value is not None:
            return {key: self._enum_to_value(value) if hasattr(value, "value") else value}
        return {}

    def _enum_to_value(self, value: tp.Any) -> tp.Any:
        return value.value if hasattr(value, "value") else value


class DBSyncManager:
    def __init__(self) -> None:
        """Initialize the DB Sync manager with default config."""
        self.shared_tmp = temptools.get_pytest_shared_tmp()

    def recreate_database(self) -> None:
        """Reinitializes the PostgreSQL database by running the `postgres-setup.sh` script.

        !!! WARNING !!!
        This is a DESTRUCTIVE operation!
        It will delete existing data and recreate the database from scratch.
        """
        scripts_dir = cluster_scripts.get_testnet_variant_scriptdir(
            testnet_variant=configuration.TESTNET_VARIANT
        )
        if not scripts_dir:
            err = f"Testnet variant '{configuration.TESTNET_VARIANT}' scripts directory not found."
            raise RuntimeError(err)

        db_script_template = scripts_dir / "postgres-setup.sh"
        if not db_script_template.exists() and configuration.BOOTSTRAP_DIR:
            db_script_template = pl.Path(configuration.BOOTSTRAP_DIR) / "postgres-setup.sh"

        if not db_script_template.exists():
            err = f"Database setup script '{db_script_template}' not found."
            raise RuntimeError(err)

        cluster_dir = cluster_nodes.get_cluster_env().state_dir
        db_script_path = cluster_dir / "postgres-setup.sh"
        shutil.copy2(src=db_script_template, dst=db_script_path)
        db_script_path.chmod(0o755)
        helpers.run_command("./postgres-setup.sh", workdir=cluster_dir)

    def get_config_builder(self) -> DBSyncConfigBuilder:
        """Get a fresh `DBSyncConfigBuilder` config builder instance with default config."""
        return DBSyncConfigBuilder()

    def update_config(self, config: dict | DBSyncConfigBuilder | None) -> pl.Path:
        """
        Update the `dbsync-config.yaml` file with new settings.

        Args:
            config: Config dict or `DBSyncConfigBuilder` instance.

        Returns:
            Path to the modified config file.
        """
        cluster_dir = cluster_nodes.get_cluster_env().state_dir
        config_file = cluster_dir / "dbsync-config.yaml"

        if not config:
            return config_file

        if isinstance(config, DBSyncConfigBuilder):
            config = config.build()
        else:
            default_config = DBSyncConfigBuilder().build()
            config = {**default_config, **config}

        LOGGER.debug(f"Effective config: {config}")

        with open(config_file, encoding="utf-8") as fp_in:
            current_dbsync_config: dict = yaml.safe_load(fp_in) or {}

        current_dbsync_config["insert_options"] = config

        with open(config_file, "w", encoding="utf-8") as fp_out:
            yaml.safe_dump(current_dbsync_config, fp_out)

        return config_file

    def is_db_sync_running(self) -> bool:
        """Check if the `cardano-db-sync` service is actively running.

        Returns:
            bool: True if the service is running and executable, False otherwise.
        """
        if not shutil.which("cardano-db-sync"):
            return False
        return cluster_nodes.services_status(service_names=["dbsync"])[0].status == "RUNNING"

    def stop_db_sync(self) -> None:
        """Stop `cardano-db-sync` service."""
        cluster_nodes.services_action(
            service_names=["dbsync"], action="stop", instance_num=cluster_nodes.get_instance_num()
        )

    def start_db_sync(self) -> None:
        """Start `cardano-db-sync` service."""
        cluster_nodes.services_action(
            service_names=["dbsync"], action="start", instance_num=cluster_nodes.get_instance_num()
        )

    def restart_with_config(
        self, custom_config: dict | DBSyncConfigBuilder | None = None
    ) -> pl.Path:
        """
        Restart `cardano-db-sync` with a new configuration, ensuring atomic updates.

        Steps:
            1. Stops the service.
            2. Recreates the database.
            3. Applies the new config.
            4. Restarts the service and waits for sync completion.

        Args:
            custom_config: Either a config dict or `DBSyncConfigBuilder` instance.

        Returns:
            Path to the updated config file.
        """
        with locking.FileLockIfXdist(f"{self.shared_tmp}/db_sync_config.lock"):
            self.stop_db_sync()
            self.recreate_database()
            config_file = self.update_config(config=custom_config)
            self.start_db_sync()
            assert self.is_db_sync_running(), "Error: db-sync service is not running!"
            dbsync_utils.wait_for_db_sync_completion()
            return config_file
