"""Tests for db-sync handling of off-chain voting anchor metadata.

When an anchor downloads with a matching hash, db-sync stores it regardless of whether it parses
(cardano-db-sync PR #2005 / issue #1995) and records the outcome in
``off_chain_vote_data.is_valid``:

* ``TRUE``  - valid JSON decodable against CIP-100/108/119; related tables are populated.
* ``FALSE`` - valid JSON that no supported CIP schema accepts; stored, related tables empty.
* ``NULL``  - not valid JSON; ``json`` holds an error object and ``bytes`` the raw data.

db-sync falls back to CIP-100 decoding, so a CIP-100 document on a DRep anchor is still
``is_valid = TRUE`` (with an empty ``off_chain_vote_drep_data``).

A hash mismatch is the one case not stored (retried, recorded in ``off_chain_vote_fetch_error``);
it is covered by ``test_drep.py::TestDReps::test_register_wrong_metadata``. db-sync's fetch and
hash check are anchor-type-agnostic, so the gov-action variant is not duplicated here.

Gov-action variants live in :class:`TestGovActionAnchor`, DRep variants in :class:`TestDrepAnchor`.
"""

import json
import logging
import pathlib as pl
import typing as tp

import allure
import pytest
from _pytest.fixtures import FixtureRequest
from cardano_clusterlib import clusterlib

from cardano_node_tests.cluster_management import cluster_management
from cardano_node_tests.tests import common
from cardano_node_tests.tests import reqs_conway as reqc
from cardano_node_tests.utils import clusterlib_utils
from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import dbsync_utils
from cardano_node_tests.utils import governance_utils
from cardano_node_tests.utils import helpers
from cardano_node_tests.utils.versions import VERSIONS

LOGGER = logging.getLogger(__name__)
DATA_DIR = pl.Path(__file__).parent.parent / "data"

pytestmark = [
    pytest.mark.skipif(
        VERSIONS.transaction_era < VERSIONS.CONWAY_FIRST,
        reason="runs only with Tx era >= Conway",
    ),
    pytest.mark.dbsync_config,
    pytest.mark.needs_dbsync,
]

# Each anchor is a committed data file served at a stable public raw URL. The hash and bytes are
# computed locally from the file, so the committed files are the single source of truth.
CONFORMANT_ANCHOR_FILE = DATA_DIR / "ga_anchor.json"
CONFORMANT_ANCHOR_URL = common.PUBLIC_ACTION_ANCHOR_URL
NON_CONFORMANT_ANCHOR_FILE = DATA_DIR / "ga_anchor_nonconf.json"
NON_CONFORMANT_ANCHOR_URL = common.PUBLIC_ACTION_ANCHOR_NONCONFORMANT_URL
INVALID_ANCHOR_FILE = DATA_DIR / "ga_anchor_invalid.json"
INVALID_ANCHOR_URL = common.PUBLIC_ACTION_ANCHOR_INVALID_URL


def _assert_unparsed_anchor_data(
    *,
    db_data: dbsync_types.OffChainVoteDataRecord,
    url: str,
    anchor_file: pl.Path,
    data_hash: str,
    anchor_type: str,
    expected_is_valid: bool | None,
) -> None:
    """Assert the shape of a stored anchor whose body was not parsed into related tables.

    Covers the is_valid=FALSE (valid JSON, not CIP-decodable) and is_valid=NULL (not valid JSON)
    cases: the row and the raw bytes are present, but no authors/references/gov-action/
    external-update rows were derived from it. The expected ``bytes`` are taken from the
    committed ``anchor_file`` (the same content the URL serves).
    """
    assert db_data.hash == data_hash, f"Unexpected hash: {db_data.hash} vs {data_hash}"
    assert db_data.is_valid is expected_is_valid, (
        f"Unexpected is_valid: {db_data.is_valid} vs {expected_is_valid}"
    )

    # db-sync stores the raw downloaded bytes verbatim, so they must equal the committed file.
    assert db_data.bytes == anchor_file.read_bytes().hex(), (
        "Stored bytes do not match the committed anchor file"
    )

    # `warning` records *why* db-sync could not decode the body. For valid JSON that fails the
    # CIP schema (is_valid=FALSE) db-sync stores the decoder error here; for content that is not
    # valid JSON at all (is_valid=NULL) there is nothing to decode, so it stays NULL.
    if expected_is_valid is False:
        assert db_data.warning, f"Expected a decode warning, got: {db_data.warning!r}"
    else:
        assert db_data.warning is None, f"Unexpected warning: {db_data.warning}"

    # Fields that are only derived from a decodable body must stay empty here.
    assert db_data.language == "", f"Unexpected language: {db_data.language}"
    assert db_data.comment is None, f"Unexpected comment: {db_data.comment}"
    assert db_data.authors == [], f"Unexpected authors: {db_data.authors}"
    assert db_data.references == [], f"Unexpected references: {db_data.references}"
    assert db_data.gov_action_data == {}, f"Unexpected gov action data: {db_data.gov_action_data}"
    assert db_data.external_updates == [], (
        f"Unexpected external updates: {db_data.external_updates}"
    )

    assert db_data.voting_anchor["url"] == url, "Unexpected voting anchor url"
    assert db_data.voting_anchor["data_hash"] == data_hash, "Unexpected voting anchor hash"
    assert db_data.voting_anchor["type"] == anchor_type, "Unexpected voting anchor type"


def _assert_invalid_json_error(*, db_data: dbsync_types.OffChainVoteDataRecord) -> None:
    """Assert the error object db-sync stores in ``json`` for content that is not valid JSON.

    Asserts on stable substrings and key presence instead of the exact decoder message, which
    can change between db-sync versions.
    """
    assert isinstance(db_data.json, dict), f"Stored JSON is not an error object: {db_data.json!r}"
    assert "not valid JSON" in db_data.json.get("error", ""), (
        f"Missing expected error in stored JSON: {db_data.json}"
    )
    assert "parse_error" in db_data.json, f"Missing parse_error in stored JSON: {db_data.json}"


class TestGovActionAnchor:
    """Tests for off-chain voting anchor metadata attached to governance (info) actions."""

    @pytest.fixture
    def pool_user_ug(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster_use_governance: governance_utils.GovClusterT,
    ) -> clusterlib.PoolUser:
        """Create a pool user for "use governance"."""
        cluster, __ = cluster_use_governance
        key = helpers.get_current_line_str()
        name_template = common.get_test_id(cluster)
        # Re-fund when the balance drops below `min_amount` so each proposal can cover the gov
        # action deposit (it is never returned here). Cf. test_committee.pool_user_lg.
        return common.get_registered_pool_user(
            name_template=name_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            caching_key=key,
            amount=400_000_000,
            min_amount=350_000_000,
        )

    @staticmethod
    def _propose_info_action(
        *,
        cluster: clusterlib.ClusterLib,
        pool_user: clusterlib.PoolUser,
        name_template: str,
        anchor_url: str,
        anchor_data_hash: str,
    ) -> str:
        """Create and submit an info action carrying the given anchor; return its tx hash.

        Submitting the proposal registers the ``gov_action`` voting anchor that db-sync's
        off-chain fetch thread downloads. No votes or ratification are needed to populate
        ``off_chain_vote_data``, so the full action lifecycle is intentionally omitted here.

        The ``pool_user`` fixture keeps the payment address funded (see its ``min_amount``), so
        each proposal can cover the gov action deposit.
        """
        deposit_amt = cluster.g_query.get_gov_action_deposit()

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.cli016, reqc.cip031a_03, reqc.cip054_06)]
        info_action = cluster.g_governance.action.create_info(
            action_name=name_template,
            deposit_amt=deposit_amt,
            anchor_url=anchor_url,
            anchor_data_hash=anchor_data_hash,
            deposit_return_stake_vkey_file=pool_user.stake.vkey_file,
        )
        [r.success() for r in (reqc.cli016, reqc.cip031a_03, reqc.cip054_06)]

        tx_files_action = clusterlib.TxFiles(
            proposal_files=[info_action.action_file],
            signing_key_files=[pool_user.payment.skey_file],
        )

        # Make sure we have enough time to submit the proposal in one epoch
        clusterlib_utils.wait_for_epoch_interval(
            cluster_obj=cluster, start=1, stop=common.EPOCH_STOP_SEC_BUFFER
        )

        reqc.cli023.start(url=helpers.get_vcs_link())
        tx_output_action = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{name_template}_action",
            src_address=pool_user.payment.address,
            build_method=clusterlib_utils.BuildMethods.BUILD,
            tx_files=tx_files_action,
        )
        reqc.cli023.success()

        out_utxos_action = cluster.g_query.get_utxo(tx_raw_output=tx_output_action)
        assert (
            clusterlib.filter_utxos(utxos=out_utxos_action, address=pool_user.payment.address)[
                0
            ].amount
            == clusterlib.calculate_utxos_balance(tx_output_action.txins)
            - tx_output_action.fee
            - deposit_amt
        ), f"Incorrect balance for source address `{pool_user.payment.address}`"

        return cluster.g_transaction.get_txid(tx_body_file=tx_output_action.out_file)

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_valid_voting_anchor_json(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test an info action with a valid, CIP-conformant anchor (is_valid=TRUE).

        * Propose an info action with a CIP-100/CIP-108 conformant anchor.
        * Verify db-sync stores ``off_chain_vote_data`` with ``is_valid = TRUE``, the raw bytes
          and no decode warning.
        * Verify the authors, references and external updates are populated and match the file.
        """
        cluster, __ = cluster_use_governance
        temp_template = common.get_test_id(cluster)

        anchor_data_hash = cluster.g_governance.get_anchor_data_hash(
            file_text=CONFORMANT_ANCHOR_FILE
        )
        with open(CONFORMANT_ANCHOR_FILE, encoding="utf-8") as anchor_fp:
            json_anchor_file = json.load(anchor_fp)

        action_txid = self._propose_info_action(
            cluster=cluster,
            pool_user=pool_user_ug,
            name_template=temp_template,
            anchor_url=CONFORMANT_ANCHOR_URL,
            anchor_data_hash=anchor_data_hash,
        )
        voting_anchor_id = dbsync_utils.get_gov_action_voting_anchor_id(txhash=action_txid)

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.db007, reqc.db015, reqc.db017, reqc.db018, reqc.db020)]
        db_data = dbsync_utils.wait_for_off_chain_vote_data(
            data_hash=anchor_data_hash, voting_anchor_id=voting_anchor_id
        )

        # `check_action_data` compares the decoded content; check the storage-level fields that
        # the FALSE/NULL variants assert via `_assert_unparsed_anchor_data` here as well.
        assert db_data.bytes == CONFORMANT_ANCHOR_FILE.read_bytes().hex(), (
            "Stored bytes do not match the committed anchor file"
        )
        assert db_data.warning is None, f"Unexpected warning: {db_data.warning}"
        assert db_data.voting_anchor["url"] == CONFORMANT_ANCHOR_URL, "Unexpected voting anchor url"
        assert db_data.voting_anchor["type"] == "gov_action", "Unexpected voting anchor type"

        dbsync_utils.check_action_data(
            json_anchor_file=json_anchor_file,
            anchor_data_hash=anchor_data_hash,
            action_txid=action_txid,
            voting_anchor_id=voting_anchor_id,
        )
        [r.success() for r in (reqc.db007, reqc.db015, reqc.db017, reqc.db018, reqc.db020)]

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_valid_voting_anchor_json_not_conforming_to_cip_100(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test an info action with valid JSON that does not conform to CIP-100 (is_valid=FALSE).

        * Propose an info action with valid JSON that db-sync cannot decode against CIP-100.
        * Verify db-sync stores ``off_chain_vote_data`` with ``is_valid = FALSE``, the parsed
          JSON, the raw bytes, and empty related tables.
        """
        cluster, __ = cluster_use_governance
        temp_template = common.get_test_id(cluster)

        anchor_data_hash = cluster.g_governance.get_anchor_data_hash(
            file_text=NON_CONFORMANT_ANCHOR_FILE
        )
        with open(NON_CONFORMANT_ANCHOR_FILE, encoding="utf-8") as anchor_fp:
            json_anchor_file = json.load(anchor_fp)

        action_txid = self._propose_info_action(
            cluster=cluster,
            pool_user=pool_user_ug,
            name_template=temp_template,
            anchor_url=NON_CONFORMANT_ANCHOR_URL,
            anchor_data_hash=anchor_data_hash,
        )
        voting_anchor_id = dbsync_utils.get_gov_action_voting_anchor_id(txhash=action_txid)

        reqc.db007.start(url=helpers.get_vcs_link())
        reqc.db015.start(url=helpers.get_vcs_link())
        db_data = dbsync_utils.wait_for_off_chain_vote_data(
            data_hash=anchor_data_hash, voting_anchor_id=voting_anchor_id
        )

        # Valid JSON is stored verbatim, so the db representation matches the file.
        assert db_data.json == json_anchor_file, (
            "Stored JSON does not match the anchor file content"
        )
        _assert_unparsed_anchor_data(
            db_data=db_data,
            url=NON_CONFORMANT_ANCHOR_URL,
            anchor_file=NON_CONFORMANT_ANCHOR_FILE,
            data_hash=anchor_data_hash,
            anchor_type="gov_action",
            expected_is_valid=False,
        )
        reqc.db015.success()
        reqc.db007.success()

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_invalid_voting_anchor_json(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test an info action with content that is not valid JSON (is_valid=NULL).

        This is the scenario from issue #1995: the hash matches but the body cannot be parsed.

        * Propose an info action whose anchor content is not valid JSON.
        * Verify db-sync stores ``off_chain_vote_data`` with ``is_valid = NULL``, an error
          object in ``json``, the raw bytes, and empty related tables.
        """
        cluster, __ = cluster_use_governance
        temp_template = common.get_test_id(cluster)

        anchor_data_hash = cluster.g_governance.get_anchor_data_hash(file_text=INVALID_ANCHOR_FILE)

        action_txid = self._propose_info_action(
            cluster=cluster,
            pool_user=pool_user_ug,
            name_template=temp_template,
            anchor_url=INVALID_ANCHOR_URL,
            anchor_data_hash=anchor_data_hash,
        )
        voting_anchor_id = dbsync_utils.get_gov_action_voting_anchor_id(txhash=action_txid)

        reqc.db007.start(url=helpers.get_vcs_link())
        reqc.db015.start(url=helpers.get_vcs_link())
        db_data = dbsync_utils.wait_for_off_chain_vote_data(
            data_hash=anchor_data_hash, voting_anchor_id=voting_anchor_id
        )

        # The `json` column holds a generated error object rather than the (unparseable) body.
        _assert_invalid_json_error(db_data=db_data)
        _assert_unparsed_anchor_data(
            db_data=db_data,
            url=INVALID_ANCHOR_URL,
            anchor_file=INVALID_ANCHOR_FILE,
            data_hash=anchor_data_hash,
            anchor_type="gov_action",
            expected_is_valid=None,
        )
        reqc.db015.success()
        reqc.db007.success()


class TestDrepAnchor:
    """Tests for off-chain voting anchor metadata attached to DRep registrations.

    Issue #1995 was reported against a DRep (CIP-119) anchor, and db-sync decodes the anchor
    according to its on-chain type, so the DRep path is exercised separately here. The
    valid-metadata DRep path (``is_valid=TRUE`` with a populated ``off_chain_vote_drep_data``)
    and the hash-mismatch path are already covered by ``test_drep.py``, so this class adds the
    CIP-100 fallback (``is_valid=TRUE``), not-CIP-decodable (``is_valid=FALSE``) and
    invalid-JSON (``is_valid=NULL``) cases, all of which leave ``off_chain_vote_drep_data``
    empty.
    """

    @pytest.fixture
    def payment_addr(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
    ) -> clusterlib.AddressRecord:
        """Create a payment address with funds."""
        test_id = common.get_test_id(cluster)
        key = helpers.get_current_line_str()
        return common.get_payment_addr(
            name_template=test_id,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            caching_key=key,
        )

    @staticmethod
    def _register_drep_with_anchor(
        *,
        cluster: clusterlib.ClusterLib,
        cluster_manager: cluster_management.ClusterManager,
        payment_addr: clusterlib.AddressRecord,
        request: FixtureRequest,
        name_template: str,
        drep_metadata_url: str,
        drep_metadata_hash: str,
    ) -> governance_utils.DRepRegistration:
        """Register a DRep with the given metadata anchor and schedule its retirement.

        The DRep is retired in a finalizer so it does not affect DRep distribution in other
        tests sharing the cluster.
        """
        deposit_drep_amt = cluster.g_query.get_drep_deposit()
        clusterlib_utils.fund_from_faucet(
            payment_addr,
            cluster_obj=cluster,
            all_faucets=cluster_manager.cache.addrs_data,
            amount=deposit_drep_amt + 10_000_000,
        )

        reg_drep = governance_utils.get_drep_reg_record(
            cluster_obj=cluster,
            name_template=name_template,
            drep_metadata_url=drep_metadata_url,
            drep_metadata_hash=drep_metadata_hash,
        )

        tx_files_reg = clusterlib.TxFiles(
            certificate_files=[reg_drep.registration_cert],
            signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
        )

        def _retire_drep() -> None:
            """Retire the DRep so it does not affect other tests."""
            drep_state = cluster.g_query.get_drep_state(drep_vkey_file=reg_drep.key_pair.vkey_file)
            if not drep_state:
                LOGGER.warning(
                    f"DRep '{reg_drep.drep_id}' is not registered at cleanup time "
                    "(the registration tx may still be in the mempool), nothing to retire."
                )
                return
            ret_cert = cluster.g_governance.drep.gen_retirement_cert(
                cert_name=f"{name_template}_cleanup",
                deposit_amt=reg_drep.deposit,
                drep_vkey_file=reg_drep.key_pair.vkey_file,
            )
            tx_files_ret = clusterlib.TxFiles(
                certificate_files=[ret_cert],
                signing_key_files=[payment_addr.skey_file, reg_drep.key_pair.skey_file],
            )
            clusterlib_utils.build_and_submit_tx(
                cluster_obj=cluster,
                name_template=f"{name_template}_ret_cleanup",
                src_address=payment_addr.address,
                tx_files=tx_files_ret,
                deposit=-reg_drep.deposit,
            )

            ret_drep_state = cluster.g_query.get_drep_state(
                drep_vkey_file=reg_drep.key_pair.vkey_file
            )
            assert not ret_drep_state, "DRep was not retired"

        # Register the finalizer before the registration tx is even submitted: the submit call
        # can raise after the tx was in fact included, and the finalizer itself skips retirement
        # when the DRep is not registered, so registering the finalizer early cannot hurt but
        # prevents a leak.
        request.addfinalizer(_retire_drep)

        tx_output_reg = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{name_template}_reg",
            src_address=payment_addr.address,
            tx_files=tx_files_reg,
            deposit=reg_drep.deposit,
        )

        reg_drep_state = cluster.g_query.get_drep_state(drep_vkey_file=reg_drep.key_pair.vkey_file)
        assert reg_drep_state and reg_drep_state[0][0]["keyHash"] == reg_drep.drep_id, (
            "DRep was not registered"
        )

        reg_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_reg)
        assert (
            clusterlib.filter_utxos(utxos=reg_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_reg.txins)
            - tx_output_reg.fee
            - reg_drep.deposit
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        return reg_drep

    @staticmethod
    def _get_drep_voting_anchor_id(*, reg_drep: governance_utils.DRepRegistration) -> int:
        """Return the ``voting_anchor_id`` db-sync assigned to the DRep registration."""

        def _query_func() -> int:
            drep_data = dbsync_utils.get_drep(
                drep_hash=reg_drep.drep_id, drep_deposit=reg_drep.deposit
            )
            if drep_data is None:
                msg = f"DRep {reg_drep.drep_id} registration not in db-sync yet"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            # db-sync writes each block in a single db transaction, so a registration row
            # without its voting anchor is a deterministic defect - fail without retrying.
            assert drep_data.voting_anchor_id is not None, (
                f"DRep {reg_drep.drep_id} registration in db-sync has no voting anchor"
            )
            return drep_data.voting_anchor_id

        return tp.cast(int, dbsync_utils.retry_query(query_func=_query_func, timeout=120))

    @staticmethod
    def _assert_empty_drep_data(*, voting_anchor_id: int) -> None:
        """Assert that no ``off_chain_vote_drep_data`` rows were derived for the anchor.

        Relies on db-sync inserting ``off_chain_vote_data`` and any derived
        ``off_chain_vote_drep_data`` rows in the same transaction, so once the vote-data row is
        visible (callers wait for it first), the absence of drep-data rows is conclusive.
        """
        drep_rows = list(
            dbsync_queries.query_off_chain_vote_drep_data(voting_anchor_id=voting_anchor_id)
        )
        assert drep_rows == [], (
            f"Expected no off_chain_vote_drep_data rows for anchor {voting_anchor_id}, "
            f"found {len(drep_rows)}"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_drep_cip100_anchor(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        request: FixtureRequest,
    ):
        """Register a DRep whose anchor is a CIP-100/CIP-108 document (is_valid=TRUE).

        db-sync decodes DRep anchors as CIP-119 and falls back to CIP-100. A CIP-100/CIP-108
        document therefore still decodes (``is_valid = TRUE``), but carries no CIP-119 DRep
        fields, so ``off_chain_vote_drep_data`` stays empty.

        * Register a DRep whose anchor is the CIP-100/CIP-108 conformant gov-action document.
        * Verify db-sync stores ``off_chain_vote_data`` with ``is_valid = TRUE``, the parsed
          JSON, the raw bytes and no decode warning.
        * Verify no ``off_chain_vote_drep_data`` rows were derived from it.
        """
        temp_template = common.get_test_id(cluster)

        drep_metadata_hash = cluster.g_governance.drep.get_metadata_hash(
            drep_metadata_file=CONFORMANT_ANCHOR_FILE
        )
        with open(CONFORMANT_ANCHOR_FILE, encoding="utf-8") as anchor_fp:
            json_anchor_file = json.load(anchor_fp)

        reg_drep = self._register_drep_with_anchor(
            cluster=cluster,
            cluster_manager=cluster_manager,
            payment_addr=payment_addr,
            request=request,
            name_template=temp_template,
            drep_metadata_url=CONFORMANT_ANCHOR_URL,
            drep_metadata_hash=drep_metadata_hash,
        )

        voting_anchor_id = self._get_drep_voting_anchor_id(reg_drep=reg_drep)

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.db015, reqc.db016)]
        db_data = dbsync_utils.wait_for_off_chain_vote_data(
            data_hash=drep_metadata_hash, voting_anchor_id=voting_anchor_id
        )

        assert db_data.is_valid is True, f"Unexpected is_valid: {db_data.is_valid}"
        assert db_data.json == json_anchor_file, (
            "Stored JSON does not match the anchor file content"
        )
        assert db_data.bytes == CONFORMANT_ANCHOR_FILE.read_bytes().hex(), (
            "Stored bytes do not match the committed anchor file"
        )
        assert db_data.warning is None, f"Unexpected warning: {db_data.warning}"
        assert db_data.voting_anchor["url"] == CONFORMANT_ANCHOR_URL, "Unexpected voting anchor url"
        assert db_data.voting_anchor["type"] == "drep", "Unexpected voting anchor type"
        self._assert_empty_drep_data(voting_anchor_id=voting_anchor_id)
        [r.success() for r in (reqc.db015, reqc.db016)]

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_drep_anchor_json_not_conforming(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        request: FixtureRequest,
    ):
        """Register a DRep with valid JSON that db-sync cannot decode (is_valid=FALSE).

        Uses metadata that is valid JSON but not CIP-100-decodable, so db-sync stores it with
        ``is_valid = FALSE`` and leaves ``off_chain_vote_drep_data`` empty. The same content is
        also used by a gov-action test, so the DRep row is looked up by its own voting anchor id.
        """
        temp_template = common.get_test_id(cluster)

        drep_metadata_hash = cluster.g_governance.drep.get_metadata_hash(
            drep_metadata_file=NON_CONFORMANT_ANCHOR_FILE
        )
        with open(NON_CONFORMANT_ANCHOR_FILE, encoding="utf-8") as anchor_fp:
            json_anchor_file = json.load(anchor_fp)

        reg_drep = self._register_drep_with_anchor(
            cluster=cluster,
            cluster_manager=cluster_manager,
            payment_addr=payment_addr,
            request=request,
            name_template=temp_template,
            drep_metadata_url=NON_CONFORMANT_ANCHOR_URL,
            drep_metadata_hash=drep_metadata_hash,
        )

        voting_anchor_id = self._get_drep_voting_anchor_id(reg_drep=reg_drep)

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.db015, reqc.db016)]
        db_data = dbsync_utils.wait_for_off_chain_vote_data(
            data_hash=drep_metadata_hash, voting_anchor_id=voting_anchor_id
        )

        assert db_data.json == json_anchor_file, (
            "Stored JSON does not match the anchor file content"
        )
        _assert_unparsed_anchor_data(
            db_data=db_data,
            url=NON_CONFORMANT_ANCHOR_URL,
            anchor_file=NON_CONFORMANT_ANCHOR_FILE,
            data_hash=drep_metadata_hash,
            anchor_type="drep",
            expected_is_valid=False,
        )
        self._assert_empty_drep_data(voting_anchor_id=voting_anchor_id)
        [r.success() for r in (reqc.db015, reqc.db016)]

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    def test_drep_invalid_anchor_json(
        self,
        cluster_manager: cluster_management.ClusterManager,
        cluster: clusterlib.ClusterLib,
        payment_addr: clusterlib.AddressRecord,
        request: FixtureRequest,
    ):
        """Register a DRep whose anchor content is not valid JSON (is_valid=NULL).

        * Register a DRep with an anchor whose content is not valid JSON.
        * Verify db-sync stores ``off_chain_vote_data`` with ``is_valid = NULL``, an error
          object in ``json``, and leaves ``off_chain_vote_drep_data`` empty.
        """
        temp_template = common.get_test_id(cluster)

        drep_metadata_hash = cluster.g_governance.drep.get_metadata_hash(
            drep_metadata_file=INVALID_ANCHOR_FILE
        )

        reg_drep = self._register_drep_with_anchor(
            cluster=cluster,
            cluster_manager=cluster_manager,
            payment_addr=payment_addr,
            request=request,
            name_template=temp_template,
            drep_metadata_url=INVALID_ANCHOR_URL,
            drep_metadata_hash=drep_metadata_hash,
        )

        voting_anchor_id = self._get_drep_voting_anchor_id(reg_drep=reg_drep)

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.db015, reqc.db016)]
        db_data = dbsync_utils.wait_for_off_chain_vote_data(
            data_hash=drep_metadata_hash, voting_anchor_id=voting_anchor_id
        )

        _assert_invalid_json_error(db_data=db_data)
        _assert_unparsed_anchor_data(
            db_data=db_data,
            url=INVALID_ANCHOR_URL,
            anchor_file=INVALID_ANCHOR_FILE,
            data_hash=drep_metadata_hash,
            anchor_type="drep",
            expected_is_valid=None,
        )
        self._assert_empty_drep_data(voting_anchor_id=voting_anchor_id)
        [r.success() for r in (reqc.db015, reqc.db016)]
