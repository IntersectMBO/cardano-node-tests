import dataclasses
import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC
from datetime import datetime

from cardano_node_tests.utils import http_client

logger = logging.getLogger(__name__)

# Full S3 namespace URL string
S3_NS_URL = "http://s3.amazonaws.com/doc/2006-03-01/"


@dataclasses.dataclass
class SnapshotFile:
    """Dataclass to hold parsed snapshot file information."""

    key: str
    name: str
    last_modified: datetime  # Timezone-aware datetime object
    size: int
    size_gb: float = dataclasses.field(init=False)

    def __post_init__(self) -> None:
        self.size_gb = self.size / (1024**3)


class DBSyncSnapshotService:
    """Service class to interact with DB-Sync S3 Snapshots repository."""

    BUCKET_URL: str = "https://update-cardano-mainnet.iohk.io"
    ROOT_PREFIX: str = "cardano-db-sync/"

    def _get_s3_objects(self, prefix: str = "", delimiter: str = "") -> bytes:
        """Fetch XML content from the S3 bucket using REST API."""
        params = {"list-type": "2", "prefix": prefix, "delimiter": delimiter}

        response = http_client.get_session().get(self.BUCKET_URL, params=params, timeout=30)
        response.raise_for_status()
        return response.content

    def _parse_s3_xml(self, xml_content: bytes) -> tuple[list[str], list[SnapshotFile]]:
        """Parse S3 XML response using exact namespace search paths with None checks."""
        root = ET.fromstring(xml_content)
        ns_tag = f"{{{S3_NS_URL}}}"

        # 1. Extract folders (CommonPrefixes)
        folders = []
        for prefix in root.findall(f".//{ns_tag}CommonPrefixes"):
            prefix_tag = prefix.find(f"{ns_tag}Prefix")
            if prefix_tag is not None and prefix_tag.text:
                folder_path = prefix_tag.text
                if folder_path.endswith("/"):
                    folder_name = folder_path.strip("/").split("/")[-1]
                    folders.append(folder_name)

        # 2. Extract files (Contents)
        files = []
        for content in root.findall(f".//{ns_tag}Contents"):
            key_tag = content.find(f"{ns_tag}Key")
            modified_tag = content.find(f"{ns_tag}LastModified")
            size_tag = content.find(f"{ns_tag}Size")

            if (key_tag is None or modified_tag is None or size_tag is None) or not (
                key_tag.text and modified_tag.text and size_tag.text
            ):
                logger.warning(
                    "Skipping malformed S3 object entry: Missing Key, LastModified, or Size tag."
                )
                continue  # Skip this entry if critical tags are missing

            key = key_tag.text or ""
            last_modified_str = modified_tag.text or ""

            file_date = datetime.strptime(last_modified_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=UTC
            )

            files.append(
                SnapshotFile(
                    key=key,
                    name=key.split("/")[-1],
                    last_modified=file_date,
                    size=int(size_tag.text or 0),
                )
            )

        return folders, files

    def get_latest_version(self) -> str:
        """Find the numerically latest db-sync version folder."""
        xml_content = self._get_s3_objects(prefix=self.ROOT_PREFIX, delimiter="/")
        folders, _ = self._parse_s3_xml(xml_content)

        version_folders = [f for f in folders if re.match(r"^\d+\.\d+$", f)]

        if not version_folders:
            err_msg = "No version folders found in S3 response."
            raise RuntimeError(err_msg)

        latest_version = sorted(
            version_folders, key=lambda v: [int(part) for part in v.split(".")]
        )[-1]
        return latest_version

    def get_latest_snapshot(self, version: str) -> SnapshotFile:
        """Find the latest snapshot file for a given version."""
        version_prefix = f"{self.ROOT_PREFIX}{version}/"
        xml_content = self._get_s3_objects(prefix=version_prefix)
        _, files = self._parse_s3_xml(xml_content)

        snapshot_files = [
            f for f in files if f.name.endswith(".tgz") and "snapshot" in f.name.lower()
        ]

        if not snapshot_files:
            file_names = [f.name for f in files]
            logger.warning(f"Files found in S3 response for {version_prefix}: {file_names}")
            error_msg = (
                f"No snapshot files found for version {version}."
                f" All files in response: {file_names}"
            )
            raise RuntimeError(error_msg)

        latest_snapshot = max(snapshot_files, key=lambda x: x.last_modified)
        return latest_snapshot
