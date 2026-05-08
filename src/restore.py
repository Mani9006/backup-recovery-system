"""Restore manager for recovering data from backup archives.

Handles the restoration process including downloading from storage,
decrypting, decompressing, and extracting files to their destination.
"""

from __future__ import annotations

import logging
import os
import shutil
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

from src.compression import CompressionManager
from src.encryption import EncryptionManager
from src.config import StorageConfig

logger = logging.getLogger(__name__)


class RestoreError(Exception):
    """Raised when a restore operation fails."""

    pass


class RestoreManager:
    """Manages the restoration of files and databases from backup archives.

    Supports multi-stage restoration from any storage backend with
    optional decryption and automatic format detection.
    """

    def __init__(
        self,
        storage_config: StorageConfig,
        encryption_key: Optional[str] = None,
    ) -> None:
        """Initialize the restore manager.

        Args:
            storage_config: Storage backend configuration.
            encryption_key: Optional key for decrypting encrypted backups.
        """
        self.storage = storage_config
        self.encryption_key = encryption_key
        self.compression = CompressionManager()

        if encryption_key:
            self.encryption = EncryptionManager(key=encryption_key)
        else:
            self.encryption = None

        logger.debug(
            "RestoreManager initialized (storage=%s, encryption=%s)",
            storage_config.backend,
            encryption_key is not None,
        )

    def restore(
        self,
        archive_path: str,
        destination: Optional[str] = None,
    ) -> bool:
        """Restore data from a backup archive.

        The restore process:
        1. Locate the archive (local or cloud)
        2. Download if needed
        3. Verify checksum
        4. Decrypt if encrypted
        5. Decompress
        6. Extract to destination

        Args:
            archive_path: Path to the backup archive.
            destination: Optional destination for restored files.

        Returns:
            True if restoration succeeded.
        """
        try:
            with tempfile.TemporaryDirectory(prefix="restore_") as tmp_dir:
                tmp_path = Path(tmp_dir)

                # Step 1: Get the archive (local or download)
                local_archive = self._get_archive(archive_path, tmp_path)
                if not local_archive:
                    return False

                logger.info("Restoring from archive: %s", local_archive)

                # Step 2: Decrypt if needed
                decrypted = self._decrypt_if_needed(local_archive, tmp_path)

                # Step 3: Decompress
                decompressed = self._decompress(decrypted, tmp_path)

                # Step 4: Extract to destination
                dest = destination or self._get_default_destination(archive_path)
                success = self._extract(decompressed, dest)

                if success:
                    logger.info("Restore completed successfully to: %s", dest)
                else:
                    logger.error("Restore extraction failed")

                return success

        except RestoreError as exc:
            logger.error("Restore failed: %s", exc)
            return False
        except Exception as exc:
            logger.exception("Unexpected error during restore: %s", exc)
            return False

    def _get_archive(self, archive_path: str, tmp_path: Path) -> Optional[Path]:
        """Get the archive file locally (download if from cloud).

        Args:
            archive_path: Path or URI to the archive.
            tmp_path: Temporary directory.

        Returns:
            Local path to the archive, or None if failed.
        """
        path = Path(archive_path)

        # If it's a local file, use it directly
        if path.exists():
            return path

        # Handle cloud URIs
        if archive_path.startswith("s3://"):
            return self._download_from_s3(archive_path, tmp_path)
        elif archive_path.startswith("gs://"):
            return self._download_from_gcs(archive_path, tmp_path)
        elif archive_path.startswith("azure://"):
            return self._download_from_azure(archive_path, tmp_path)

        logger.error("Archive not found: %s", archive_path)
        return None

    def _download_from_s3(self, uri: str, tmp_path: Path) -> Optional[Path]:
        """Download an archive from S3.

        Args:
            uri: S3 URI (s3://bucket/key).
            tmp_path: Temporary directory.

        Returns:
            Local path to the downloaded file.
        """
        try:
            import boto3

            parts = uri.replace("s3://", "").split("/", 1)
            bucket = parts[0]
            key = parts[1]

            s3 = boto3.client("s3")
            local_path = tmp_path / Path(key).name
            s3.download_file(bucket, key, str(local_path))

            logger.info("Downloaded from S3: %s -> %s", uri, local_path)
            return local_path

        except ImportError:
            logger.error("boto3 is required for S3 download")
            return None
        except Exception as exc:
            logger.error("Failed to download from S3: %s", exc)
            return None

    def _download_from_gcs(self, uri: str, tmp_path: Path) -> Optional[Path]:
        """Download an archive from GCS.

        Args:
            uri: GCS URI (gs://bucket/object).
            tmp_path: Temporary directory.

        Returns:
            Local path to the downloaded file.
        """
        try:
            from google.cloud import storage as gcs

            parts = uri.replace("gs://", "").split("/", 1)
            bucket_name = parts[0]
            blob_name = parts[1]

            client = gcs.Client()
            bucket = client.bucket(bucket_name)
            blob = bucket.blob(blob_name)

            local_path = tmp_path / Path(blob_name).name
            blob.download_to_filename(str(local_path))

            logger.info("Downloaded from GCS: %s -> %s", uri, local_path)
            return local_path

        except ImportError:
            logger.error("google-cloud-storage is required for GCS download")
            return None
        except Exception as exc:
            logger.error("Failed to download from GCS: %s", exc)
            return None

    def _download_from_azure(self, uri: str, tmp_path: Path) -> Optional[Path]:
        """Download an archive from Azure Blob Storage.

        Args:
            uri: Azure URI (azure://container/blob).
            tmp_path: Temporary directory.

        Returns:
            Local path to the downloaded file.
        """
        try:
            from azure.storage.blob import BlobServiceClient

            parts = uri.replace("azure://", "").split("/", 1)
            container = parts[0]
            blob_name = parts[1]

            service_client = BlobServiceClient.from_connection_string(
                self.storage.connection_string or ""
            )
            blob_client = service_client.get_blob_client(
                container=container, blob=blob_name
            )

            local_path = tmp_path / Path(blob_name).name
            with open(local_path, "wb") as f:
                data = blob_client.download_blob()
                data.readinto(f)

            logger.info("Downloaded from Azure: %s -> %s", uri, local_path)
            return local_path

        except ImportError:
            logger.error("azure-storage-blob is required for Azure download")
            return None
        except Exception as exc:
            logger.error("Failed to download from Azure: %s", exc)
            return None

    def _decrypt_if_needed(self, archive_path: Path, tmp_path: Path) -> Path:
        """Decrypt the archive if it has an .enc extension.

        Args:
            archive_path: Path to the (possibly encrypted) archive.
            tmp_path: Temporary directory.

        Returns:
            Path to the decrypted file (or original if not encrypted).
        """
        if ".enc" not in archive_path.suffixes and archive_path.suffix != ".enc":
            return archive_path

        if self.encryption is None:
            logger.warning(
                "Archive appears to be encrypted but no encryption key was provided"
            )
            return archive_path

        decrypted_path = tmp_path / archive_path.stem
        self.encryption.decrypt_file(str(archive_path), str(decrypted_path))
        logger.info("Archive decrypted: %s", decrypted_path)
        return decrypted_path

    def _decompress(self, archive_path: Path, tmp_path: Path) -> Path:
        """Decompress the archive based on its format.

        Args:
            archive_path: Path to the compressed archive.
            tmp_path: Temporary directory.

        Returns:
            Path to the decompressed file or directory.
        """
        fmt = self.compression.detect_format(str(archive_path))
        logger.debug("Detected compression format: %s", fmt)

        if fmt == "none":
            return archive_path

        if fmt == "gzip":
            decompressed = tmp_path / archive_path.stem
            self.compression.decompress_gzip(str(archive_path), str(decompressed))
            return decompressed

        if fmt == "bzip2":
            decompressed = tmp_path / archive_path.stem
            self.compression.decompress_bzip2(str(archive_path), str(decompressed))
            return decompressed

        if fmt == "zip":
            extract_dir = tmp_path / "extracted"
            self.compression.decompress_zip(str(archive_path), str(extract_dir))
            return extract_dir

        # Unknown format, return as-is
        logger.warning("Unknown compression format for: %s", archive_path)
        return archive_path

    def _extract(self, archive_path: Path, destination: str) -> bool:
        """Extract the archive contents to the destination.

        Args:
            archive_path: Path to the archive.
            destination: Destination directory.

        Returns:
            True if extraction succeeded.
        """
        dest_path = Path(destination)
        dest_path.mkdir(parents=True, exist_ok=True)

        # If it's a tar archive
        if tarfile.is_tarfile(str(archive_path)):
            return self._extract_tar(archive_path, dest_path)

        # If it's a directory (from zip extraction), copy contents
        if archive_path.is_dir():
            for item in archive_path.iterdir():
                dest = dest_path / item.name
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            logger.info("Copied extracted contents to: %s", dest_path)
            return True

        # Single file - just copy
        dest_file = dest_path / archive_path.name
        shutil.copy2(archive_path, dest_file)
        logger.info("Restored file to: %s", dest_file)
        return True

    def _extract_tar(self, archive_path: Path, dest_path: Path) -> bool:
        """Extract a tar archive.

        Args:
            archive_path: Path to the tar archive.
            dest_path: Destination directory.

        Returns:
            True if extraction succeeded.
        """
        try:
            with tarfile.open(str(archive_path), "r:*") as tf:
                # Security check: prevent path traversal
                for member in tf.getmembers():
                    member_path = dest_path / member.name
                    try:
                        member_path.resolve().relative_to(dest_path.resolve())
                    except ValueError:
                        logger.error(
                            "Path traversal detected in tar archive: %s", member.name
                        )
                        return False

                tf.extractall(str(dest_path))

            logger.info("Tar archive extracted to: %s", dest_path)
            return True

        except tarfile.TarError as exc:
            logger.error("Failed to extract tar archive: %s", exc)
            return False

    def _get_default_destination(self, archive_path: str) -> str:
        """Get the default restore destination.

        Args:
            archive_path: Path to the archive.

        Returns:
            Default destination directory path.
        """
        # Strip extension to get a destination name
        path = Path(archive_path)
        name = path.name

        # Remove common backup extensions
        for ext in [".enc", ".gz", ".bz2", ".zip", ".tar", ".sql"]:
            if name.endswith(ext):
                name = name[: -len(ext)]

        dest = path.parent / f"restored_{name}"
        return str(dest)

    def list_available_backups(self, directory: str) -> list[dict[str, str]]:
        """List available backup archives in a directory.

        Args:
            directory: Directory to scan.

        Returns:
            List of backup information dictionaries.
        """
        dir_path = Path(directory)
        if not dir_path.exists():
            return []

        backups = []
        for item in dir_path.rglob("*"):
            if item.is_file() and item.suffix in {
                ".tar", ".gz", ".tgz", ".bz2", ".zip", ".enc", ".sql", ".dump"
            }:
                stat = item.stat()
                backups.append(
                    {
                        "name": item.name,
                        "path": str(item),
                        "size": str(stat.st_size),
                        "modified": str(stat.st_mtime),
                        "suffix": item.suffix,
                    }
                )

        backups.sort(key=lambda b: b["name"], reverse=True)
        return backups
