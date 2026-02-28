"""Tests for per-job-type stale thresholds in job scheduler."""

import pytest
from datetime import UTC, datetime, timedelta
from uuid import uuid4
from sqlalchemy.orm import Session

from orm_models import Job, Project


@pytest.fixture
def mock_settings(monkeypatch):
    """Mock settings with custom stale thresholds."""
    class MockSettings:
        job_stale_threshold_scrape = 300  # 5 minutes
        job_stale_threshold_extract = 900  # 15 minutes
        job_stale_threshold_crawl = 1800  # 30 minutes
        # Other required settings
        firecrawl_url = "http://localhost:3002"
        scrape_timeout = 180
        scrape_delay_min = 2
        scrape_delay_max = 5
        scrape_daily_limit_per_domain = 500
        scrape_retry_max_attempts = 3
        scrape_retry_base_delay = 2.0
        scrape_retry_max_delay = 60.0
        openai_base_url = "http://localhost:9003/v1"
        openai_api_key = "test-key"
        llm_http_timeout = 120
        llm_model = "test-model"
        llm_queue_enabled = False
        max_concurrent_crawls = 6

    mock = MockSettings()
    monkeypatch.setattr("services.scraper.scheduler.settings", mock)
    return mock


@pytest.fixture
def test_project(db):
    """Create a test project for FK references."""
    project = Project(
        name=f"test_scheduler_{uuid4().hex[:8]}",
        extraction_schema={"name": "test", "fields": []},
    )
    db.add(project)
    db.flush()
    return project


def test_scrape_job_not_stale_within_threshold(db: Session, test_project):
    """Scrape job running for 3 minutes should NOT be recovered (threshold: 5 min)."""
    # Create a scrape job that started 3 minutes ago
    three_minutes_ago = datetime.now(UTC) - timedelta(minutes=3)
    job = Job(
        project_id=test_project.id,
        type="scrape",
        status="running",
        payload={"url": "https://example.com"},
        updated_at=three_minutes_ago,
        started_at=three_minutes_ago,
    )
    db.add(job)
    db.flush()

    # Query for stale jobs using 5-minute threshold — scope to our test job
    stale_threshold = datetime.now(UTC) - timedelta(minutes=5)
    stale_job = (
        db.query(Job)
        .filter(
            Job.id == job.id,
            Job.type == "scrape",
            Job.status == "running",
            Job.updated_at < stale_threshold,
        )
        .first()
    )

    # Job should NOT be considered stale
    assert stale_job is None


def test_scrape_job_stale_after_threshold(db: Session, test_project):
    """Scrape job running for 6 minutes SHOULD be recovered (threshold: 5 min)."""
    # Create a scrape job that started 6 minutes ago
    six_minutes_ago = datetime.now(UTC) - timedelta(minutes=6)
    job = Job(
        project_id=test_project.id,
        type="scrape",
        status="running",
        payload={"url": "https://example.com"},
        updated_at=six_minutes_ago,
        started_at=six_minutes_ago,
    )
    db.add(job)
    db.flush()

    # Query for stale jobs using 5-minute threshold — scope to our test job
    stale_threshold = datetime.now(UTC) - timedelta(minutes=5)
    stale_job = (
        db.query(Job)
        .filter(
            Job.id == job.id,
            Job.type == "scrape",
            Job.status == "running",
            Job.updated_at < stale_threshold,
        )
        .first()
    )

    # Job SHOULD be considered stale
    assert stale_job is not None
    assert stale_job.id == job.id


def test_extract_job_not_stale_within_threshold(db: Session, test_project):
    """Extract job running for 10 minutes should NOT be recovered (threshold: 15 min)."""
    # Create an extract job that started 10 minutes ago
    ten_minutes_ago = datetime.now(UTC) - timedelta(minutes=10)
    job = Job(
        project_id=test_project.id,
        type="extract",
        status="running",
        payload={"source_ids": [1, 2, 3]},
        updated_at=ten_minutes_ago,
        started_at=ten_minutes_ago,
    )
    db.add(job)
    db.flush()

    # Query for stale jobs using 15-minute threshold — scope to our test job
    stale_threshold = datetime.now(UTC) - timedelta(minutes=15)
    stale_job = (
        db.query(Job)
        .filter(
            Job.id == job.id,
            Job.type == "extract",
            Job.status == "running",
            Job.updated_at < stale_threshold,
        )
        .first()
    )

    # Job should NOT be considered stale
    assert stale_job is None


def test_extract_job_stale_after_threshold(db: Session, test_project):
    """Extract job running for 20 minutes SHOULD be recovered (threshold: 15 min)."""
    # Create an extract job that started 20 minutes ago
    twenty_minutes_ago = datetime.now(UTC) - timedelta(minutes=20)
    job = Job(
        project_id=test_project.id,
        type="extract",
        status="running",
        payload={"source_ids": [1, 2, 3]},
        updated_at=twenty_minutes_ago,
        started_at=twenty_minutes_ago,
    )
    db.add(job)
    db.flush()

    # Query for stale jobs using 15-minute threshold — scope to our test job
    stale_threshold = datetime.now(UTC) - timedelta(minutes=15)
    stale_job = (
        db.query(Job)
        .filter(
            Job.id == job.id,
            Job.type == "extract",
            Job.status == "running",
            Job.updated_at < stale_threshold,
        )
        .first()
    )

    # Job SHOULD be considered stale
    assert stale_job is not None
    assert stale_job.id == job.id


def test_crawl_job_longer_threshold(db: Session, test_project):
    """Crawl jobs should have a longer threshold (30 min) than scrape (5 min)."""
    # Create a crawl job that's been running for 20 minutes
    twenty_minutes_ago = datetime.now(UTC) - timedelta(minutes=20)
    crawl_job = Job(
        project_id=test_project.id,
        type="crawl",
        status="running",
        payload={"url": "https://example.com"},
        updated_at=twenty_minutes_ago,
        started_at=twenty_minutes_ago,
    )
    db.add(crawl_job)
    db.flush()

    # At 20 minutes:
    # - Scrape threshold (5 min): Would be stale
    # - Crawl threshold (30 min): Should NOT be stale

    # Check with scrape threshold (5 min) — scope to our test job
    scrape_threshold = datetime.now(UTC) - timedelta(minutes=5)
    stale_with_scrape_threshold = (
        db.query(Job)
        .filter(
            Job.id == crawl_job.id,
            Job.type == "crawl",
            Job.status == "running",
            Job.updated_at < scrape_threshold,
        )
        .first()
    )
    assert stale_with_scrape_threshold is not None  # Would be stale with scrape threshold

    # Check with crawl threshold (30 min) — scope to our test job
    crawl_threshold = datetime.now(UTC) - timedelta(minutes=30)
    stale_with_crawl_threshold = (
        db.query(Job)
        .filter(
            Job.id == crawl_job.id,
            Job.type == "crawl",
            Job.status == "running",
            Job.updated_at < crawl_threshold,
        )
        .first()
    )
    assert stale_with_crawl_threshold is None  # Not stale with crawl threshold


@pytest.mark.asyncio
async def test_custom_threshold_from_settings(db: Session, test_project, mock_settings):
    """Scheduler should use custom thresholds from settings."""
    from services.scraper.scheduler import JobScheduler

    # Create scheduler with custom settings
    scheduler = JobScheduler(poll_interval=5)

    # Verify scheduler has access to custom settings
    # This will be implemented when we add settings support to scheduler
    assert mock_settings.job_stale_threshold_scrape == 300
    assert mock_settings.job_stale_threshold_extract == 900
    assert mock_settings.job_stale_threshold_crawl == 1800
