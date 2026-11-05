"""Unit tests for db-sync metadata checks without a database or cluster."""

import typing as tp

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
