"""Tests for the retention policy module."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from src.retention import BackupPeriod, RetentionError, RetentionManager
from src.config import RetentionPolicy


class TestRetentionManager:
    """Tests for RetentionManager."""

    def _make_policy(self, daily: int = 7, weekly: int = 4, monthly: int = 12) -> RetentionPolicy:
        """Create a test retention policy."""
        return RetentionPolicy(daily=daily, weekly=weekly, monthly=monthly)

    def test_init(self):
        """Test initialization."""
        policy = self._make_policy(14, 8, 24)
        mgr = RetentionManager(policy)
        assert mgr.policy.daily == 14
        assert mgr.policy.weekly == 8
        assert mgr.policy.monthly == 24

    def test_classify_backup_daily(self, tmp_path: Path):
        """Test classifying a non-special day as daily."""
        # Create a file and set mtime to a Tuesday (not Monday, not 1st of month)
        test_file = tmp_path / "backup.tar.gz"
        test_file.touch()

        # Set mtime to Jan 16, 2024 (Tuesday)
        mtime = datetime(2024, 1, 16, 12, 0, 0).timestamp()
        os.utime(test_file, (mtime, mtime))

        mgr = RetentionManager(self._make_policy())
        period = mgr.classify_backup(test_file)
        assert period == BackupPeriod.DAILY

    def test_classify_backup_weekly(self, tmp_path: Path):
        """Test classifying a Monday as weekly."""
        test_file = tmp_path / "backup.tar.gz"
        test_file.touch()

        # Monday, Jan 15, 2024
        mtime = datetime(2024, 1, 15, 12, 0, 0).timestamp()
        os.utime(test_file, (mtime, mtime))

        mgr = RetentionManager(self._make_policy())
        period = mgr.classify_backup(test_file)
        assert period == BackupPeriod.WEEKLY

    def test_classify_backup_monthly(self, tmp_path: Path):
        """Test classifying 1st of month as monthly."""
        test_file = tmp_path / "backup.tar.gz"
        test_file.touch()

        # Jan 1, 2024 (also a Monday, but monthly takes precedence by logic)
        mtime = datetime(2024, 1, 1, 12, 0, 0).timestamp()
        os.utime(test_file, (mtime, mtime))

        mgr = RetentionManager(self._make_policy())
        period = mgr.classify_backup(test_file)
        # First we check if it's the first of month -> monthly
        assert period == BackupPeriod.MONTHLY

    def test_classify_nonexistent(self, tmp_path: Path):
        """Test classifying a nonexistent file returns None."""
        mgr = RetentionManager(self._make_policy())
        result = mgr.classify_backup(tmp_path / "nonexistent")
        assert result is None

    def test_should_keep_recent(self, tmp_path: Path):
        """Test that recent files are kept."""
        test_file = tmp_path / "backup.tar.gz"
        test_file.touch()
        # Just created, should be kept

        mgr = RetentionManager(self._make_policy())
        assert mgr.should_keep(test_file) is True

    def test_should_keep_old(self, tmp_path: Path):
        """Test that old files are not kept."""
        test_file = tmp_path / "backup.tar.gz"
        test_file.touch()

        # Set mtime to 30 days ago (older than daily=7)
        old_time = (datetime.now() - timedelta(days=30)).timestamp()
        os.utime(test_file, (old_time, old_time))

        mgr = RetentionManager(self._make_policy(daily=7))
        # This is a daily backup, 30 days old > 7 day policy
        assert mgr.should_keep(test_file) is False

    def test_list_backups(self, tmp_path: Path):
        """Test listing backup files."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        (backup_dir / "backup_20240101.tar.gz").touch()
        (backup_dir / "backup_20240102.tar.gz").touch()
        (backup_dir / "readme.txt").write_text("not a backup")

        mgr = RetentionManager(self._make_policy())
        backups = mgr.list_backups(backup_dir)

        # Should find the backup files
        assert len(backups) >= 2

    def test_list_backups_empty(self, tmp_path: Path):
        """Test listing an empty directory."""
        mgr = RetentionManager(self._make_policy())
        backups = mgr.list_backups(tmp_path / "empty")
        assert backups == []

    def test_cleanup_local(self, tmp_path: Path):
        """Test local cleanup of old backups."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        # Create a recent backup (should be kept)
        recent = backup_dir / "backup_recent.tar.gz"
        recent.touch()

        # Create an old backup (should be removed)
        old = backup_dir / "backup_old.tar.gz"
        old.touch()
        old_time = (datetime.now() - timedelta(days=30)).timestamp()
        os.utime(old, (old_time, old_time))

        mgr = RetentionManager(self._make_policy(daily=7))
        result = mgr.cleanup_local(backup_dir)

        assert result["removed"] >= 1
        assert result["kept"] >= 1

    def test_cleanup_nonexistent(self, tmp_path: Path):
        """Test cleanup on nonexistent directory."""
        mgr = RetentionManager(self._make_policy())
        result = mgr.cleanup_local(tmp_path / "nonexistent")
        assert result == {"kept": 0, "removed": 0}

    def test_format_size(self):
        """Test human-readable size formatting."""
        assert RetentionManager._format_size(100) == "100.0 B"
        assert RetentionManager._format_size(1024) == "1.0 KB"
        assert RetentionManager._format_size(1024 * 1024) == "1.0 MB"
        assert RetentionManager._format_size(1024 * 1024 * 1024) == "1.0 GB"

    def test_get_retention_summary(self, tmp_path: Path):
        """Test retention summary generation."""
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()

        (backup_dir / "backup_20240101.tar.gz").write_bytes(b"data")

        mgr = RetentionManager(self._make_policy())
        summary = mgr.get_retention_summary(backup_dir)

        assert "total_backups" in summary
        assert "total_size_bytes" in summary
        assert "total_size_human" in summary
        assert "policy" in summary
        assert summary["policy"]["daily"] == 7


import os  # For os.utime
