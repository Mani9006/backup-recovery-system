"""Notification system for backup events.

Supports webhook notifications (HTTP POST) and email notifications
(SMTP). Can send notifications on backup success and/or failure.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Optional

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None  # type: ignore[assignment]

try:
    import jinja2
except ImportError:  # pragma: no cover
    jinja2 = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """Raised when a notification cannot be sent."""

    pass


class NotificationChannel(Enum):
    """Supported notification channels."""

    WEBHOOK = "webhook"
    EMAIL = "email"


@dataclass
class NotificationPayload:
    """Standard notification payload structure."""

    event: str  # backup.success, backup.failure
    job_name: str
    timestamp: str
    details: str
    error: Optional[str] = None
    hostname: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "event": self.event,
            "job_name": self.job_name,
            "timestamp": self.timestamp,
            "details": self.details,
            "error": self.error,
            "hostname": self.hostname,
        }

    def to_markdown(self) -> str:
        """Format notification as Markdown for webhook display."""
        emoji = "✅" if "success" in self.event else "❌"
        lines = [
            f"## {emoji} Backup {self.event.split('.')[-1].title()}",
            "",
            f"**Job:** `{self.job_name}`",
            f"**Time:** {self.timestamp}",
            f"**Details:** {self.details}",
        ]
        if self.error:
            lines.append(f"**Error:** `{self.error}`")
        if self.hostname:
            lines.append(f"**Host:** `{self.hostname}`")
        return "\n".join(lines)

    def to_plaintext(self) -> str:
        """Format notification as plain text for email."""
        status = "SUCCESS" if "success" in self.event else "FAILURE"
        lines = [
            f"Backup {status}: {self.job_name}",
            f"Timestamp: {self.timestamp}",
            f"Details: {self.details}",
        ]
        if self.error:
            lines.append(f"Error: {self.error}")
        return "\n".join(lines)


class NotificationManager:
    """Manages notifications for backup events.

    Supports webhook (HTTP POST) and email (SMTP) notifications
    with rich templates and error handling.
    """

    def __init__(
        self,
        config,
    ) -> None:
        """Initialize the notification manager.

        Args:
            config: Notification configuration object.
        """
        self.on_success = getattr(config, "on_success", False)
        self.on_failure = getattr(config, "on_failure", True)
        self.webhook_url = getattr(config, "webhook_url", None)
        self.email_to = getattr(config, "email_to", None)
        self.email_from = getattr(config, "email_from", None)
        self.smtp_host = getattr(config, "smtp_host", None)
        self.smtp_port = getattr(config, "smtp_port", 587)

        self._hostname = self._get_hostname()

    @staticmethod
    def _get_hostname() -> str:
        """Get the system hostname."""
        import socket

        try:
            return socket.gethostname()
        except Exception:
            return "unknown"

    def notify_success(self, job_name: str, details: str = "") -> None:
        """Send a success notification.

        Args:
            job_name: Name of the backup job.
            details: Additional details about the backup.
        """
        if not self.on_success:
            logger.debug("Success notifications are disabled")
            return

        payload = NotificationPayload(
            event="backup.success",
            job_name=job_name,
            timestamp=datetime.now().isoformat(),
            details=details,
            hostname=self._hostname,
        )

        self._send(payload)

    def notify_failure(self, job_name: str, error: str, details: str = "") -> None:
        """Send a failure notification.

        Args:
            job_name: Name of the backup job.
            error: Error message describing the failure.
            details: Additional details about the backup attempt.
        """
        if not self.on_failure:
            logger.debug("Failure notifications are disabled")
            return

        payload = NotificationPayload(
            event="backup.failure",
            job_name=job_name,
            timestamp=datetime.now().isoformat(),
            details=details,
            error=error,
            hostname=self._hostname,
        )

        self._send(payload)

    def _send(self, payload: NotificationPayload) -> None:
        """Send notification through all configured channels.

        Args:
            payload: Notification payload to send.
        """
        if self.webhook_url:
            try:
                self._send_webhook(payload)
            except Exception as exc:
                logger.error("Webhook notification failed: %s", exc)

        if self.email_to and self.smtp_host:
            try:
                self._send_email(payload)
            except Exception as exc:
                logger.error("Email notification failed: %s", exc)

    def _send_webhook(self, payload: NotificationPayload) -> None:
        """Send notification via webhook HTTP POST.

        Args:
            payload: Notification payload.
        """
        if requests is None:
            raise NotificationError(
                "The 'requests' library is required for webhook notifications. "
                "Install it with: pip install requests"
            )

        if not self.webhook_url:
            raise NotificationError("Webhook URL is not configured")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "BackupRecovery/1.0",
        }

        # Try multiple payload formats for compatibility
        formats_to_try = [
            payload.to_dict(),  # JSON format
            {
                "text": payload.to_markdown(),
                "markdown": payload.to_markdown(),
                **payload.to_dict(),
            },
        ]

        last_error = None
        for data in formats_to_try:
            try:
                response = requests.post(
                    self.webhook_url,
                    json=data,
                    headers=headers,
                    timeout=30,
                )

                if response.status_code < 400:
                    logger.info(
                        "Webhook notification sent: %s (HTTP %d)",
                        payload.event,
                        response.status_code,
                    )
                    return
                else:
                    last_error = f"HTTP {response.status_code}: {response.text}"

            except requests.RequestException as exc:
                last_error = str(exc)

        raise NotificationError(f"Webhook notification failed: {last_error}")

    def _send_email(self, payload: NotificationPayload) -> None:
        """Send notification via email using SMTP.

        Args:
            payload: Notification payload.
        """
        import smtplib
        from email.mime.text import MIMEText

        if not self.email_to:
            raise NotificationError("Email recipient is not configured")
        if not self.smtp_host:
            raise NotificationError("SMTP host is not configured")

        subject = f"[{payload.event.upper()}] Backup: {payload.job_name}"
        body = payload.to_plaintext()

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.email_from or "backup-recovery@localhost"
        msg["To"] = self.email_to

        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                server.starttls()
                server.send_message(msg)

            logger.info(
                "Email notification sent to %s: %s", self.email_to, subject
            )

        except Exception as exc:
            raise NotificationError(f"Failed to send email: {exc}") from exc

    def send_custom_webhook(
        self,
        url: str,
        data: dict[str, Any],
        headers: Optional[dict[str, str]] = None,
    ) -> bool:
        """Send a custom webhook notification.

        Args:
            url: Webhook URL.
            data: Custom payload data.
            headers: Optional additional headers.

        Returns:
            True if the webhook was sent successfully.
        """
        if requests is None:
            logger.error("The 'requests' library is required for webhooks")
            return False

        headers = headers or {}
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("User-Agent", "BackupRecovery/1.0")

        try:
            response = requests.post(url, json=data, headers=headers, timeout=30)
            return response.status_code < 400
        except requests.RequestException as exc:
            logger.error("Custom webhook failed: %s", exc)
            return False
