"""Retention policy manager for backup lifecycle management.

Implements grandfather-father-son style backup rotation with
daily, weekly, and monthly retention periods.
"""

from __future__ import annotations

import logging
import re
import shutil
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Optional

from src.config import RetentionPolicy

logger = logging.getLogger(__name__)


class RetentionError(Exception):
    """Raised when a retention operation fails."""

    pass


class BackupPeriod(Enum):
    """Backup classification period."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"


class RetentionManager:
    """Manages backup retention policies.

    Implements a grandfather-father-son rotation scheme:
    - Daily backups are kept for N days
    - Weekly backups (first of the week) are kept for N weeks
    - Monthly backups (first of the month) are kept for N months

    Backup files are classified based on their modification timestamps.
    """

    def __init__(self, policy: RetentionPolicy) -> None:
        """Initialize the retention manager.

        Args:
            policy: Retention policy configuration.
        """
        self.policy = policy
        logger.debug(
            "RetentionManager initialized (daily=%d, weekly=%d, monthly=%d)",
            policy.daily,
            policy.weekly,
            policy.monthly,
        )

    def classify_backup(self, file_path: Path) -> Optional[BackupPeriod]:
        """Classify a backup file by its period type.

        A backup is classified as:
        - MONTHLY: if it's the first day of the month
        - WEEKLY: if it's the first day of the week (Monday)
        - DAILY: otherwise

        Args:
            file_path: Path to the backup file.

        Returns:
            The backup period classification, or None if the file doesn't exist.
        """
        if not file_path.exists():
            return None

        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)

        # Check if monthly (first day of month)
        if mtime.day == 1:
            return BackupPeriod.MONTHLY

        # Check if weekly (Monday = 0)
        if mtime.weekday() == 0:
            return BackupPeriod.WEEKLY

        return BackupPeriod.DAILY

    def should_keep(self, file_path: Path) -> bool:
        """Determine if a backup file should be kept based on retention policy.

        Args:
            file_path: Path to the backup file.

        Returns:
            True if the file should be retained.
        """
        if not file_path.exists():
            return False

        period = self.classify_backup(file_path)
        if period is None:
            return False

        mtime = datetime.fromtimestamp(file_path.stat().st_mtime)
        now = datetime.now()

        if period == BackupPeriod.DAILY:
            cutoff = now - timedelta(days=self.policy.daily)
            keep = mtime >= cutoff
            logger.debug(
                "Daily backup %s: mtime=%s, cutoff=%s, keep=%s",
                file_path.name,
                mtime.strftime("%Y-%m-%d"),
                cutoff.strftime("%Y-%m-%d"),
                keep,
            )
            return keep

        elif period == BackupPeriod.WEEKLY:
            # Keep for N weeks
            cutoff = now - timedelta(weeks=self.policy.weekly)
            keep = mtime >= cutoff
            logger.debug(
                "Weekly backup %s: mtime=%s, cutoff=%s, keep=%s",
                file_path.name,
                mtime.strftime("%Y-%m-%d"),
                cutoff.strftime("%Y-%m-%d"),
                keep,
            )
            return keep

        elif period == BackupPeriod.MONTHLY:
            # Keep for N months (approximate: 30 days per month)
            cutoff = now - timedelta(days=self.policy.monthly * 30)
            keep = mtime >= cutoff
            logger.debug(
                "Monthly backup %s: mtime=%s, cutoff=%s, keep=%s",
                file_path.name,
                mtime.strftime("%Y-%m-%d"),
                cutoff.strftime("%Y-%m-%d"),
                keep,
            )
            return keep

        return False

    def list_backups(self, directory: Path) -> list[Path]:
        """List all backup files in a directory.

        Args:
            directory: Directory to scan.

        Returns:
            Sorted list of backup file paths.
        """
        if not directory.exists():
            return []

        # Common backup file extensions
        backup_extensions = {
            ".tar", ".gz", ".tgz", ".bz2", ".zip", ".enc", ".sql", ".dump"
        }

        backups = []
        for item in directory.iterdir():
            if item.is_file():
                # Include files with backup extensions or checksum files
                if item.suffix in backup_extensions or "_" in item.name:
                    backups.append(item)

        # Sort by modification time (newest first)
        backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return backups

    def cleanup_local(self, backup_dir: Path) -> dict[str, int]:
        """Clean up old backups in a local directory.

        Args:
            backup_dir: Directory containing backup files.

        Returns:
            Dictionary with 'kept' and 'removed' counts.
        """
        if not backup_dir.exists():
            logger.warning("Backup directory does not exist: %s", backup_dir)
            return {"kept": 0, "removed": 0}

        backups = self.list_backups(backup_dir)
        logger.info(
            "Retention cleanup: %d backup files found in %s", len(backups), backup_dir
        )

        kept = 0
        removed = 0

        for backup in backups:
            if backup.suffix == ".sha256":
                # Keep checksum files if their corresponding backup is kept
                # We'll process these later
                continue

            if self.should_keep(backup):
                kept += 1
                logger.debug("Keeping backup: %s", backup.name)
            else:
                try:
                    backup.unlink()
                    removed += 1
                    logger.info("Removed old backup: %s", backup.name)

                    # Also remove checksum file if it exists
                    checksum = backup.with_suffix(backup.suffix + ".sha256")
                    if not checksum.exists():
                        checksum = backup.parent / f"{backup.name}.sha256"
                    if checksum.exists():
                        checksum.unlink()
                        logger.debug("Removed checksum file: %s", checksum.name)

                except OSError as exc:
                    logger.error("Failed to remove %s: %s", backup, exc)

        logger.info(
            "Retention cleanup complete: %d kept, %d removed", kept, removed
        )
        return {"kept": kept, "removed": removed}

    def get_retention_summary(self, backup_dir: Path) -> dict[str, Any]:
        """Get a summary of the retention status for a backup directory.

        Args:
            backup_dir: Directory containing backup files.

        Returns:
            Dictionary with retention statistics.
        """
        backups = self.list_backups(backup_dir)
        now = datetime.now()

        daily_count = 0
        weekly_count = 0
        monthly_count = 0
        total_size = 0

        daily_oldest = None
        weekly_oldest = None
        monthly_oldest = None

        for backup in backups:
            if backup.suffix == ".sha256":
                continue

            period = self.classify_backup(backup)
            size = backup.stat().st_size
            total_size += size

            if period == BackupPeriod.DAILY:
                daily_count += 1
                mtime = datetime.fromtimestamp(backup.stat().st_mtime)
                if daily_oldest is None or mtime < daily_oldest:
                    daily_oldest = mtime

            elif period == BackupPeriod.WEEKLY:
                weekly_count += 1
                mtime = datetime.fromtimestamp(backup.stat().st_mtime)
                if weekly_oldest is None or mtime < weekly_oldest:
                    weekly_oldest = mtime

            elif period == BackupPeriod.MONTHLY:
                monthly_count += 1
                mtime = datetime.fromtimestamp(backup.stat().st_mtime)
                if monthly_oldest is None or mtime < monthly_oldest:
                    monthly_oldest = mtime

        return {
            "total_backups": len(backups),
            "daily_backups": daily_count,
            "weekly_backups": weekly_count,
            "monthly_backups": monthly_count,
            "total_size_bytes": total_size,
            "total_size_human": self._format_size(total_size),
            "daily_oldest": daily_oldest.isoformat() if daily_oldest else None,
            "weekly_oldest": weekly_oldest.isoformat() if weekly_oldest else None,
            "monthly_oldest": monthly_oldest.isoformat() if monthly_oldest else None,
            "policy": {
                "daily": self.policy.daily,
                "weekly": self.policy.weekly,
                "monthly": self.policy.monthly,
            },
        }

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """Format a byte size as a human-readable string.

        Args:
            size_bytes: Size in bytes.

        Returns:
            Human-readable string (e.g., '1.5 MB').
        """
        for unit in ["B", "KB", "MB", "GB", "TB"]:
            if size_bytes < 1024.0:
                return f"{size_bytes:.1f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.1f} PB"
