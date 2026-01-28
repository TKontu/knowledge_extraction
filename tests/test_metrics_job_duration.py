"""Tests for metrics collector job duration calculations."""

from datetime import datetime, timedelta, timezone

import pytest

from src.orm_models import Job
from src.services.metrics.collector import MetricsCollector


@pytest.fixture
def completed_jobs(db):
    """Create completed jobs with timestamps."""
    jobs = []
    base_time = datetime.now(timezone.utc)

    # Job 1: 10 seconds duration
    job1 = Job(
        type="scrape",
        status="completed",
        started_at=base_time,
        completed_at=base_time + timedelta(seconds=10),
    )
    jobs.append(job1)

    # Job 2: 20 seconds duration
    job2 = Job(
        type="scrape",
        status="completed",
        started_at=base_time,
        completed_at=base_time + timedelta(seconds=20),
    )
    jobs.append(job2)

    db.add_all(jobs)
    db.commit()
    return jobs


@pytest.fixture
def mixed_jobs(db):
    """Create jobs of different types."""
    jobs = []
    base_time = datetime.now(timezone.utc)

    # Scrape job: 15 seconds
    scrape_job = Job(
        type="scrape",
        status="completed",
        started_at=base_time,
        completed_at=base_time + timedelta(seconds=15),
    )
    jobs.append(scrape_job)

    # Extract job: 30 seconds
    extract_job = Job(
        type="extract",
        status="completed",
        started_at=base_time,
        completed_at=base_time + timedelta(seconds=30),
    )
    jobs.append(extract_job)

    # Crawl job: 60 seconds
    crawl_job = Job(
        type="crawl",
        status="completed",
        started_at=base_time,
        completed_at=base_time + timedelta(seconds=60),
    )
    jobs.append(crawl_job)

    db.add_all(jobs)
    db.commit()
    return jobs


class TestJobDurationMetrics:
    """Tests for _job_duration_by_type method."""

    def test_calculates_avg_duration(self, db, completed_jobs):
        """Average duration calculated correctly."""
        collector = MetricsCollector(db)
        stats = collector._job_duration_by_type()

        # Should have stats for scrape type
        assert "scrape" in stats
        scrape_stats = stats["scrape"]

        # Average: (10 + 20) / 2 = 15
        assert scrape_stats.avg_seconds == pytest.approx(15.0)
        assert scrape_stats.min_seconds == pytest.approx(10.0)
        assert scrape_stats.max_seconds == pytest.approx(20.0)
        assert scrape_stats.count == 2

    def test_handles_no_completed_jobs(self, db):
        """Returns empty dict when no completed jobs."""
        collector = MetricsCollector(db)
        stats = collector._job_duration_by_type()

        # Should return empty dict
        assert stats == {}

    def test_groups_by_job_type(self, db, mixed_jobs):
        """Separate stats for each job type."""
        collector = MetricsCollector(db)
        stats = collector._job_duration_by_type()

        # Should have stats for all three types
        assert "scrape" in stats
        assert "extract" in stats
        assert "crawl" in stats

        # Check individual stats
        assert stats["scrape"].avg_seconds == pytest.approx(15.0)
        assert stats["extract"].avg_seconds == pytest.approx(30.0)
        assert stats["crawl"].avg_seconds == pytest.approx(60.0)

        # Each should have count of 1
        assert stats["scrape"].count == 1
        assert stats["extract"].count == 1
        assert stats["crawl"].count == 1

    def test_excludes_jobs_without_timestamps(self, db):
        """Jobs with NULL started_at/completed_at excluded."""
        base_time = datetime.now(timezone.utc)

        # Job without started_at
        job1 = Job(
            type="scrape",
            status="completed",
            started_at=None,
            completed_at=base_time,
        )

        # Job without completed_at
        job2 = Job(
            type="scrape",
            status="completed",
            started_at=base_time,
            completed_at=None,
        )

        # Valid job
        job3 = Job(
            type="scrape",
            status="completed",
            started_at=base_time,
            completed_at=base_time + timedelta(seconds=10),
        )

        db.add_all([job1, job2, job3])
        db.commit()

        collector = MetricsCollector(db)
        stats = collector._job_duration_by_type()

        # Should only include the valid job
        assert "scrape" in stats
        assert stats["scrape"].count == 1
        assert stats["scrape"].avg_seconds == pytest.approx(10.0)

    def test_excludes_non_completed_jobs(self, db):
        """Only completed jobs are included in stats."""
        base_time = datetime.now(timezone.utc)

        # Pending job
        job1 = Job(
            type="scrape",
            status="pending",
            started_at=base_time,
            completed_at=base_time + timedelta(seconds=10),
        )

        # Running job
        job2 = Job(
            type="scrape",
            status="running",
            started_at=base_time,
            completed_at=None,
        )

        # Completed job
        job3 = Job(
            type="scrape",
            status="completed",
            started_at=base_time,
            completed_at=base_time + timedelta(seconds=20),
        )

        db.add_all([job1, job2, job3])
        db.commit()

        collector = MetricsCollector(db)
        stats = collector._job_duration_by_type()

        # Should only include the completed job
        assert "scrape" in stats
        assert stats["scrape"].count == 1
        assert stats["scrape"].avg_seconds == pytest.approx(20.0)
