"""Alert models and types."""

from datetime import datetime
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class AlertLevel(str, Enum):
    """Alert severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Types of alerts the system can generate."""

    # Data consistency alerts
    EMBEDDING_FAILURE = "embedding_failure"
    PARTIAL_EXTRACTION = "partial_extraction"
    ORPHANED_EXTRACTIONS = "orphaned_extractions"

    # Operational alerts
    JOB_FAILED = "job_failed"
    SERVICE_DEGRADED = "service_degraded"

    # Recovery alerts
    RECOVERY_STARTED = "recovery_started"
    RECOVERY_COMPLETED = "recovery_completed"


class Alert(BaseModel):
    """Alert payload for notifications."""

    type: AlertType
    level: AlertLevel
    title: str
    message: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Context
    project_id: UUID | None = None
    source_id: UUID | None = None
    job_id: UUID | None = None

    # Additional details
    details: dict[str, Any] = Field(default_factory=dict)

    def to_webhook_payload(self) -> dict[str, Any]:
        """Convert to webhook-friendly payload."""
        return {
            "type": self.type.value,
            "level": self.level.value,
            "title": self.title,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "project_id": str(self.project_id) if self.project_id else None,
            "source_id": str(self.source_id) if self.source_id else None,
            "job_id": str(self.job_id) if self.job_id else None,
            "details": self.details,
        }

    def to_slack_payload(self) -> dict[str, Any]:
        """Convert to Slack webhook format."""
        color_map = {
            AlertLevel.INFO: "#36a64f",
            AlertLevel.WARNING: "#ffcc00",
            AlertLevel.ERROR: "#ff6600",
            AlertLevel.CRITICAL: "#ff0000",
        }

        fields = []
        if self.project_id:
            fields.append({"title": "Project", "value": str(self.project_id), "short": True})
        if self.source_id:
            fields.append({"title": "Source", "value": str(self.source_id), "short": True})
        if self.job_id:
            fields.append({"title": "Job", "value": str(self.job_id), "short": True})

        for key, value in self.details.items():
            fields.append({"title": key, "value": str(value), "short": True})

        return {
            "attachments": [
                {
                    "color": color_map.get(self.level, "#808080"),
                    "title": f"[{self.level.value.upper()}] {self.title}",
                    "text": self.message,
                    "fields": fields,
                    "ts": int(self.timestamp.timestamp()),
                }
            ]
        }
