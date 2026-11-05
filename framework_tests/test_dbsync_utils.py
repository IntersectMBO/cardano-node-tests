"""Unit tests for db-sync checks without a database or cluster."""

import typing as tp
from unittest import mock

import pytest
from packaging import version

from cardano_node_tests.utils import dbsync_queries
from cardano_node_tests.utils import dbsync_types
from cardano_node_tests.utils import dbsync_utils

IMAGE_PAYLOAD = "aW1hZ2U="
IMAGE_URI = f"data:image/png;base64,{IMAGE_PAYLOAD}"
IMAGE_URL = "https://example.com/image.png"
IMAGE_HASH = "ab" * 32


@pytest.fixture
def check_image(monkeypatch: pytest.MonkeyPatch) -> tp.Callable[..., None]:
    """Check image metadata using stubbed db-sync rows and version information."""
    monkeypatch.setattr(dbsync_utils.configuration, "HAS_DBSYNC", True)

    def _check(
        *,
        dbsync_version: str,
        content_url: str,
        stored_url: str,
        image_hash: str | None = None,
        expected_hash: str | None = None,
    ) -> None:
        """Run the full DRep metadata checker for the given image representation."""
        monkeypatch.setattr(dbsync_utils.VERSIONS, "dbsync", version.parse(dbsync_version))
        image = {"contentUrl": content_url}
        if expected_hash is not None:
            image["sha256"] = expected_hash
        body = {
            "paymentAddress": "payment-address",
            "givenName": "DRep",
            "objectives": "Objectives",
            "motivations": "Motivations",
            "qualifications": "Qualifications",
        }
        metadata = {"body": {**body, "image": image}}
        db_row = dbsync_queries.OffChainVoteDrepDataDBRow(
            id=1,
            hash=memoryview(b""),
            language="en",
            comment=None,
            json=metadata,
            bytes=memoryview(b""),
            warning=None,
            is_valid=True,
            payment_address=body["paymentAddress"],
            given_name=body["givenName"],
            objectives=body["objectives"],
            motivations=body["motivations"],
            qualifications=body["qualifications"],
            image_url=stored_url,
            image_hash=image_hash,
        )
        monkeypatch.setattr(
            dbsync_queries,
            "query_off_chain_vote_drep_data",
            lambda **_kwargs: iter([db_row]),
        )
        drep_data = dbsync_types.DrepRegistrationRecord(
            id=1,
            tx_id=1,
            cert_index=0,
            deposit=0,
            drep_hash_id=1,
            voting_anchor_id=1,
            hash_hex="",
            hash_bech32="",
            has_script=False,
        )
        dbsync_utils.check_off_chain_drep_registration(drep_data=drep_data, metadata=metadata)

    return _check


@pytest.mark.parametrize(
    ("dbsync_version", "stored_url"),
    [
        ("13.7.2.1", IMAGE_PAYLOAD),
        ("13.8.0.0", IMAGE_URI),
        ("13.9.0.0", IMAGE_URI),
    ],
)
class TestInlineDrepImage:
    """Check both historical image formats without accepting regressions in newer releases."""

    def test_image_url(
        self,
        check_image: tp.Callable[..., None],
        dbsync_version: str,
        stored_url: str,
    ):
        """Accept only the image representation expected for the db-sync version."""
        check_image(dbsync_version=dbsync_version, content_url=IMAGE_URI, stored_url=stored_url)

        wrong_url = IMAGE_URI if stored_url == IMAGE_PAYLOAD else IMAGE_PAYLOAD
        with pytest.raises(AssertionError, match="'image_url' value is different"):
            check_image(dbsync_version=dbsync_version, content_url=IMAGE_URI, stored_url=wrong_url)

    def test_wrong_payload(
        self,
        check_image: tp.Callable[..., None],
        dbsync_version: str,
        stored_url: str,
    ):
        """Reject incorrect image content in either storage format."""
        with pytest.raises(AssertionError, match="'image_url' value is different"):
            check_image(
                dbsync_version=dbsync_version,
                content_url=IMAGE_URI,
                stored_url=f"{stored_url}wrong",
            )

    def test_unexpected_hash(
        self,
        check_image: tp.Callable[..., None],
        dbsync_version: str,
        stored_url: str,
    ):
        """Reject a hash for an inline image on old and new db-sync versions."""
        with pytest.raises(AssertionError, match="'image_hash' must be empty"):
            check_image(
                dbsync_version=dbsync_version,
                content_url=IMAGE_URI,
                stored_url=stored_url,
                image_hash=IMAGE_HASH,
            )


@pytest.mark.parametrize("dbsync_version", ["13.7.2.1", "13.8.0.0"])
class TestUrlDrepImage:
    """Keep URL-based image checks unchanged across db-sync versions."""

    def test_image_url(self, check_image: tp.Callable[..., None], dbsync_version: str):
        """Accept the original URL and matching image hash."""
        check_image(
            dbsync_version=dbsync_version,
            content_url=IMAGE_URL,
            stored_url=IMAGE_URL,
            image_hash=IMAGE_HASH,
            expected_hash=IMAGE_HASH,
        )

    def test_wrong_hash(self, check_image: tp.Callable[..., None], dbsync_version: str):
        """Reject an incorrect hash for a URL-based image."""
        with pytest.raises(AssertionError, match="'image_hash' value is different"):
            check_image(
                dbsync_version=dbsync_version,
                content_url=IMAGE_URL,
                stored_url=IMAGE_URL,
                image_hash="wrong",
                expected_hash=IMAGE_HASH,
            )


@pytest.fixture
def epoch_stake_query(monkeypatch: pytest.MonkeyPatch) -> mock.Mock:
    """Return two stake rows per requested epoch and record the queries."""
    monkeypatch.setattr(dbsync_utils.configuration, "HAS_DBSYNC", True)

    def _query(
        *, pool_id_bech32: str, epoch_number: int
    ) -> tp.Iterator[dbsync_queries.EpochStakeDBRow]:
        """Return epoch-specific amounts so incorrect epoch mappings cannot pass."""
        assert pool_id_bech32 == "pool1"
        assert epoch_number >= 1
        return iter(
            dbsync_queries.EpochStakeDBRow(
                id=index,
                hash=memoryview(b""),
                view=pool_id_bech32,
                amount=multiplier * epoch_number,
                epoch_number=epoch_number,
            )
            for index, multiplier in enumerate((10, 20), start=1)
        )

    query = mock.Mock(side_effect=_query)
    monkeypatch.setattr(dbsync_queries, "query_epoch_stake", query)
    return query


class TestPoolStakeSnapshot:
    """Check snapshot comparisons at startup and after epoch history becomes available."""

    def test_epoch_zero(self, epoch_stake_query: mock.Mock):
        """Do not query nonexistent epochs 0 and -1, even for nonzero genesis stake."""
        dbsync_utils.check_pool_stake_snapshot(
            pool_id="pool1",
            epoch_no=0,
            pool_stake_snapshots={"stakeSet": 100, "stakeGo": 200},
        )

        epoch_stake_query.assert_not_called()

    def test_epoch_one(self, epoch_stake_query: mock.Mock):
        """Check set against epoch 1 but omit go, whose epoch 0 has no history."""
        dbsync_utils.check_pool_stake_snapshot(
            pool_id="pool1",
            epoch_no=1,
            pool_stake_snapshots={"stakeSet": 30, "stakeGo": 200},
        )

        epoch_stake_query.assert_called_once_with(pool_id_bech32="pool1", epoch_number=1)

    @pytest.mark.parametrize("epoch_no", [2, 10])
    def test_later_epochs(self, epoch_stake_query: mock.Mock, epoch_no: int):
        """Sum all rows for set's current epoch and go's previous epoch."""
        dbsync_utils.check_pool_stake_snapshot(
            pool_id="pool1",
            epoch_no=epoch_no,
            pool_stake_snapshots={"stakeSet": 30 * epoch_no, "stakeGo": 30 * (epoch_no - 1)},
        )

        assert epoch_stake_query.call_args_list == [
            mock.call(pool_id_bech32="pool1", epoch_number=epoch_no),
            mock.call(pool_id_bech32="pool1", epoch_number=epoch_no - 1),
        ]

    @pytest.mark.parametrize(
        ("epoch_no", "snapshots", "snapshot_name", "stake_epoch", "db_sum", "snapshot_sum"),
        [
            (1, {"stakeSet": 31, "stakeGo": 200}, "stakeSet", 1, 30, 31),
            (2, {"stakeSet": 61, "stakeGo": 30}, "stakeSet", 2, 60, 61),
            (2, {"stakeSet": 60, "stakeGo": 31}, "stakeGo", 1, 30, 31),
        ],
    )
    def test_mismatch(
        self,
        epoch_stake_query: mock.Mock,
        epoch_no: int,
        snapshots: dict[str, int | None],
        snapshot_name: str,
        stake_epoch: int,
        db_sum: int,
        snapshot_sum: int,
    ):
        """Keep genuine mismatches failing and report their pool, epoch, and amounts."""
        with pytest.raises(AssertionError, match=snapshot_name) as excinfo:
            dbsync_utils.check_pool_stake_snapshot(
                pool_id="pool1", epoch_no=epoch_no, pool_stake_snapshots=snapshots
            )

        assert "pool1" in str(excinfo.value)
        assert f"node epoch {epoch_no}" in str(excinfo.value)
        assert f"db-sync epoch {stake_epoch}): {db_sum} != {snapshot_sum}" in str(excinfo.value)
        assert epoch_stake_query.call_count == min(epoch_no, 2)

    def test_both_mismatches(self, epoch_stake_query: mock.Mock):
        """Report both failed comparisons instead of stopping after the set snapshot."""
        with pytest.raises(AssertionError, match="stakeSet") as excinfo:
            dbsync_utils.check_pool_stake_snapshot(
                pool_id="pool1",
                epoch_no=2,
                pool_stake_snapshots={"stakeSet": 61, "stakeGo": 31},
            )

        assert "stakeGo" in str(excinfo.value)
        assert epoch_stake_query.call_count == 2

    @pytest.mark.parametrize("snapshot_sum", [0, None])
    def test_zero_stake(self, epoch_stake_query: mock.Mock, snapshot_sum: int | None):
        """An empty query is valid when the corresponding pool snapshot has no stake."""
        epoch_stake_query.side_effect = lambda **_kwargs: iter(())
        dbsync_utils.check_pool_stake_snapshot(
            pool_id="pool1",
            epoch_no=2,
            pool_stake_snapshots={"stakeSet": snapshot_sum, "stakeGo": snapshot_sum},
        )

        assert epoch_stake_query.call_count == 2

    def test_missing_stake(self, epoch_stake_query: mock.Mock):
        """Do not silently omit missing data once the requested epoch should exist."""
        epoch_stake_query.side_effect = lambda **_kwargs: iter(())
        with pytest.raises(AssertionError, match="0 != 30"):
            dbsync_utils.check_pool_stake_snapshot(
                pool_id="pool1",
                epoch_no=1,
                pool_stake_snapshots={"stakeSet": 30, "stakeGo": 200},
            )

        epoch_stake_query.assert_called_once_with(pool_id_bech32="pool1", epoch_number=1)

    def test_no_dbsync(self, epoch_stake_query: mock.Mock, monkeypatch: pytest.MonkeyPatch):
        """Leave the optional check disabled when db-sync is not available."""
        monkeypatch.setattr(dbsync_utils.configuration, "HAS_DBSYNC", False)
        dbsync_utils.check_pool_stake_snapshot(
            pool_id="pool1",
            epoch_no=10,
            pool_stake_snapshots={"stakeSet": 300, "stakeGo": 270},
        )

        epoch_stake_query.assert_not_called()
