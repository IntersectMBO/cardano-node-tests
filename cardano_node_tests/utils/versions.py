"""Cardano node version, cluster era, transaction era, db-sync version."""

import typing as tp

from packaging import version

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers


class Versions:
    """Cluster era, transaction era, node version info."""

    BYRON: tp.Final[int] = 1
    SHELLEY: tp.Final[int] = 2
    ALLEGRA: tp.Final[int] = 3
    MARY: tp.Final[int] = 4
    ALONZO: tp.Final[int] = 6
    BABBAGE: tp.Final[int] = 8
    CONWAY: tp.Final[int] = 9

    DEFAULT_CLUSTER_ERA: tp.Final[int] = 9
    DEFAULT_TX_ERA: tp.Final[int] = 9
    LAST_KNOWN_ERA: tp.Final[int] = 9

    MAP: tp.ClassVar[dict[int, str]] = {
        1: "byron",
        2: "shelley",
        3: "allegra",
        4: "mary",
        6: "alonzo",
        8: "babbage",
        9: "conway",
    }

    def __init__(self) -> None:
        self.cluster_era_name = configuration.CLUSTER_ERA or self.MAP[self.DEFAULT_CLUSTER_ERA]
        self.cluster_era = getattr(self, self.cluster_era_name.upper())

        self.command_era_name = configuration.COMMAND_ERA

        if self.command_era_name and self.command_era_name in self.MAP.values():
            self.transaction_era_name: str = self.command_era_name
        else:
            self.transaction_era_name = self.MAP[self.DEFAULT_TX_ERA]

        self.transaction_era = getattr(self, self.transaction_era_name.upper())

        node_version_db = self.get_cardano_node_version()
        self.node = version.parse(node_version_db["version"])
        self.ghc = node_version_db["ghc"]
        self.platform = node_version_db["platform"]
        self.git_rev = node_version_db["git_rev"]

        cli_version_db = self.get_cardano_cli_version()
        self.cli = version.parse(cli_version_db["version"])
        self.cli_ghc = cli_version_db["ghc"]
        self.cli_platform = cli_version_db["platform"]
        self.cli_git_rev = cli_version_db["git_rev"]

        dbsync_version_db = self.get_dbsync_version() if configuration.HAS_DBSYNC else {}
        self.dbsync = version.parse(dbsync_version_db.get("version") or "0")
        self.dbsync_ghc = dbsync_version_db.get("ghc")
        self.dbsync_platform = dbsync_version_db.get("platform")
        self.dbsync_git_rev = dbsync_version_db.get("git_rev")

        smash_version_db = self.get_smash_version() if configuration.HAS_SMASH else {}
        self.smash = version.parse(smash_version_db.get("version") or "0")
        self.smash_ghc = smash_version_db.get("ghc")
        self.smash_platform = smash_version_db.get("platform")
        self.smash_git_rev = smash_version_db.get("git_rev")

    def _get_cardano_version(self, *, version_str: str) -> dict:
        """Return version info for cardano-*."""
        env_info, git_info, *__ = version_str.splitlines()
        ver, platform, ghc, *__ = env_info.split(" - ")
        version_db = {
            "version": ver.split()[-1],
            "platform": platform,
            "ghc": ghc,
            "git_rev": git_info.split()[-1],
        }
        return version_db

    def get_cardano_node_version(self) -> dict:
        """Return version info for cardano-node."""
        out = helpers.run_command("cardano-node --version").decode().strip()
        return self._get_cardano_version(version_str=out)

    def get_cardano_cli_version(self) -> dict:
        """Return version info for cardano-cli."""
        out = helpers.run_command("cardano-cli --version").decode().strip()
        return self._get_cardano_version(version_str=out)

    def get_dbsync_version(self) -> dict:
        """Return version info for db-sync."""
        out = helpers.run_command("cardano-db-sync --version").decode().strip()
        return self._get_cardano_version(version_str=out)

    def get_smash_version(self) -> dict:
        """Return version info for smash."""
        out = helpers.run_command("cardano-smash-server --version").decode().strip()
        return self._get_cardano_version(version_str=out)

    def __repr__(self) -> str:
        return (
            f"<Versions: cluster_era={self.cluster_era}, "
            f"transaction_era={self.transaction_era}, "
            f"node={self.node}, "
            f"dbsync={self.dbsync},"
            f"smash={self.smash}>"
        )


# Versions don't change during test run, so it can be used as constant
VERSIONS = Versions()
