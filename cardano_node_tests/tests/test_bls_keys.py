"""Tests for generating and registering the node BLS (Leios voting) keys."""

import dataclasses
import logging
import pathlib as pl
import re
import typing as tp

import allure
import cbor2
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import addrs_common
from cardano_node_tests.tests import bls
from cardano_node_tests.tests import common
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)

pytestmark = pytest.mark.skipif(
    VERSIONS.transaction_era < VERSIONS.DIJKSTRA_FIRST,
    reason="BLS keys are available only in Dijkstra+ eras",
)


class TestBlsKeys:
    """Tests for generating node BLS key pairs."""

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Generate a node BLS key pair.

        * Generate the key pair using `gen_bls_key_pair`
        * Check that the key files were created with the expected names
        * Check that both keys are text envelopes of the expected type and description
        * Check that the raw key material has the expected size
        """
        temp_template = common.get_test_id(cluster)

        key_pair = cluster.g_node.gen_bls_key_pair(node_name=temp_template)

        assert key_pair.vkey_file == pl.Path(f"{temp_template}_bls.vkey"), key_pair
        assert key_pair.skey_file == pl.Path(f"{temp_template}_bls.skey"), key_pair
        assert key_pair.vkey_file.exists(), f"The file `{key_pair.vkey_file}` doesn't exist"
        assert key_pair.skey_file.exists(), f"The file `{key_pair.skey_file}` doesn't exist"

        bls.check_envelope_key(key_file=key_pair.vkey_file, spec=bls.VKEY_SPEC)
        bls.check_envelope_key(key_file=key_pair.skey_file, spec=bls.SKEY_SPEC)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair_unique(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Check that each generated BLS key pair is unique.

        * Generate several BLS key pairs
        * Check that all the generated verification keys differ
        * Check that all the generated signing keys differ
        """
        temp_template = common.get_test_id(cluster)
        num_keys = 3

        key_pairs = [
            cluster.g_node.gen_bls_key_pair(node_name=f"{temp_template}_{i}")
            for i in range(num_keys)
        ]

        vkeys = {
            bls.check_envelope_key(key_file=k.vkey_file, spec=bls.VKEY_SPEC) for k in key_pairs
        }
        skeys = {
            bls.check_envelope_key(key_file=k.skey_file, spec=bls.SKEY_SPEC) for k in key_pairs
        }

        assert len(vkeys) == num_keys, "The generated verification keys are not unique"
        assert len(skeys) == num_keys, "The generated signing keys are not unique"

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.parametrize("out_format", ("text-envelope", "bech32"))
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair_out_format(
        self,
        cluster: clusterlib.ClusterLib,
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

        cluster.cli(
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

        check_key = (
            bls.check_envelope_key if out_format == "text-envelope" else bls.check_bech32_key
        )
        check_key(key_file=vkey_file, spec=bls.VKEY_SPEC)
        check_key(key_file=skey_file, spec=bls.SKEY_SPEC)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_gen_bls_key_pair_matching_keys(
        self,
        cluster: clusterlib.ClusterLib,
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

        bls_key_pair = cluster.g_node.gen_bls_key_pair(node_name=temp_template)
        vkey = bls.check_envelope_key(key_file=bls_key_pair.vkey_file, spec=bls.VKEY_SPEC)
        bls.check_envelope_key(key_file=bls_key_pair.skey_file, spec=bls.SKEY_SPEC)

        node_vrf = cluster.g_node.gen_vrf_key_pair(node_name=f"{temp_template}_vrf")
        node_cold = cluster.g_node.gen_cold_key_pair_and_counter(node_name=f"{temp_template}_cold")
        owner_stake = cluster.g_stake_address.gen_stake_key_pair(key_name=f"{temp_template}_owner")

        pool_data = clusterlib.PoolData(
            pool_name=f"pool_{clusterlib.get_rand_str(4)}",
            pool_pledge=5,
            pool_cost=500_000_000,
            pool_margin=0.01,
        )

        pool_reg_cert_file = cluster.g_stake_pool.gen_pool_registration_cert(
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
        assert len(cert_bls_pop) == bls.POP_LEN, (
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
            cluster.cli(
                [
                    "node",
                    "key-gen-BLS",
                    f"--{given_arg}",
                    f"{temp_template}_bls.key",
                ]
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert re.search(rf"Missing:[^\n]*--{missing_arg}", exc_value), exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_nonexistent_out_dir(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Try to generate a BLS key pair into a dir that doesn't exist.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        out_dir = pl.Path(f"{temp_template}_nonexistent")
        assert not out_dir.exists(), f"The dir `{out_dir}` already exists"

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
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
    def test_bls_vkey_not_accepted_as_signing_key(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Try to build a pool registration certificate with a BLS verification key.

        The certificate carries a proof of possession, which the CLI can derive only from
        the signing key, so there is no command that takes the verification key instead.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        node_vrf = cluster.g_node.gen_vrf_key_pair(node_name=f"{temp_template}_vrf")
        node_cold = cluster.g_node.gen_cold_key_pair_and_counter(node_name=f"{temp_template}_cold")
        owner_stake = cluster.g_stake_address.gen_stake_key_pair(key_name=f"{temp_template}_owner")
        bls_key_pair = cluster.g_node.gen_bls_key_pair(node_name=temp_template)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.g_stake_pool.gen_pool_registration_cert(
                pool_data=clusterlib.PoolData(
                    pool_name=f"pool_{clusterlib.get_rand_str(4)}",
                    pool_pledge=5,
                    pool_cost=500_000_000,
                    pool_margin=0.01,
                ),
                vrf_vkey_file=node_vrf.vkey_file,
                cold_vkey_file=node_cold.vkey_file,
                owner_stake_vkey_files=[owner_stake.vkey_file],
                bls_signing_key_file=bls_key_pair.vkey_file,
            )
        exc_value = str(excinfo.value)
        with common.allow_unstable_error_messages():
            assert "TextEnvelope type error" in exc_value, exc_value
            assert bls.SKEY_SPEC.envelope_type in exc_value, exc_value

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    def test_bls_skey_not_accepted_as_ed25519(
        self,
        cluster: clusterlib.ClusterLib,
    ):
        """Try to derive a verification key from a BLS signing key using `key verification-key`.

        The BLS signing key is not an ed25519 key, so it is not accepted by the generic
        `cardano-cli key verification-key` command.

        Expect failure.
        """
        temp_template = common.get_test_id(cluster)

        bls_key_pair = cluster.g_node.gen_bls_key_pair(node_name=temp_template)

        with pytest.raises(clusterlib.CLIError) as excinfo:
            cluster.cli(
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
            assert bls.SKEY_SPEC.envelope_type in exc_value, exc_value


@dataclasses.dataclass(frozen=True)
class MismatchedPopPool:
    """A stake pool registered with a BLS key that carries another key's proof."""

    pool_id: str
    # Hex encoded, the way the ledger reports them
    vkey: str
    pop: str
    tx_output: clusterlib.TxRawOutput


def register_pool_with_mismatched_pop(
    *,
    cluster_obj: clusterlib.ClusterLib,
    pool_user: clusterlib.PoolUser,
    temp_template: str,
    testfile_temp_dir: pl.Path,
    request: FixtureRequest,
) -> MismatchedPopPool:
    """Register a stake pool whose BLS key carries the proof of possession of another key.

    The proof cannot be produced on its own - the CLI derives it from the signing key and
    emits it only inside a registration certificate - so the mismatch is built by taking
    the proof out of a second pool's certificate and splicing it into the first one.

    The deregistration of the pool is scheduled as a finalizer.

    Args:
        cluster_obj: An instance of `clusterlib.ClusterLib`.
        pool_user: A pool user that owns the pool and pays for the registration.
        temp_template: A test identifier used for naming the created files.
        testfile_temp_dir: A directory to run the deregistration in.
        request: A pytest fixture request, used to register the deregistration.

    Returns:
        MismatchedPopPool: The pool ID, the registered key and proof, and the
            registration transaction.
    """
    node_vrf = cluster_obj.g_node.gen_vrf_key_pair(node_name=f"{temp_template}_vrf")
    node_cold = cluster_obj.g_node.gen_cold_key_pair_and_counter(node_name=f"{temp_template}_cold")

    # A pool name is limited to 50 characters, which a test ID can eat on its own, so the
    # pool is named after a random string instead
    pool_data = clusterlib.PoolData(
        pool_name=f"pool_{clusterlib.get_rand_str(4)}",
        pool_pledge=5,
        pool_cost=cluster_obj.g_query.get_protocol_params().get("minPoolCost", 0),
        pool_margin=0.01,
    )

    # The proof of possession exists only inside a registration certificate, so a
    # second certificate is generated just to take its proof
    certs = {}
    for name in ("own", "other"):
        key_pair = cluster_obj.g_node.gen_bls_key_pair(node_name=f"{temp_template}_{name}")
        certs[name] = cluster_obj.g_stake_pool.gen_pool_registration_cert(
            pool_data=dataclasses.replace(pool_data, pool_name=f"{pool_data.pool_name}_{name}"),
            vrf_vkey_file=node_vrf.vkey_file,
            cold_vkey_file=node_cold.vkey_file,
            owner_stake_vkey_files=[pool_user.stake.vkey_file],
            bls_signing_key_file=key_pair.skey_file,
        )

    own_envelope = clusterlib_utils.load_envelope(envelope_file=certs["own"])
    own_cert = cbor2.loads(bytes.fromhex(own_envelope["cborHex"]))
    other_cert = clusterlib_utils.load_envelope_cbor(envelope_file=certs["other"])

    assert len(own_cert) == common.POOL_REG_CERT_DIJKSTRA_ITEMS, (
        f"Unexpected pool registration certificate: {own_cert}"
    )

    own_vkey, own_pop = own_cert[common.POOL_REG_CERT_BLS_IX]
    __, other_pop = other_cert[common.POOL_REG_CERT_BLS_IX]
    for name, pop in (("own", own_pop), ("other", other_pop)):
        assert len(pop) == bls.POP_LEN, f"Unexpected {name} proof length: {len(pop)}"
    assert other_pop != own_pop, "The two certificates carry the same proof of possession"

    # Re-encoding has to be faithful, otherwise the submitted certificate would differ
    # from the generated one in more than the proof and a rejection would say nothing
    # about the proof
    mismatched_cert: list[tp.Any] = list(own_cert)
    assert cbor2.dumps(mismatched_cert).hex() == own_envelope["cborHex"], (
        "Re-encoding the pool registration certificate is not faithful"
    )

    mismatched_cert[common.POOL_REG_CERT_BLS_IX] = [own_vkey, other_pop]
    mismatched_cert_file = pl.Path(f"{temp_template}_mismatched_pop.cert")
    helpers.write_json(
        out_file=mismatched_cert_file,
        content={**own_envelope, "cborHex": cbor2.dumps(mismatched_cert).hex()},
    )

    tx_files = clusterlib.TxFiles(
        certificate_files=[mismatched_cert_file],
        signing_key_files=[
            pool_user.payment.skey_file,
            pool_user.stake.skey_file,
            node_cold.skey_file,
        ],
    )
    tx_output = cluster_obj.g_transaction.send_tx(
        src_address=pool_user.payment.address,
        tx_name=f"{temp_template}_reg_pool",
        tx_files=tx_files,
    )

    def _deregister() -> None:
        with helpers.change_cwd(testfile_temp_dir):
            cluster_obj.g_stake_pool.deregister_stake_pool(
                pool_owners=[pool_user],
                cold_key_pair=node_cold,
                epoch=cluster_obj.g_query.get_epoch() + 2,
                pool_name=pool_data.pool_name,
                tx_name=f"{temp_template}_cleanup",
            )

    request.addfinalizer(_deregister)

    return MismatchedPopPool(
        pool_id=cluster_obj.g_stake_pool.get_stake_pool_id(node_cold.vkey_file),
        vkey=own_vkey.hex(),
        pop=other_pop.hex(),
        tx_output=tx_output,
    )


class TestBlsProofOfPossession:
    """Tests for the proof of possession that accompanies a registered BLS key."""

    @pytest.fixture
    def pool_user(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.PoolUser:
        """Create a pool user with a registered stake address."""
        return addrs_common.get_registered_pool_user(
            name_template=common.get_test_id(cluster),
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            caching_key=helpers.get_current_line_str(),
            amount=900_000_000,
            min_amount=600_000_000,
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.testnets
    @pytest.mark.smoke
    @pytest.mark.dbsync
    def test_mismatched_pop_is_not_verified(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Register a pool with a BLS key whose proof of possession belongs to another key.

        CIP-0164 makes two statements about the proof, because BLS aggregate signatures
        are otherwise open to rogue-key attacks. It "is mandatory and verified at
        registration", and "Only a key with a valid proof of possession may occupy a
        committee seat or contribute to a certificate".

        Only the second one is implemented. The Dijkstra `POOL` rule reuses
        `ShelleyPoolPredFailure` unchanged and has no BLS predicate failure at all, so a
        registration carrying a proof that does not belong to the key is accepted and the
        pair is stored verbatim. The proof is verified later, when the committee is
        seated - see `test_mismatched_pop_is_not_seated`.

        This test pins the registration side down, so that the day the ledger starts
        rejecting such a registration is a day this test fails and says so, rather than a
        silent change of behaviour.

        * Register a pool with a BLS key and the proof of possession of another key
        * Check that the registration is accepted
        * Check that the ledger stored the mismatched key and proof exactly as submitted
        """
        temp_template = common.get_test_id(cluster)

        pool = register_pool_with_mismatched_pop(
            cluster_obj=cluster,
            pool_user=pool_user,
            temp_template=temp_template,
            testfile_temp_dir=testfile_temp_dir,
            request=request,
        )

        # The key and the proof are stored the way they were submitted, unverified
        bls_key = bls.get_registered_bls_key(cluster_obj=cluster, pool_id=pool.pool_id)
        assert bls.get_bls_pub_key(bls_key_state=bls_key) == pool.vkey, (
            f"The pool didn't register the expected BLS key: {bls_key}"
        )
        assert bls_key["bksKey"]["blsPossessionProof"] == pool.pop, (
            f"The pool didn't register the mismatched proof of possession: {bls_key}"
        )

        dbsync_utils.check_tx(cluster_obj=cluster, tx_raw_output=pool.tx_output)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.leios
    @pytest.mark.long
    def test_mismatched_pop_is_not_seated(
        self,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        testfile_temp_dir: pl.Path,
        request: FixtureRequest,
    ):
        """Check that a key with a mismatched proof of possession cannot vote.

        This is the half of the CIP-0164 requirement that is implemented, and it is the
        half that matters: the registration is accepted with the proof unverified, but
        `mkLeiosCommittee` verifies it when it builds the committee and admits the seat
        keyless, so the key can neither vote nor contribute to a certificate.

        The pool keeps its seat and the ledger keeps reporting the key it registered -
        what it doesn't get is the ability to vote with the weight the seat carries. The
        key is well inside its lifetime here, so age cannot be the reason.

        * Register a pool with a BLS key and the proof of possession of another key
        * Wait until the committee is seated from a snapshot that holds the key
        * Check that the pool is on the committee and that the key is reported
        * Check that the key is not past its lifetime, so only the proof can disqualify it
        * Check that the seat is not voting
        """
        temp_template = common.get_test_id(cluster)

        pool = register_pool_with_mismatched_pop(
            cluster_obj=cluster,
            pool_user=pool_user,
            temp_template=temp_template,
            testfile_temp_dir=testfile_temp_dir,
            request=request,
        )
        bls_key = bls.get_registered_bls_key(cluster_obj=cluster, pool_id=pool.pool_id)
        assert bls.get_bls_pub_key(bls_key_state=bls_key) == pool.vkey, (
            f"The pool didn't register the expected BLS key: {bls_key}"
        )
        registered_in = bls_key["bksRegisteredIn"]

        this_epoch = cluster.wait_for_epoch(
            epoch_no=registered_in + bls.BLS_ACTIVATION_EPOCHS, padding_seconds=5
        )
        seat = bls.get_committee_seat(cluster_obj=cluster, pool_id=pool.pool_id)
        assert seat, (
            f"The pool has no Leios committee seat in epoch {this_epoch}, so there is "
            "nothing to say about its key"
        )
        assert bls.get_bls_pub_key(bls_key_state=seat.get("key")) == pool.vkey, (
            f"The seat doesn't report the registered BLS key: {seat}"
        )
        assert seat["key"]["bksRegisteredIn"] == registered_in, (
            f"The seat reports the key as registered in epoch "
            f"{seat['key']['bksRegisteredIn']} instead of {registered_in}: {seat}"
        )

        # An aged-out key cannot vote either, so rule that out
        max_key_age = bls.get_max_key_age(cluster_obj=cluster)
        assert this_epoch < registered_in + max_key_age, (
            f"The BLS key registered in epoch {registered_in} is already past its "
            f"{max_key_age} epoch lifetime in epoch {this_epoch}, so the proof of "
            "possession is not what keeps it from voting"
        )

        assert seat["voting"] is False, (
            f"The pool is voting in epoch {this_epoch} with a BLS key whose proof of "
            f"possession belongs to another key: {seat}"
        )
