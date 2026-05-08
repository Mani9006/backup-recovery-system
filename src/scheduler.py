"""Cron-like scheduler for backup jobs.

Uses the croniter library to evaluate cron expressions and determine
when backup jobs should be executed.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

try:
    from croniter import croniter
except ImportError:  # pragma: no cover
    croniter = None  # type: ignore[assignment]

from src.config import AppConfig, BackupJobConfig

logger = logging.getLogger(__name__)


class SchedulerError(Exception):
    """Raised when a scheduler operation fails."""

    pass


@dataclass
class ScheduledJob:
    """Internal representation of a scheduled job."""

    config: BackupJobConfig
    last_run: Optional[datetime] = None
    next_run: Optional[datetime] = None
    run_count: int = 0
    failure_count: int = 0

    def update_schedule(self, base_time: Optional[datetime] = None) -> None:
        """Calculate the next run time based on the cron schedule.

        Args:
            base_time: Optional base time for calculation (defaults to now).
        """
        if croniter is None:
            raise SchedulerError(
                "croniter library is required for scheduling. "
                "Install it with: pip install croniter"
            )

        itr = croniter(self.config.schedule, base_time or datetime.now())
        self.next_run = itr.get_next(datetime)


class Scheduler:
    """Manages scheduling and execution of backup jobs.

    Uses a polling loop to check for jobs that need to be executed
    based on their cron expressions.
    """

    def __init__(
        self,
        config: AppConfig,
        poll_interval: int = 60,
        job_executor: Optional[Callable[[BackupJobConfig], bool]] = None,
    ) -> None:
        """Initialize the scheduler.

        Args:
            config: Application configuration with backup jobs.
            poll_interval: How often to check for jobs (seconds).
            job_executor: Optional custom function to execute jobs.
        """
        self.config = config
        self.poll_interval = poll_interval
        self._job_executor = job_executor
        self._scheduled_jobs: list[ScheduledJob] = []
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # Initialize scheduled jobs
        self._init_jobs()

    def _init_jobs(self) -> None:
        """Initialize the internal scheduled job list."""
        self._scheduled_jobs = []
        for job_config in self.config.jobs:
            if not job_config.enabled:
                logger.debug("Skipping disabled job: %s", job_config.name)
                continue

            scheduled = ScheduledJob(config=job_config)
            try:
                scheduled.update_schedule()
                logger.info(
                    "Scheduled job '%s' with cron '%s' -> next run: %s",
                    job_config.name,
                    job_config.schedule,
                    scheduled.next_run.strftime("%Y-%m-%d %H:%M:%S") if scheduled.next_run else "unknown",
                )
            except SchedulerError as exc:
                logger.error(
                    "Failed to schedule job '%s': %s", job_config.name, exc
                )
                continue

            self._scheduled_jobs.append(scheduled)

    def start(self) -> None:
        """Start the scheduler polling loop.

        Blocks until stop() is called from another thread.
        """
        if self._running:
            logger.warning("Scheduler is already running")
            return

        self._running = True
        logger.info("Scheduler started (poll interval: %ds)", self.poll_interval)

        try:
            self._run_loop()
        except Exception as exc:
            logger.exception("Scheduler loop encountered an error: %s", exc)
        finally:
            self._running = False
            logger.info("Scheduler stopped")

    def _run_loop(self) -> None:
        """Main polling loop."""
        while self._running:
            self._check_and_execute_jobs()

            # Sleep in short intervals to allow quick shutdown
            for _ in range(self.poll_interval):
                if not self._running:
                    break
                time.sleep(1)

    def _check_and_execute_jobs(self) -> None:
        """Check all jobs and execute any that are due."""
        now = datetime.now()

        with self._lock:
            for scheduled in self._scheduled_jobs:
                if scheduled.next_run is None:
                    continue

                if now >= scheduled.next_run:
                    logger.info(
                        "Job '%s' is due (scheduled for %s)",
                        scheduled.config.name,
                        scheduled.next_run.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                    self._execute_job(scheduled)

                    # Schedule next run
                    try:
                        scheduled.update_schedule(base_time=now)
                        logger.info(
                            "Next run for '%s': %s",
                            scheduled.config.name,
                            scheduled.next_run.strftime("%Y-%m-%d %H:%M:%S") if scheduled.next_run else "unknown",
                        )
                    except SchedulerError as exc:
                        logger.error(
                            "Failed to reschedule job '%s': %s",
                            scheduled.config.name,
                            exc,
                        )

    def _execute_job(self, scheduled: ScheduledJob) -> None:
        """Execute a single scheduled job.

        Args:
            scheduled: The scheduled job to execute.
        """
        job = scheduled.config
        scheduled.last_run = datetime.now()
        scheduled.run_count += 1

        logger.info("Executing job '%s' (run #%d)", job.name, scheduled.run_count)

        try:
            if self._job_executor:
                success = self._job_executor(job)
            else:
                success = self._default_executor(job)

            if success:
                logger.info("Job '%s' executed successfully", job.name)
            else:
                logger.error("Job '%s' execution failed", job.name)
                scheduled.failure_count += 1

        except Exception as exc:
            logger.exception("Job '%s' raised an exception: %s", job.name, exc)
            scheduled.failure_count += 1

    def _default_executor(self, job: BackupJobConfig) -> bool:
        """Default job execution handler.

        Args:
            job: Backup job configuration.

        Returns:
            True if execution succeeded, False otherwise.
        """
        # Import here to avoid circular dependencies
        from src.notifications import NotificationManager

        notifier = NotificationManager(self.config.notifications)

        try:
            if job.job_type == "file":
                from src.backup.file_backup import FileBackup

                handler = FileBackup(job, self.config.storage)
            elif job.job_type == "database":
                from src.backup.database_backup import DatabaseBackup

                handler = DatabaseBackup(job, self.config.storage)
            elif job.job_type == "cloud":
                from src.backup.cloud_backup import CloudBackup

                handler = CloudBackup(job, self.config.storage)
            else:
                logger.error("Unknown job type: %s", job.job_type)
                return False

            result = handler.run()

            if result:
                notifier.notify_success(
                    job_name=job.name,
                    details=f"Type: {job.job_type}, Destination: {job.destination}",
                )
            else:
                notifier.notify_failure(
                    job_name=job.name,
                    error="Backup execution returned failure",
                )

            return result

        except Exception as exc:
            logger.exception("Job execution failed: %s", exc)
            notifier.notify_failure(job_name=job.name, error=str(exc))
            return False

    def stop(self) -> None:
        """Stop the scheduler polling loop."""
        logger.info("Stopping scheduler...")
        self._running = False

    def get_status(self) -> dict[str, Any]:
        """Get the current scheduler status.

        Returns:
            Dictionary with scheduler status information.
        """
        with self._lock:
            jobs = []
            for scheduled in self._scheduled_jobs:
                jobs.append(
                    {
                        "name": scheduled.config.name,
                        "enabled": scheduled.config.enabled,
                        "schedule": scheduled.config.schedule,
                        "last_run": (
                            scheduled.last_run.isoformat() if scheduled.last_run else None
                        ),
                        "next_run": (
                            scheduled.next_run.isoformat() if scheduled.next_run else None
                        ),
                        "run_count": scheduled.run_count,
                        "failure_count": scheduled.failure_count,
                    }
                )

        return {
            "running": self._running,
            "poll_interval": self.poll_interval,
            "total_jobs": len(self._scheduled_jobs),
            "jobs": jobs,
        }

    def add_job(self, job_config: BackupJobConfig) -> None:
        """Add a new job to the scheduler at runtime.

        Args:
            job_config: Backup job configuration to add.
        """
        with self._lock:
            scheduled = ScheduledJob(config=job_config)
            scheduled.update_schedule()
            self._scheduled_jobs.append(scheduled)
            logger.info("Added job '%s' to scheduler", job_config.name)

    def remove_job(self, job_name: str) -> bool:
        """Remove a job from the scheduler.

        Args:
            job_name: Name of the job to remove.

        Returns:
            True if the job was found and removed, False otherwise.
        """
        with self._lock:
            for i, scheduled in enumerate(self._scheduled_jobs):
                if scheduled.config.name == job_name:
                    self._scheduled_jobs.pop(i)
                    logger.info("Removed job '%s' from scheduler", job_name)
                    return True
        logger.warning("Job '%s' not found in scheduler", job_name)
        return False
