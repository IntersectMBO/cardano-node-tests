from cardano_node_tests.utils.versions import VERSIONS


BUILD_USABLE = (
    VERSIONS.transaction_era >= VERSIONS.MARY and VERSIONS.transaction_era == VERSIONS.cluster_era
)
BUILD_SKIP_MSG = (
    f"cannot use `build` with cluster era '{VERSIONS.cluster_era_name}` "
    f"and TX era '{VERSIONS.transaction_era_name}'"
)
