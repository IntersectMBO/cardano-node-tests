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

    def __init__(self) -> None:
        cluster_era = configuration.CLUSTER_ERA or "mary"
        # if not specified otherwise, transaction era is the same as cluster era
        transaction_era = configuration.TX_ERA or cluster_era

        self.cluster_era = getattr(self, cluster_era.upper(), 1)
        self.transaction_era = getattr(self, transaction_era.upper(), 1)

        versions_db = self.get_cardano_version()
        self.node = version.parse(versions_db["cardano-node"])
        self.ghc = versions_db["ghc"]
        self.platform = versions_db["platform"]
        self.git_rev = versions_db["git_rev"]

    def get_cardano_version(self) -> dict:
        """Return version info for cardano-node."""
        out = helpers.run_command("cardano-node --version").decode().strip()
        env_info, git_info, *__ = out.splitlines()
        node, platform, ghc, *__ = env_info.split(" - ")
        version_db = {
            "cardano-node": node.split(" ")[-1],
            "platform": platform,
            "ghc": ghc,
            "git_rev": git_info.split(" ")[-1],
        }
        return version_db

    def __repr__(self) -> str:
        return (
            f"<Versions: cluster_era={self.cluster_era}, "
            f"transaction_era={self.transaction_era}, "
            f"node={self.node}>"
        )


# versions don't change during test run, so it can be used as constant
VERSIONS = Versions()
