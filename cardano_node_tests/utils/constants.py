from cardano_node_tests.utils.versions import VERSIONS


NETWORK_MAGIC_LOCAL = 42
DBSYNC_DB = "dbsync"

TESTNET_POOL_IDS = (
    "pool18yslg3q320jex6gsmetukxvzm7a20qd90wsll9anlkrfua38flr",
    "pool15sfcpy4tps5073gmra0e6tm2dgtrn004yr437qmeh44sgjlg2ex",
    "pool1csh8x6227uphxz67nr8qhmd8c7nsyct2ptn7t0yjkhqu7neauwu",
)

BUILD_USABLE = (
    VERSIONS.transaction_era >= VERSIONS.MARY and VERSIONS.transaction_era == VERSIONS.cluster_era
)
BUILD_SKIP_MSG = (
    f"cannot use `build` with cluster era '{VERSIONS.cluster_era_name}` "
    f"and TX era '{VERSIONS.transaction_era_name}'"
)
