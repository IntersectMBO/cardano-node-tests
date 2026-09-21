"""Tests for node BLS key generation."""

import dataclasses
import logging
import pathlib as pl
import re

import allure
import pytest
from cardano_clusterlib import clusterlib

from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.cluster_era < VERSIONS.DIJKSTRA_FIRST,
    reason="BLS keys are available only in Dijkstra+ eras",
)

# Size of the proof of possession that accompanies the BLS key in a pool registration certificate
POP_LEN = 48


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


class TestBlsKeys:
    """Tests for generating node BLS key pairs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_dijkstra_cmd: clusterlib.ClusterLib,
    ):
        """Generate a node BLS key pair.

        * Generate the key pair using `gen_bls_key_pair`
        * Check that the key files were created with the expected names
        * Check that both keys are text envelopes of the expected type and description
        * Check that the raw key material has the expected size
        """
        temp_template = common.get_test_id(cluster)

        key_pair = cluster_dijkstra_cmd.g_node.gen_bls_key_pair(node_name=temp_template)

        assert key_pair.vkey_file == pl.Path(f"{temp_template}_bls.vkey"), key_pair
        assert key_pair.skey_file == pl.Path(f"{temp_template}_bls.skey"), key_pair
        assert key_pair.vkey_file.exists(), f"The file `{key_pair.vkey_file}` doesn't exist"
        assert key_pair.skey_file.exists(), f"The file `{key_pair.skey_file}` doesn't exist"

        check_envelope_key(key_file=key_pair.vkey_file, spec=VKEY_SPEC)
        check_envelope_key(key_file=key_pair.skey_file, spec=SKEY_SPEC)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair_unique(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_dijkstra_cmd: clusterlib.ClusterLib,
    ):
        """Check that each generated BLS key pair is unique.

        * Generate several BLS key pairs
        * Check that all the generated verification keys differ
        * Check that all the generated signing keys differ
        """
        temp_template = common.get_test_id(cluster)
        num_keys = 3

        key_pairs = [
            cluster_dijkstra_cmd.g_node.gen_bls_key_pair(node_name=f"{temp_template}_{i}")
            for i in range(num_keys)
        ]

        vkeys = {check_envelope_key(key_file=k.vkey_file, spec=VKEY_SPEC) for k in key_pairs}
        skeys = {check_envelope_key(key_file=k.skey_file, spec=SKEY_SPEC) for k in key_pairs}

        assert len(vkeys) == num_keys, "The generated verification keys are not unique"
        assert len(skeys) == num_keys, "The generated signing keys are not unique"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("out_format", ("text-envelope", "bech32"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair_out_format(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_dijkstra_cmd: clusterlib.ClusterLib,
        out_format: str,
    ):
        """Generate a BLS key pair in the parametrized output format.

        * Generate the key pair with `--key-output-text-envelope` or `--key-output-bech32`
        * Check that the keys are in the requested format
        * Check that the raw key material has the expected size
        """
        temp_template = common.get_test_id(cluster)

        vkey_file = pl.Path(f"{temp_template}_bls.vkey")
        skey_file = pl.Path(f"{temp_template}_bls.skey")

        cluster_dijkstra_cmd.cli(
            [
                "node",
                "key-gen-BLS",
                f"--key-output-{out_format}",
                "--verification-key-file",
                str(vkey_file),
                "--signing-key-file",
                str(skey_file),
            ]
        )

        check_key = check_envelope_key if out_format == "text-envelope" else check_bech32_key
        check_key(key_file=vkey_file, spec=VKEY_SPEC)
        check_key(key_file=skey_file, spec=SKEY_SPEC)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair_matching_keys(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_dijkstra_cmd: clusterlib.ClusterLib,
    ):
        """Check that the generated BLS verification key matches the signing key.

        The pool registration certificate stores the BLS verification key and a proof of
        possession, both derived from the BLS signing key. So the certificate can be used to
        check that the generated key pair is consistent.

        * Generate a BLS key pair
        * Generate a pool registration certificate using the BLS signing key
        * Check that the certificate contains the generated BLS verification key
        * Check that the certificate contains a proof of possession of the expected size
        """
        temp_template = common.get_test_id(cluster)

        bls_key_pair = cluster_dijkstra_cmd.g_node.gen_bls_key_pair(node_name=temp_template)
        vkey = check_envelope_key(key_file=bls_key_pair.vkey_file, spec=VKEY_SPEC)
        check_envelope_key(key_file=bls_key_pair.skey_file, spec=SKEY_SPEC)

        node_vrf = cluster.g_node.gen_vrf_key_pair(node_name=f"{temp_template}_vrf")
        node_cold = cluster.g_node.gen_cold_key_pair_and_counter(node_name=f"{temp_template}_cold")
        owner_stake = cluster.g_stake_address.gen_stake_key_pair(key_name=f"{temp_template}_owner")

        pool_data = clusterlib.PoolData(
            pool_name=f"pool_{temp_template}",
            pool_pledge=5,
            pool_cost=500_000_000,
            pool_margin=0.01,
        )

        pool_reg_cert_file = cluster_dijkstra_cmd.g_stake_pool.gen_pool_registration_cert(
            pool_data=pool_data,
            vrf_vkey_file=node_vrf.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[owner_stake.vkey_file],
            bls_signing_key_file=bls_key_pair.skey_file,
        )

        # The Dijkstra pool registration certificate has one item more than the Conway one -
        # the BLS verification key with its proof of possession.
        cert_cbor = clusterlib_utils.load_envelope_cbor(envelope_file=pool_reg_cert_file)
        assert len(cert_cbor) == common.POOL_REG_CERT_DIJKSTRA_ITEMS, (
            f"Unexpected pool registration certificate: {cert_cbor}"
        )

        cert_bls_key, cert_bls_pop = cert_cbor[common.POOL_REG_CERT_BLS_IX]
        assert cert_bls_key == vkey, (
            f"The certificate BLS key `{cert_bls_key.hex()}` doesn't match the generated "
            f"verification key `{vkey.hex()}`"
        )
        assert len(cert_bls_pop) == POP_LEN, (
            f"Unexpected proof of possession length: {len(cert_bls_pop)}"
        )


class TestNegativeBlsKeys:
    """Negative tests for generating node BLS key pairs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("missing_arg", ("verification-key-file", "signing-key-file"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_missing_key_file_arg(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_dijkstra_cmd: clusterlib.ClusterLib,
        missing_arg: str,
    ):
        """Try to generate a BLS key pair without one of the mandatory output file arguments.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        given_arg = (
            "signing-key-file"
            if missing_arg == "verification-key-file"
            else "verification-key-file"
        )

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_dijkstra_cmd.cli(
                [
                    "node",
                    "key-gen-BLS",
                    f"--{given_arg}",
                    f"{temp_template}_bls.key",
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert re.search(rf"Missing: +--{missing_arg}", exc_value), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_nonexistent_out_dir(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_dijkstra_cmd: clusterlib.ClusterLib,
    ):
        """Try to generate a BLS key pair into a dir that doesn't exist.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        out_dir = pl.Path(f"{temp_template}_nonexistent")
        assert not out_dir.exists(), f"The dir `{out_dir}` already exists"

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_dijkstra_cmd.cli(
                [
                    "node",
                    "key-gen-BLS",
                    "--verification-key-file",
                    str(out_dir / "bls.vkey"),
                    "--signing-key-file",
                    str(out_dir / "bls.skey"),
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "does not exist" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_key_gen_unavailable_in_conway_cmd(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_conway_cmd: clusterlib.ClusterLib,
    ):
        """Try to generate a BLS key pair using the `conway` command era.

        BLS keys don't exist in Conway, so the `cardano-cli conway node` command group has no
        `key-gen-BLS` subcommand.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_conway_cmd.cli(
                [
                    "node",
                    "key-gen-BLS",
                    "--verification-key-file",
                    f"{temp_template}_bls.vkey",
                    "--signing-key-file",
                    f"{temp_template}_bls.skey",
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "Invalid argument `key-gen-BLS'" in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_bls_skey_not_accepted_as_ed25519(
        self,
        cluster: clusterlib.ClusterLib,
        cluster_dijkstra_cmd: clusterlib.ClusterLib,
    ):
        """Try to derive a verification key from a BLS signing key using `key verification-key`.

        The BLS signing key is not an ed25519 key, so it is not accepted by the generic
        `cardano-cli key verification-key` command.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        bls_key_pair = cluster_dijkstra_cmd.g_node.gen_bls_key_pair(node_name=temp_template)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster_dijkstra_cmd.cli(
                [
                    "key",
                    "verification-key",
                    "--signing-key-file",
                    str(bls_key_pair.skey_file),
                    "--verification-key-file",
                    f"{temp_template}_derived.vkey",
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "TextEnvelope type error" in exc_value, exc_value
            assert SKEY_SPEC.envelope_type in exc_value, exc_value
