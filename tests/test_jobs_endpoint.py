"""Tests for jobs API endpoints."""

import pytest
from datetime import datetime, UTC, timedelta
from uuid import uuid4
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from orm_models import Job, Project


@pytest.fixture
def auth_headers(valid_api_key):
    """Return authentication headers with valid API key."""
    return {"X-API-Key": valid_api_key}


@pytest.fixture
def test_project(db: Session) -> Project:
    """Create a test project for FK references."""
    project = Project(
        name=f"test_jobs_project_{uuid4().hex[:8]}",
        extraction_schema={"name": "test", "fields": []},
    )
    db.add(project)
    db.flush()
    return project


@pytest.fixture
def sample_jobs(db: Session, test_project: Project) -> list[Job]:
    """Create sample jobs for testing."""
    now = datetime.now(UTC)
    jobs = [
        Job(
            id=uuid4(),
            project_id=test_project.id,
            type="scrape",
            status="completed",
            payload={"urls": ["https://example.com"], "company": "Example Corp"},
            result={"pages": 1},
            created_at=now - timedelta(days=2),
            started_at=now - timedelta(days=2, hours=23),
            completed_at=now - timedelta(days=2, hours=22),
        ),
        Job(
            id=uuid4(),
            project_id=test_project.id,
            type="extract",
            status="completed",
            payload={"source_ids": ["abc-123"]},
            result={"extractions": 5},
            created_at=now - timedelta(days=1),
            started_at=now - timedelta(days=1, hours=23),
            completed_at=now - timedelta(days=1, hours=22),
        ),
        Job(
            id=uuid4(),
            project_id=test_project.id,
            type="scrape",
            status="failed",
            payload={"urls": ["https://bad.com"]},
            error="Connection timeout",
            created_at=now - timedelta(hours=12),
            started_at=now - timedelta(hours=12),
        ),
        Job(
            id=uuid4(),
            project_id=test_project.id,
            type="scrape",
            status="running",
            payload={"urls": ["https://test.com"]},
            created_at=now - timedelta(hours=1),
            started_at=now - timedelta(hours=1),
        ),
        Job(
            id=uuid4(),
            project_id=test_project.id,
            type="extract",
            status="queued",
            payload={"source_ids": ["xyz-789"]},
            created_at=now,
        ),
    ]

    for job in jobs:
        db.add(job)
    db.flush()

    for job in jobs:
        db.refresh(job)

    return jobs


class TestListJobs:
    """Tests for GET /api/v1/jobs endpoint."""

    def test_list_jobs_returns_all(self, client: TestClient, auth_headers, sample_jobs: list[Job]) -> None:
        """Test that listing jobs returns results with correct structure."""
        response = client.get("/api/v1/jobs?limit=100", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Shared DB has many jobs; just verify structure and minimum count
        assert data["total"] >= 5
        assert isinstance(data["jobs"], list)
        assert len(data["jobs"]) > 0
        # Verify job structure
        first_job = data["jobs"][0]
        assert "id" in first_job
        assert "type" in first_job
        assert "status" in first_job
        assert "created_at" in first_job

    def test_list_jobs_filter_by_type(self, client: TestClient, auth_headers, sample_jobs: list[Job]) -> None:
        """Test filtering jobs by type."""
        response = client.get("/api/v1/jobs?type=scrape", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["total"] >= 3  # At least our 3 scrape jobs
        assert all(job["type"] == "scrape" for job in data["jobs"])

    def test_list_jobs_filter_by_status(self, client: TestClient, auth_headers, sample_jobs: list[Job]) -> None:
        """Test filtering jobs by status."""
        response = client.get("/api/v1/jobs?status=completed", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["total"] >= 2  # At least our 2 completed jobs
        assert all(job["status"] == "completed" for job in data["jobs"])

    def test_list_jobs_filter_by_date_range(self, client: TestClient, auth_headers, sample_jobs: list[Job]) -> None:
        """Test filtering jobs by date range."""
        now = datetime.now(UTC)
        # Use naive ISO format (without timezone) to avoid URL encoding issues
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")

        response = client.get(f"/api/v1/jobs?created_after={yesterday}", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        # Should return jobs created in last 24 hours (at least our fixture jobs)
        assert data["total"] >= 3

    def test_list_jobs_pagination(self, client: TestClient, auth_headers, sample_jobs: list[Job]) -> None:
        """Test pagination works correctly."""
        # First page
        response = client.get("/api/v1/jobs?limit=2&offset=0", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert len(data["jobs"]) == 2
        assert data["total"] >= 5
        assert data["limit"] == 2
        assert data["offset"] == 0

        # Second page
        response = client.get("/api/v1/jobs?limit=2&offset=2", headers=auth_headers)
        assert response.status_code == 200
        data = response.json()

        assert len(data["jobs"]) == 2
        assert data["offset"] == 2

    def test_list_jobs_sorted_newest_first(self, client: TestClient, auth_headers, sample_jobs: list[Job]) -> None:
        """Test that jobs are sorted by created_at descending (newest first)."""
        response = client.get("/api/v1/jobs", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        jobs = data["jobs"]
        assert len(jobs) > 1

        # Verify jobs are sorted newest first
        for i in range(len(jobs) - 1):
            # Parse ISO timestamps and compare
            current_time = datetime.fromisoformat(jobs[i]["created_at"].replace("Z", "+00:00"))
            next_time = datetime.fromisoformat(jobs[i + 1]["created_at"].replace("Z", "+00:00"))
            assert current_time >= next_time


class TestGetJob:
    """Tests for GET /api/v1/jobs/{job_id} endpoint."""

    def test_get_job_returns_details(self, client: TestClient, auth_headers, sample_jobs: list[Job]) -> None:
        """Test that getting a job returns full details."""
        job = sample_jobs[0]
        response = client.get(f"/api/v1/jobs/{job.id}", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()

        assert data["id"] == str(job.id)
        assert data["type"] == job.type
        assert data["status"] == job.status
        assert "payload" in data
        assert data["payload"] == job.payload

    def test_get_job_not_found(self, client: TestClient, auth_headers) -> None:
        """Test that getting a non-existent job returns 404."""
        fake_id = str(uuid4())
        response = client.get(f"/api/v1/jobs/{fake_id}", headers=auth_headers)

        assert response.status_code == 404

    def test_get_job_invalid_uuid(self, client: TestClient, auth_headers) -> None:
        """Test that invalid UUID returns 422."""
        response = client.get("/api/v1/jobs/not-a-uuid", headers=auth_headers)

        assert response.status_code == 422
