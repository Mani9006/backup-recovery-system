"""File backup handler for filesystem-based backup operations.

Supports recursive directory traversal, glob patterns, exclusion rules,
and incremental backup tracking.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import tarfile
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.compression import CompressionManager
from src.encryption import EncryptionManager
from src.config import BackupJobConfig, StorageConfig
from src.integrity import IntegrityVerifier
from src.retention import RetentionManager

logger = logging.getLogger(__name__)


class FileBackupError(Exception):
    """Raised when a file backup operation fails."""

    pass


class FileBackup:
    """Handles file and directory backup operations.

    Creates compressed and optionally encrypted archives of source files
    and directories, storing them in the configured storage backend.
    """

    # Supported archive formats
    FORMAT_TAR = "tar"
    FORMAT_ZIP = "zip"

    def __init__(
        self,
        job_config: BackupJobConfig,
        storage_config: StorageConfig,
    ) -> None:
        """Initialize the file backup handler.

        Args:
            job_config: Configuration for this backup job.
            storage_config: Storage backend configuration.
        """
        self.job = job_config
        self.storage = storage_config
        self.compression = CompressionManager(method=job_config.compression)
        self.encryption = None
        if job_config.encryption and job_config.encryption_key:
            self.encryption = EncryptionManager(key=job_config.encryption_key)
        self.integrity = IntegrityVerifier()
        self.retention = RetentionManager(job_config.retention)

    def run(self) -> bool:
        """Execute the file backup job.

        Returns:
            True if the backup completed successfully, False otherwise.
        """
        source_path = Path(self.job.source)
        if not source_path.exists():
            logger.error("Source path does not exist: %s", source_path)
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        archive_name = f"{self.job.destination}_{timestamp}"

        try:
            with tempfile.TemporaryDirectory(prefix="backup_") as tmp_dir:
                tmp_path = Path(tmp_dir)

                # Step 1: Create archive
                archive_path = self._create_archive(source_path, tmp_path, archive_name)
                logger.info("Archive created: %s", archive_path)

                # Step 2: Compress if needed (beyond archive creation)
                compressed_path = self._apply_compression(archive_path, tmp_path)

                # Step 3: Encrypt if configured
                final_path = self._apply_encryption(compressed_path, tmp_path)

                # Step 4: Generate checksum
                checksum_path = self._generate_checksum(final_path, tmp_path)

                # Step 5: Store to backend
                storage_success = self._store_backup(final_path, checksum_path)
                if not storage_success:
                    logger.error("Failed to store backup")
                    return False

                # Step 6: Apply retention policy
                self._apply_retention()

                logger.info(
                    "File backup '%s' completed successfully: %s",
                    self.job.name,
                    final_path.name,
                )
                return True

        except FileBackupError as exc:
            logger.error("File backup failed: %s", exc)
            return False
        except Exception as exc:
            logger.exception("Unexpected error during file backup: %s", exc)
            return False

    def _create_archive(
        self,
        source: Path,
        tmp_path: Path,
        archive_name: str,
    ) -> Path:
        """Create an archive from the source path.

        Args:
            source: Source file or directory to archive.
            tmp_path: Temporary directory for intermediate files.
            archive_name: Base name for the archive file.

        Returns:
            Path to the created archive.
        """
        if source.is_file():
            archive_path = tmp_path / f"{archive_name}.tar"
            self._create_tar_archive([source], archive_path, source.parent)
        elif source.is_dir():
            archive_path = tmp_path / f"{archive_name}.tar"
            files_to_backup = self._collect_files(source)
            self._create_tar_archive(files_to_backup, archive_path, source)
        else:
            raise FileBackupError(f"Source is neither a file nor directory: {source}")

        return archive_path

    def _collect_files(
        self,
        source: Path,
        excludes: Optional[list[str]] = None,
    ) -> list[Path]:
        """Collect all files to backup from the source directory.

        Args:
            source: Root directory to scan.
            excludes: Optional list of glob patterns to exclude.

        Returns:
            List of file paths to include in the backup.
        """
        excludes = excludes or []
        files = []

        # Parse job options for exclusions
        job_excludes = self.job.options.get("exclude", [])
        if isinstance(job_excludes, str):
            job_excludes = [job_excludes]
        excludes.extend(job_excludes)

        for root, dirnames, filenames in os.walk(source):
            root_path = Path(root)

            # Filter out excluded directories
            dirnames[:] = [
                d
                for d in dirnames
                if not any(
                    root_path.joinpath(d).match(pattern) for pattern in excludes
                )
            ]

            for filename in filenames:
                file_path = root_path / filename

                # Skip excluded files
                if any(file_path.match(pattern) for pattern in excludes):
                    logger.debug("Excluding file: %s", file_path)
                    continue

                # Skip hidden files if configured
                if self.job.options.get("skip_hidden", False):
                    if filename.startswith("."):
                        continue

                files.append(file_path)

        logger.info("Collected %d files for backup", len(files))
        return files

    def _create_tar_archive(
        self,
        files: list[Path],
        archive_path: Path,
        base_dir: Path,
    ) -> None:
        """Create a tar archive from a list of files.

        Args:
            files: List of file paths to include.
            archive_path: Output archive path.
            base_dir: Base directory for relative path calculation.
        """
        mode = "w"
        if self.job.compression == "gzip":
            mode = "w:gz"
        elif self.job.compression == "bzip2":
            mode = "w:bz2"

        logger.debug("Creating tar archive with mode '%s': %s", mode, archive_path)
        with tarfile.open(archive_path, mode) as tar:
            for file_path in files:
                arcname = str(file_path.relative_to(base_dir))
                tar.add(file_path, arcname=arcname)
                logger.debug("Added to archive: %s", arcname)

    def _apply_compression(self, archive_path: Path, tmp_path: Path) -> Path:
        """Apply additional compression to the archive if needed.

        Args:
            archive_path: Path to the archive file.
            tmp_path: Temporary directory for intermediate files.

        Returns:
            Path to the compressed file (may be the same as input).
        """
        if self.job.compression == "zip":
            zip_path = tmp_path / archive_path.with_suffix(".zip").name
            self.compression.compress_zip(archive_path, zip_path)
            return zip_path

        # gzip/bzip2 are handled during tar creation
        return archive_path

    def _apply_encryption(self, file_path: Path, tmp_path: Path) -> Path:
        """Apply encryption to the file if configured.

        Args:
            file_path: Path to the file to encrypt.
            tmp_path: Temporary directory for intermediate files.

        Returns:
            Path to the encrypted file (or original if encryption is disabled).
        """
        if self.encryption is None:
            return file_path

        encrypted_path = tmp_path / f"{file_path.name}.enc"
        self.encryption.encrypt_file(str(file_path), str(encrypted_path))
        logger.info("File encrypted: %s", encrypted_path)
        return encrypted_path

    def _generate_checksum(self, file_path: Path, tmp_path: Path) -> Path:
        """Generate a SHA-256 checksum file for the backup.

        Args:
            file_path: Path to the backup file.
            tmp_path: Temporary directory for the checksum file.

        Returns:
            Path to the checksum file.
        """
        checksum = self.integrity.compute_checksum(str(file_path))
        checksum_path = tmp_path / f"{file_path.name}.sha256"
        with open(checksum_path, "w", encoding="utf-8") as f:
            f.write(f"{checksum}  {file_path.name}\n")
        logger.info("Checksum generated: %s", checksum)
        return checksum_path

    def _store_backup(self, backup_path: Path, checksum_path: Path) -> bool:
        """Store the backup file to the configured storage backend.

        Args:
            backup_path: Path to the backup file.
            checksum_path: Path to the checksum file.

        Returns:
            True if storage succeeded, False otherwise.
        """
        backend = self.storage.backend

        try:
            if backend == "local":
                return self._store_local(backup_path, checksum_path)
            elif backend == "s3":
                return self._store_s3(backup_path, checksum_path)
            elif backend == "gcs":
                return self._store_gcs(backup_path, checksum_path)
            elif backend == "azure":
                return self._store_azure(backup_path, checksum_path)
            else:
                logger.error("Unknown storage backend: %s", backend)
                return False
        except Exception as exc:
            logger.exception("Failed to store backup: %s", exc)
            return False

    def _store_local(self, backup_path: Path, checksum_path: Path) -> bool:
        """Store backup to local filesystem.

        Args:
            backup_path: Path to the backup file.
            checksum_path: Path to the checksum file.

        Returns:
            True if storage succeeded.
        """
        dest_dir = Path(self.storage.path) / self.job.destination
        dest_dir.mkdir(parents=True, exist_ok=True)

        dest_backup = dest_dir / backup_path.name
        dest_checksum = dest_dir / checksum_path.name

        shutil.copy2(backup_path, dest_backup)
        shutil.copy2(checksum_path, dest_checksum)

        logger.info("Backup stored locally: %s", dest_backup)
        return True

    def _store_s3(self, backup_path: Path, checksum_path: Path) -> bool:
        """Store backup to AWS S3.

        Args:
            backup_path: Path to the backup file.
            checksum_path: Path to the checksum file.

        Returns:
            True if storage succeeded.
        """
        try:
            from src.storage.s3 import S3Storage

            s3 = S3Storage(
                bucket=self.storage.bucket or "backups",
                region=self.storage.region or "us-east-1",
                access_key=self.storage.access_key,
                secret_key=self.storage.secret_key,
            )
            key_prefix = f"{self.job.destination}/"
            s3.upload(str(backup_path), f"{key_prefix}{backup_path.name}")
            s3.upload(str(checksum_path), f"{key_prefix}{checksum_path.name}")
            logger.info("Backup stored to S3: s3://%s/%s", s3.bucket, key_prefix)
            return True
        except ImportError:
            logger.error("boto3 is required for S3 storage")
            return False

    def _store_gcs(self, backup_path: Path, checksum_path: Path) -> bool:
        """Store backup to Google Cloud Storage.

        Args:
            backup_path: Path to the backup file.
            checksum_path: Path to the checksum file.

        Returns:
            True if storage succeeded.
        """
        try:
            from src.storage.gcs import GCSStorage

            gcs = GCSStorage(
                bucket=self.storage.bucket or "backups",
                project_id=self.storage.options.get("project_id") if hasattr(self.storage, 'options') else None,
            )
            blob_prefix = f"{self.job.destination}/"
            gcs.upload(str(backup_path), f"{blob_prefix}{backup_path.name}")
            gcs.upload(str(checksum_path), f"{blob_prefix}{checksum_path.name}")
            logger.info("Backup stored to GCS: gs://%s/%s", gcs.bucket, blob_prefix)
            return True
        except ImportError:
            logger.error("google-cloud-storage is required for GCS storage")
            return False

    def _store_azure(self, backup_path: Path, checksum_path: Path) -> bool:
        """Store backup to Azure Blob Storage.

        Args:
            backup_path: Path to the backup file.
            checksum_path: Path to the checksum file.

        Returns:
            True if storage succeeded.
        """
        try:
            from src.storage.azure import AzureStorage

            azure = AzureStorage(
                container=self.storage.bucket or "backups",
                connection_string=self.storage.connection_string,
            )
            blob_prefix = f"{self.job.destination}/"
            azure.upload(str(backup_path), f"{blob_prefix}{backup_path.name}")
            azure.upload(str(checksum_path), f"{blob_prefix}{checksum_path.name}")
            logger.info("Backup stored to Azure: azure://%s/%s", azure.container, blob_prefix)
            return True
        except ImportError:
            logger.error("azure-storage-blob is required for Azure storage")
            return False

    def _apply_retention(self) -> None:
        """Apply the retention policy to clean up old backups."""
        try:
            retention = RetentionManager(self.job.retention)
            if self.storage.backend == "local":
                backup_dir = Path(self.storage.path) / self.job.destination
                if backup_dir.exists():
                    retention.cleanup_local(backup_dir)
            else:
                # For cloud storage, cleanup requires the storage client
                logger.info("Retention cleanup for cloud storage not yet implemented")
        except Exception as exc:
            logger.warning("Retention cleanup failed (non-fatal): %s", exc)
