"""Tests for the configuration module."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from src.config import (
    AppConfig,
    BackupJobConfig,
    ConfigLoader,
    ConfigurationError,
    NotificationConfig,
    RetentionPolicy,
    StorageConfig,
)


class TestRetentionPolicy:
    """Tests for RetentionPolicy dataclass."""

    def test_default_values(self):
        """Test that default retention values are correct."""
        policy = RetentionPolicy()
        assert policy.daily == 7
        assert policy.weekly == 4
        assert policy.monthly == 12

    def test_from_dict(self):
        """Test creating RetentionPolicy from dictionary."""
        data = {"daily": 14, "weekly": 8, "monthly": 24}
        policy = RetentionPolicy.from_dict(data)
        assert policy.daily == 14
        assert policy.weekly == 8
        assert policy.monthly == 24

    def test_from_dict_partial(self):
        """Test creating RetentionPolicy from partial dictionary."""
        data = {"daily": 10}
        policy = RetentionPolicy.from_dict(data)
        assert policy.daily == 10
        assert policy.weekly == 4  # default
        assert policy.monthly == 12  # default


class TestStorageConfig:
    """Tests for StorageConfig dataclass."""

    def test_default_values(self):
        """Test that default storage config values are correct."""
        config = StorageConfig()
        assert config.backend == "local"
        assert config.path == "./backups"
        assert config.bucket is None

    def test_from_dict(self):
        """Test creating StorageConfig from dictionary."""
        data = {
            "backend": "s3",
            "path": "/custom/backups",
            "bucket": "my-backups",
            "region": "us-west-2",
        }
        config = StorageConfig.from_dict(data)
        assert config.backend == "s3"
        assert config.path == "/custom/backups"
        assert config.bucket == "my-backups"
        assert config.region == "us-west-2"


class TestNotificationConfig:
    """Tests for NotificationConfig dataclass."""

    def test_default_values(self):
        """Test that default notification config values are correct."""
        config = NotificationConfig()
        assert config.on_success is False
        assert config.on_failure is True
        assert config.smtp_port == 587

    def test_from_dict(self):
        """Test creating NotificationConfig from dictionary."""
        data = {
            "on_success": True,
            "webhook_url": "https://hooks.example.com/notify",
            "email_to": "admin@example.com",
        }
        config = NotificationConfig.from_dict(data)
        assert config.on_success is True
        assert config.webhook_url == "https://hooks.example.com/notify"
        assert config.email_to == "admin@example.com"


class TestBackupJobConfig:
    """Tests for BackupJobConfig dataclass."""

    def test_default_values(self):
        """Test that default job config values are correct."""
        config = BackupJobConfig(name="test_job", job_type="file")
        assert config.name == "test_job"
        assert config.job_type == "file"
        assert config.schedule == "0 2 * * *"
        assert config.compression == "gzip"
        assert config.encryption is False
        assert config.enabled is True

    def test_from_dict(self):
        """Test creating BackupJobConfig from dictionary."""
        data = {
            "type": "database",
            "schedule": "0 3 * * 0",
            "source": "mydb",
            "destination": "db_backups",
            "compression": "zip",
            "encryption": True,
            "encryption_key": "secret123",
            "retention": {"daily": 14},
            "enabled": False,
        }
        config = BackupJobConfig.from_dict("my_job", data)
        assert config.name == "my_job"
        assert config.job_type == "database"
        assert config.schedule == "0 3 * * 0"
        assert config.compression == "zip"
        assert config.encryption is True
        assert config.encryption_key == "secret123"
        assert config.enabled is False


class TestConfigLoader:
    """Tests for ConfigLoader."""

    def test_load_valid_config(self, tmp_path: Path):
        """Test loading a valid configuration file."""
        config_file = tmp_path / "config.yaml"
        config_content = """
log_level: INFO
storage:
  backend: local
  path: ./backups
notifications:
  on_failure: true
jobs:
  documents:
    type: file
    schedule: "0 2 * * *"
    source: ./documents
    destination: docs_backup
    compression: gzip
    encryption: false
"""
        config_file.write_text(config_content)

        config = ConfigLoader.load(config_file)
        assert isinstance(config, AppConfig)
        assert config.log_level == "INFO"
        assert len(config.jobs) == 1
        assert config.jobs[0].name == "documents"

    def test_load_nonexistent_file(self):
        """Test that loading a nonexistent file raises an error."""
        with pytest.raises(ConfigurationError, match="not found"):
            ConfigLoader.load("/nonexistent/config.yaml")

    def test_load_invalid_yaml(self, tmp_path: Path):
        """Test that invalid YAML raises an error."""
        config_file = tmp_path / "bad.yaml"
        config_file.write_text("invalid: yaml: [{")

        with pytest.raises(ConfigurationError, match="Invalid YAML"):
            ConfigLoader.load(config_file)

    def test_load_invalid_log_level(self, tmp_path: Path):
        """Test that invalid log level raises an error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
log_level: INVALID
storage:
  backend: local
jobs: {}
""")

        with pytest.raises(ConfigurationError, match="Invalid log_level"):
            ConfigLoader.load(config_file)

    def test_load_invalid_backend(self, tmp_path: Path):
        """Test that invalid storage backend raises an error."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
storage:
  backend: unknown
jobs: {}
""")

        with pytest.raises(ConfigurationError, match="Invalid storage backend"):
            ConfigLoader.load(config_file)

    def test_env_substitution(self, tmp_path: Path, monkeypatch):
        """Test environment variable substitution in config."""
        monkeypatch.setenv("BACKUP_BUCKET", "test-bucket")
        monkeypatch.setenv("BACKUP_REGION", "eu-west-1")

        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
storage:
  backend: s3
  bucket: ${BACKUP_BUCKET}
  region: ${BACKUP_REGION}
jobs: {}
""")

        config = ConfigLoader.load(config_file)
        assert config.storage.bucket == "test-bucket"
        assert config.storage.region == "eu-west-1"

    def test_env_substitution_with_default(self, tmp_path: Path):
        """Test env substitution with default value."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
storage:
  backend: s3
  bucket: ${MISSING_VAR:-default-bucket}
jobs: {}
""")

        config = ConfigLoader.load(config_file)
        assert config.storage.bucket == "default-bucket"

    def test_create_default(self, tmp_path: Path):
        """Test creating a default configuration file."""
        output = tmp_path / "default.yaml"
        ConfigLoader.create_default(output)

        assert output.exists()
        content = output.read_text()
        assert "jobs:" in content
        assert "log_level:" in content

    def test_validate_with_no_jobs(self, tmp_path: Path, caplog):
        """Test that empty jobs list triggers a warning."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
log_level: INFO
storage:
  backend: local
jobs: {}
""")

        config = ConfigLoader.load(config_file)
        assert len(config.jobs) == 0
