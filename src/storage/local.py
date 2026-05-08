"""Local filesystem storage backend.

Provides file operations for storing and retrieving backup files
from the local filesystem.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class LocalStorageError(Exception):
    """Raised when a local storage operation fails."""

    pass


class LocalStorage:
    """Filesystem-based storage backend for local backups.

    Manages backup files in a configurable base directory with
    support for subdirectories, file listing, and cleanup.
    """

    def __init__(self, base_path: str = "./backups") -> None:
        """Initialize the local storage backend.

        Args:
            base_path: Base directory for backup storage.

        Raises:
            LocalStorageError: If the base path cannot be created.
        """
        self.base_path = Path(base_path).resolve()

        try:
            self.base_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise LocalStorageError(
                f"Cannot create backup directory: {self.base_path}"
            ) from exc

        logger.debug("LocalStorage initialized: %s", self.base_path)

    def upload(self, local_path: str, remote_key: str) -> None:
        """Upload (copy) a file to local storage.

        Args:
            local_path: Path to the local file to upload.
            remote_key: Relative path within the base directory.

        Raises:
            LocalStorageError: If the file cannot be copied.
        """
        source = Path(local_path)
        if not source.exists():
            raise LocalStorageError(f"Source file not found: {local_path}")

        dest = self.base_path / remote_key
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(local_path, dest)
            logger.info("File stored locally: %s -> %s", local_path, dest)
        except OSError as exc:
            raise LocalStorageError(f"Failed to store file: {exc}") from exc

    def download(self, remote_key: str, local_path: str) -> None:
        """Download (copy) a file from local storage.

        Args:
            remote_key: Relative path within the base directory.
            local_path: Destination path for the downloaded file.

        Raises:
            LocalStorageError: If the file cannot be copied.
        """
        source = self.base_path / remote_key
        if not source.exists():
            raise LocalStorageError(f"File not found in storage: {remote_key}")

        try:
            dest = Path(local_path)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, dest)
            logger.info("File retrieved locally: %s -> %s", source, dest)
        except OSError as exc:
            raise LocalStorageError(f"Failed to retrieve file: {exc}") from exc

    def list_files(self, prefix: str = "") -> list[dict]:
        """List files in the storage backend.

        Args:
            prefix: Optional path prefix to filter by.

        Returns:
            List of file information dictionaries.
        """
        search_dir = self.base_path / prefix if prefix else self.base_path

        if not search_dir.exists():
            return []

        files = []
        for item in search_dir.rglob("*"):
            if item.is_file():
                stat = item.stat()
                files.append(
                    {
                        "key": str(item.relative_to(self.base_path)),
                        "size": stat.st_size,
                        "modified": stat.st_mtime,
                        "name": item.name,
                    }
                )

        files.sort(key=lambda f: f["modified"], reverse=True)
        return files

    def delete(self, remote_key: str) -> None:
        """Delete a file from storage.

        Args:
            remote_key: Relative path within the base directory.

        Raises:
            LocalStorageError: If the file cannot be deleted.
        """
        target = self.base_path / remote_key
        if not target.exists():
            logger.warning("File not found for deletion: %s", remote_key)
            return

        try:
            target.unlink()
            logger.info("Deleted from local storage: %s", target)
        except OSError as exc:
            raise LocalStorageError(f"Failed to delete file: {exc}") from exc

    def exists(self, remote_key: str) -> bool:
        """Check if a file exists in storage.

        Args:
            remote_key: Relative path within the base directory.

        Returns:
            True if the file exists.
        """
        return (self.base_path / remote_key).exists()

    def get_size(self, remote_key: str) -> int:
        """Get the size of a file in storage.

        Args:
            remote_key: Relative path within the base directory.

        Returns:
            File size in bytes.

        Raises:
            LocalStorageError: If the file does not exist.
        """
        target = self.base_path / remote_key
        if not target.exists():
            raise LocalStorageError(f"File not found: {remote_key}")
        return target.stat().st_size

    def cleanup_old_files(self, prefix: str = "", max_age_days: int = 30) -> int:
        """Remove files older than a specified age.

        Args:
            prefix: Optional path prefix to filter by.
            max_age_days: Maximum age in days.

        Returns:
            Number of files removed.
        """
        import time

        search_dir = self.base_path / prefix if prefix else self.base_path
        cutoff = time.time() - (max_age_days * 24 * 3600)
        removed = 0

        if not search_dir.exists():
            return 0

        for item in search_dir.rglob("*"):
            if item.is_file() and item.stat().st_mtime < cutoff:
                try:
                    item.unlink()
                    removed += 1
                    logger.debug("Cleaned up old file: %s", item)
                except OSError as exc:
                    logger.warning("Failed to clean up %s: %s", item, exc)

        logger.info("Cleanup removed %d files older than %d days", removed, max_age_days)
        return removed
