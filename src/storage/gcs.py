"""Google Cloud Storage backend.

Provides upload, download, and management operations for
storing backup files in Google Cloud Storage buckets.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

try:
    from google.cloud import storage as gcs
    from google.cloud.storage import Blob, Bucket
    from google.api_core.exceptions import NotFound, GoogleAPICallError
except ImportError:  # pragma: no cover
    gcs = None  # type: ignore[assignment]
    Blob = None  # type: ignore[misc,assignment]
    Bucket = None  # type: ignore[misc,assignment]
    NotFound = Exception  # type: ignore[misc,assignment]
    GoogleAPICallError = Exception  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class GCSStorageError(Exception):
    """Raised when a GCS storage operation fails."""

    pass


class GCSStorage:
    """Google Cloud Storage backend for cloud-based backup storage.

    Manages backup files in GCS buckets with support for
    lifecycle policies and access control.
    """

    def __init__(
        self,
        bucket: str,
        project_id: Optional[str] = None,
        credentials_path: Optional[str] = None,
    ) -> None:
        """Initialize the GCS storage backend.

        Args:
            bucket: GCS bucket name.
            project_id: Google Cloud project ID.
            credentials_path: Path to service account JSON key file.

        Raises:
            GCSStorageError: If google-cloud-storage is not installed.
        """
        if gcs is None:
            raise GCSStorageError(
                "The 'google-cloud-storage' library is required for GCS storage. "
                "Install it with: pip install google-cloud-storage"
            )

        self.bucket_name = bucket
        self.project_id = project_id

        try:
            if credentials_path:
                self._client = gcs.Client.from_service_account_json(
                    credentials_path,
                    project=project_id,
                )
            else:
                self._client = gcs.Client(project=project_id)
        except Exception as exc:
            raise GCSStorageError(
                f"Failed to initialize GCS client: {exc}. "
                "Ensure GOOGLE_APPLICATION_CREDENTIALS is set or "
                "default credentials are configured."
            ) from exc

        self._bucket = self._client.bucket(bucket)

        # Ensure bucket exists
        self._ensure_bucket()
        logger.debug(
            "GCSStorage initialized (bucket=%s, project=%s)",
            bucket,
            project_id,
        )

    def _ensure_bucket(self) -> None:
        """Create the bucket if it doesn't exist."""
        try:
            self._client.get_bucket(self.bucket_name)
            logger.debug("GCS bucket exists: %s", self.bucket_name)
        except NotFound:
            try:
                self._bucket.storage_class = "STANDARD"
                self._client.create_bucket(
                    self._bucket,
                    project=self.project_id,
                    location="US",
                )
                logger.info("Created GCS bucket: %s", self.bucket_name)
            except GoogleAPICallError as exc:
                raise GCSStorageError(
                    f"Failed to create GCS bucket '{self.bucket_name}': {exc}"
                ) from exc

    def upload(self, local_path: str, remote_key: str) -> None:
        """Upload a file to GCS.

        Args:
            local_path: Path to the local file.
            remote_key: GCS blob name.

        Raises:
            GCSStorageError: If the upload fails.
        """
        source = Path(local_path)
        if not source.exists():
            raise GCSStorageError(f"Source file not found: {local_path}")

        try:
            blob = self._bucket.blob(remote_key)
            blob.upload_from_filename(str(local_path))
            logger.info(
                "Uploaded to GCS: %s -> gs://%s/%s",
                local_path,
                self.bucket_name,
                remote_key,
            )
        except GoogleAPICallError as exc:
            raise GCSStorageError(f"GCS upload failed: {exc}") from exc

    def download(self, remote_key: str, local_path: str) -> None:
        """Download a file from GCS.

        Args:
            remote_key: GCS blob name.
            local_path: Destination path for the downloaded file.

        Raises:
            GCSStorageError: If the download fails.
        """
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            blob = self._bucket.blob(remote_key)
            blob.download_to_filename(str(local_path))
            logger.info(
                "Downloaded from GCS: gs://%s/%s -> %s",
                self.bucket_name,
                remote_key,
                local_path,
            )
        except NotFound:
            raise GCSStorageError(f"GCS blob not found: {remote_key}")
        except GoogleAPICallError as exc:
            raise GCSStorageError(f"GCS download failed: {exc}") from exc

    def list_files(self, prefix: str = "") -> list[dict]:
        """List files in the GCS bucket.

        Args:
            prefix: Optional blob prefix to filter by.

        Returns:
            List of file information dictionaries.
        """
        try:
            blobs = self._client.list_blobs(
                self.bucket_name,
                prefix=prefix,
            )

            files = []
            for blob in blobs:
                files.append(
                    {
                        "key": blob.name,
                        "size": blob.size,
                        "modified": blob.updated.isoformat() if blob.updated else None,
                        "content_type": blob.content_type,
                    }
                )

            files.sort(key=lambda f: f["modified"] or "", reverse=True)
            return files

        except GoogleAPICallError as exc:
            raise GCSStorageError(f"Failed to list GCS blobs: {exc}") from exc

    def delete(self, remote_key: str) -> None:
        """Delete a blob from GCS.

        Args:
            remote_key: GCS blob name.

        Raises:
            GCSStorageError: If deletion fails.
        """
        try:
            blob = self._bucket.blob(remote_key)
            blob.delete()
            logger.info("Deleted from GCS: gs://%s/%s", self.bucket_name, remote_key)
        except NotFound:
            logger.warning("GCS blob not found for deletion: %s", remote_key)
        except GoogleAPICallError as exc:
            raise GCSStorageError(f"GCS delete failed: {exc}") from exc

    def exists(self, remote_key: str) -> bool:
        """Check if a blob exists in GCS.

        Args:
            remote_key: GCS blob name.

        Returns:
            True if the blob exists.
        """
        try:
            blob = self._bucket.blob(remote_key)
            return blob.exists()
        except GoogleAPICallError as exc:
            raise GCSStorageError(f"GCS exists check failed: {exc}") from exc

    def get_size(self, remote_key: str) -> int:
        """Get the size of a GCS blob.

        Args:
            remote_key: GCS blob name.

        Returns:
            Blob size in bytes.

        Raises:
            GCSStorageError: If the blob doesn't exist.
        """
        try:
            blob = self._bucket.get_blob(remote_key)
            if blob is None:
                raise GCSStorageError(f"GCS blob not found: {remote_key}")
            return blob.size
        except GoogleAPICallError as exc:
            raise GCSStorageError(f"Failed to get GCS blob size: {exc}") from exc

    def generate_signed_url(self, remote_key: str, expiration: int = 3600) -> str:
        """Generate a signed URL for temporary access.

        Args:
            remote_key: GCS blob name.
            expiration: URL expiration time in seconds.

        Returns:
            Signed URL string.
        """
        try:
            blob = self._bucket.blob(remote_key)
            url = blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                method="GET",
            )
            return url
        except GoogleAPICallError as exc:
            raise GCSStorageError(f"Failed to generate signed URL: {exc}") from exc
