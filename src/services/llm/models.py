"""Data models for LLM request queue."""

import json
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import Any


# Valid request types
VALID_REQUEST_TYPES = frozenset({
    "extract_facts",
    "extract_field_group",
    "extract_entities",
})

# Valid response statuses
VALID_STATUSES = frozenset({
    "success",
    "error",
    "timeout",
})


class InvalidRequestTypeError(ValueError):
    """Raised when request type is invalid."""

    pass


class InvalidStatusError(ValueError):
    """Raised when response status is invalid."""

    pass


@dataclass
class LLMRequest:
    """Request message for LLM queue.

    Attributes:
        request_id: Unique identifier for correlation.
        request_type: Type of extraction (extract_facts, extract_field_group, extract_entities).
        payload: Type-specific payload data.
        priority: Request priority (0=low, 5=normal, 10=high).
        created_at: When request was created.
        timeout_at: When request should be considered expired.
        retry_count: Number of times this request has been retried.
    """

    request_id: str
    request_type: str
    payload: dict[str, Any]
    priority: int
    created_at: datetime
    timeout_at: datetime
    retry_count: int = 0

    def __post_init__(self):
        """Validate request after initialization."""
        if self.request_type not in VALID_REQUEST_TYPES:
            raise InvalidRequestTypeError(
                f"Invalid request type: {self.request_type}. "
                f"Must be one of: {', '.join(VALID_REQUEST_TYPES)}"
            )

    def is_expired(self) -> bool:
        """Check if request has expired.

        Returns:
            True if current time is past timeout_at.
        """
        return datetime.now(UTC) > self.timeout_at

    def to_json(self) -> str:
        """Serialize request to JSON string.

        Returns:
            JSON string representation.
        """
        return json.dumps({
            "request_id": self.request_id,
            "request_type": self.request_type,
            "payload": self.payload,
            "priority": self.priority,
            "created_at": self.created_at.isoformat(),
            "timeout_at": self.timeout_at.isoformat(),
            "retry_count": self.retry_count,
        })

    @classmethod
    def from_json(cls, json_str: str) -> "LLMRequest":
        """Deserialize request from JSON string.

        Args:
            json_str: JSON string representation.

        Returns:
            LLMRequest instance.
        """
        data = json.loads(json_str)
        return cls(
            request_id=data["request_id"],
            request_type=data["request_type"],
            payload=data["payload"],
            priority=data["priority"],
            created_at=datetime.fromisoformat(data["created_at"]),
            timeout_at=datetime.fromisoformat(data["timeout_at"]),
            retry_count=data.get("retry_count", 0),
        )


@dataclass
class LLMResponse:
    """Response message stored in Redis.

    Attributes:
        request_id: Correlation ID matching the request.
        status: Response status (success, error, timeout).
        result: Extracted data if successful.
        error: Error message if failed.
        processing_time_ms: Time taken to process in milliseconds.
        completed_at: When processing completed.
    """

    request_id: str
    status: str
    result: dict[str, Any] | None
    error: str | None
    processing_time_ms: int
    completed_at: datetime

    def __post_init__(self):
        """Validate response after initialization."""
        if self.status not in VALID_STATUSES:
            raise InvalidStatusError(
                f"Invalid status: {self.status}. "
                f"Must be one of: {', '.join(VALID_STATUSES)}"
            )

    def to_json(self) -> str:
        """Serialize response to JSON string.

        Returns:
            JSON string representation.
        """
        return json.dumps({
            "request_id": self.request_id,
            "status": self.status,
            "result": self.result,
            "error": self.error,
            "processing_time_ms": self.processing_time_ms,
            "completed_at": self.completed_at.isoformat(),
        })

    @classmethod
    def from_json(cls, json_str: str) -> "LLMResponse":
        """Deserialize response from JSON string.

        Args:
            json_str: JSON string representation.

        Returns:
            LLMResponse instance.
        """
        data = json.loads(json_str)
        return cls(
            request_id=data["request_id"],
            status=data["status"],
            result=data["result"],
            error=data["error"],
            processing_time_ms=data["processing_time_ms"],
            completed_at=datetime.fromisoformat(data["completed_at"]),
        )
