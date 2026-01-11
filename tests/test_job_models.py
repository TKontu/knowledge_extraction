"""Tests for job-related Pydantic models."""

import pytest
from pydantic import ValidationError

from models import JobSummary, JobListResponse, JobDetailResponse


class TestJobSummary:
    """Tests for JobSummary model."""

    def test_job_summary_serialization(self) -> None:
        """Test that JobSummary serializes correctly."""
        summary = JobSummary(
            id="123e4567-e89b-12d3-a456-426614174000",
            type="scrape",
            status="completed",
            created_at="2026-01-11T10:00:00Z",
            started_at="2026-01-11T10:01:00Z",
            completed_at="2026-01-11T10:05:00Z",
            error=None,
        )

        assert summary.id == "123e4567-e89b-12d3-a456-426614174000"
        assert summary.type == "scrape"
        assert summary.status == "completed"
        assert summary.created_at == "2026-01-11T10:00:00Z"
        assert summary.started_at == "2026-01-11T10:01:00Z"
        assert summary.completed_at == "2026-01-11T10:05:00Z"
        assert summary.error is None

    def test_job_summary_with_error(self) -> None:
        """Test JobSummary with error message."""
        summary = JobSummary(
            id="123e4567-e89b-12d3-a456-426614174000",
            type="extract",
            status="failed",
            created_at="2026-01-11T10:00:00Z",
            error="Connection timeout",
        )

        assert summary.status == "failed"
        assert summary.error == "Connection timeout"
        assert summary.started_at is None
        assert summary.completed_at is None


class TestJobListResponse:
    """Tests for JobListResponse model."""

    def test_job_list_response_pagination(self) -> None:
        """Test that JobListResponse includes pagination metadata."""
        jobs = [
            JobSummary(
                id=f"id-{i}",
                type="scrape",
                status="completed",
                created_at="2026-01-11T10:00:00Z",
            )
            for i in range(3)
        ]

        response = JobListResponse(
            jobs=jobs,
            total=100,
            limit=3,
            offset=0,
        )

        assert len(response.jobs) == 3
        assert response.total == 100
        assert response.limit == 3
        assert response.offset == 0

    def test_job_list_response_empty(self) -> None:
        """Test JobListResponse with no jobs."""
        response = JobListResponse(
            jobs=[],
            total=0,
            limit=50,
            offset=0,
        )

        assert response.jobs == []
        assert response.total == 0


class TestJobDetailResponse:
    """Tests for JobDetailResponse model."""

    def test_job_detail_response_includes_payload(self) -> None:
        """Test that JobDetailResponse includes full payload."""
        detail = JobDetailResponse(
            id="123e4567-e89b-12d3-a456-426614174000",
            type="scrape",
            status="completed",
            payload={"urls": ["https://example.com"], "company": "Example Corp"},
            result={"pages_scraped": 1, "success": True},
            error=None,
            created_at="2026-01-11T10:00:00Z",
            started_at="2026-01-11T10:01:00Z",
            completed_at="2026-01-11T10:05:00Z",
        )

        assert detail.payload == {"urls": ["https://example.com"], "company": "Example Corp"}
        assert detail.result == {"pages_scraped": 1, "success": True}
        assert detail.error is None

    def test_job_detail_response_with_error(self) -> None:
        """Test JobDetailResponse with error and no result."""
        detail = JobDetailResponse(
            id="123e4567-e89b-12d3-a456-426614174000",
            type="extract",
            status="failed",
            payload={"source_ids": ["abc-123"]},
            result=None,
            error="Database connection failed",
            created_at="2026-01-11T10:00:00Z",
            started_at="2026-01-11T10:01:00Z",
            completed_at=None,
        )

        assert detail.status == "failed"
        assert detail.error == "Database connection failed"
        assert detail.result is None
        assert detail.completed_at is None
