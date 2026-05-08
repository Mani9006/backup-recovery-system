"""Azure Blob Storage backend.

Provides upload, download, and management operations for
storing backup files in Azure Blob Storage containers.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

try:
    from azure.storage.blob import BlobServiceClient, BlobClient, ContainerClient
    from azure.core.exceptions import ResourceNotFoundError, AzureError
except ImportError:  # pragma: no cover
    BlobServiceClient = None  # type: ignore[misc,assignment]
    BlobClient = None  # type: ignore[misc,assignment]
    ContainerClient = None  # type: ignore[misc,assignment]
    ResourceNotFoundError = Exception  # type: ignore[misc,assignment]
    AzureError = Exception  # type: ignore[misc,assignment]

logger = logging.getLogger(__name__)


class AzureStorageError(Exception):
    """Raised when an Azure storage operation fails."""

    pass


class AzureStorage:
    """Azure Blob Storage backend for cloud-based backup storage.

    Manages backup files in Azure Blob Storage containers with
    support for access tiers and SAS tokens.
    """

    def __init__(
        self,
        container: str,
        connection_string: Optional[str] = None,
        account_name: Optional[str] = None,
        account_key: Optional[str] = None,
    ) -> None:
        """Initialize the Azure storage backend.

        Args:
            container: Blob container name.
            connection_string: Azure Storage connection string.
            account_name: Storage account name.
            account_key: Storage account key.

        Raises:
            AzureStorageError: If azure-storage-blob is not installed.
        """
        if BlobServiceClient is None:
            raise AzureStorageError(
                "The 'azure-storage-blob' library is required for Azure storage. "
                "Install it with: pip install azure-storage-blob"
            )

        self.container = container

        try:
            if connection_string:
                self._service_client = BlobServiceClient.from_connection_string(
                    connection_string
                )
            elif account_name and account_key:
                account_url = f"https://{account_name}.blob.core.windows.net"
                self._service_client = BlobServiceClient(
                    account_url=account_url,
                    credential=account_key,
                )
            else:
                # Try environment variable
                env_conn = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
                if env_conn:
                    self._service_client = BlobServiceClient.from_connection_string(
                        env_conn
                    )
                else:
                    raise AzureStorageError(
                        "Azure Storage credentials not provided. "
                        "Set connection_string, account_name + account_key, "
                        "or AZURE_STORAGE_CONNECTION_STRING environment variable."
                    )
        except AzureError as exc:
            raise AzureStorageError(f"Failed to initialize Azure client: {exc}") from exc

        self._container_client = self._service_client.get_container_client(container)

        # Ensure container exists
        self._ensure_container()
        logger.debug(
            "AzureStorage initialized (container=%s)",
            container,
        )

    def _ensure_container(self) -> None:
        """Create the container if it doesn't exist."""
        try:
            self._container_client.get_container_properties()
            logger.debug("Azure container exists: %s", self.container)
        except ResourceNotFoundError:
            try:
                self._container_client.create_container()
                logger.info("Created Azure container: %s", self.container)
            except AzureError as exc:
                raise AzureStorageError(
                    f"Failed to create Azure container '{self.container}': {exc}"
                ) from exc

    def upload(self, local_path: str, remote_key: str) -> None:
        """Upload a file to Azure Blob Storage.

        Args:
            local_path: Path to the local file.
            remote_key: Blob name.

        Raises:
            AzureStorageError: If the upload fails.
        """
        source = Path(local_path)
        if not source.exists():
            raise AzureStorageError(f"Source file not found: {local_path}")

        try:
            blob_client = self._container_client.get_blob_client(remote_key)
            with open(local_path, "rb") as data:
                blob_client.upload_blob(data, overwrite=True)

            logger.info(
                "Uploaded to Azure: %s -> azure://%s/%s",
                local_path,
                self.container,
                remote_key,
            )
        except AzureError as exc:
            raise AzureStorageError(f"Azure upload failed: {exc}") from exc

    def download(self, remote_key: str, local_path: str) -> None:
        """Download a file from Azure Blob Storage.

        Args:
            remote_key: Blob name.
            local_path: Destination path for the downloaded file.

        Raises:
            AzureStorageError: If the download fails.
        """
        dest = Path(local_path)
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            blob_client = self._container_client.get_blob_client(remote_key)
            with open(local_path, "wb") as f:
                stream = blob_client.download_blob()
                f.write(stream.readall())

            logger.info(
                "Downloaded from Azure: azure://%s/%s -> %s",
                self.container,
                remote_key,
                local_path,
            )
        except ResourceNotFoundError:
            raise AzureStorageError(f"Azure blob not found: {remote_key}")
        except AzureError as exc:
            raise AzureStorageError(f"Azure download failed: {exc}") from exc

    def list_files(self, prefix: str = "") -> list[dict]:
        """List blobs in the container.

        Args:
            prefix: Optional blob prefix to filter by.

        Returns:
            List of file information dictionaries.
        """
        try:
            blobs = self._container_client.list_blobs(name_starts_with=prefix)

            files = []
            for blob in blobs:
                files.append(
                    {
                        "key": blob.name,
                        "size": blob.size,
                        "modified": blob.last_modified.isoformat() if blob.last_modified else None,
                        "etag": blob.etag,
                    }
                )

            files.sort(key=lambda f: f["modified"] or "", reverse=True)
            return files

        except AzureError as exc:
            raise AzureStorageError(f"Failed to list Azure blobs: {exc}") from exc

    def delete(self, remote_key: str) -> None:
        """Delete a blob from Azure.

        Args:
            remote_key: Blob name.

        Raises:
            AzureStorageError: If deletion fails.
        """
        try:
            blob_client = self._container_client.get_blob_client(remote_key)
            blob_client.delete_blob()
            logger.info("Deleted from Azure: azure://%s/%s", self.container, remote_key)
        except ResourceNotFoundError:
            logger.warning("Azure blob not found for deletion: %s", remote_key)
        except AzureError as exc:
            raise AzureStorageError(f"Azure delete failed: {exc}") from exc

    def exists(self, remote_key: str) -> bool:
        """Check if a blob exists.

        Args:
            remote_key: Blob name.

        Returns:
            True if the blob exists.
        """
        try:
            blob_client = self._container_client.get_blob_client(remote_key)
            blob_client.get_blob_properties()
            return True
        except ResourceNotFoundError:
            return False
        except AzureError as exc:
            raise AzureStorageError(f"Azure exists check failed: {exc}") from exc

    def get_size(self, remote_key: str) -> int:
        """Get the size of an Azure blob.

        Args:
            remote_key: Blob name.

        Returns:
            Blob size in bytes.

        Raises:
            AzureStorageError: If the blob doesn't exist.
        """
        try:
            blob_client = self._container_client.get_blob_client(remote_key)
            props = blob_client.get_blob_properties()
            return props.size
        except AzureError as exc:
            raise AzureStorageError(f"Failed to get Azure blob size: {exc}") from exc

    def generate_sas_url(self, remote_key: str, expiry_hours: int = 1) -> str:
        """Generate a SAS URL for temporary access.

        Args:
            remote_key: Blob name.
            expiry_hours: URL expiry in hours.

        Returns:
            SAS URL string.
        """
        try:
            from azure.storage.blob import generate_blob_sas, BlobSasPermissions
            from datetime import datetime, timedelta, timezone

            blob_client = self._container_client.get_blob_client(remote_key)

            # Get account info from the service client
            account_name = self._service_client.account_name

            sas_token = generate_blob_sas(
                account_name=account_name,
                container_name=self.container,
                blob_name=remote_key,
                account_key=self._service_client.credential.account_key,
                permission=BlobSasPermissions(read=True),
                expiry=datetime.now(timezone.utc) + timedelta(hours=expiry_hours),
            )

            return f"{blob_client.url}?{sas_token}"

        except ImportError:
            raise AzureStorageError("SAS generation requires azure-storage-blob")
        except AzureError as exc:
            raise AzureStorageError(f"Failed to generate SAS URL: {exc}") from exc
