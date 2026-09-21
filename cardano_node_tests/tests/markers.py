"""Pytest markers, `skipif` conditions and parametrization shared by tests."""

import enum

import pytest

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import configuration
from cardano_node_tests.utils.versions import VERSIONS
from cardano_node_tests.utils.versions import EraName

_COMPAT_ERAS = (
    EraName.SHELLEY,
    EraName.ALLEGRA,
    EraName.MARY,
    EraName.ALONZO,
    EraName.BABBAGE,
)

ORDER5_BYRON = (
    pytest.mark.order(5) if "_fast" not in configuration.TESTNET_VARIANT else pytest.mark.noop
)
LONG_BYRON = pytest.mark.long if "_fast" not in configuration.TESTNET_VARIANT else pytest.mark.noop


class XdSplits(enum.StrEnum):
    """Known split keys for the ``xdist_split`` pytest marker.

    Warning:
        Do not combine this marker with ``cluster_manager.get(marker=...)``. The two
        work against each other: ``marker=`` groups tagged tests onto the same cluster
        instance so they run close together, while ``xdist_split`` spreads them across
        workers to keep them apart.

    Each member names a heavy shared resource. Tests that lock such a resource
    declare the matching key (or several, as separate positional args) so the
    custom xdist scheduler caps concurrent in-flight tests sharing the key at
    the cluster instance capacity, instead of letting workers stall on the
    cluster manager. See ``cardano_node_tests.pytest_plugins.xdist_scheduler``.

    Example:
        ``@pytest.mark.xdist_split(markers.XdSplits.governance, markers.XdSplits.heavy)``

    Members:
        governance: Governance setup (committee, DReps, constitution, etc.).
        heavy: Tests that require a lot of locked cluster resources.
    """

    governance = "governance"
    heavy = "heavy"


_BLD_SKIP_REASON = ""
if VERSIONS.transaction_era != VERSIONS.cluster_era:
    _BLD_SKIP_REASON = "transaction era must be the same as node era"
BUILD_UNUSABLE = bool(_BLD_SKIP_REASON)

# Common `skipif`s
SKIPIF_BUILD_UNUSABLE = pytest.mark.skipif(
    BUILD_UNUSABLE,
    reason=(
        f"cannot use `build` with Tx era '{VERSIONS.transaction_era_name}': {_BLD_SKIP_REASON}"
    ),
)

SKIPIF_BUILD_EST_1199 = pytest.mark.skipif(
    True,  # We don't want to execute `issues.cli_1199.is_blocked()` during import time
    reason="`build-estimate` fails to balance tx with no txouts",
)

SKIPIF_WRONG_ERA = pytest.mark.skipif(
    not (
        VERSIONS.cluster_era >= VERSIONS.DEFAULT_CLUSTER_ERA
        and VERSIONS.transaction_era == VERSIONS.cluster_era
    ),
    reason="meant to run with default era or higher, where cluster era == Tx era",
)

SKIPIF_TOKENS_UNUSABLE = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.MARY_FIRST,
    reason="native tokens are available only in Mary+ eras",
)

_PLUTUS_SKIP_REASON = ""
if VERSIONS.transaction_era < VERSIONS.ALONZO_FIRST:
    _PLUTUS_SKIP_REASON = "Plutus is available only in Alonzo+ eras"
SKIPIF_PLUTUS_UNUSABLE = pytest.mark.skipif(
    bool(_PLUTUS_SKIP_REASON),
    reason=_PLUTUS_SKIP_REASON,
)

SKIPIF_PLUTUSV2_UNUSABLE = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.BABBAGE_FIRST,
    reason="Plutus V2 is available only in Babbage+ eras",
)

_PLUTUSV3_SKIP_REASON = ""
if VERSIONS.transaction_era < VERSIONS.CONWAY_FIRST:
    _PLUTUSV3_SKIP_REASON = "Plutus V3 is available only in Conway+ eras"
_PLUTUSV3_UNUSABLE = bool(_PLUTUSV3_SKIP_REASON)
SKIPIF_PLUTUSV3_UNUSABLE = pytest.mark.skipif(
    _PLUTUSV3_UNUSABLE,
    reason=_PLUTUSV3_SKIP_REASON,
)

SKIPIF_ON_TESTNET = pytest.mark.skipif(
    not cluster_nodes.get_cluster_type().is_local,
    reason="not supposed to run on long-running testnet",
)

SKIPIF_ON_LOCAL = pytest.mark.skipif(
    cluster_nodes.get_cluster_type().is_local,
    reason="supposed to run on long-running testnet",
)


# Common parametrization

PARAM_BUILD_METHOD = pytest.mark.parametrize(
    "build_method",
    (
        clusterlib_utils.BuildMethods.BUILD_RAW,
        pytest.param(clusterlib_utils.BuildMethods.BUILD, marks=SKIPIF_BUILD_UNUSABLE),
        clusterlib_utils.BuildMethods.BUILD_EST,
    ),
)

PARAM_BUILD_METHOD_NO_EST = pytest.mark.parametrize(
    "build_method",
    (
        clusterlib_utils.BuildMethods.BUILD_RAW,
        pytest.param(
            clusterlib_utils.BuildMethods.BUILD,
            marks=SKIPIF_BUILD_UNUSABLE,
        ),
        pytest.param(
            clusterlib_utils.BuildMethods.BUILD_EST,
            marks=SKIPIF_BUILD_EST_1199,
        ),
    ),
)

PARAM_PLUTUS_VERSION = pytest.mark.parametrize(
    "plutus_version",
    (
        "v1",
        pytest.param("v2", marks=SKIPIF_PLUTUSV2_UNUSABLE),
    ),
    ids=("plutus_v1", "plutus_v2"),
)

PARAM_PLUTUS3_VERSION = pytest.mark.parametrize(
    "plutus_version",
    (
        "v1",
        pytest.param("v2", marks=SKIPIF_PLUTUSV2_UNUSABLE),
        pytest.param("v3", marks=SKIPIF_PLUTUSV3_UNUSABLE),
    ),
    ids=("plutus_v1", "plutus_v2", "plutus_v3"),
)

PARAM_PLUTUS2ONWARDS_VERSION = pytest.mark.parametrize(
    "plutus_version",
    (
        "v2",
        pytest.param("v3", marks=SKIPIF_PLUTUSV3_UNUSABLE),
    ),
    ids=("plutus_v2", "plutus_v3"),
)

PARAM_COMPAT_ERAS = pytest.mark.parametrize("era", _COMPAT_ERAS)
