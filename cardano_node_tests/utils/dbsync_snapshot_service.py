import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from dataclasses import field
from datetime import datetime
from datetime import timezone
from typing import List
from typing import Tuple

import requests

logger = logging.getLogger(__name__)

# Full S3 namespace URL string
S3_NS_URL = "http://s3.amazonaws.com/doc/2006-03-01/"


@dataclass
class SnapshotFile:
    """Dataclass to hold parsed snapshot file information."""

    key: str
    name: str
    last_modified: datetime  # Timezone-aware datetime object
    size: int
    size_gb: float = field(init=False)

    def __post_init__(self) -> None:
        self.size_gb = self.size / (1024**3)


class DBSyncSnapshotService:
    """Service class to interact with DB-Sync S3 Snapshots repository."""

    BUCKET_URL: str = "https://update-cardano-mainnet.iohk.io"
    ROOT_PREFIX: str = "cardano-db-sync/"

    def _get_s3_objects(self, prefix: str = "", delimiter: str = "") -> bytes:
        """Fetch XML content from the S3 bucket using REST API."""
        params = {"list-type": "2", "prefix": prefix, "delimiter": delimiter}

        response = requests.get(self.BUCKET_URL, params=params)
        response.raise_for_status()
        return response.content

    def _parse_s3_xml(self, xml_content: bytes) -> Tuple[List[str], List[SnapshotFile]]:
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

            if not all(
                [
                    key_tag is not None and key_tag.text,
                    modified_tag is not None and modified_tag.text,
                    size_tag is not None and size_tag.text,
                ]
            ):
                logger.warning(
                    "Skipping malformed S3 object entry: Missing Key, LastModified, or Size."
                )
                continue  # Skip this entry if critical data is missing

            # Use explicit variables to store the text content only if it exists
            key_text = key_tag.text if key_tag is not None else None
            modified_text = modified_tag.text if modified_tag is not None else None
            size_text = size_tag.text if size_tag is not None else None

            # Ensure all three critical tags and their text content exist
            if not all([key_text, modified_text, size_text]):
                logger.warning(
                    "Skipping malformed S3 object entry: Missing Key, LastModified, or Size."
                )
                continue  # Skip this entry if critical data is missing

            key = key_text
            last_modified_str = modified_text
            size_str = size_text

            if last_modified_str is None:
                continue

            if key is None:
                continue

            file_date = datetime.strptime(last_modified_str, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
                tzinfo=timezone.utc
            )

            files.append(
                SnapshotFile(
                    key=key,
                    name=key.split("/")[-1],
                    last_modified=file_date,
                    size=int(size_str) if size_str else 0,
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

        # Filter: Revert to the original working filter (.tgz AND 'snapshot')
        snapshot_files = [
            f for f in files if f.name.endswith(".tgz") and "snapshot" in f.name.lower()
        ]

        if not snapshot_files:
            file_names = [f.name for f in files]
            logger.warning(f"Files found in S3 response for {version_prefix}: {file_names}")
            error_msg = (
                f"No snapshot files found for version {version}. Filtered files: {file_names}"
            )
            raise RuntimeError(error_msg)

        latest_snapshot = max(snapshot_files, key=lambda x: x.last_modified)
        return latest_snapshot
