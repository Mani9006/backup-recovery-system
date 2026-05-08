"""Database backup handler for relational database systems.

Supports PostgreSQL (pg_dump), MySQL (mysqldump), SQLite (file copy),
and provides a generic command-based backup interface for other databases.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
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


class DatabaseBackupError(Exception):
    """Raised when a database backup operation fails."""

    pass


class DatabaseBackup:
    """Handles database backup operations for various database engines.

    Supports PostgreSQL, MySQL/MariaDB, and SQLite out of the box.
    Can be extended to support other databases through custom commands.
    """

    # Known database engines and their dump utilities
    SUPPORTED_ENGINES = {
        "postgresql": {
            "dump_cmd": "pg_dump",
            "env_vars": ["PGHOST", "PGPORT", "PGUSER", "PGPASSWORD"],
            "default_port": "5432",
        },
        "postgres": {
            "dump_cmd": "pg_dump",
            "env_vars": ["PGHOST", "PGPORT", "PGUSER", "PGPASSWORD"],
            "default_port": "5432",
        },
        "mysql": {
            "dump_cmd": "mysqldump",
            "env_vars": ["MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PWD"],
            "default_port": "3306",
        },
        "mariadb": {
            "dump_cmd": "mysqldump",
            "env_vars": ["MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PWD"],
            "default_port": "3306",
        },
        "sqlite": {
            "dump_cmd": None,  # Uses file copy approach
            "env_vars": [],
            "default_port": None,
        },
    }

    def __init__(
        self,
        job_config: BackupJobConfig,
        storage_config: StorageConfig,
    ) -> None:
        """Initialize the database backup handler.

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

        # Parse database-specific options
        self.engine = job_config.options.get("engine", "postgresql")
        self.database = job_config.options.get("database", "")
        self.host = job_config.options.get("host", "localhost")
        self.port = job_config.options.get("port")
        self.username = job_config.options.get("username", "")
        self.password = job_config.options.get("password", "")
        self.custom_args = job_config.options.get("custom_args", [])

    def run(self) -> bool:
        """Execute the database backup job.

        Returns:
            True if the backup completed successfully, False otherwise.
        """
        if self.engine not in self.SUPPORTED_ENGINES:
            logger.error(
                "Unsupported database engine: %s. Supported: %s",
                self.engine,
                list(self.SUPPORTED_ENGINES.keys()),
            )
            return False

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self.job.destination}_{timestamp}"

        try:
            with tempfile.TemporaryDirectory(prefix="db_backup_") as tmp_dir:
                tmp_path = Path(tmp_dir)
                dump_path = tmp_path / f"{backup_name}.sql"

                # Step 1: Create database dump
                if self.engine in ("postgresql", "postgres"):
                    success = self._dump_postgresql(dump_path)
                elif self.engine in ("mysql", "mariadb"):
                    success = self._dump_mysql(dump_path)
                elif self.engine == "sqlite":
                    success = self._dump_sqlite(dump_path)
                else:
                    logger.error("Unhandled engine: %s", self.engine)
                    return False

                if not success:
                    logger.error("Database dump failed")
                    return False

                logger.info("Database dump created: %s (size: %d bytes)", dump_path, dump_path.stat().st_size)

                # Step 2: Compress the dump
                compressed_path = self._compress_dump(dump_path, tmp_path, backup_name)

                # Step 3: Encrypt if configured
                final_path = self._apply_encryption(compressed_path, tmp_path)

                # Step 4: Generate checksum
                checksum_path = self._generate_checksum(final_path, tmp_path)

                # Step 5: Store backup
                storage_success = self._store_backup(final_path, checksum_path)
                if not storage_success:
                    return False

                # Step 6: Apply retention
                self._apply_retention()

                logger.info("Database backup '%s' completed successfully", self.job.name)
                return True

        except DatabaseBackupError as exc:
            logger.error("Database backup failed: %s", exc)
            return False
        except Exception as exc:
            logger.exception("Unexpected error during database backup: %s", exc)
            return False

    def _dump_postgresql(self, output_path: Path) -> bool:
        """Create a PostgreSQL database dump.

        Args:
            output_path: Path where the dump file will be written.

        Returns:
            True if the dump succeeded.
        """
        cmd = [
            "pg_dump",
            "--host", self.host,
            "--port", str(self.port or "5432"),
            "--username", self.username,
            "--file", str(output_path),
            "--verbose",
        ]

        # Add custom arguments
        cmd.extend(self.custom_args)

        # Add the database name (must be last)
        cmd.append(self.database)

        env = os.environ.copy()
        env["PGPASSWORD"] = self.password

        return self._run_dump_command(cmd, env)

    def _dump_mysql(self, output_path: Path) -> bool:
        """Create a MySQL/MariaDB database dump.

        Args:
            output_path: Path where the dump file will be written.

        Returns:
            True if the dump succeeded.
        """
        cmd = [
            "mysqldump",
            "--host", self.host,
            "--port", str(self.port or "3306"),
            "--user", self.username,
            "--result-file", str(output_path),
        ]

        # Add custom arguments
        cmd.extend(self.custom_args)

        # Add the database name (must be last)
        cmd.append(self.database)

        env = os.environ.copy()
        env["MYSQL_PWD"] = self.password

        return self._run_dump_command(cmd, env)

    def _dump_sqlite(self, output_path: Path) -> bool:
        """Create a SQLite database backup (file copy + integrity check).

        Args:
            output_path: Path where the dump file will be written.

        Returns:
            True if the backup succeeded.
        """
        source_db = Path(self.job.source)
        if not source_db.exists():
            logger.error("SQLite database not found: %s", source_db)
            return False

        try:
            # Copy the database file
            shutil.copy2(source_db, output_path)

            # Verify database integrity using sqlite3
            result = subprocess.run(
                ["sqlite3", str(source_db), "PRAGMA integrity_check;"],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0 or "ok" not in result.stdout.lower():
                logger.error("SQLite integrity check failed: %s", result.stderr)
                return False

            logger.info("SQLite database backed up successfully")
            return True

        except FileNotFoundError:
            logger.error("sqlite3 command not found. Is SQLite installed?")
            return False
        except subprocess.TimeoutExpired:
            logger.error("SQLite integrity check timed out")
            return False

    def _run_dump_command(self, cmd: list[str], env: dict[str, str]) -> bool:
        """Execute a database dump command.

        Args:
            cmd: Command and arguments list.
            env: Environment variables dictionary.

        Returns:
            True if the command succeeded.
        """
        logger.debug("Running dump command: %s", " ".join(cmd))

        try:
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                timeout=3600,
            )

            if result.returncode != 0:
                logger.error(
                    "Dump command failed (exit code %d): %s",
                    result.returncode,
                    result.stderr,
                )
                return False

            if result.stderr:
                logger.debug("Dump stderr: %s", result.stderr)

            return True

        except FileNotFoundError as exc:
            logger.error("Dump command not found: %s. Is the database client installed?", exc)
            return False
        except subprocess.TimeoutExpired:
            logger.error("Dump command timed out after 1 hour")
            return False

    def _compress_dump(
        self,
        dump_path: Path,
        tmp_path: Path,
        backup_name: str,
    ) -> Path:
        """Compress the database dump file.

        Args:
            dump_path: Path to the SQL dump file.
            tmp_path: Temporary directory.
            backup_name: Base name for the compressed file.

        Returns:
            Path to the compressed file.
        """
        if self.job.compression == "gzip":
            compressed = tmp_path / f"{backup_name}.sql.gz"
            self.compression.compress_gzip(str(dump_path), str(compressed))
            return compressed
        elif self.job.compression == "zip":
            compressed = tmp_path / f"{backup_name}.sql.zip"
            self.compression.compress_zip(str(dump_path), str(compressed))
            return compressed
        else:
            # No compression - rename to .sql file
            final = tmp_path / f"{backup_name}.sql"
            shutil.move(str(dump_path), str(final))
            return final

    def _apply_encryption(self, file_path: Path, tmp_path: Path) -> Path:
        """Apply encryption to the backup file if configured.

        Args:
            file_path: Path to the file to encrypt.
            tmp_path: Temporary directory for output.

        Returns:
            Path to the encrypted file (or original if not configured).
        """
        if self.encryption is None:
            return file_path

        encrypted_path = tmp_path / f"{file_path.name}.enc"
        self.encryption.encrypt_file(str(file_path), str(encrypted_path))
        logger.info("Database backup encrypted: %s", encrypted_path)
        return encrypted_path

    def _generate_checksum(self, file_path: Path, tmp_path: Path) -> Path:
        """Generate SHA-256 checksum for the backup file.

        Args:
            file_path: Path to the backup file.
            tmp_path: Temporary directory.

        Returns:
            Path to the checksum file.
        """
        checksum = self.integrity.compute_checksum(str(file_path))
        checksum_path = tmp_path / f"{file_path.name}.sha256"
        with open(checksum_path, "w", encoding="utf-8") as f:
            f.write(f"{checksum}  {file_path.name}\n")
        logger.info("Database backup checksum: %s", checksum)
        return checksum_path

    def _store_backup(self, backup_path: Path, checksum_path: Path) -> bool:
        """Store the backup to the configured storage backend.

        Args:
            backup_path: Path to the backup file.
            checksum_path: Path to the checksum file.

        Returns:
            True if storage succeeded.
        """
        backend = self.storage.backend

        try:
            if backend == "local":
                return self._store_local(backup_path, checksum_path)
            elif backend in ("s3", "gcs", "azure"):
                # Import storage backend dynamically
                storage_module = __import__(f"src.storage.{backend}", fromlist=["Storage"])
                storage_class = getattr(storage_module, f"{backend.upper()}Storage")
                kwargs = {"bucket": self.storage.bucket or "backups"}
                if backend == "s3":
                    kwargs.update({
                        "region": self.storage.region,
                        "access_key": self.storage.access_key,
                        "secret_key": self.storage.secret_key,
                    })
                elif backend == "azure":
                    kwargs = {"container": self.storage.bucket or "backups"}
                storage = storage_class(**kwargs)
                prefix = f"{self.job.destination}/"
                storage.upload(str(backup_path), f"{prefix}{backup_path.name}")
                storage.upload(str(checksum_path), f"{prefix}{checksum_path.name}")
                return True
            else:
                logger.error("Unknown storage backend: %s", backend)
                return False
        except ImportError as exc:
            logger.error("Storage backend '%s' requires additional dependencies: %s", backend, exc)
            return False
        except Exception as exc:
            logger.exception("Failed to store database backup: %s", exc)
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

        logger.info("Database backup stored locally: %s", dest_backup)
        return True

    def _apply_retention(self) -> None:
        """Apply retention policy to clean up old database backups."""
        try:
            if self.storage.backend == "local":
                backup_dir = Path(self.storage.path) / self.job.destination
                if backup_dir.exists():
                    self.retention.cleanup_local(backup_dir)
            else:
                logger.info("Cloud retention cleanup not yet implemented for database backups")
        except Exception as exc:
            logger.warning("Retention cleanup failed (non-fatal): %s", exc)
