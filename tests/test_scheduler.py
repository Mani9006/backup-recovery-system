"""Tests for the scheduler module."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

try:
    from croniter import croniter
except ImportError:
    pytest.skip("croniter not installed", allow_module_level=True)

from src.scheduler import ScheduledJob, Scheduler, SchedulerError
from src.config import AppConfig, BackupJobConfig, StorageConfig, RetentionPolicy


class TestScheduledJob:
    """Tests for ScheduledJob dataclass."""

    def test_initial_state(self):
        """Test that a new job has the correct initial state."""
        job_config = BackupJobConfig(
            name="test",
            job_type="file",
            schedule="0 2 * * *",
        )
        scheduled = ScheduledJob(config=job_config)
        assert scheduled.last_run is None
        assert scheduled.next_run is None
        assert scheduled.run_count == 0
        assert scheduled.failure_count == 0

    def test_update_schedule(self):
        """Test that schedule updates correctly."""
        job_config = BackupJobConfig(
            name="test",
            job_type="file",
            schedule="0 2 * * *",  # 2 AM daily
        )
        scheduled = ScheduledJob(config=job_config)
        scheduled.update_schedule()

        assert scheduled.next_run is not None
        # Next run should be at 2 AM
        assert scheduled.next_run.hour == 2
        assert scheduled.next_run.minute == 0

    def test_update_schedule_custom(self):
        """Test schedule update with a different cron expression."""
        job_config = BackupJobConfig(
            name="test",
            job_type="file",
            schedule="*/5 * * * *",  # Every 5 minutes
        )
        scheduled = ScheduledJob(config=job_config)
        now = datetime.now()
        scheduled.update_schedule(base_time=now)

        assert scheduled.next_run is not None
        # Should be within 6 minutes
        diff = scheduled.next_run - now
        assert diff <= timedelta(minutes=6)


class TestScheduler:
    """Tests for the Scheduler class."""

    def _make_config(self, job_count: int = 1) -> AppConfig:
        """Create a test configuration with N jobs."""
        jobs = []
        for i in range(job_count):
            jobs.append(
                BackupJobConfig(
                    name=f"job_{i}",
                    job_type="file",
                    schedule="0 2 * * *",
                    source="./test",
                    destination=f"backup_{i}",
                    enabled=True,
                )
            )
        return AppConfig(jobs=jobs)

    def test_init(self):
        """Test scheduler initialization."""
        config = self._make_config(2)
        scheduler = Scheduler(config=config, poll_interval=30)

        assert scheduler.config == config
        assert scheduler.poll_interval == 30
        assert len(scheduler._scheduled_jobs) == 2
        assert scheduler._running is False

    def test_init_with_disabled_jobs(self):
        """Test that disabled jobs are not scheduled."""
        jobs = [
            BackupJobConfig(name="enabled", job_type="file", enabled=True),
            BackupJobConfig(name="disabled", job_type="file", enabled=False),
        ]
        config = AppConfig(jobs=jobs)
        scheduler = Scheduler(config=config)

        assert len(scheduler._scheduled_jobs) == 1
        assert scheduler._scheduled_jobs[0].config.name == "enabled"

    def test_add_job(self):
        """Test adding a job at runtime."""
        config = self._make_config(1)
        scheduler = Scheduler(config=config)
        assert len(scheduler._scheduled_jobs) == 1

        new_job = BackupJobConfig(
            name="added_job",
            job_type="file",
            schedule="0 3 * * *",
        )
        scheduler.add_job(new_job)

        assert len(scheduler._scheduled_jobs) == 2
        job_names = [j.config.name for j in scheduler._scheduled_jobs]
        assert "added_job" in job_names

    def test_remove_job(self):
        """Test removing a job at runtime."""
        config = self._make_config(2)
        scheduler = Scheduler(config=config)
        assert len(scheduler._scheduled_jobs) == 2

        result = scheduler.remove_job("job_0")
        assert result is True
        assert len(scheduler._scheduled_jobs) == 1

    def test_remove_nonexistent_job(self):
        """Test removing a job that doesn't exist."""
        config = self._make_config(1)
        scheduler = Scheduler(config=config)

        result = scheduler.remove_job("nonexistent")
        assert result is False

    def test_get_status(self):
        """Test getting scheduler status."""
        config = self._make_config(1)
        scheduler = Scheduler(config=config)

        status = scheduler.get_status()
        assert status["running"] is False
        assert status["total_jobs"] == 1
        assert "jobs" in status
        assert len(status["jobs"]) == 1
        assert status["jobs"][0]["name"] == "job_0"

    def test_start_stop(self):
        """Test starting and stopping the scheduler."""
        config = self._make_config(1)
        scheduler = Scheduler(config=config, poll_interval=1)

        # Mock _run_loop to avoid blocking and track running state
        running_states = []
        def mock_run_loop():
            while scheduler._running:
                running_states.append(scheduler._running)
                break  # Exit after one iteration

        with patch.object(scheduler, "_run_loop", mock_run_loop):
            scheduler.start()

        # _running is False after start() completes (finally block)
        assert scheduler._running is False
        # But it was True during execution
        assert True in running_states

    def test_execute_job(self):
        """Test job execution through scheduler."""
        config = self._make_config(1)
        mock_executor = MagicMock(return_value=True)
        scheduler = Scheduler(config=config, job_executor=mock_executor)

        scheduled = scheduler._scheduled_jobs[0]
        scheduler._execute_job(scheduled)

        assert scheduled.run_count == 1
        assert scheduled.failure_count == 0
        mock_executor.assert_called_once()

    def test_execute_job_failure(self):
        """Test job execution failure handling."""
        config = self._make_config(1)
        mock_executor = MagicMock(return_value=False)
        scheduler = Scheduler(config=config, job_executor=mock_executor)

        scheduled = scheduler._scheduled_jobs[0]
        scheduler._execute_job(scheduled)

        assert scheduled.run_count == 1
        assert scheduled.failure_count == 1

    def test_execute_job_exception(self):
        """Test job execution exception handling."""
        config = self._make_config(1)
        mock_executor = MagicMock(side_effect=RuntimeError("boom"))
        scheduler = Scheduler(config=config, job_executor=mock_executor)

        scheduled = scheduler._scheduled_jobs[0]
        scheduler._execute_job(scheduled)

        assert scheduled.run_count == 1
        assert scheduled.failure_count == 1

    def test_check_and_execute_jobs(self):
        """Test the check and execute loop."""
        config = self._make_config(1)
        mock_executor = MagicMock(return_value=True)
        scheduler = Scheduler(config=config, job_executor=mock_executor)

        # Force the job to be due
        scheduled = scheduler._scheduled_jobs[0]
        scheduled.next_run = datetime.now() - timedelta(minutes=1)

        scheduler._check_and_execute_jobs()

        mock_executor.assert_called_once()
        assert scheduled.run_count == 1
