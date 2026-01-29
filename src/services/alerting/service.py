"""Alert service for sending operational notifications."""

import time
from uuid import UUID

import httpx
import structlog

from services.alerting.models import Alert, AlertLevel, AlertType

logger = structlog.get_logger(__name__)

# Default throttle window: 5 minutes per alert type + project combination
DEFAULT_THROTTLE_SECONDS = 300


class AlertService:
    """Service for sending alerts via configured backends.

    Supports:
    - Webhook (generic JSON or Slack-formatted)
    - Logging (always enabled as fallback)
    - Throttling to prevent alert storms
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        webhook_format: str = "json",
        enabled: bool = True,
        throttle_seconds: int = DEFAULT_THROTTLE_SECONDS,
    ) -> None:
        """Initialize alert service.

        Args:
            webhook_url: URL to POST alerts to. If None, only logs alerts.
            webhook_format: Format for webhook payload ('json' or 'slack').
            enabled: Master switch for alerting.
            throttle_seconds: Minimum seconds between same alert type+project webhooks.
        """
        self._webhook_url = webhook_url
        self._webhook_format = webhook_format
        self._enabled = enabled
        self._throttle_seconds = throttle_seconds
        self._client: httpx.AsyncClient | None = None
        # Track last webhook send time per (alert_type, project_id) tuple
        self._last_webhook_times: dict[tuple[str, str | None], float] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=10.0)
        return self._client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

    async def send(self, alert: Alert) -> bool:
        """Send an alert.

        Args:
            alert: Alert to send.

        Returns:
            True if alert was sent successfully (or logging-only mode).
        """
        if not self._enabled:
            return True

        # Always log the alert
        log_method = {
            AlertLevel.INFO: logger.info,
            AlertLevel.WARNING: logger.warning,
            AlertLevel.ERROR: logger.error,
            AlertLevel.CRITICAL: logger.critical,
        }.get(alert.level, logger.info)

        log_method(
            "alert_triggered",
            alert_type=alert.type.value,
            title=alert.title,
            message=alert.message,
            project_id=str(alert.project_id) if alert.project_id else None,
            source_id=str(alert.source_id) if alert.source_id else None,
            **alert.details,
        )

        # Send to webhook if configured
        if self._webhook_url:
            return await self._send_webhook(alert)

        return True

    def _is_throttled(self, alert: Alert) -> bool:
        """Check if alert should be throttled.

        Args:
            alert: Alert to check.

        Returns:
            True if alert should be skipped due to throttling.
        """
        if self._throttle_seconds <= 0:
            return False

        key = (alert.type.value, str(alert.project_id) if alert.project_id else None)
        now = time.monotonic()
        last_time = self._last_webhook_times.get(key)

        if last_time is not None:
            elapsed = now - last_time
            if elapsed < self._throttle_seconds:
                logger.debug(
                    "alert_throttled",
                    alert_type=alert.type.value,
                    project_id=str(alert.project_id) if alert.project_id else None,
                    seconds_until_next=int(self._throttle_seconds - elapsed),
                )
                return True

        return False

    def _record_webhook_sent(self, alert: Alert) -> None:
        """Record that a webhook was sent for throttling purposes."""
        key = (alert.type.value, str(alert.project_id) if alert.project_id else None)
        self._last_webhook_times[key] = time.monotonic()

    async def _send_webhook(self, alert: Alert) -> bool:
        """Send alert to webhook with throttling."""
        # Check throttling before sending
        if self._is_throttled(alert):
            return True  # Return True since alert was logged, just webhook skipped

        try:
            client = await self._get_client()

            if self._webhook_format == "slack":
                payload = alert.to_slack_payload()
            else:
                payload = alert.to_webhook_payload()

            response = await client.post(
                self._webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )

            if response.status_code >= 400:
                logger.error(
                    "webhook_delivery_failed",
                    status_code=response.status_code,
                    response=response.text[:200],
                    alert_type=alert.type.value,
                )
                return False

            # Record successful send for throttling
            self._record_webhook_sent(alert)

            logger.debug(
                "webhook_delivered",
                alert_type=alert.type.value,
                status_code=response.status_code,
            )
            return True

        except httpx.RequestError as e:
            logger.error(
                "webhook_request_error",
                error=str(e),
                alert_type=alert.type.value,
            )
            return False

    # Convenience methods for common alerts

    async def alert_embedding_failure(
        self,
        project_id: UUID,
        source_id: UUID,
        extractions_affected: int,
        error: str,
    ) -> bool:
        """Alert when embeddings fail after extraction succeeds."""
        return await self.send(
            Alert(
                type=AlertType.EMBEDDING_FAILURE,
                level=AlertLevel.ERROR,
                title="Embedding Generation Failed",
                message=(
                    f"Failed to generate embeddings for {extractions_affected} extractions. "
                    f"Data saved to PostgreSQL but not searchable in Qdrant."
                ),
                project_id=project_id,
                source_id=source_id,
                details={
                    "extractions_affected": extractions_affected,
                    "error": error[:500],  # Truncate long errors
                    "recovery_action": f"POST /projects/{project_id}/extractions/recover",
                },
            )
        )

    async def alert_orphaned_extractions(
        self,
        project_id: UUID,
        orphan_count: int,
    ) -> bool:
        """Alert when orphaned extractions are detected."""
        return await self.send(
            Alert(
                type=AlertType.ORPHANED_EXTRACTIONS,
                level=AlertLevel.WARNING,
                title="Orphaned Extractions Detected",
                message=(
                    f"Found {orphan_count} extractions without embeddings. "
                    f"These are not searchable until recovered."
                ),
                project_id=project_id,
                details={
                    "orphan_count": orphan_count,
                    "recovery_action": f"POST /projects/{project_id}/extractions/recover",
                },
            )
        )

    async def alert_job_failed(
        self,
        job_id: UUID,
        job_type: str,
        error: str,
        project_id: UUID | None = None,
    ) -> bool:
        """Alert when a job fails."""
        return await self.send(
            Alert(
                type=AlertType.JOB_FAILED,
                level=AlertLevel.ERROR,
                title=f"{job_type.title()} Job Failed",
                message=f"Job {job_id} failed: {error[:200]}",
                project_id=project_id,
                job_id=job_id,
                details={
                    "job_type": job_type,
                    "error": error[:500],
                },
            )
        )

    async def alert_recovery_completed(
        self,
        recovered: int,
        failed: int,
        project_id: UUID | None = None,
    ) -> bool:
        """Alert when recovery completes.

        Args:
            recovered: Number of extractions successfully recovered.
            failed: Number of extractions that failed recovery.
            project_id: Optional project UUID. None indicates global recovery.
        """
        level = AlertLevel.INFO if failed == 0 else AlertLevel.WARNING
        scope = f"project {project_id}" if project_id else "all projects"
        return await self.send(
            Alert(
                type=AlertType.RECOVERY_COMPLETED,
                level=level,
                title="Extraction Recovery Completed",
                message=(
                    f"Recovery finished for {scope}: "
                    f"{recovered} extractions recovered, {failed} failed."
                ),
                project_id=project_id,
                details={
                    "recovered": recovered,
                    "failed": failed,
                    "scope": "project" if project_id else "global",
                },
            )
        )


# Singleton instance (lazily initialized)
_alert_service: AlertService | None = None


def get_alert_service() -> AlertService:
    """Get the global alert service instance.

    Lazily initializes from settings on first call.
    """
    global _alert_service
    if _alert_service is None:
        from config import settings

        # Check for alerting configuration
        webhook_url = getattr(settings, "alert_webhook_url", None)
        webhook_format = getattr(settings, "alert_webhook_format", "json")
        enabled = getattr(settings, "alerting_enabled", True)

        _alert_service = AlertService(
            webhook_url=webhook_url,
            webhook_format=webhook_format,
            enabled=enabled,
        )
    return _alert_service


def reset_alert_service() -> None:
    """Reset alert service singleton (for testing)."""
    global _alert_service
    _alert_service = None


async def close_alert_service() -> None:
    """Close the alert service and release resources.

    Call this during application shutdown to properly close HTTP connections.
    """
    global _alert_service
    if _alert_service is not None:
        await _alert_service.close()
        _alert_service = None
        logger.debug("alert_service_closed")
