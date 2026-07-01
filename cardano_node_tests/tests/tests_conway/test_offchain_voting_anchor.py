"""Tests for db-sync handling of off-chain voting anchor metadata.

These tests exercise the behaviour introduced in cardano-db-sync PR #2005
(https://github.com/IntersectMBO/cardano-db-sync/pull/2005, fixes issue #1995): when an
anchor downloads with a matching hash, db-sync stores it regardless of whether it parses,
and records the outcome in ``off_chain_vote_data.is_valid``:

* ``TRUE``  - valid JSON that db-sync can decode (CIP-100 / CIP-108 / CIP-119); the related
  tables (authors, references, external updates, gov action / DRep data) are populated from it.
* ``FALSE`` - valid JSON that db-sync cannot decode against any supported CIP schema (e.g. a
  reference ``@type`` that CIP-100 does not allow); the JSON is stored but related tables stay
  empty.
* ``NULL``  - content that is not valid JSON; ``json`` holds an error object and ``bytes`` the
  raw data.

Note that db-sync falls back to generic CIP-100 decoding, so a valid CIP-100 document attached to
a DRep anchor is still ``is_valid = TRUE`` even though it carries no DRep-specific fields (in that
case ``off_chain_vote_drep_data`` is simply left empty).

A hash mismatch is the one case that is *not* stored: it is retried and recorded in
``off_chain_vote_fetch_error`` instead. That path is covered for DRep anchors by
``test_drep.py::TestDReps::test_register_wrong_metadata``; it cannot be reproduced for gov-action
anchors because cardano-cli validates the anchor hash against the fetched URL at build time.

The gov-action (info action) variants live in :class:`TestGovActionAnchor`; the DRep variants in
:class:`TestDrepAnchor`.
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
        VERSIONS.transaction_era < VERSIONS.CONWAY,
        reason="runs only with Tx era >= Conway",
    ),
    pytest.mark.dbsync_config,
]

# Anchor content is fetched by db-sync over HTTP, so each anchor lives in a committed data file
# *and* is served at a stable (shortened) URL that resolves to that same file. The expected hash
# and bytes are computed locally from the committed file - no values are hard-coded here - so the
# files are the single source of truth.
#   * conformant     - valid CIP-100/CIP-108 document -> db-sync decodes it -> is_valid = TRUE
#   * non-conformant - valid JSON but invalid CIP-100 (an author is missing its witness / a
#                      reference @type is not in the allowed set; the issue #1995 scenario)
#                      -> is_valid = FALSE
#   * invalid        - not valid JSON at all -> is_valid = NULL
#
# TODO(tinyurl): the non-conformant and invalid data files are committed separately on branch
# `artur/offchain-voting-anchor-test-data`. Once that is merged to master, re-point the two
# shortlinks below (they currently resolve to personal gists) to the official committed files
# under raw.githubusercontent.com/IntersectMBO/cardano-node-tests (.../tests/data/<file>).
# A shortlink is required because an on-chain anchor URL is limited to 128 bytes. The shortlink
# strings can stay the same (content is byte-identical, so the locally computed hashes still
# match); update the two URLs below only if new shortlinks are created instead.
CONFORMANT_ANCHOR_FILE = DATA_DIR / "governance_action_anchor.json"
CONFORMANT_ANCHOR_URL = "https://tinyurl.com/cardano-qa-anchor"
NON_CONFORMANT_ANCHOR_FILE = DATA_DIR / "governance_action_anchor_cip_100_non_conformant.json"
NON_CONFORMANT_ANCHOR_URL = "https://tinyurl.com/7nz8964r"  # TODO(tinyurl): re-point to repo file
INVALID_ANCHOR_FILE = DATA_DIR / "governance_action_anchor_invalid_json.json"
INVALID_ANCHOR_URL = "https://tinyurl.com/muy62k9v"  # TODO(tinyurl): re-point to repo file


def _wait_for_off_chain_vote_data(
    *, data_hash: str, voting_anchor_id: int | None = None, timeout: int = 360
) -> dbsync_types.OffChainVoteDataRecord:
    """Wait until db-sync stores the off-chain vote data and return it.

    db-sync's off-chain fetch loop sleeps 300s between passes, so the row can appear up to
    ~5 minutes after the anchor is on-chain. A missing row once the timeout elapses is a real
    failure (a regression of the PR #2005 behaviour).

    The same content can be referenced by several anchors (e.g. a gov action and a DRep
    registration share a ``data_hash``); pass ``voting_anchor_id`` to target a specific one.
    """

    def _query_func() -> dbsync_types.OffChainVoteDataRecord:
        data = dbsync_utils.get_action_data(data_hash=data_hash, voting_anchor_id=voting_anchor_id)
        if data is None:
            msg = (
                f"No off_chain_vote_data for anchor hash {data_hash} "
                f"(voting_anchor_id={voting_anchor_id}) in db-sync yet"
            )
            raise dbsync_utils.DbSyncNoResponseError(msg)
        return data

    return tp.cast(
        "dbsync_types.OffChainVoteDataRecord",
        dbsync_utils.retry_query(query_func=_query_func, timeout=timeout),
    )


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
    # valid JSON at all (is_valid=NULL) there is nothing to decode, so it stays empty.
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
        # These tests lock a gov action deposit per proposal and never get it back (no
        # ratification/expiration lifecycle), and the user is shared (cached) across the class.
        # Fund like the other governance-proposal fixtures (cf. test_committee.pool_user_lg):
        # the fixture re-funds `amount` whenever the balance drops below `min_amount`, which runs
        # on each (function-scoped) call, so every proposal can cover the deposit.
        return common.get_registered_pool_user(
            name_template=name_template,
            cluster_manager=cluster_manager,
            cluster_obj=cluster,
            caching_key=key,
            amount=400_000_000,
            min_amount=350_000_000,
        )

    def _propose_info_action(
        self,
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

    def _get_gov_action_voting_anchor_id(self, *, action_txid: str) -> int:
        """Return the ``voting_anchor_id`` db-sync assigned to the info action proposal.

        The same anchor content can be shared with DRep tests, so callers that assert anchor
        details use this to target the gov action's own ``off_chain_vote_data`` row.
        """

        def _query_func() -> int:
            proposals = dbsync_utils.get_gov_action_proposals(txhash=action_txid)
            if not proposals:
                msg = f"Gov action proposal for tx {action_txid} not in db-sync yet"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            return proposals[0].voting_anchor_id

        return tp.cast("int", dbsync_utils.retry_query(query_func=_query_func, timeout=120))

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.upgrade_step1
    def test_valid_voting_anchor_json(
        self,
        cluster_use_governance: governance_utils.GovClusterT,
        pool_user_ug: clusterlib.PoolUser,
    ):
        """Test an info action with a valid, CIP-conformant anchor (is_valid=TRUE).

        * Propose an info action with a CIP-100/CIP-108 conformant anchor.
        * Verify db-sync stores ``off_chain_vote_data`` with ``is_valid = TRUE``.
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
        voting_anchor_id = self._get_gov_action_voting_anchor_id(action_txid=action_txid)

        _url = helpers.get_vcs_link()
        [r.start(url=_url) for r in (reqc.db007, reqc.db015, reqc.db017, reqc.db018, reqc.db020)]
        _wait_for_off_chain_vote_data(data_hash=anchor_data_hash, voting_anchor_id=voting_anchor_id)
        dbsync_utils.check_action_data(
            json_anchor_file=json_anchor_file,
            anchor_data_hash=anchor_data_hash,
            expected_is_valid=True,
            voting_anchor_id=voting_anchor_id,
        )
        [r.success() for r in (reqc.db007, reqc.db015, reqc.db017, reqc.db018, reqc.db020)]

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
    @pytest.mark.upgrade_step1
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
        voting_anchor_id = self._get_gov_action_voting_anchor_id(action_txid=action_txid)

        reqc.db007.start(url=helpers.get_vcs_link())
        reqc.db015.start(url=helpers.get_vcs_link())
        db_data = _wait_for_off_chain_vote_data(
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
    @pytest.mark.dbsync
    @pytest.mark.upgrade_step1
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
        voting_anchor_id = self._get_gov_action_voting_anchor_id(action_txid=action_txid)

        reqc.db007.start(url=helpers.get_vcs_link())
        reqc.db015.start(url=helpers.get_vcs_link())
        db_data = _wait_for_off_chain_vote_data(
            data_hash=anchor_data_hash, voting_anchor_id=voting_anchor_id
        )

        # The `json` column holds a generated error object rather than the (unparseable) body.
        # Assert on stable substrings instead of the exact decoder message, which can change.
        assert "not valid JSON" in db_data.json.get("error", ""), (
            f"Missing expected error in stored JSON: {db_data.json}"
        )
        assert "parse_error" in db_data.json, f"Missing parse_error in stored JSON: {db_data.json}"
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
    not-CIP-decodable (``is_valid=FALSE``) and invalid-JSON (``is_valid=NULL``) cases, both of
    which leave ``off_chain_vote_drep_data`` empty.
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

    def _register_drep_with_anchor(
        self,
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

        tx_output_reg = clusterlib_utils.build_and_submit_tx(
            cluster_obj=cluster,
            name_template=f"{name_template}_reg",
            src_address=payment_addr.address,
            tx_files=tx_files_reg,
            deposit=reg_drep.deposit,
        )

        reg_drep_state = cluster.g_query.get_drep_state(drep_vkey_file=reg_drep.key_pair.vkey_file)
        assert reg_drep_state[0][0]["keyHash"] == reg_drep.drep_id, "DRep was not registered"

        def _retire_drep() -> None:
            """Retire the DRep so it does not affect other tests."""
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

        request.addfinalizer(_retire_drep)

        reg_out_utxos = cluster.g_query.get_utxo(tx_raw_output=tx_output_reg)
        assert (
            clusterlib.filter_utxos(utxos=reg_out_utxos, address=payment_addr.address)[0].amount
            == clusterlib.calculate_utxos_balance(tx_output_reg.txins)
            - tx_output_reg.fee
            - reg_drep.deposit
        ), f"Incorrect balance for source address `{payment_addr.address}`"

        return reg_drep

    def _get_drep_voting_anchor_id(self, *, reg_drep: governance_utils.DRepRegistration) -> int:
        """Return the ``voting_anchor_id`` db-sync assigned to the DRep registration."""

        def _query_func() -> int:
            drep_data = dbsync_utils.get_drep(
                drep_hash=reg_drep.drep_id, drep_deposit=reg_drep.deposit
            )
            if drep_data is None or drep_data.voting_anchor_id is None:
                msg = f"DRep {reg_drep.drep_id} registration not in db-sync yet"
                raise dbsync_utils.DbSyncNoResponseError(msg)
            return drep_data.voting_anchor_id

        return tp.cast("int", dbsync_utils.retry_query(query_func=_query_func, timeout=120))

    def _assert_empty_drep_data(self, *, voting_anchor_id: int) -> None:
        """Assert that no ``off_chain_vote_drep_data`` rows were derived for the anchor."""
        drep_rows = list(
            dbsync_queries.query_off_chain_vote_drep_data(voting_anchor_id=voting_anchor_id)
        )
        assert drep_rows == [], (
            f"Expected no off_chain_vote_drep_data rows for anchor {voting_anchor_id}, "
            f"found {len(drep_rows)}"
        )

    @allure.link(helpers.get_vcs_link())
    @pytest.mark.long
    @pytest.mark.dbsync
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
        db_data = _wait_for_off_chain_vote_data(
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
    @pytest.mark.dbsync
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
        db_data = _wait_for_off_chain_vote_data(
            data_hash=drep_metadata_hash, voting_anchor_id=voting_anchor_id
        )

        assert "not valid JSON" in db_data.json.get("error", ""), (
            f"Missing expected error in stored JSON: {db_data.json}"
        )
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
