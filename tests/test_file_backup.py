"""Tests for the file backup module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.backup.file_backup import FileBackup, FileBackupError
from src.config import BackupJobConfig, RetentionPolicy, StorageConfig


class TestFileBackup:
    """Tests for FileBackup handler."""

    def _make_job(
        self,
        source: str,
        name: str = "test_backup",
        compression: str = "gzip",
        encryption: bool = False,
        **kwargs,
    ) -> BackupJobConfig:
        """Create a test backup job configuration."""
        return BackupJobConfig(
            name=name,
            job_type="file",
            source=source,
            destination=name,
            compression=compression,
            encryption=encryption,
            **kwargs,
        )

    def _make_storage(self, tmp_path: Path) -> StorageConfig:
        """Create a test storage configuration."""
        return StorageConfig(backend="local", path=str(tmp_path / "backups"))

    def test_init(self, tmp_path: Path):
        """Test FileBackup initialization."""
        job = self._make_job(str(tmp_path))
        storage = self._make_storage(tmp_path)
        handler = FileBackup(job, storage)

        assert handler.job == job
        assert handler.storage == storage

    def test_missing_source(self, tmp_path: Path):
        """Test that backup fails when source does not exist."""
        job = self._make_job("/nonexistent/path")
        storage = self._make_storage(tmp_path)
        handler = FileBackup(job, storage)

        result = handler.run()
        assert result is False

    def test_backup_single_file(self, tmp_path: Path):
        """Test backing up a single file."""
        # Create a test file
        test_file = tmp_path / "test.txt"
        test_file.write_text("Hello, World!")

        job = self._make_job(str(test_file))
        storage = self._make_storage(tmp_path)
        handler = FileBackup(job, storage)

        result = handler.run()
        assert result is True

        # Check that backup was created
        backup_dir = Path(storage.path) / job.destination
        assert backup_dir.exists()
        backup_files = list(backup_dir.glob("*"))
        assert len(backup_files) > 0

    def test_backup_directory(self, tmp_path: Path):
        """Test backing up a directory."""
        # Create test directory structure
        test_dir = tmp_path / "source"
        test_dir.mkdir()
        (test_dir / "file1.txt").write_text("Content 1")
        (test_dir / "subdir").mkdir()
        (test_dir / "subdir" / "file2.txt").write_text("Content 2")

        job = self._make_job(str(test_dir))
        storage = self._make_storage(tmp_path)
        handler = FileBackup(job, storage)

        result = handler.run()
        assert result is True

        backup_dir = Path(storage.path) / job.destination
        assert backup_dir.exists()

    def test_backup_with_exclusions(self, tmp_path: Path):
        """Test backing up with file exclusions."""
        test_dir = tmp_path / "source"
        test_dir.mkdir()
        (test_dir / "include.txt").write_text("Include me")
        (test_dir / "exclude.tmp").write_text("Exclude me")

        job = self._make_job(
            str(test_dir),
            options={"exclude": ["*.tmp"]},
        )
        storage = self._make_storage(tmp_path)
        handler = FileBackup(job, storage)

        result = handler.run()
        assert result is True

    def test_collect_files(self, tmp_path: Path):
        """Test the file collection method."""
        test_dir = tmp_path / "source"
        test_dir.mkdir()
        (test_dir / "a.txt").write_text("a")
        (test_dir / "b.txt").write_text("b")
        (test_dir / "skip.log").write_text("log")

        job = self._make_job(
            str(test_dir),
            options={"exclude": ["*.log"]},
        )
        storage = self._make_storage(tmp_path)
        handler = FileBackup(job, storage)

        files = handler._collect_files(test_dir)
        filenames = [f.name for f in files]

        assert "a.txt" in filenames
        assert "b.txt" in filenames
        assert "skip.log" not in filenames

    def test_classify_backup(self, tmp_path: Path):
        """Test backup classification by period."""
        from datetime import datetime
        import os
        from src.retention import RetentionManager, BackupPeriod

        # Create a mock backup file
        test_file = tmp_path / "backup_20240115.tar.gz"
        test_file.touch()

        # Set mtime to a known Monday (Jan 15, 2024)
        target_date = datetime(2024, 1, 15, 12, 0, 0)
        assert target_date.weekday() == 0  # Verify it's Monday
        os.utime(test_file, (target_date.timestamp(), target_date.timestamp()))

        policy = RetentionPolicy(daily=7, weekly=4, monthly=12)
        retention = RetentionManager(policy)

        period = retention.classify_backup(test_file)
        # Monday should be classified as weekly
        assert period == BackupPeriod.WEEKLY

    def test_should_keep_daily(self, tmp_path: Path):
        """Test that recent daily backups are kept."""
        from src.retention import RetentionManager

        test_file = tmp_path / "backup_20240115.tar.gz"
        test_file.touch()

        policy = RetentionPolicy(daily=7, weekly=4, monthly=12)
        retention = RetentionManager(policy)

        # Recent file should be kept
        assert retention.should_keep(test_file) is True
