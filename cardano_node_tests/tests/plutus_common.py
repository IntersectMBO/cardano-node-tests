import dataclasses
import itertools
import pathlib as pl

import pytest
from cardano_clusterlib import clusterlib
from packaging import version

from cardano_node_tests.utils import cluster_nodes
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

DATA_DIR = pl.Path(__file__).parent / "data"
PLUTUS_DIR = DATA_DIR / "plutus"
SCRIPTS_V1_DIR = PLUTUS_DIR / "v1"
SCRIPTS_V2_DIR = PLUTUS_DIR / "v2"
SCRIPTS_V3_DIR = PLUTUS_DIR / "v3"
SCRIPTS_V3_BATCH6_100_DIR = SCRIPTS_V3_DIR / "batch6" / "1.0.0"
SCRIPTS_V3_BATCH6_110_DIR = SCRIPTS_V3_DIR / "batch6" / "1.1.0"

SEPC256K1_ECDSA_DIR = PLUTUS_DIR / "sepc256k1_ecdsa"
SEPC256K1_SCHNORR_DIR = PLUTUS_DIR / "sepc256k1_schnorr"

ALWAYS_SUCCEEDS_PLUTUS_V1 = SCRIPTS_V1_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS_V1 = SCRIPTS_V1_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS_V1 = SCRIPTS_V1_DIR / "custom-guess-42-datum-42.plutus"
GUESSING_GAME_UNTYPED_PLUTUS_V1 = SCRIPTS_V1_DIR / "guess-42-datum-42-txin.plutus"

ALWAYS_SUCCEEDS_PLUTUS_V2 = SCRIPTS_V2_DIR / "always-succeeds-spending.plutus"
ALWAYS_FAILS_PLUTUS_V2 = SCRIPTS_V2_DIR / "always-fails.plutus"
GUESSING_GAME_PLUTUS_V2 = SCRIPTS_V2_DIR / "custom-guess-42-datum-42.plutus"
GUESSING_GAME_UNTYPED_PLUTUS_V2 = SCRIPTS_V2_DIR / "guess-42-datum-42-txin.plutus"
SECP256K1_LOOP_ECDSA_PLUTUS_V2 = SCRIPTS_V2_DIR / "ecdsa-secp256k1-loop.plutus"
SECP256K1_LOOP_SCHNORR_PLUTUS_V2 = SCRIPTS_V2_DIR / "schnorr-secp256k1-loop.plutus"
BYTE_STRING_ROUNDTRIP = SCRIPTS_V2_DIR / "byteStringToIntegerRoundtripPolicyV2.plutus"

ALWAYS_SUCCEEDS_PLUTUS_V3 = SCRIPTS_V3_DIR / "alwaysSucceedPolicyScriptV3.plutus"
ALWAYS_FAILS_PLUTUS_V3 = SCRIPTS_V3_DIR / "alwaysFailsPolicyScriptV3.plutus"

MINTING_PLUTUS_V1 = SCRIPTS_V1_DIR / "anyone-can-mint.plutus"
MINTING_TIME_RANGE_PLUTUS_V1 = SCRIPTS_V1_DIR / "time_range.plutus"
MINTING_WITNESS_REDEEMER_PLUTUS_V1 = SCRIPTS_V1_DIR / "witness-redeemer.plutus"
MINTING_TOKENNAME_PLUTUS_V1 = SCRIPTS_V1_DIR / "mint-tokenname.plutus"

MINTING_PLUTUS_V2 = SCRIPTS_V2_DIR / "anyone-can-mint.plutus"
MINTING_CHECK_REF_INPUTS_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-with-reference-inputs.plutus"
MINTING_CHECK_DATUM_HASH_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-datum-hash.plutus"
MINTING_CHECK_REF_SCRIPTS_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-with-reference-scripts.plutus"
MINTING_CHECK_INLINE_DATUM_PLUTUS_V2 = SCRIPTS_V2_DIR / "check-mint-inline-datum.plutus"
MINTING_SECP256K1_ECDSA_PLUTUS_V2 = SCRIPTS_V2_DIR / "secp256k1-ecdsa-policy.plutus"
MINTING_SECP256K1_SCHNORR_PLUTUS_V2 = SCRIPTS_V2_DIR / "secp256k1-schnorr-policy.plutus"

MINTING_PLUTUS_V3 = SCRIPTS_V3_DIR / "alwaysSucceedPolicyScriptV3.plutus"
MINTING_SECP256K1_ECDSA_PLUTUS_V3 = SCRIPTS_V3_DIR / "verifyEcdsaPolicyScriptV3.plutus"
MINTING_SECP256K1_SCHNORR_PLUTUS_V3 = SCRIPTS_V3_DIR / "verifySchnorrPolicyScriptV3.plutus"
MINTING_TIME_RANGE_PLUTUS_V3 = SCRIPTS_V3_DIR / "timeRangePolicyScriptV3.plutus"
MINTING_WITNESS_REDEEMER_PLUTUS_V3 = SCRIPTS_V3_DIR / "witnessRedeemerPolicyScriptV3.plutus"

STAKE_GUESS_42_PLUTUS_V1 = SCRIPTS_V1_DIR / "guess-42-stake.plutus"

STAKE_PLUTUS_V2 = SCRIPTS_V2_DIR / "stake-script.plutus"

REDEEMER_42 = PLUTUS_DIR / "42.redeemer"
REDEEMER_42_TYPED = PLUTUS_DIR / "typed-42.redeemer"
REDEEMER_42_CBOR = PLUTUS_DIR / "42.redeemer.cbor"
REDEEMER_42_TYPED_CBOR = PLUTUS_DIR / "typed-42.redeemer.cbor"
REDEEMER_43_TYPED = PLUTUS_DIR / "typed-43.redeemer"

DATUM_42 = PLUTUS_DIR / "42.datum"
DATUM_42_TYPED = PLUTUS_DIR / "typed-42.datum"
DATUM_42_CBOR = PLUTUS_DIR / "42.datum.cbor"
DATUM_42_TYPED_CBOR = PLUTUS_DIR / "typed-42.datum.cbor"
DATUM_43_TYPED = PLUTUS_DIR / "typed-43.datum"
DATUM_WITNESS_GOLDEN_NORMAL = PLUTUS_DIR / "witness_golden_normal.datum"
DATUM_WITNESS_GOLDEN_EXTENDED = PLUTUS_DIR / "witness_golden_extended.datum"
DATUM_BIG = PLUTUS_DIR / "big.datum"
DATUM_FINITE_TYPED_CBOR = PLUTUS_DIR / "typed-finite.datum.cbor"

SIGNING_KEY_GOLDEN = DATA_DIR / "golden_normal.skey"
SIGNING_KEY_GOLDEN_EXTENDED = DATA_DIR / "golden_extended.skey"


@dataclasses.dataclass(frozen=True, order=True)
class ExecutionCost:
    per_time: int
    per_space: int
    fixed_cost: int


# Scripts execution cost for Txs with single UTxO input and single Plutus script
ALWAYS_FAILS_COST = ExecutionCost(per_time=476_468, per_space=1_700, fixed_cost=133)
ALWAYS_SUCCEEDS_COST = ExecutionCost(per_time=368_100, per_space=1_700, fixed_cost=125)
GUESSING_GAME_COST = ExecutionCost(per_time=282_016_214, per_space=1_034_516, fixed_cost=80_025)
GUESSING_GAME_UNTYPED_COST = ExecutionCost(per_time=4_985_806, per_space=11_368, fixed_cost=1_016)

ALWAYS_FAILS_V2_COST = ExecutionCost(per_time=230_100, per_space=1_100, fixed_cost=81)
ALWAYS_SUCCEEDS_V2_COST = ExecutionCost(per_time=230_100, per_space=1_100, fixed_cost=81)
GUESSING_GAME_V2_COST = ExecutionCost(per_time=200_253_161, per_space=637_676, fixed_cost=51_233)
GUESSING_GAME_UNTYPED_V2_COST = ExecutionCost(
    per_time=4_985_806, per_space=11_368, fixed_cost=1_016
)
BYTE_STRING_ROUNDTRIP_V2_COST = ExecutionCost(
    per_time=168_868_800, per_space=540_612, fixed_cost=43_369
)
SECP256K1_ECDSA_LOOP_COST = ExecutionCost(
    per_time=470_000_000, per_space=128_584, fixed_cost=36_106
)
SECP256K1_SCHNORR_LOOP_COST = ExecutionCost(
    per_time=470_000_000, per_space=128_584, fixed_cost=38_455
)

ALWAYS_FAILS_V3_COST = ExecutionCost(per_time=230_100, per_space=1_100, fixed_cost=81)
ALWAYS_SUCCEEDS_V3_COST = ExecutionCost(per_time=64_100, per_space=500, fixed_cost=34)

MINTING_COST = ExecutionCost(per_time=259_868_784, per_space=978_434, fixed_cost=74_960)
MINTING_TIME_RANGE_COST = ExecutionCost(
    per_time=277_239_670, per_space=1_044_064, fixed_cost=80_232
)
MINTING_WITNESS_REDEEMER_COST = ExecutionCost(
    per_time=261_056_789, per_space=1_013_630, fixed_cost=75_278
)
MINTING_TOKENNAME_COST = ExecutionCost(per_time=162_418_952, per_space=539_860, fixed_cost=42_861)

MINTING_V2_COST = ExecutionCost(per_time=167_089_597, per_space=537_352, fixed_cost=43_053)
MINTING_V2_REF_COST = ExecutionCost(per_time=198_080_433, per_space=633_678, fixed_cost=50_845)
MINTING_V2_CHECK_REF_INPUTS_COST = ExecutionCost(
    per_time=214_916_514, per_space=696_858, fixed_cost=55_705
)
MINTING_V2_CHECK_DATUM_HASH_COST = ExecutionCost(
    per_time=244_944_118, per_space=797_302, fixed_cost=63_665
)
MINTING_V2_CHECK_REF_SCRIPTS_COST = ExecutionCost(
    per_time=208_713_230, per_space=678_512, fixed_cost=54_199
)
MINTING_V2_CHECK_INLINE_DATUM_COST = ExecutionCost(
    per_time=208_093_920, per_space=674_744, fixed_cost=53_937
)

MINTING_V3_COST = ExecutionCost(per_time=64_100, per_space=500, fixed_cost=34)
MINTING_V3_REF_COST = ExecutionCost(per_time=64_100, per_space=500, fixed_cost=34)
MINTING_V3_TIME_RANGE_COST = ExecutionCost(
    per_time=85_986_410, per_space=350_762, fixed_cost=28_174
)
MINTING_V3_WITNESS_REDEEMER_COST = ExecutionCost(
    per_time=81_663_642, per_space=333_824, fixed_cost=26_920
)


@dataclasses.dataclass(frozen=True, order=True)
class PlutusScriptData:
    script_file: pl.Path
    script_type: str
    execution_cost: ExecutionCost


BYTE_STRING_ROUNDTRIP_V2_REC = PlutusScriptData(
    script_file=BYTE_STRING_ROUNDTRIP,
    script_type=clusterlib.ScriptTypes.PLUTUS_V2,
    execution_cost=BYTE_STRING_ROUNDTRIP_V2_COST,
)


ALWAYS_FAILS = {
    "v1": PlutusScriptData(
        script_file=ALWAYS_FAILS_PLUTUS_V1,
        script_type=clusterlib.ScriptTypes.PLUTUS_V1,
        execution_cost=ALWAYS_FAILS_COST,
    ),
    "v2": PlutusScriptData(
        script_file=ALWAYS_FAILS_PLUTUS_V2,
        script_type=clusterlib.ScriptTypes.PLUTUS_V2,
        execution_cost=ALWAYS_FAILS_V2_COST,
    ),
    "v3": PlutusScriptData(
        script_file=ALWAYS_FAILS_PLUTUS_V3,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=ALWAYS_FAILS_V3_COST,
    ),
}

ALWAYS_SUCCEEDS = {
    "v1": PlutusScriptData(
        script_file=ALWAYS_SUCCEEDS_PLUTUS_V1,
        script_type=clusterlib.ScriptTypes.PLUTUS_V1,
        execution_cost=ALWAYS_SUCCEEDS_COST,
    ),
    "v2": PlutusScriptData(
        script_file=ALWAYS_SUCCEEDS_PLUTUS_V2,
        script_type=clusterlib.ScriptTypes.PLUTUS_V2,
        execution_cost=ALWAYS_SUCCEEDS_V2_COST,
    ),
    "v3": PlutusScriptData(
        script_file=ALWAYS_SUCCEEDS_PLUTUS_V3,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=ALWAYS_SUCCEEDS_V3_COST,
    ),
}

GUESSING_GAME = {
    "v1": PlutusScriptData(
        script_file=GUESSING_GAME_PLUTUS_V1,
        script_type=clusterlib.ScriptTypes.PLUTUS_V1,
        execution_cost=GUESSING_GAME_COST,
    ),
    "v2": PlutusScriptData(
        script_file=GUESSING_GAME_PLUTUS_V2,
        script_type=clusterlib.ScriptTypes.PLUTUS_V2,
        execution_cost=GUESSING_GAME_V2_COST,
    ),
}

GUESSING_GAME_UNTYPED = {
    "v1": PlutusScriptData(
        script_file=GUESSING_GAME_UNTYPED_PLUTUS_V1,
        script_type=clusterlib.ScriptTypes.PLUTUS_V1,
        execution_cost=GUESSING_GAME_UNTYPED_COST,
    ),
    "v2": PlutusScriptData(
        script_file=GUESSING_GAME_UNTYPED_PLUTUS_V2,
        script_type=clusterlib.ScriptTypes.PLUTUS_V2,
        execution_cost=GUESSING_GAME_UNTYPED_V2_COST,
    ),
}

MINTING_PLUTUS = {
    "v1": PlutusScriptData(
        script_file=MINTING_PLUTUS_V1,
        script_type=clusterlib.ScriptTypes.PLUTUS_V1,
        execution_cost=MINTING_COST,
    ),
    "v2": PlutusScriptData(
        script_file=MINTING_PLUTUS_V2,
        script_type=clusterlib.ScriptTypes.PLUTUS_V2,
        execution_cost=MINTING_V2_COST,
    ),
    "v3": PlutusScriptData(
        script_file=MINTING_PLUTUS_V3,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=MINTING_V3_COST,
    ),
}

MINTING_WITNESS_REDEEMER = {
    "v1": PlutusScriptData(
        script_file=MINTING_WITNESS_REDEEMER_PLUTUS_V1,
        script_type=clusterlib.ScriptTypes.PLUTUS_V1,
        execution_cost=MINTING_WITNESS_REDEEMER_COST,
    ),
    "v3": PlutusScriptData(
        script_file=MINTING_WITNESS_REDEEMER_PLUTUS_V3,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=MINTING_V3_WITNESS_REDEEMER_COST,
    ),
}

MINTING_TIME_RANGE = {
    "v1": PlutusScriptData(
        script_file=MINTING_TIME_RANGE_PLUTUS_V1,
        script_type=clusterlib.ScriptTypes.PLUTUS_V1,
        execution_cost=MINTING_TIME_RANGE_COST,
    ),
    "v3": PlutusScriptData(
        script_file=MINTING_TIME_RANGE_PLUTUS_V3,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=MINTING_V3_TIME_RANGE_COST,
    ),
}

MINTING_SECP256K1_ECDSA = {
    "v2": MINTING_SECP256K1_ECDSA_PLUTUS_V2,
    "v3": MINTING_SECP256K1_ECDSA_PLUTUS_V3,
}

MINTING_SECP256K1_SCHNORR = {
    "v2": MINTING_SECP256K1_SCHNORR_PLUTUS_V2,
    "v3": MINTING_SECP256K1_SCHNORR_PLUTUS_V3,
}


# ----- Bitwise tests ----- #

# These are used to fill in the execution costs of scripts where we don't yet
# know what the cost is.  We're not currently checking the costs (and it seems
# to be difficult when the script fails anyway), so the values here don't really
# matter.

UNKNOWN_PER_TIME = 1_000_000
UNKNOWN_PER_SPACE = 100_000
UNKNOWN_FIXED_COST = 777_777
UNDETERMINED_COST = ExecutionCost(
    per_time=UNKNOWN_PER_TIME, per_space=UNKNOWN_PER_SPACE, fixed_cost=UNKNOWN_FIXED_COST
)


# Rotate/shift bitwise builtins succeed pre-PV11 and fail under PV11+ on node >= 11.0.0.
_ROTATE_SHIFT_SCRIPTS_V3 = (
    ("succeedingRotateByteStringPolicyScriptV3.plutus", 22767760, 109004, 7932),
    ("succeedingShiftByteStringPolicyScriptV3.plutus", 17914625, 85787, 6242),
)
_ROTATE_SHIFT_NAMES_V3 = tuple(s[0] for s in _ROTATE_SHIFT_SCRIPTS_V3)
_PV11_BITWISE_FAILS = VERSIONS.cluster_era >= 11 and VERSIONS.node >= version.parse("11.0.0")


# Tuples of (script_name, per_time, per_space, fixed_cost) for each succeeding bitwise script.
SUCCEEDING_BITWISE_SCRIPTS_V3 = (
    ("succeedingAndByteStringPolicyScriptV3.plutus", 19260610, 102266, 7290),
    ("succeedingOrByteStringPolicyScriptV3.plutus", 19260610, 102266, 7290),
    ("succeedingXorByteStringPolicyScriptV3.plutus", 19260610, 102266, 7290),
    ("succeedingComplementByteStringPolicyScriptV3.plutus", 5860345, 30027, 2156),
    ("succeedingCountSetBitsPolicyScriptV3.plutus", 9211420, 45324, 3280),
    ("succeedingFindFirstSetBitPolicyScriptV3.plutus", 8071583, 40221, 2903),
    ("succeedingReadBitPolicyScriptV3.plutus", 15272720, 82724, 5875),
    ("succeedingReplicateBytePolicyScriptV3.plutus", 4585827, 22946, 1655),
    ("succeedingWriteBitsPolicyScriptV3.plutus", 90629019, 462457, 33219),
    *(() if _PV11_BITWISE_FAILS else _ROTATE_SHIFT_SCRIPTS_V3),
)


FAILING_BITWISE_SCRIPT_FILES_V3 = (
    "failingReadBitPolicyScriptV3_1.plutus",
    "failingReadBitPolicyScriptV3_2.plutus",
    "failingReadBitPolicyScriptV3_3.plutus",
    "failingReadBitPolicyScriptV3_4.plutus",
    "failingReadBitPolicyScriptV3_5.plutus",
    "failingReadBitPolicyScriptV3_6.plutus",
    "failingReadBitPolicyScriptV3_7.plutus",
    "failingReadBitPolicyScriptV3_8.plutus",
    "failingReadBitPolicyScriptV3_9.plutus",
    "failingReadBitPolicyScriptV3_10.plutus",
    "failingReadBitPolicyScriptV3_11.plutus",
    "failingReadBitPolicyScriptV3_12.plutus",
    "failingReadBitPolicyScriptV3_13.plutus",
    "failingReadBitPolicyScriptV3_14.plutus",
    "failingReplicateBytePolicyScriptV3_1.plutus",
    "failingReplicateBytePolicyScriptV3_2.plutus",
    "failingReplicateBytePolicyScriptV3_3.plutus",
    "failingReplicateBytePolicyScriptV3_4.plutus",
    "failingReplicateBytePolicyScriptV3_5.plutus",
    "failingReplicateBytePolicyScriptV3_6.plutus",
    "failingWriteBitsPolicyScriptV3_1.plutus",
    "failingWriteBitsPolicyScriptV3_2.plutus",
    "failingWriteBitsPolicyScriptV3_3.plutus",
    "failingWriteBitsPolicyScriptV3_4.plutus",
    "failingWriteBitsPolicyScriptV3_5.plutus",
    "failingWriteBitsPolicyScriptV3_6.plutus",
    "failingWriteBitsPolicyScriptV3_7.plutus",
    "failingWriteBitsPolicyScriptV3_8.plutus",
    "failingWriteBitsPolicyScriptV3_9.plutus",
    "failingWriteBitsPolicyScriptV3_10.plutus",
    "failingWriteBitsPolicyScriptV3_11.plutus",
    "failingWriteBitsPolicyScriptV3_12.plutus",
    "failingWriteBitsPolicyScriptV3_13.plutus",
    "failingWriteBitsPolicyScriptV3_14.plutus",
    "failingWriteBitsPolicyScriptV3_15.plutus",
    "failingWriteBitsPolicyScriptV3_16.plutus",
    "failingWriteBitsPolicyScriptV3_17.plutus",
    "failingWriteBitsPolicyScriptV3_18.plutus",
    "failingWriteBitsPolicyScriptV3_19.plutus",
    *(_ROTATE_SHIFT_NAMES_V3 if _PV11_BITWISE_FAILS else ()),
)


SUCCEEDING_MINTING_BITWISE_SCRIPTS_V3 = tuple(
    PlutusScriptData(
        script_file=SCRIPTS_V3_DIR / script_name,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=ExecutionCost(per_time=per_time, per_space=per_space, fixed_cost=fixed_cost),
    )
    for script_name, per_time, per_space, fixed_cost in SUCCEEDING_BITWISE_SCRIPTS_V3
)


FAILING_MINTING_BITWISE_SCRIPTS_V3 = tuple(
    PlutusScriptData(
        script_file=SCRIPTS_V3_DIR / n,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=UNDETERMINED_COST,
    )
    for n in FAILING_BITWISE_SCRIPT_FILES_V3
)

MINTING_RIPEMD_160_PLUTUS_V3 = SCRIPTS_V3_DIR / "succeedingRipemd_160Policy.plutus"
MINTING_RIPEMD_160_V3 = PlutusScriptData(
    script_file=MINTING_RIPEMD_160_PLUTUS_V3,
    script_type=clusterlib.ScriptTypes.PLUTUS_V3,
    execution_cost=ExecutionCost(per_time=6597196, per_space=14710, fixed_cost=1325),
)

SUCCEEDING_MINTING_RIPEMD_160_SCRIPTS_V3 = (MINTING_RIPEMD_160_V3,)

# ------ Batch 6 builtins (Plutus V3 only) ------ #
#
# Includes tests for casing on constants, which will be released together with batch 6 in PV 11

# Extra batch6 scripts available only on node >= 10.7.0.
_NODE_10_7_PLUS = VERSIONS.node >= version.parse("10.7.0")
_BATCH6_NODE_10_7_EXTRA_SUCCEEDING = (
    ("succeedingG1MultiScalarMulPolicyScript1_V3_110.plutus", 9183574944, 437274, 687367),
    ("succeedingG1MultiScalarMulPolicyScript2_V3_110.plutus", 9915620336, 477870, 742490),
    ("succeedingG2MultiScalarMulPolicyScript1a_V3_110.plutus", 9008519498, 199418, 661021),
    ("succeedingG2MultiScalarMulPolicyScript1b_V3_110.plutus", 7170645678, 250628, 531465),
    ("succeedingG2MultiScalarMulPolicyScript2a_V3_110.plutus", 8521851043, 193470, 625589),
    ("succeedingG2MultiScalarMulPolicyScript2b_V3_110.plutus", 9372322810, 304876, 693336),
    ("succeedingDeleteExistingCoinPolicyScript_V3_100.plutus", 2272287, 6579, 544),
    ("succeedingDeleteExistingCoinPolicyScript_V3_110.plutus", 2176287, 5979, 502),
    ("succeedingDeleteMissingCoinPolicyScript_V3_100.plutus", 2291175, 6579, 545),
    ("succeedingDeleteMissingCoinPolicyScript_V3_110.plutus", 2195175, 5979, 504),
    ("succeedingInsertExistingCoinPolicyScript_V3_100.plutus", 2291175, 6579, 545),
    ("succeedingInsertExistingCoinPolicyScript_V3_110.plutus", 2195175, 5979, 504),
    ("succeedingInsertNewCoinPolicyScript_V3_100.plutus", 1769425, 5692, 457),
    ("succeedingInsertNewCoinPolicyScript_V3_110.plutus", 1673425, 5092, 415),
    ("succeedingLookupMissingCoinPolicyScript_V3_100.plutus", 1265613, 4847, 371),
    ("succeedingLookupMissingCoinPolicyScript_V3_110.plutus", 1169613, 4247, 330),
    ("succeedingScaleValueNegativePolicyScript_V3_100.plutus", 3973637, 7417, 715),
    ("succeedingScaleValueNegativePolicyScript_V3_110.plutus", 3877637, 6817, 673),
    ("succeedingScaleValuePositivePolicyScript_V3_100.plutus", 3973637, 7417, 715),
    ("succeedingScaleValuePositivePolicyScript_V3_110.plutus", 3877637, 6817, 673),
    ("succeedingScaleValueZeroPolicyScript_V3_100.plutus", 2846094, 6228, 565),
    ("succeedingScaleValueZeroPolicyScript_V3_110.plutus", 2750094, 5628, 524),
    ("succeedingUnionValueAssociativePolicyScript_V3_100.plutus", 7288077, 11023, 1162),
    ("succeedingUnionValueAssociativePolicyScript_V3_110.plutus", 7192077, 10423, 1120),
    (
        "succeedingUnionValueAssociativeSingleCoinPolicyScript_V3_100.plutus",
        5989072,
        10893,
        1061,
    ),
    (
        "succeedingUnionValueAssociativeSingleCoinPolicyScript_V3_110.plutus",
        5893072,
        10293,
        1019,
    ),
    ("succeedingUnionValueCommutativePolicyScript_V3_100.plutus", 4964712, 8860, 870),
    ("succeedingUnionValueCommutativePolicyScript_V3_110.plutus", 4868712, 8260, 828),
    ("succeedingUnionValueCommutativeSingleCoinPolicyScript_V3_100.plutus", 4615604, 8816, 842),
    ("succeedingUnionValueCommutativeSingleCoinPolicyScript_V3_110.plutus", 4519604, 8216, 800),
    ("succeedingUnionValueEmptyIdentityPolicyScript_V3_100.plutus", 5679334, 9379, 951),
    ("succeedingUnionValueEmptyIdentityPolicyScript_V3_110.plutus", 5583334, 8779, 910),
    ("succeedingUnionValueInversablePolicyScript_V3_100.plutus", 3456713, 7406, 677),
    ("succeedingUnionValueInversablePolicyScript_V3_110.plutus", 3360713, 6806, 636),
    ("succeedingValueContainsDisjointPolicyScript_V3_100.plutus", 2611834, 6536, 566),
    ("succeedingValueContainsDisjointPolicyScript_V3_110.plutus", 2515834, 5936, 524),
    ("succeedingValueContainsEmptyPolicyScript_V3_100.plutus", 3522852, 7880, 709),
    ("succeedingValueContainsEmptyPolicyScript_V3_110.plutus", 3426852, 7280, 668),
    ("succeedingValueContainsIsSubValuePolicyScript_V3_100.plutus", 3087582, 7123, 634),
    ("succeedingValueContainsIsSubValuePolicyScript_V3_110.plutus", 2991582, 6523, 593),
    ("succeedingValueContainsReflexivePolicyScript_V3_100.plutus", 2078910, 5391, 461),
    ("succeedingValueContainsReflexivePolicyScript_V3_110.plutus", 1982910, 4791, 420),
    ("succeedingValueContainsRightExtraKeyPolicyScript_V3_100.plutus", 3637590, 8010, 725),
    ("succeedingValueContainsRightExtraKeyPolicyScript_V3_110.plutus", 3541590, 7410, 683),
    ("succeedingValueContainsRightHigherAmountPolicyScript_V3_100.plutus", 2563834, 6236, 545),
    ("succeedingValueContainsRightHigherAmountPolicyScript_V3_110.plutus", 2467834, 5636, 504),
    ("succeedingValueDataRoundTripPolicyScript_V3_100.plutus", 3610826, 6095, 613),
    ("succeedingValueDataRoundTripPolicyScript_V3_110.plutus", 3514826, 5495, 571),
    (
        "succeedingValueContainsIsSubValueSmallerAmountPolicyScript_V3_100.plutus",
        3087582,
        7123,
        634,
    ),
    (
        "succeedingValueContainsIsSubValueSmallerAmountPolicyScript_V3_110.plutus",
        2991582,
        6523,
        593,
    ),
)
_BATCH6_NODE_10_7_EXTRA_FAILING = (
    "failingInsertInvalidCurrencySymbolPolicyScript_V3_100.plutus",
    "failingInsertInvalidCurrencySymbolPolicyScript_V3_110.plutus",
    "failingInsertInvalidTokenNamePolicyScript_V3_100.plutus",
    "failingInsertInvalidTokenNamePolicyScript_V3_110.plutus",
    "failingInsertOverflowQuantityPolicyScript_V3_100.plutus",
    "failingInsertOverflowQuantityPolicyScript_V3_110.plutus",
    "failingInsertUnderflowQuantityPolicyScript_V3_100.plutus",
    "failingInsertUnderflowQuantityPolicyScript_V3_110.plutus",
    "failingScaleValueOverflowPolicyScript_V3_100.plutus",
    "failingScaleValueOverflowPolicyScript_V3_110.plutus",
    "failingScaleValueUnderflowPolicyScript_V3_100.plutus",
    "failingScaleValueUnderflowPolicyScript_V3_110.plutus",
    "failingUnValueDataInvalidDataPolicyScript_V3_100.plutus",
    "failingUnValueDataInvalidDataPolicyScript_V3_110.plutus",
    "failingUnionValueOverflowPolicyScript_V3_100.plutus",
    "failingUnionValueOverflowPolicyScript_V3_110.plutus",
    "failingUnionValueUnderflowPolicyScript_V3_100.plutus",
    "failingUnionValueUnderflowPolicyScript_V3_110.plutus",
    "failingValueContainsLeftNegativePolicyScript_V3_100.plutus",
    "failingValueContainsLeftNegativePolicyScript_V3_110.plutus",
    "failingValueContainsRightNegativePolicyScript_V3_100.plutus",
    "failingValueContainsRightNegativePolicyScript_V3_110.plutus",
)


# Tuples of (script_name, per_time, per_space, fixed_cost) for each succeeding batch6 script.
SUCCEEDING_BATCH6_SCRIPTS_V3 = (
    ("caseBoolHappy_V3_110.plutus", 208100, 1400, 96),
    ("caseIntegerHappy_V3_110.plutus", 208100, 1400, 96),
    ("caseListHappy_V3_110.plutus", 1484744, 6638, 491),
    ("casePairHappy_V3_110.plutus", 848864, 3804, 281),
    ("caseUnitHappy_V3_110.plutus", 96100, 700, 48),
    ("succeedingDropListPolicyScript_V3_110.plutus", 65064783, 258749, 19621),
    ("succeedingExpModIntegerExponentOnePolicyScript_V3_110.plutus", 14492504, 30930, 2830),
    ("succeedingExpModIntegerInversePolicyScript_V3_110.plutus", 35417284, 55402, 5751),
    ("succeedingExpModIntegerPolicyScript_V3_110.plutus", 346637025, 174626, 35069),
    ("succeedingIndexArrayPolicyScript_V3_110.plutus", 2831492, 12706, 938),
    ("succeedingLengthOfArrayPolicyScript_V3_110.plutus", 2060965, 9018, 669),
    ("succeedingListToArrayPolicyScript_V3_110.plutus", 3918949, 15619, 1184),
    *(_BATCH6_NODE_10_7_EXTRA_SUCCEEDING if _NODE_10_7_PLUS else ()),
)

FAILING_BATCH6_SCRIPT_FILES_V3 = (
    "caseBoolUnhappyMoreBranches_V3_110.plutus",
    "caseBoolUnhappyNoBranches_V3_110.plutus",
    "caseIntegerUnhappyNoBranches_V3_110.plutus",
    "caseIntegerUnhappyNoMatchNegative_V3_110.plutus",
    "caseIntegerUnhappyNoMatchOver_V3_110.plutus",
    "caseListUnhappyMoreBranches_V3_110.plutus",
    "caseListUnhappyNoBranches_V3_110.plutus",
    "caseListUnhappyNoMatchNil_V3_110.plutus",
    "casePairUnhappyMoreBranches_V3_110.plutus",
    "casePairUnhappyNoBranches_V3_110.plutus",
    "caseUnitUnhappyMoreBranches_V3_110.plutus",
    "caseUnitUnhappyNoBranches_V3_110.plutus",
    "failingExpModIntegerScript_V3_110_1.plutus",
    "failingExpModIntegerScript_V3_110_10.plutus",
    "failingExpModIntegerScript_V3_110_11.plutus",
    "failingExpModIntegerScript_V3_110_12.plutus",
    "failingExpModIntegerScript_V3_110_13.plutus",
    "failingExpModIntegerScript_V3_110_14.plutus",
    "failingExpModIntegerScript_V3_110_15.plutus",
    "failingExpModIntegerScript_V3_110_16.plutus",
    "failingExpModIntegerScript_V3_110_17.plutus",
    "failingExpModIntegerScript_V3_110_18.plutus",
    "failingExpModIntegerScript_V3_110_2.plutus",
    "failingExpModIntegerScript_V3_110_3.plutus",
    "failingExpModIntegerScript_V3_110_4.plutus",
    "failingExpModIntegerScript_V3_110_5.plutus",
    "failingExpModIntegerScript_V3_110_6.plutus",
    "failingExpModIntegerScript_V3_110_7.plutus",
    "failingExpModIntegerScript_V3_110_8.plutus",
    "failingExpModIntegerScript_V3_110_9.plutus",
    *(_BATCH6_NODE_10_7_EXTRA_FAILING if _NODE_10_7_PLUS else ()),
)

OVERSPENDING_BATCH6_SCRIPT_FILES_V3 = (
    "expensiveDropListPolicyScript_V3_110_1.plutus",
    "expensiveDropListPolicyScript_V3_110_2.plutus",
    "expensiveDropListPolicyScript_V3_110_3.plutus",
    "expensiveDropListPolicyScript_V3_110_4.plutus",
    "expensiveDropListPolicyScript_V3_110_5.plutus",
)


def _get_script_data_succ(r: tuple) -> PlutusScriptData:
    script_name, per_time, per_space, fixed_cost = r
    sdir = SCRIPTS_V3_BATCH6_100_DIR if "_100.plutus" in script_name else SCRIPTS_V3_BATCH6_110_DIR
    return PlutusScriptData(
        script_file=sdir / script_name,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=ExecutionCost(per_time=per_time, per_space=per_space, fixed_cost=fixed_cost),
    )


def _get_script_data_fail(script_name: str) -> PlutusScriptData:
    sdir = SCRIPTS_V3_BATCH6_100_DIR if "_100.plutus" in script_name else SCRIPTS_V3_BATCH6_110_DIR
    return PlutusScriptData(
        script_file=sdir / script_name,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=UNDETERMINED_COST,
    )


SUCCEEDING_MINTING_BATCH6_SCRIPTS_V3 = tuple(
    _get_script_data_succ(r) for r in SUCCEEDING_BATCH6_SCRIPTS_V3
)

FAILING_MINTING_BATCH6_SCRIPTS_V3 = tuple(
    _get_script_data_fail(r) for r in FAILING_BATCH6_SCRIPT_FILES_V3
)

OVERSPENDING_MINTING_BATCH6_SCRIPTS_V3 = tuple(
    PlutusScriptData(
        script_file=SCRIPTS_V3_BATCH6_110_DIR / n,
        script_type=clusterlib.ScriptTypes.PLUTUS_V3,
        execution_cost=UNDETERMINED_COST,
    )
    for n in OVERSPENDING_BATCH6_SCRIPT_FILES_V3
)


@dataclasses.dataclass(frozen=True, order=True)
class PlutusOp:
    script_file: clusterlib.FileType
    datum_file: pl.Path | None = None
    datum_cbor_file: pl.Path | None = None
    datum_value: str | None = None
    redeemer_file: pl.Path | None = None
    redeemer_cbor_file: pl.Path | None = None
    redeemer_value: str | None = None
    execution_cost: ExecutionCost | None = None


@dataclasses.dataclass(frozen=True, order=True)
class ScriptCost:
    fee: int
    collateral: int  # Lovelace amount > minimum UTxO value
    min_collateral: int  # minimum needed collateral


def check_plutus_costs(
    plutus_costs: list[dict], expected_costs: list[ExecutionCost], frac: float = 0.15
) -> None:
    """Check plutus transaction cost.

    units: the time is in picoseconds and the space is in bytes.
    """
    if cluster_nodes.get_cluster_type().type == cluster_nodes.ClusterType.TESTNET:
        # We have the costs calibrated only for local testnet
        return

    # Sort records by total cost
    sorted_plutus = sorted(
        plutus_costs,
        key=lambda x: (
            x["executionUnits"]["memory"] + x["executionUnits"]["steps"] + x["lovelaceCost"]
        ),
    )
    sorted_expected = sorted(expected_costs, key=lambda x: x.per_space + x.per_time + x.fixed_cost)

    errors = []
    for costs, expected_values in zip(sorted_plutus, sorted_expected):
        tx_time = costs["executionUnits"]["steps"]
        tx_space = costs["executionUnits"]["memory"]
        lovelace_cost = costs["lovelaceCost"]

        if not helpers.is_in_interval(tx_time, expected_values.per_time, frac=frac):
            errors.append(f"time: {tx_time} vs {expected_values.per_time}")
        if not helpers.is_in_interval(tx_space, expected_values.per_space, frac=frac):
            errors.append(f"space: {tx_space} vs {expected_values.per_space}")
        if not helpers.is_in_interval(lovelace_cost, expected_values.fixed_cost, frac=frac):
            errors.append(f"fixed cost: {lovelace_cost} vs {expected_values.fixed_cost}")

    if errors:
        raise AssertionError("\n".join(errors))


def get_cost_per_unit(protocol_params: dict) -> ExecutionCost:
    """Get execution cost per unit in Lovelace."""
    return ExecutionCost(
        per_time=protocol_params["executionUnitPrices"]["priceSteps"] or 0,
        per_space=protocol_params["executionUnitPrices"]["priceMemory"] or 0,
        fixed_cost=protocol_params["txFeeFixed"] or 0,
    )


def compute_cost(
    execution_cost: ExecutionCost, protocol_params: dict, collateral_fraction_offset: float = 1.0
) -> ScriptCost:
    """Compute fee and collateral required for the Plutus script."""
    cost_per_unit = get_cost_per_unit(protocol_params=protocol_params)
    fee_redeem = (
        round(
            execution_cost.per_time * cost_per_unit.per_time
            + execution_cost.per_space * cost_per_unit.per_space
        )
        + execution_cost.fixed_cost
    )

    collateral_fraction = protocol_params["collateralPercentage"] / 100
    min_collateral = int(fee_redeem * collateral_fraction * collateral_fraction_offset)
    # Eventual return collateral UTxO value must be bigger than min UTxO value
    collateral_amount = max(min_collateral + 1_000_000, 2_000_000)

    return ScriptCost(fee=fee_redeem, collateral=collateral_amount, min_collateral=min_collateral)


def txout_factory(
    address: str,
    amount: int,
    plutus_op: PlutusOp,
    coin: str = clusterlib.DEFAULT_COIN,
    embed_datum: bool = False,
    inline_datum: bool = False,
) -> clusterlib.TxOut:
    """Create `TxOut` object."""
    datum_hash_file: clusterlib.FileType = ""
    datum_hash_cbor_file: clusterlib.FileType = ""
    datum_hash_value = ""
    datum_embed_file: clusterlib.FileType = ""
    datum_embed_cbor_file: clusterlib.FileType = ""
    datum_embed_value = ""
    inline_datum_file: clusterlib.FileType = ""
    inline_datum_cbor_file: clusterlib.FileType = ""
    inline_datum_value = ""

    if embed_datum:
        datum_embed_file = plutus_op.datum_file or ""
        datum_embed_cbor_file = plutus_op.datum_cbor_file or ""
        datum_embed_value = plutus_op.datum_value or ""
    elif inline_datum:
        inline_datum_file = plutus_op.datum_file or ""
        inline_datum_cbor_file = plutus_op.datum_cbor_file or ""
        inline_datum_value = plutus_op.datum_value or ""
    else:
        datum_hash_file = plutus_op.datum_file or ""
        datum_hash_cbor_file = plutus_op.datum_cbor_file or ""
        datum_hash_value = plutus_op.datum_value or ""

    txout = clusterlib.TxOut(
        address=address,
        amount=amount,
        coin=coin,
        datum_hash_file=datum_hash_file,
        datum_hash_cbor_file=datum_hash_cbor_file,
        datum_hash_value=datum_hash_value,
        datum_embed_file=datum_embed_file,
        datum_embed_cbor_file=datum_embed_cbor_file,
        datum_embed_value=datum_embed_value,
        inline_datum_file=inline_datum_file,
        inline_datum_cbor_file=inline_datum_cbor_file,
        inline_datum_value=inline_datum_value,
    )
    return txout


def check_return_collateral(cluster_obj: clusterlib.ClusterLib, tx_output: clusterlib.TxRawOutput):
    """Check if collateral is returned on Plutus script failure."""
    return_collateral_utxos = cluster_obj.g_query.get_utxo(tx_raw_output=tx_output)
    protocol_params = cluster_obj.g_query.get_protocol_params()

    # When total collateral amount is specified, it is necessary to specify also return
    # collateral `TxOut` to get the change, otherwise all collaterals will be collected
    if tx_output.total_collateral_amount and not tx_output.return_collateral_txouts:
        assert not return_collateral_utxos, "Return collateral UTxO was unexpectedly created"
        return

    if not (tx_output.return_collateral_txouts or tx_output.total_collateral_amount):
        return

    # Check that correct return collateral UTxO was created
    assert return_collateral_utxos, "Return collateral UTxO was NOT created"

    # Check that return collateral is the only output and that the index matches
    out_utxos_ix = {r.utxo_ix for r in return_collateral_utxos}
    assert len(out_utxos_ix) == 1, "There are other outputs other than return collateral"
    # TODO: the index of change can be either 0 (in old node versions) or `txouts_count`,
    # that affects index of return collateral UTxO
    assert return_collateral_utxos[0].utxo_ix in (
        tx_output.txouts_count,
        tx_output.txouts_count + 1,
    )

    returned_amount = clusterlib.calculate_utxos_balance(utxos=return_collateral_utxos)

    tx_collaterals_nested = [
        r.collaterals
        for r in (
            *tx_output.script_txins,
            *tx_output.mint,
            *tx_output.complex_certs,
            *tx_output.script_withdrawals,
        )
    ]
    tx_collaterals = list(set(itertools.chain.from_iterable(tx_collaterals_nested)))
    tx_collaterals_amount = clusterlib.calculate_utxos_balance(utxos=tx_collaterals)

    collateral_charged = tx_collaterals_amount - return_collateral_utxos[0].amount

    tx_tokens = {r.coin for r in tx_collaterals if r.coin != clusterlib.DEFAULT_COIN}

    if tx_output.return_collateral_txouts:
        return_txouts_amount = clusterlib.calculate_utxos_balance(
            utxos=list(tx_output.return_collateral_txouts)
        )
        assert returned_amount == return_txouts_amount, (
            f"Incorrect amount for return collateral: {returned_amount} != {return_txouts_amount}"
        )

        tx_return_addresses = {r.address for r in tx_output.return_collateral_txouts}
        return_utxos_addresses = {r.address for r in return_collateral_utxos}
        assert tx_return_addresses == return_utxos_addresses, (
            "Return collateral addresses don't match: "
            f"{tx_return_addresses} != {return_utxos_addresses}"
        )

        for coin in tx_tokens:
            assert clusterlib.calculate_utxos_balance(
                utxos=return_collateral_utxos, coin=coin
            ) == clusterlib.calculate_utxos_balance(
                utxos=tx_output.return_collateral_txouts, coin=coin
            ), f"Incorrect return collateral token balance for token '{coin}'"

    # Automatic return collateral with `transaction build` command
    elif tx_output.change_address:
        # Check that the collateral amount charged corresponds to 'collateralPercentage'
        assert collateral_charged == round(
            tx_output.fee * protocol_params["collateralPercentage"] / 100
        ), "The collateral amount charged is not the expected amount"

        assert tx_output.change_address == return_collateral_utxos[0].address, (
            "Return collateral address doesn't match change address"
        )

        # The returned amount is the total of all collaterals minus fee
        expected_return_amount = int(tx_collaterals_amount - collateral_charged)

        assert returned_amount == expected_return_amount, (
            "TX collateral output amount doesn't match "
            f"({returned_amount} != {expected_return_amount})"
        )

        for coin in tx_tokens:
            assert clusterlib.calculate_utxos_balance(
                utxos=return_collateral_utxos, coin=coin
            ) == clusterlib.calculate_utxos_balance(utxos=tx_collaterals, coin=coin), (
                f"Incorrect return collateral token balance for token '{coin}'"
            )

    dbsync_utils.check_tx_phase_2_failure(
        cluster_obj=cluster_obj,
        tx_raw_output=tx_output,
        collateral_charged=collateral_charged,
    )


def xfail_on_secp_error(cluster_obj: clusterlib.ClusterLib, algorithm: str, err_msg: str):
    """Xfail a test based on error message when using SECP functions."""
    before_pv8 = clusterlib_utils.get_protocol_version(cluster_obj=cluster_obj) < 8

    # The SECP256k1 functions should work from PV8.
    # Before PV8 the SECP256k1 is blocked or limited by high cost model
    is_forbidden = (
        f"Forbidden builtin function: (builtin verify{algorithm.capitalize()}Secp256k1Signature)"
        in err_msg
        or f"Builtin function Verify{algorithm.capitalize()}Secp256k1Signature "
        "is not available in language PlutusV2 at and protocol version 7.0"
        in err_msg
        or "MalformedScriptWitnesses" in err_msg
    )

    is_overspending = (
        "The machine terminated part way through evaluation due to "
        "overspending the budget." in err_msg
    )

    if before_pv8 and (is_forbidden or is_overspending):
        pytest.xfail("The SECP256k1 builtin functions are not allowed before protocol version 8")
