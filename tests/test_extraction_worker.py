"""Tests for ExtractionWorker."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from orm_models import Job
from src.services.extraction.worker import ExtractionWorker


@pytest.fixture
def mock_db():
    """Mock database session."""
    return Mock()


@pytest.fixture
def mock_pipeline_service():
    """Mock ExtractionPipelineService."""
    return AsyncMock()


@pytest.fixture
def extraction_worker(mock_db, mock_pipeline_service):
    """Create ExtractionWorker with mocked dependencies."""
    return ExtractionWorker(
        db=mock_db,
        pipeline_service=mock_pipeline_service,
    )


class TestExtractionWorker:
    """Tests for ExtractionWorker."""

    async def test_worker_processes_queued_jobs(self, extraction_worker, mock_db):
        """Worker processes jobs with queued status."""
        project_id = uuid4()

        # Create a mock job
        job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={"project_id": str(project_id)},
        )

        # Mock pipeline service
        mock_result = Mock()
        mock_result.sources_processed = 5
        mock_result.sources_failed = 0
        mock_result.total_extractions = 10
        extraction_worker.pipeline_service.process_project_pending.return_value = (
            mock_result
        )

        # Process job
        await extraction_worker.process_job(job)

        # Verify job was updated
        assert job.status == "completed"
        assert job.started_at is not None
        assert job.completed_at is not None
        assert job.result is not None

    async def test_worker_updates_job_status(self, extraction_worker, mock_db):
        """Worker updates job status through lifecycle."""
        project_id = uuid4()

        job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={"project_id": str(project_id)},
        )

        # Mock pipeline service
        mock_result = Mock()
        mock_result.sources_processed = 3
        mock_result.sources_failed = 0
        mock_result.total_extractions = 6
        extraction_worker.pipeline_service.process_project_pending.return_value = (
            mock_result
        )

        # Process job
        await extraction_worker.process_job(job)

        # Verify status transitions: queued -> running -> completed
        assert job.status == "completed"
        assert job.result["sources_processed"] == 3
        assert job.result["total_extractions"] == 6

    async def test_worker_handles_job_failure(self, extraction_worker, mock_db):
        """Worker handles job failures gracefully."""
        project_id = uuid4()

        job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={"project_id": str(project_id)},
        )

        # Mock pipeline service to raise exception
        extraction_worker.pipeline_service.process_project_pending.side_effect = (
            Exception("Pipeline error")
        )

        # Process job
        await extraction_worker.process_job(job)

        # Verify job is marked as failed
        assert job.status == "failed"
        assert job.error == "pipeline error"
        assert job.completed_at is not None
