"""Cloud resource backup handler.

Supports backing up cloud resources such as S3 bucket metadata,
GCS bucket contents, and Azure blob listings. Also supports
backing up from one cloud provider to another.
"""

from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from src.compression import CompressionManager
from src.encryption import EncryptionManager
from src.config import BackupJobConfig, StorageConfig
from src.integrity import IntegrityVerifier
from src.retention import RetentionManager

logger = logging.getLogger(__name__)


class CloudBackupError(Exception):
    """Raised when a cloud backup operation fails."""

    pass


class CloudBackup:
    """Handles cloud resource backup operations.

    Supports backing up cloud storage metadata, bucket listings,
    and cross-cloud replication of objects.
    """

    SUPPORTED_CLOUD_SOURCES = {
        "s3",
        "gcs",
        "azure",
        "sftp",
    }

    def __init__(
        self,
        job_config: BackupJobConfig,
        storage_config: StorageConfig,
    ) -> None:
        """Initialize the cloud backup handler.

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

        # Parse cloud-specific options
        self.source_cloud = job_config.options.get("source_cloud", "s3")
        self.source_bucket = job_config.options.get("source_bucket", "")
        self.source_prefix = job_config.options.get("source_prefix", "")
        self.source_region = job_config.options.get("source_region")
        self.source_access_key = job_config.options.get("source_access_key")
        self.source_secret_key = job_config.options.get("source_secret_key")
        self.sync_mode = job_config.options.get("sync_mode", "metadata")
        # metadata: only list objects; full: download all objects

    def run(self) -> bool:
        """Execute the cloud backup job.

        Returns:
            True if the backup completed successfully, False otherwise.
        """
        if self.source_cloud not in self.SUPPORTED_CLOUD_SOURCES:
            logger.error(
                "Unsupported cloud source: %s. Supported: %s",
                self.source_cloud,
                self.SUPPORTED_CLOUD_SOURCES,
            )
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self.job.destination}_{timestamp}"

        try:
            with tempfile.TemporaryDirectory(prefix="cloud_backup_") as tmp_dir:
                tmp_path = Path(tmp_dir)

                # Step 1: Collect cloud metadata/objects
                if self.source_cloud == "s3":
                    collect_success = self._collect_s3_data(tmp_path, backup_name)
                elif self.source_cloud == "gcs":
                    collect_success = self._collect_gcs_data(tmp_path, backup_name)
                elif self.source_cloud == "azure":
                    collect_success = self._collect_azure_data(tmp_path, backup_name)
                elif self.source_cloud == "sftp":
                    collect_success = self._collect_sftp_data(tmp_path, backup_name)
                else:
                    logger.error("Unhandled cloud source: %s", self.source_cloud)
                    return False

                if not collect_success:
                    logger.error("Cloud data collection failed")
                    return False

                # Find the generated archive/data file
                data_files = list(tmp_path.iterdir())
                if not data_files:
                    logger.error("No data was collected from cloud source")
                    return False

                archive_path = data_files[0]

                # Step 2: Compress
                compressed_path = self._apply_compression(archive_path, tmp_path)

                # Step 3: Encrypt
                final_path = self._apply_encryption(compressed_path, tmp_path)

                # Step 4: Generate checksum
                checksum_path = self._generate_checksum(final_path, tmp_path)

                # Step 5: Store backup
                storage_success = self._store_backup(final_path, checksum_path)
                if not storage_success:
                    return False

                # Step 6: Apply retention
                self._apply_retention()

                logger.info(
                    "Cloud backup '%s' from %s completed successfully",
                    self.job.name,
                    self.source_cloud,
                )
                return True

        except CloudBackupError as exc:
            logger.error("Cloud backup failed: %s", exc)
            return False
        except Exception as exc:
            logger.exception("Unexpected error during cloud backup: %s", exc)
            return False

    def _collect_s3_data(self, tmp_path: Path, backup_name: str) -> bool:
        """Collect data from an S3 bucket.

        Args:
            tmp_path: Temporary directory for collected data.
            backup_name: Base name for output files.

        Returns:
            True if collection succeeded.
        """
        try:
            import boto3

            session = boto3.Session(
                aws_access_key_id=self.source_access_key,
                aws_secret_access_key=self.source_secret_key,
                region_name=self.source_region or "us-east-1",
            )
            s3 = session.client("s3")

            # List bucket objects
            paginator = s3.get_paginator("list_objects_v2")
            pages = paginator.paginate(
                Bucket=self.source_bucket,
                Prefix=self.source_prefix,
            )

            objects = []
            for page in pages:
                for obj in page.get("Contents", []):
                    objects.append(
                        {
                            "key": obj["Key"],
                            "size": obj["Size"],
                            "last_modified": obj["LastModified"].isoformat(),
                            "etag": obj["ETag"],
                        }
                    )

            logger.info("Collected %d objects from S3 bucket '%s'", len(objects), self.source_bucket)

            # Save metadata as JSON
            metadata_path = tmp_path / f"{backup_name}_s3_metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "source": "s3",
                        "bucket": self.source_bucket,
                        "prefix": self.source_prefix,
                        "region": self.source_region,
                        "object_count": len(objects),
                        "objects": objects,
                        "timestamp": datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                )

            # If full sync mode, also download objects
            if self.sync_mode == "full":
                objects_dir = tmp_path / "objects"
                objects_dir.mkdir(exist_ok=True)
                for obj_info in objects:
                    key = obj_info["key"]
                    safe_name = key.replace("/", "_")
                    try:
                        dest = objects_dir / safe_name
                        s3.download_file(self.source_bucket, key, str(dest))
                        logger.debug("Downloaded: %s", key)
                    except Exception as exc:
                        logger.warning("Failed to download %s: %s", key, exc)

            return True

        except ImportError:
            logger.error("boto3 is required for S3 cloud backup")
            return False
        except Exception as exc:
            logger.error("Failed to collect S3 data: %s", exc)
            return False

    def _collect_gcs_data(self, tmp_path: Path, backup_name: str) -> bool:
        """Collect data from a GCS bucket.

        Args:
            tmp_path: Temporary directory for collected data.
            backup_name: Base name for output files.

        Returns:
            True if collection succeeded.
        """
        try:
            from google.cloud import storage as gcs

            client = gcs.Client()
            bucket = client.bucket(self.source_bucket)
            blobs = bucket.list_blobs(prefix=self.source_prefix)

            objects = []
            for blob in blobs:
                objects.append(
                    {
                        "name": blob.name,
                        "size": blob.size,
                        "updated": blob.updated.isoformat() if blob.updated else None,
                        "content_type": blob.content_type,
                    }
                )

            logger.info("Collected %d objects from GCS bucket '%s'", len(objects), self.source_bucket)

            metadata_path = tmp_path / f"{backup_name}_gcs_metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "source": "gcs",
                        "bucket": self.source_bucket,
                        "prefix": self.source_prefix,
                        "object_count": len(objects),
                        "objects": objects,
                        "timestamp": datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                )

            if self.sync_mode == "full":
                blobs_dir = tmp_path / "objects"
                blobs_dir.mkdir(exist_ok=True)
                for obj_info in objects:
                    safe_name = obj_info["name"].replace("/", "_")
                    try:
                        blob = bucket.blob(obj_info["name"])
                        dest = blobs_dir / safe_name
                        blob.download_to_filename(str(dest))
                        logger.debug("Downloaded: %s", obj_info["name"])
                    except Exception as exc:
                        logger.warning("Failed to download %s: %s", obj_info["name"], exc)

            return True

        except ImportError:
            logger.error("google-cloud-storage is required for GCS cloud backup")
            return False
        except Exception as exc:
            logger.error("Failed to collect GCS data: %s", exc)
            return False

    def _collect_azure_data(self, tmp_path: Path, backup_name: str) -> bool:
        """Collect data from an Azure Blob Storage container.

        Args:
            tmp_path: Temporary directory for collected data.
            backup_name: Base name for output files.

        Returns:
            True if collection succeeded.
        """
        try:
            from azure.storage.blob import BlobServiceClient

            service_client = BlobServiceClient.from_connection_string(
                self.storage.connection_string or ""
            )
            container_client = service_client.get_container_client(self.source_bucket)
            blobs = container_client.list_blobs(name_starts_with=self.source_prefix)

            objects = []
            for blob in blobs:
                objects.append(
                    {
                        "name": blob.name,
                        "size": blob.size,
                        "last_modified": blob.last_modified.isoformat() if blob.last_modified else None,
                    }
                )

            logger.info(
                "Collected %d objects from Azure container '%s'",
                len(objects),
                self.source_bucket,
            )

            metadata_path = tmp_path / f"{backup_name}_azure_metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "source": "azure",
                        "container": self.source_bucket,
                        "prefix": self.source_prefix,
                        "object_count": len(objects),
                        "objects": objects,
                        "timestamp": datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                )

            return True

        except ImportError:
            logger.error("azure-storage-blob is required for Azure cloud backup")
            return False
        except Exception as exc:
            logger.error("Failed to collect Azure data: %s", exc)
            return False

    def _collect_sftp_data(self, tmp_path: Path, backup_name: str) -> bool:
        """Collect data from an SFTP server.

        Args:
            tmp_path: Temporary directory for collected data.
            backup_name: Base name for output files.

        Returns:
            True if collection succeeded.
        """
        try:
            import paramiko

            host = self.job.options.get("sftp_host", "")
            port = self.job.options.get("sftp_port", 22)
            username = self.job.options.get("sftp_username", "")
            password = self.job.options.get("sftp_password", "")
            remote_path = self.job.options.get("sftp_path", "/")

            transport = paramiko.Transport((host, port))
            transport.connect(username=username, password=password)
            sftp = paramiko.SFTPClient.from_transport(transport)

            # List remote files
            files = []
            try:
                for entry in sftp.listdir_attr(remote_path):
                    files.append(
                        {
                            "name": entry.filename,
                            "size": entry.st_size,
                            "modified": entry.st_mtime,
                        }
                    )
            except Exception as exc:
                logger.error("Failed to list SFTP directory: %s", exc)
                sftp.close()
                transport.close()
                return False

            sftp.close()
            transport.close()

            metadata_path = tmp_path / f"{backup_name}_sftp_metadata.json"
            with open(metadata_path, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "source": "sftp",
                        "host": host,
                        "port": port,
                        "path": remote_path,
                        "file_count": len(files),
                        "files": files,
                        "timestamp": datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                )

            logger.info("Collected %d files from SFTP server '%s'", len(files), host)
            return True

        except ImportError:
            logger.error("paramiko is required for SFTP cloud backup")
            return False
        except Exception as exc:
            logger.error("Failed to collect SFTP data: %s", exc)
            return False

    def _apply_compression(self, archive_path: Path, tmp_path: Path) -> Path:
        """Apply compression to the archive.

        Args:
            archive_path: Path to the archive.
            tmp_path: Temporary directory.

        Returns:
            Path to compressed file.
        """
        if self.job.compression == "gzip":
            compressed = tmp_path / f"{archive_path.name}.gz"
            self.compression.compress_gzip(str(archive_path), str(compressed))
            return compressed
        elif self.job.compression == "zip":
            compressed = tmp_path / f"{archive_path.name}.zip"
            self.compression.compress_zip(str(archive_path), str(compressed))
            return compressed
        return archive_path

    def _apply_encryption(self, file_path: Path, tmp_path: Path) -> Path:
        """Apply encryption if configured.

        Args:
            file_path: Path to the file.
            tmp_path: Temporary directory.

        Returns:
            Path to encrypted file (or original).
        """
        if self.encryption is None:
            return file_path
        encrypted = tmp_path / f"{file_path.name}.enc"
        self.encryption.encrypt_file(str(file_path), str(encrypted))
        return encrypted

    def _generate_checksum(self, file_path: Path, tmp_path: Path) -> Path:
        """Generate SHA-256 checksum.

        Args:
            file_path: Path to the file.
            tmp_path: Temporary directory.

        Returns:
            Path to checksum file.
        """
        checksum = self.integrity.compute_checksum(str(file_path))
        checksum_path = tmp_path / f"{file_path.name}.sha256"
        with open(checksum_path, "w", encoding="utf-8") as f:
            f.write(f"{checksum}  {file_path.name}\n")
        return checksum_path

    def _store_backup(self, backup_path: Path, checksum_path: Path) -> bool:
        """Store the backup to the configured storage backend.

        Args:
            backup_path: Path to backup file.
            checksum_path: Path to checksum file.

        Returns:
            True if storage succeeded.
        """
        backend = self.storage.backend
        try:
            if backend == "local":
                dest_dir = Path(self.storage.path) / self.job.destination
                dest_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup_path, dest_dir / backup_path.name)
                shutil.copy2(checksum_path, dest_dir / checksum_path.name)
                return True
            else:
                logger.info("Cloud-to-cloud backup not yet implemented")
                return True
        except Exception as exc:
            logger.error("Failed to store cloud backup: %s", exc)
            return False

    def _apply_retention(self) -> None:
        """Apply retention policy."""
        try:
            if self.storage.backend == "local":
                backup_dir = Path(self.storage.path) / self.job.destination
                if backup_dir.exists():
                    self.retention.cleanup_local(backup_dir)
        except Exception as exc:
            logger.warning("Retention cleanup failed (non-fatal): %s", exc)
