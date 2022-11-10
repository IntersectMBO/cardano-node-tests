"""Cardano node version, cluster era, transaction era."""
from packaging import version

from cardano_node_tests.utils import configuration
from cardano_node_tests.utils import helpers


class Versions:
    """Cluster era, transaction era, node version info."""

    BYRON = 1
    SHELLEY = 2
    ALLEGRA = 3
    MARY = 4
    ALONZO = 5
    BABBAGE = 6

    DEFAULT_CLUSTER_ERA = 6
    DEFAULT_TX_ERA = 6
    LAST_KNOWN_ERA = 6

    MAP = {1: "byron", 2: "shelley", 3: "allegra", 4: "mary", 5: "alonzo", 6: "babbage"}

    def __init__(self) -> None:
        self.cluster_era_name = configuration.CLUSTER_ERA or self.MAP[self.DEFAULT_CLUSTER_ERA]
        self.transaction_era_name = configuration.TX_ERA or self.MAP[self.DEFAULT_TX_ERA]

        self.cluster_era = getattr(self, self.cluster_era_name.upper())
        self.transaction_era = getattr(self, self.transaction_era_name.upper())

        cardano_version_db = self.get_cardano_version()
        self.node = version.parse(cardano_version_db["version"])
        self.ghc = cardano_version_db["ghc"]
        self.platform = cardano_version_db["platform"]
        self.git_rev = cardano_version_db["git_rev"]

        dbsync_version_db = self.get_dbsync_version() if configuration.HAS_DBSYNC else {}
        self.dbsync = version.parse(dbsync_version_db.get("version") or "0")
        self.dbsync_platform = dbsync_version_db.get("platform")
        self.dbsync_ghc = dbsync_version_db.get("ghc")
        self.dbsync_git_rev = dbsync_version_db.get("git_rev")

    def get_cardano_version(self) -> dict:
        """Return version info for cardano-node."""
        out = helpers.run_command("cardano-node --version").decode().strip()
        env_info, git_info, *__ = out.splitlines()
        node, platform, ghc, *__ = env_info.split(" - ")
        version_db = {
            "version": node.split()[-1],
            "platform": platform,
            "ghc": ghc,
            "git_rev": git_info.split()[-1],
        }
        return version_db

    def get_dbsync_version(self) -> dict:
        """Return version info for db-sync."""
        out = helpers.run_command(f"{configuration.DBSYNC_BIN} --version").decode().strip()
        env_info, git_info, *__ = out.splitlines()
        dbsync, platform, ghc, *__ = env_info.split(" - ")
        version_db = {
            "version": dbsync.split()[-1],
            "platform": platform,
            "ghc": ghc,
            "git_rev": git_info.split()[-1],
        }
        return version_db

    def __repr__(self) -> str:
        return (
            f"<Versions: cluster_era={self.cluster_era}, "
            f"transaction_era={self.transaction_era}, "
            f"node={self.node}, "
            f"dbsync={self.dbsync}>"
        )


# versions don't change during test run, so it can be used as constant
VERSIONS = Versions()
