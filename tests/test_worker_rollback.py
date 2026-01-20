"""Tests for worker rollback behavior on exceptions."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from orm_models import Job


class TestScraperWorkerRollback:
    """Test that ScraperWorker calls db.rollback() on exception."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = MagicMock()
        db.commit = MagicMock()
        db.rollback = MagicMock()
        return db

    @pytest.fixture
    def mock_firecrawl_client(self):
        """Create a mock Firecrawl client."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def scrape_job(self):
        """Create a test scrape job."""
        job = MagicMock(spec=Job)
        job.id = uuid4()
        job.type = "scrape"
        job.status = "queued"
        job.payload = {"urls": ["https://example.com"], "project_id": str(uuid4())}
        job.started_at = None
        job.completed_at = None
        job.error = None
        job.result = None
        return job

    @pytest.mark.asyncio
    async def test_rollback_called_on_exception(
        self, mock_db, mock_firecrawl_client, scrape_job
    ):
        """Verify db.rollback() is called when an exception occurs during job processing."""
        from services.scraper.worker import ScraperWorker

        # Create worker
        worker = ScraperWorker(
            db=mock_db,
            firecrawl_client=mock_firecrawl_client,
        )

        # Make something raise an exception after initial commit
        # The job.status setter simulates an error happening during processing
        call_count = 0

        def mock_commit():
            nonlocal call_count
            call_count += 1
            # First commit (setting status to running) succeeds
            # But we want to test when an error happens during processing
            if call_count == 1:
                return  # First commit succeeds
            # Subsequent commits succeed too

        mock_db.commit.side_effect = mock_commit

        # Make the scrape fail with an unexpected error
        mock_firecrawl_client.scrape.side_effect = RuntimeError("Unexpected error")

        # Also need to mock the project repo to raise
        with patch.object(worker, "project_repo") as mock_project_repo:
            mock_project_repo.get_default_project = AsyncMock(
                side_effect=RuntimeError("Database connection lost")
            )

            # Process the job
            await worker.process_job(scrape_job)

        # Verify rollback was called
        assert mock_db.rollback.called, "db.rollback() should be called on exception"

        # Verify job was marked as failed
        assert scrape_job.status == "failed"
        assert scrape_job.error is not None  # Some error was recorded

    @pytest.mark.asyncio
    async def test_no_partial_data_on_exception(
        self, mock_db, mock_firecrawl_client, scrape_job
    ):
        """Verify that partial data is rolled back when an exception occurs."""
        from services.scraper.worker import ScraperWorker

        # Track commit calls
        commits = []
        rollbacks = []

        def track_commit():
            commits.append(datetime.now(UTC))

        def track_rollback():
            rollbacks.append(datetime.now(UTC))

        mock_db.commit.side_effect = track_commit
        mock_db.rollback.side_effect = track_rollback

        worker = ScraperWorker(
            db=mock_db,
            firecrawl_client=mock_firecrawl_client,
        )

        # Make project repo fail mid-processing
        with patch.object(worker, "project_repo") as mock_project_repo:
            mock_project_repo.get_default_project = AsyncMock(
                side_effect=ValueError("Invalid state")
            )

            await worker.process_job(scrape_job)

        # Should have at least one rollback
        assert len(rollbacks) >= 1, "Should call rollback on exception"


class TestExtractionWorkerRollback:
    """Test that ExtractionWorker calls db.rollback() on exception."""

    @pytest.fixture
    def mock_db(self):
        """Create a mock database session."""
        db = MagicMock()
        db.commit = MagicMock()
        db.rollback = MagicMock()
        return db

    @pytest.fixture
    def mock_pipeline_service(self):
        """Create a mock pipeline service."""
        service = AsyncMock()
        return service

    @pytest.fixture
    def extract_job(self):
        """Create a test extraction job."""
        job = MagicMock(spec=Job)
        job.id = uuid4()
        job.type = "extract"
        job.status = "queued"
        job.payload = {"project_id": str(uuid4()), "source_ids": [str(uuid4())]}
        job.started_at = None
        job.completed_at = None
        job.error = None
        job.result = None
        return job

    @pytest.mark.asyncio
    async def test_rollback_called_on_exception(
        self, mock_db, mock_pipeline_service, extract_job
    ):
        """Verify db.rollback() is called when an exception occurs during extraction."""
        from services.extraction.worker import ExtractionWorker

        # Create worker
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
        )

        # Make pipeline service raise an exception
        mock_pipeline_service.process_batch.side_effect = RuntimeError(
            "LLM service unavailable"
        )

        # Process the job
        await worker.process_job(extract_job)

        # Verify rollback was called
        assert mock_db.rollback.called, "db.rollback() should be called on exception"

        # Verify job was marked as failed
        assert extract_job.status == "failed"
        assert "LLM service unavailable" in extract_job.error

    @pytest.mark.asyncio
    async def test_rollback_called_on_value_error(self, mock_db, mock_pipeline_service):
        """Verify db.rollback() is called when project_id is missing."""
        from services.extraction.worker import ExtractionWorker

        job = MagicMock(spec=Job)
        job.id = uuid4()
        job.type = "extract"
        job.status = "queued"
        job.payload = {}  # Missing project_id
        job.started_at = None
        job.completed_at = None
        job.error = None
        job.result = None

        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
        )

        await worker.process_job(job)

        # Verify rollback was called
        assert mock_db.rollback.called, "db.rollback() should be called on ValueError"
        assert job.status == "failed"
        assert "project_id" in job.error.lower()
