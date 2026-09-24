"""The node BLS (Leios voting) keys, shared by the BLS tests.

Holds what the key files look like, what the ledger reports for a registered key, and
the two facts that follow from the key being registered on chain - how long it stays
honoured, and when the Leios committee starts holding it.

The scheme is BLS12-381 in its minimal signature size variant, so the verification key is
96 bytes in G2 and a signature is 48 bytes in G1. CIP-0164 fixes the on-chain encodings
in Appendix B::

    leios_bls_verification_key = bytes .size 96
    leios_bls_signature        = bytes .size 48
    leios_bls_pop              = bytes .size 48

The signing key is a 32 byte scalar. It never goes on chain, so the CIP says nothing
about it.
"""

import dataclasses
import math
import pathlib as pl

from cardano_clusterlib import clusterlib

from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers

# Size of the proof of possession that accompanies the BLS key in a pool registration
# certificate. It is mandatory: BLS aggregate signatures are otherwise open to rogue-key
# attacks, so only a key with a valid proof of possession may occupy a committee seat.
POP_LEN = 48

# Number of epoch boundaries between the transaction that registers a BLS key and the
# epoch in which the Leios committee holds it. The first boundary applies the pool
# update, the second seats the committee from the snapshot that saw the update. That is
# the VRF key schedule, which CIP-0164 aligns voting keys with.
BLS_ACTIVATION_EPOCHS = 2


@dataclasses.dataclass(frozen=True)
class KeySpec:
    """Expected properties of a generated BLS key."""

    envelope_type: str
    envelope_desc: str
    bech32_prefix: str
    # Size of the raw key material, in bytes
    key_len: int


VKEY_SPEC = KeySpec(
    envelope_type="BlsVerificationKey_bls12-381-BLS-Signature-Minimal-Signature-Size",
    envelope_desc="BLS12-381 verification key",
    bech32_prefix="bls_vk1",
    key_len=96,
)
SKEY_SPEC = KeySpec(
    envelope_type="BlsSigningKey_bls12-381-BLS-Signature-Minimal-Signature-Size",
    envelope_desc="BLS12-381 signing key",
    bech32_prefix="bls_sk1",
    key_len=32,
)


def check_envelope_key(*, key_file: pl.Path, spec: KeySpec) -> bytes:
    """Check that the file is a BLS key text envelope of the expected kind.

    Args:
        key_file: A path to the key file.
        spec: The expected properties of the key.

    Returns:
        bytes: The raw key material.
    """
    envelope = clusterlib_utils.load_envelope(envelope_file=key_file)
    assert envelope["type"] == spec.envelope_type, envelope
    assert envelope["description"] == spec.envelope_desc, envelope

    key: bytes = clusterlib_utils.decode_envelope_cbor(envelope=envelope)
    assert len(key) == spec.key_len, f"Unexpected key length: {len(key)}"

    return key


def check_bech32_key(*, key_file: pl.Path, spec: KeySpec) -> bytes:
    """Check that the file is a bech32 encoded BLS key of the expected kind.

    Args:
        key_file: A path to the key file.
        spec: The expected properties of the key.

    Returns:
        bytes: The raw key material.
    """
    key_bech32 = key_file.read_text().strip()
    assert key_bech32.startswith(spec.bech32_prefix), key_bech32

    key = bytes.fromhex(helpers.decode_bech32(bech32=key_bech32))
    assert len(key) == spec.key_len, f"Unexpected key length: {len(key)}"

    return key


def get_vkey_hex(*, vkey_file: pl.Path) -> str:
    """Return the raw BLS verification key of a key file, hex encoded.

    The ledger reports a registered key as hex, while the key file stores it CBOR-wrapped
    inside a text envelope. The envelope is checked on the way, so that a signing key
    passed here by mistake is reported as such instead of as a key mismatch.

    Args:
        vkey_file: A path to the BLS verification key file.

    Returns:
        str: The hex encoded verification key.
    """
    return check_envelope_key(key_file=vkey_file, spec=VKEY_SPEC).hex()


def get_bls_pub_key(*, bls_key_state: dict | None) -> str | None:
    """Return the hex encoded BLS verification key of a key state record.

    Both the ``spsBlsKey`` of a pool and the ``key`` of a Leios committee seat are a key
    with its registration epoch, and either can be missing - a pool doesn't have to have
    a BLS key.

    Args:
        bls_key_state: A BLS key state record, or `None` when there is none.

    Returns:
        str | None: The hex encoded verification key, or `None` when there is no key.
    """
    return ((bls_key_state or {}).get("bksKey") or {}).get("blsPubKey")


def get_registered_bls_key(*, cluster_obj: clusterlib.ClusterLib, pool_id: str) -> dict:
    """Return the BLS key state the ledger holds for a pool.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_id: An ID of the stake pool (Bech32-encoded or hex-encoded).

    Returns:
        dict: The ``spsBlsKey`` record, i.e. the key with the epoch it was registered
            in, or an empty dict when the pool has no registered BLS key.
    """
    pool_params = cluster_obj.g_query.get_pool_state(stake_pool_id=pool_id).pool_params
    return helpers.get_pool_param("spsBlsKey", pool_params=pool_params) or {}


def get_committee_seat(*, cluster_obj: clusterlib.ClusterLib, pool_id: str) -> dict:
    """Return the Leios committee seat of a pool in the current epoch.

    The committee is reported as a whole regardless of the queried pool, so a single
    pool ID is enough to get it.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_id: An ID of the stake pool (Bech32-encoded or hex-encoded).

    Returns:
        dict: The seat of the pool, or an empty dict when the pool holds no seat.
    """
    pool_id_dec = helpers.decode_bech32(pool_id) if pool_id.startswith("pool") else pool_id
    snapshot = cluster_obj.g_query.get_stake_snapshot(stake_pool_ids=[pool_id_dec])
    committee: list[dict] = snapshot.get("leiosCommittee") or []
    return next((s for s in committee if s["poolId"] == pool_id_dec), {})


def get_max_key_age(*, cluster_obj: clusterlib.ClusterLib) -> int:
    """Return the BLS key lifetime in epochs, as the ledger derives it from genesis.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.

    Returns:
        int: The number of epochs a registered BLS key is honoured for.
    """
    genesis = cluster_obj.genesis
    kes_lifetime = int(genesis["maxKESEvolutions"]) * int(genesis["slotsPerKESPeriod"])
    return math.ceil(kes_lifetime / int(genesis["epochLength"])) + 2
