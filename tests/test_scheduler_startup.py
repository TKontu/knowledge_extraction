"""Tests for scheduler startup resilience: stale cleanup + worker stagger."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from orm_models import Job, Project


# ---------------------------------------------------------------------------
# Unit tests (mocked DB)
# ---------------------------------------------------------------------------


class TestCleanupStaleJobsUnit:
    """Unit tests for _cleanup_stale_jobs using mocked DB."""

    @pytest.fixture
    def mock_container(self):
        """Create a mock ServiceContainer."""
        container = MagicMock()
        container.firecrawl_client = MagicMock()
        container.rate_limiter = MagicMock()
        container.retry_config = MagicMock()
        container.llm_queue = MagicMock()
        container.extraction_embedding = MagicMock()
        container.embedding_service = MagicMock()
        return container

    @pytest.mark.asyncio
    async def test_cleanup_disabled_by_config(self, mock_container):
        """When scheduler_cleanup_stale_on_startup=False, no DB queries are made."""
        from services.scraper.scheduler import JobScheduler

        with (
            patch("services.scraper.scheduler.settings") as mock_settings,
            patch("services.scraper.scheduler.SessionLocal") as mock_session_local,
            patch("services.scraper.scheduler.get_shutdown_manager") as mock_sm,
        ):
            mock_settings.scheduler.cleanup_stale_on_startup = False
            mock_settings.scheduler.startup_stagger_seconds = 0.0
            mock_settings.crawl.max_concurrent_crawls = 1

            # Make shutdown manager signal shutdown immediately to prevent loops
            shutdown = MagicMock()
            shutdown.is_shutting_down = True
            mock_sm.return_value = shutdown

            scheduler = JobScheduler(services=mock_container, poll_interval=5)
            await scheduler.start()
            await scheduler.stop()

            # SessionLocal should NOT have been called for cleanup
            mock_session_local.assert_not_called()

    @pytest.mark.asyncio
    async def test_cleanup_error_does_not_crash_startup(self, mock_container):
        """If cleanup raises, startup should continue."""
        from services.scraper.scheduler import JobScheduler

        mock_db = MagicMock()
        mock_db.query.side_effect = RuntimeError("DB connection failed")
        mock_db.rollback = MagicMock()
        mock_db.close = MagicMock()

        with (
            patch("services.scraper.scheduler.settings") as mock_settings,
            patch("services.scraper.scheduler.SessionLocal", return_value=mock_db),
            patch("services.scraper.scheduler.get_shutdown_manager") as mock_sm,
        ):
            mock_settings.scheduler.cleanup_stale_on_startup = True
            mock_settings.scheduler.startup_stagger_seconds = 0.0
            mock_settings.crawl.max_concurrent_crawls = 1

            shutdown = MagicMock()
            shutdown.is_shutting_down = True
            mock_sm.return_value = shutdown

            scheduler = JobScheduler(services=mock_container, poll_interval=5)
            # Should not raise
            await scheduler.start()
            await scheduler.stop()

            # Rollback should have been called on error
            mock_db.rollback.assert_called_once()


class TestWorkerStagger:
    """Tests for staggered worker startup."""

    @pytest.fixture
    def mock_container(self):
        container = MagicMock()
        container.firecrawl_client = MagicMock()
        container.rate_limiter = MagicMock()
        container.retry_config = MagicMock()
        container.llm_queue = MagicMock()
        container.extraction_embedding = MagicMock()
        container.embedding_service = MagicMock()
        return container

    @pytest.mark.asyncio
    async def test_workers_start_with_stagger_delays(self, mock_container):
        """With stagger > 0, asyncio.sleep should be called between workers."""
        from services.scraper.scheduler import JobScheduler

        sleep_calls = []

        async def track_sleep(duration):
            sleep_calls.append(duration)

        with (
            patch("services.scraper.scheduler.settings") as mock_settings,
            patch("services.scraper.scheduler.asyncio.sleep", side_effect=track_sleep),
            patch("services.scraper.scheduler.asyncio.create_task") as mock_create_task,
            patch("services.scraper.scheduler.get_shutdown_manager") as mock_sm,
            patch("services.scraper.scheduler.SessionLocal"),
        ):
            mock_settings.scheduler.cleanup_stale_on_startup = False
            mock_settings.scheduler.startup_stagger_seconds = 0.5
            mock_settings.crawl.max_concurrent_crawls = 2

            shutdown = MagicMock()
            shutdown.is_shutting_down = True
            mock_sm.return_value = shutdown

            mock_create_task.return_value = MagicMock()

            scheduler = JobScheduler(services=mock_container, poll_interval=5)
            await scheduler.start()

            # Expected sleeps: after scrape (0.5), after crawl-0 (0.5),
            # after crawl-1 (0.5), before extract (0.5), before consolidate (0.5)
            assert len(sleep_calls) == 5
            assert all(d == 0.5 for d in sleep_calls)

    @pytest.mark.asyncio
    async def test_zero_stagger_skips_sleeps(self, mock_container):
        """With stagger = 0, no sleeps between workers."""
        from services.scraper.scheduler import JobScheduler

        sleep_calls = []

        async def track_sleep(duration):
            sleep_calls.append(duration)

        with (
            patch("services.scraper.scheduler.settings") as mock_settings,
            patch("services.scraper.scheduler.asyncio.sleep", side_effect=track_sleep),
            patch("services.scraper.scheduler.asyncio.create_task") as mock_create_task,
            patch("services.scraper.scheduler.get_shutdown_manager") as mock_sm,
            patch("services.scraper.scheduler.SessionLocal"),
        ):
            mock_settings.scheduler.cleanup_stale_on_startup = False
            mock_settings.scheduler.startup_stagger_seconds = 0.0
            mock_settings.crawl.max_concurrent_crawls = 2

            shutdown = MagicMock()
            shutdown.is_shutting_down = True
            mock_sm.return_value = shutdown

            mock_create_task.return_value = MagicMock()

            scheduler = JobScheduler(services=mock_container, poll_interval=5)
            await scheduler.start()

            # No stagger sleeps should occur
            assert len(sleep_calls) == 0


# ---------------------------------------------------------------------------
# Integration tests (real DB via conftest `db` fixture)
# ---------------------------------------------------------------------------


@pytest.fixture
def test_project(db):
    """Create a test project for FK references."""
    project = Project(
        name=f"test_startup_{uuid4().hex[:8]}",
        extraction_schema={"name": "test", "fields": []},
    )
    db.add(project)
    db.flush()
    return project


class TestCleanupStaleJobsIntegration:
    """Integration tests using real DB session."""

    @pytest.mark.asyncio
    async def test_running_jobs_marked_failed_on_startup(self, db, test_project):
        """Running jobs from a crashed instance should be marked failed."""
        from services.scraper.scheduler import JobScheduler

        running_job = Job(
            project_id=test_project.id,
            type="scrape",
            status="running",
            payload={"urls": ["https://example.com"]},
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        db.add(running_job)
        db.flush()

        mock_container = MagicMock()

        with (
            patch("services.scraper.scheduler.SessionLocal", return_value=db),
        ):
            original_commit = db.commit
            original_close = db.close
            db.commit = MagicMock()
            db.close = MagicMock()

            try:
                scheduler = JobScheduler(services=mock_container, poll_interval=5)
                counts = await scheduler._cleanup_stale_jobs()
            finally:
                db.commit = original_commit
                db.close = original_close

        # Check in-memory state (identity map — same object modified by cleanup)
        assert running_job.status == "failed"
        assert "Server restart" in running_job.error
        assert running_job.completed_at is not None
        assert counts.get("running", 0) >= 1

    @pytest.mark.asyncio
    async def test_cancelling_jobs_marked_failed_on_startup(self, db, test_project):
        """Cancelling jobs from a crashed instance should be marked failed."""
        from services.scraper.scheduler import JobScheduler

        cancelling_job = Job(
            project_id=test_project.id,
            type="extract",
            status="cancelling",
            payload={"source_ids": ["abc"]},
            started_at=datetime.now(UTC) - timedelta(minutes=10),
            updated_at=datetime.now(UTC) - timedelta(minutes=5),
        )
        db.add(cancelling_job)
        db.flush()

        mock_container = MagicMock()

        with (
            patch("services.scraper.scheduler.SessionLocal", return_value=db),
        ):
            original_commit = db.commit
            original_close = db.close
            db.commit = MagicMock()
            db.close = MagicMock()

            try:
                scheduler = JobScheduler(services=mock_container, poll_interval=5)
                counts = await scheduler._cleanup_stale_jobs()
            finally:
                db.commit = original_commit
                db.close = original_close

        # Check in-memory state (identity map — same object modified by cleanup)
        assert cancelling_job.status == "failed"
        assert "Server restart" in cancelling_job.error
        assert counts.get("cancelling", 0) >= 1

    @pytest.mark.asyncio
    async def test_queued_jobs_left_untouched(self, db, test_project):
        """Queued jobs should NOT be modified during cleanup."""
        from services.scraper.scheduler import JobScheduler

        queued_job = Job(
            project_id=test_project.id,
            type="scrape",
            status="queued",
            payload={"urls": ["https://example.com"]},
        )
        db.add(queued_job)
        db.flush()

        mock_container = MagicMock()

        with (
            patch("services.scraper.scheduler.SessionLocal", return_value=db),
        ):
            original_commit = db.commit
            original_close = db.close
            db.commit = MagicMock()
            db.close = MagicMock()

            try:
                scheduler = JobScheduler(services=mock_container, poll_interval=5)
                counts = await scheduler._cleanup_stale_jobs()
            finally:
                db.commit = original_commit
                db.close = original_close

        assert queued_job.status == "queued"
        assert queued_job.error is None
