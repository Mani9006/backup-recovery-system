"""Configuration management for the backup system.

Handles loading, validation, and access to YAML-based configuration files.
Supports environment variable substitution and schema validation.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class ConfigurationError(Exception):
    """Raised when configuration is invalid or cannot be loaded."""

    pass


@dataclass
class RetentionPolicy:
    """Retention policy settings for backup rotation."""

    daily: int = 7
    weekly: int = 4
    monthly: int = 12

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetentionPolicy":
        """Create RetentionPolicy from a dictionary."""
        return cls(
            daily=data.get("daily", 7),
            weekly=data.get("weekly", 4),
            monthly=data.get("monthly", 12),
        )


@dataclass
class StorageConfig:
    """Storage backend configuration."""

    backend: str = "local"  # local, s3, gcs, azure
    path: str = "./backups"
    bucket: Optional[str] = None
    region: Optional[str] = None
    access_key: Optional[str] = None
    secret_key: Optional[str] = None
    connection_string: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StorageConfig":
        """Create StorageConfig from a dictionary."""
        return cls(
            backend=data.get("backend", "local"),
            path=data.get("path", "./backups"),
            bucket=data.get("bucket"),
            region=data.get("region"),
            access_key=data.get("access_key"),
            secret_key=data.get("secret_key"),
            connection_string=data.get("connection_string"),
        )


@dataclass
class NotificationConfig:
    """Notification settings for backup events."""

    on_success: bool = False
    on_failure: bool = True
    webhook_url: Optional[str] = None
    email_to: Optional[str] = None
    email_from: Optional[str] = None
    smtp_host: Optional[str] = None
    smtp_port: int = 587

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NotificationConfig":
        """Create NotificationConfig from a dictionary."""
        return cls(
            on_success=data.get("on_success", False),
            on_failure=data.get("on_failure", True),
            webhook_url=data.get("webhook_url"),
            email_to=data.get("email_to"),
            email_from=data.get("email_from"),
            smtp_host=data.get("smtp_host"),
            smtp_port=data.get("smtp_port", 587),
        )


@dataclass
class BackupJobConfig:
    """Configuration for an individual backup job."""

    name: str
    job_type: str  # file, database, cloud
    schedule: str = "0 2 * * *"  # cron expression
    source: str = ""
    destination: str = ""
    compression: str = "gzip"  # gzip, zip, none
    encryption: bool = False
    encryption_key: Optional[str] = None
    retention: RetentionPolicy = field(default_factory=RetentionPolicy)
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: dict[str, Any]) -> "BackupJobConfig":
        """Create BackupJobConfig from a dictionary."""
        return cls(
            name=name,
            job_type=data.get("type", "file"),
            schedule=data.get("schedule", "0 2 * * *"),
            source=data.get("source", ""),
            destination=data.get("destination", ""),
            compression=data.get("compression", "gzip"),
            encryption=data.get("encryption", False),
            encryption_key=data.get("encryption_key"),
            retention=RetentionPolicy.from_dict(data.get("retention", {})),
            enabled=data.get("enabled", True),
            options=data.get("options", {}),
        )


@dataclass
class AppConfig:
    """Top-level application configuration."""

    log_level: str = "INFO"
    storage: StorageConfig = field(default_factory=StorageConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    jobs: list[BackupJobConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AppConfig":
        """Create AppConfig from a dictionary."""
        jobs = []
        for name, job_data in data.get("jobs", {}).items():
            jobs.append(BackupJobConfig.from_dict(name, job_data))
        return cls(
            log_level=data.get("log_level", "INFO"),
            storage=StorageConfig.from_dict(data.get("storage", {})),
            notifications=NotificationConfig.from_dict(data.get("notifications", {})),
            jobs=jobs,
        )


class ConfigLoader:
    """Loads and validates configuration from YAML files."""

    # Environment variable pattern: ${VAR} or ${VAR:-default}
    ENV_PATTERN = re.compile(r"\$\{([^}:]+)(?::-([^}]*))?\}")

    @staticmethod
    def _substitute_env_vars(value: str) -> str:
        """Substitute environment variables in a string value."""

        def replacer(match: re.Match[str]) -> str:
            var_name = match.group(1)
            default = match.group(2)
            env_value = os.environ.get(var_name)
            if env_value is not None:
                return env_value
            if default is not None:
                return default
            return match.group(0)

        return ConfigLoader.ENV_PATTERN.sub(replacer, value)

    @classmethod
    def _process_env_substitution(cls, obj: Any) -> Any:
        """Recursively process environment variable substitution."""
        if isinstance(obj, str):
            return cls._substitute_env_vars(obj)
        if isinstance(obj, dict):
            return {k: cls._process_env_substitution(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [cls._process_env_substitution(item) for item in obj]
        return obj

    @classmethod
    def load(cls, path: str | Path) -> AppConfig:
        """Load configuration from a YAML file.

        Args:
            path: Path to the YAML configuration file.

        Returns:
            Parsed and validated AppConfig instance.

        Raises:
            ConfigurationError: If the file cannot be read or parsed.
        """
        if yaml is None:
            raise ConfigurationError(
                "PyYAML is required for configuration loading. "
                "Install it with: pip install pyyaml"
            )

        config_path = Path(path)
        if not config_path.exists():
            raise ConfigurationError(f"Configuration file not found: {config_path}")

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                raw_data = yaml.safe_load(f)
        except yaml.YAMLError as exc:
            raise ConfigurationError(f"Invalid YAML syntax: {exc}") from exc
        except OSError as exc:
            raise ConfigurationError(f"Cannot read configuration file: {exc}") from exc

        if raw_data is None:
            raw_data = {}

        # Substitute environment variables
        processed_data = cls._process_env_substitution(raw_data)

        # Basic schema validation
        cls._validate(processed_data)

        logger.info("Configuration loaded from %s", config_path)
        return AppConfig.from_dict(processed_data)

    @staticmethod
    def _validate(data: dict[str, Any]) -> None:
        """Validate the configuration data structure.

        Args:
            data: Parsed configuration dictionary.

        Raises:
            ConfigurationError: If validation fails.
        """
        valid_log_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        log_level = data.get("log_level", "INFO")
        if log_level not in valid_log_levels:
            raise ConfigurationError(
                f"Invalid log_level '{log_level}'. Must be one of: {valid_log_levels}"
            )

        valid_backends = {"local", "s3", "gcs", "azure"}
        storage = data.get("storage", {})
        backend = storage.get("backend", "local")
        if backend not in valid_backends:
            raise ConfigurationError(
                f"Invalid storage backend '{backend}'. Must be one of: {valid_backends}"
            )

        valid_compressions = {"gzip", "zip", "none"}
        jobs = data.get("jobs", {})
        if not jobs:
            logger.warning("No backup jobs defined in configuration")

        for job_name, job_data in jobs.items():
            if not job_data.get("source"):
                logger.warning("Job '%s' has no source defined", job_name)
            compression = job_data.get("compression", "gzip")
            if compression not in valid_compressions:
                raise ConfigurationError(
                    f"Job '{job_name}': invalid compression '{compression}'. "
                    f"Must be one of: {valid_compressions}"
                )

    @classmethod
    def create_default(cls, path: str | Path) -> None:
        """Create a default configuration file at the given path.

        Args:
            path: Path where the default configuration will be written.
        """
        if yaml is None:
            raise ConfigurationError("PyYAML is required to create default configuration")

        default_config = {
            "log_level": "INFO",
            "storage": {
                "backend": "local",
                "path": "./backups",
            },
            "notifications": {
                "on_success": False,
                "on_failure": True,
            },
            "jobs": {
                "documents": {
                    "type": "file",
                    "schedule": "0 2 * * *",
                    "source": "./documents",
                    "destination": "documents_backup",
                    "compression": "gzip",
                    "encryption": False,
                    "retention": {
                        "daily": 7,
                        "weekly": 4,
                        "monthly": 12,
                    },
                    "enabled": True,
                }
            },
        }

        config_path = Path(path)
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(default_config, f, default_flow_style=False, sort_keys=False)

        logger.info("Default configuration written to %s", config_path)
