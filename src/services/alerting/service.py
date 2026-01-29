"""Alert service for sending operational notifications."""

from uuid import UUID

import httpx
import structlog

from services.alerting.models import Alert, AlertLevel, AlertType

logger = structlog.get_logger(__name__)


class AlertService:
    """Service for sending alerts via configured backends.

    Supports:
    - Webhook (generic JSON or Slack-formatted)
    - Logging (always enabled as fallback)
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        webhook_format: str = "json",
        enabled: bool = True,
    ) -> None:
        """Initialize alert service.

        Args:
            webhook_url: URL to POST alerts to. If None, only logs alerts.
            webhook_format: Format for webhook payload ('json' or 'slack').
            enabled: Master switch for alerting.
        """
        self._webhook_url = webhook_url
        self._webhook_format = webhook_format
        self._enabled = enabled
        self._client: httpx.AsyncClient | None = None

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

    async def _send_webhook(self, alert: Alert) -> bool:
        """Send alert to webhook."""
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
                    "recovery_action": "POST /projects/{project_id}/extractions/recover",
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
                    "recovery_action": "POST /projects/{project_id}/extractions/recover",
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
        project_id: UUID,
        recovered: int,
        failed: int,
    ) -> bool:
        """Alert when recovery completes."""
        level = AlertLevel.INFO if failed == 0 else AlertLevel.WARNING
        return await self.send(
            Alert(
                type=AlertType.RECOVERY_COMPLETED,
                level=level,
                title="Extraction Recovery Completed",
                message=(
                    f"Recovery finished: {recovered} extractions recovered, {failed} failed."
                ),
                project_id=project_id,
                details={
                    "recovered": recovered,
                    "failed": failed,
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
