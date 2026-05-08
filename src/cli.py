"""Command-line interface for the Automated Backup & Recovery System.

Provides commands for running backups, restores, listing jobs,
validating configurations, and managing the scheduler.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path
from typing import Optional, Sequence

from src.config import ConfigLoader, ConfigurationError
from src.compression import CompressionManager
from src.encryption import EncryptionManager
from src.integrity import IntegrityVerifier
from src.notifications import NotificationManager
from src.restore import RestoreManager
from src.retention import RetentionManager
from src.scheduler import Scheduler

logger = logging.getLogger(__name__)


def setup_logging(level: str = "INFO") -> None:
    """Configure the logging system.

    Args:
        level: Logging level as a string.
    """
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the CLI.

    Returns:
        Configured ArgumentParser instance.
    """
    parser = argparse.ArgumentParser(
        prog="backup-recovery",
        description="Automated Backup & Recovery System - "
        "Backup files, databases, and cloud resources with "
        "scheduling, compression, encryption, and retention policies.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --config config.yaml backup --job documents
  %(prog)s --config config.yaml restore --archive backups/documents_20240115.tar.gz
  %(prog)s --config config.yaml schedule
  %(prog)s --config config.yaml validate
  %(prog)s --config config.yaml list-jobs
        """,
    )

    parser.add_argument(
        "--config",
        "-c",
        type=str,
        default="config.yaml",
        help="Path to the configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # backup command
    backup_parser = subparsers.add_parser(
        "backup",
        help="Run a backup job",
        description="Execute a backup job by name or run all enabled jobs.",
    )
    backup_parser.add_argument(
        "--job",
        "-j",
        type=str,
        help="Name of the backup job to run (runs all if omitted)",
    )
    backup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without executing",
    )

    # restore command
    restore_parser = subparsers.add_parser(
        "restore",
        help="Restore from a backup archive",
        description="Restore files or databases from a backup archive.",
    )
    restore_parser.add_argument(
        "--archive",
        "-a",
        type=str,
        required=True,
        help="Path to the backup archive to restore from",
    )
    restore_parser.add_argument(
        "--destination",
        "-d",
        type=str,
        help="Destination path for the restore (defaults to original location)",
    )
    restore_parser.add_argument(
        "--encryption-key",
        type=str,
        help="Encryption key for encrypted archives",
    )
    restore_parser.add_argument(
        "--verify",
        action="store_true",
        default=True,
        help="Verify archive integrity before restoring (default: True)",
    )

    # schedule command
    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Run the backup scheduler daemon",
        description="Start the scheduler to run backup jobs on their defined schedules.",
    )
    schedule_parser.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Scheduler polling interval in seconds (default: 60)",
    )

    # validate command
    subparsers.add_parser(
        "validate",
        help="Validate the configuration file",
        description="Check the configuration file for errors and missing values.",
    )

    # list-jobs command
    subparsers.add_parser(
        "list-jobs",
        help="List all configured backup jobs",
        description="Display a table of all backup jobs with their status.",
    )

    # verify command
    verify_parser = subparsers.add_parser(
        "verify",
        help="Verify backup archive integrity",
        description="Check the integrity of a backup archive using checksums.",
    )
    verify_parser.add_argument(
        "--archive",
        "-a",
        type=str,
        required=True,
        help="Path to the backup archive",
    )
    verify_parser.add_argument(
        "--checksum-file",
        type=str,
        help="Path to the checksum file",
    )

    # init command
    init_parser = subparsers.add_parser(
        "init",
        help="Create a default configuration file",
        description="Generate a default configuration file as a starting point.",
    )
    init_parser.add_argument(
        "--output",
        "-o",
        type=str,
        default="config.yaml",
        help="Output path for the configuration file (default: config.yaml)",
    )

    return parser


def cmd_backup(
    args: argparse.Namespace,
    config_path: str,
) -> int:
    """Handle the backup command.

    Args:
        args: Parsed command-line arguments.
        config_path: Path to the configuration file.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    try:
        app_config = ConfigLoader.load(config_path)
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    notifier = NotificationManager(app_config.notifications)

    jobs_to_run = app_config.jobs
    if args.job:
        jobs_to_run = [j for j in jobs_to_run if j.name == args.job]
        if not jobs_to_run:
            logger.error("Backup job '%s' not found", args.job)
            return 1

    if args.dry_run:
        logger.info("DRY RUN mode - no changes will be made")

    success_count = 0
    failure_count = 0

    for job in jobs_to_run:
        if not job.enabled:
            logger.info("Skipping disabled job: %s", job.name)
            continue

        logger.info("Running backup job: %s (type=%s)", job.name, job.job_type)

        if args.dry_run:
            logger.info("[DRY RUN] Would backup %s to %s", job.source, job.destination)
            success_count += 1
            continue

        try:
            result = _execute_backup_job(job, app_config)
            if result:
                logger.info("Backup job '%s' completed successfully", job.name)
                notifier.notify_success(
                    job_name=job.name,
                    details=f"Type: {job.job_type}, Destination: {job.destination}",
                )
                success_count += 1
            else:
                logger.error("Backup job '%s' failed", job.name)
                notifier.notify_failure(
                    job_name=job.name,
                    error="Backup execution returned failure",
                )
                failure_count += 1
        except Exception as exc:
            logger.exception("Backup job '%s' failed with exception", job.name)
            notifier.notify_failure(
                job_name=job.name,
                error=str(exc),
            )
            failure_count += 1

    logger.info("Backup run complete: %d success, %d failure", success_count, failure_count)
    return 0 if failure_count == 0 else 1


def _execute_backup_job(job, app_config) -> bool:
    """Execute a single backup job.

    Args:
        job: Backup job configuration.
        app_config: Application configuration.

    Returns:
        True if the backup succeeded, False otherwise.
    """
    # Dispatch to the appropriate backup handler based on job type
    backup_module_map = {
        "file": "src.backup.file_backup",
        "database": "src.backup.database_backup",
        "cloud": "src.backup.cloud_backup",
    }

    module_name = backup_module_map.get(job.job_type, "src.backup.file_backup")

    try:
        if job.job_type == "file":
            from src.backup.file_backup import FileBackup

            handler = FileBackup(job, app_config.storage)
        elif job.job_type == "database":
            from src.backup.database_backup import DatabaseBackup

            handler = DatabaseBackup(job, app_config.storage)
        elif job.job_type == "cloud":
            from src.backup.cloud_backup import CloudBackup

            handler = CloudBackup(job, app_config.storage)
        else:
            logger.error("Unknown backup type: %s", job.job_type)
            return False

        return handler.run()
    except ImportError as exc:
        logger.error("Failed to import backup handler for type '%s': %s", job.job_type, exc)
        return False


def cmd_restore(
    args: argparse.Namespace,
    config_path: str,
) -> int:
    """Handle the restore command.

    Args:
        args: Parsed command-line arguments.
        config_path: Path to the configuration file.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    try:
        app_config = ConfigLoader.load(config_path)
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    archive_path = Path(args.archive)
    if not archive_path.exists():
        logger.error("Archive not found: %s", archive_path)
        return 1

    if args.verify:
        logger.info("Verifying archive integrity...")
        verifier = IntegrityVerifier()
        if not verifier.verify_archive(str(archive_path)):
            logger.error("Archive integrity check failed")
            return 1
        logger.info("Archive integrity verified")

    restore_manager = RestoreManager(
        storage_config=app_config.storage,
        encryption_key=args.encryption_key or app_config.jobs[0].encryption_key if app_config.jobs else None,
    )

    try:
        result = restore_manager.restore(
            archive_path=str(archive_path),
            destination=args.destination,
        )
        if result:
            logger.info("Restore completed successfully")
            return 0
        logger.error("Restore failed")
        return 1
    except Exception as exc:
        logger.exception("Restore failed with exception")
        return 1


def cmd_schedule(
    args: argparse.Namespace,
    config_path: str,
) -> int:
    """Handle the schedule command.

    Args:
        args: Parsed command-line arguments.
        config_path: Path to the configuration file.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    try:
        app_config = ConfigLoader.load(config_path)
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    scheduler = Scheduler(
        config=app_config,
        poll_interval=args.interval,
    )

    # Setup signal handlers for graceful shutdown
    def _signal_handler(signum, frame):
        logger.info("Received signal %d, shutting down scheduler...", signum)
        scheduler.stop()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("Starting scheduler with %d jobs (poll interval: %ds)",
                len([j for j in app_config.jobs if j.enabled]), args.interval)
    scheduler.start()
    return 0


def cmd_validate(
    args: argparse.Namespace,
    config_path: str,
) -> int:
    """Handle the validate command.

    Args:
        args: Parsed command-line arguments.
        config_path: Path to the configuration file.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    logger.info("Validating configuration: %s", config_path)
    try:
        app_config = ConfigLoader.load(config_path)
        logger.info("Configuration is valid")
        logger.info("Jobs configured: %d", len(app_config.jobs))
        for job in app_config.jobs:
            status = "enabled" if job.enabled else "disabled"
            logger.info("  - %s (%s, %s)", job.name, job.job_type, status)
        return 0
    except ConfigurationError as exc:
        logger.error("Configuration validation failed: %s", exc)
        return 1


def cmd_list_jobs(
    args: argparse.Namespace,
    config_path: str,
) -> int:
    """Handle the list-jobs command.

    Args:
        args: Parsed command-line arguments.
        config_path: Path to the configuration file.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    try:
        app_config = ConfigLoader.load(config_path)
    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1

    print(f"\n{'Job Name':<20} {'Type':<12} {'Schedule':<20} {'Status':<10} Source")
    print("-" * 90)
    for job in app_config.jobs:
        status = "ENABLED" if job.enabled else "DISABLED"
        print(
            f"{job.name:<20} {job.job_type:<12} {job.schedule:<20} "
            f"{status:<10} {job.source}"
        )
    print(f"\nTotal: {len(app_config.jobs)} jobs")
    return 0


def cmd_verify(
    args: argparse.Namespace,
    config_path: str,
) -> int:
    """Handle the verify command.

    Args:
        args: Parsed command-line arguments.
        config_path: Path to the configuration file.

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    archive_path = Path(args.archive)
    if not archive_path.exists():
        logger.error("Archive not found: %s", archive_path)
        return 1

    verifier = IntegrityVerifier()
    logger.info("Verifying archive: %s", archive_path)

    if verifier.verify_archive(str(archive_path), args.checksum_file):
        logger.info("Archive integrity: VERIFIED")
        return 0
    else:
        logger.error("Archive integrity: FAILED")
        return 1


def cmd_init(
    args: argparse.Namespace,
    config_path: str,
) -> int:
    """Handle the init command.

    Args:
        args: Parsed command-line arguments.
        config_path: Path to the configuration file (unused).

    Returns:
        Exit code (0 for success, 1 for failure).
    """
    output_path = Path(args.output)
    if output_path.exists():
        logger.warning("Configuration file already exists: %s", output_path)
        response = input("Overwrite? [y/N]: ")
        if response.lower() != "y":
            logger.info("Aborted")
            return 0

    try:
        ConfigLoader.create_default(output_path)
        logger.info("Default configuration created at: %s", output_path)
        return 0
    except ConfigurationError as exc:
        logger.error("Failed to create configuration: %s", exc)
        return 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Main entry point for the CLI.

    Args:
        argv: Optional sequence of command-line arguments.

    Returns:
        Exit code (0 for success, non-zero for failure).
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    log_level = "DEBUG" if args.verbose else None
    if log_level:
        setup_logging(log_level)
    else:
        setup_logging("INFO")

    command_handlers = {
        "backup": cmd_backup,
        "restore": cmd_restore,
        "schedule": cmd_schedule,
        "validate": cmd_validate,
        "list-jobs": cmd_list_jobs,
        "verify": cmd_verify,
        "init": cmd_init,
    }

    handler = command_handlers.get(args.command)
    if handler is None:
        logger.error("Unknown command: %s", args.command)
        return 1

    return handler(args, args.config)


if __name__ == "__main__":
    sys.exit(main())
