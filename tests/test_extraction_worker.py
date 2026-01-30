"""Tests for ExtractionWorker."""

import pytest
from datetime import datetime, UTC
from uuid import uuid4
from unittest.mock import AsyncMock, MagicMock, Mock, patch

from src.services.extraction.worker import ExtractionWorker, SchemaExtractionResult
from orm_models import Job, Project


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


@pytest.fixture
def mock_pipeline_service():
    """Mock ExtractionPipelineService."""
    return AsyncMock()


@pytest.fixture
def mock_settings():
    """Mock application settings."""
    settings = Mock()
    settings.llm_base_url = "http://localhost:8000"
    settings.llm_model = "test-model"
    settings.llm_http_timeout = 60
    return settings


@pytest.fixture
def extraction_worker(mock_db, mock_pipeline_service):
    """Create ExtractionWorker with mocked dependencies (no settings)."""
    return ExtractionWorker(
        db=mock_db,
        pipeline_service=mock_pipeline_service,
    )


@pytest.fixture
def extraction_worker_with_settings(mock_db, mock_pipeline_service, mock_settings):
    """Create ExtractionWorker with settings for schema extraction."""
    return ExtractionWorker(
        db=mock_db,
        pipeline_service=mock_pipeline_service,
        settings=mock_settings,
        llm_queue=None,
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
        assert "Pipeline error" in job.error
        assert job.completed_at is not None


class TestSchemaExtractionSelection:
    """Tests for automatic schema-based extraction selection."""

    def test_has_extraction_schema_returns_true_for_project_with_schema(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Worker detects project with extraction_schema."""
        project_id = uuid4()

        # Mock project with extraction schema
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = {
            "name": "test_schema",
            "field_groups": [{"name": "test_group", "fields": []}],
        }
        mock_db.query.return_value.filter.return_value.first.return_value = mock_project

        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        has_schema, project = worker._has_extraction_schema(project_id)

        assert has_schema is True
        assert project == mock_project

    def test_has_extraction_schema_returns_false_for_project_without_schema(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Worker detects project without extraction_schema."""
        project_id = uuid4()

        # Mock project without extraction schema
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = None
        mock_db.query.return_value.filter.return_value.first.return_value = mock_project

        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        has_schema, project = worker._has_extraction_schema(project_id)

        assert has_schema is False
        assert project == mock_project

    def test_has_extraction_schema_returns_false_for_empty_field_groups(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Worker returns False for schema with empty field_groups."""
        project_id = uuid4()

        # Mock project with schema but no field groups
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = {"name": "test_schema", "field_groups": []}
        mock_db.query.return_value.filter.return_value.first.return_value = mock_project

        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        has_schema, project = worker._has_extraction_schema(project_id)

        assert has_schema is False

    async def test_worker_uses_schema_pipeline_when_project_has_schema(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Worker uses SchemaExtractionPipeline when project has extraction_schema."""
        project_id = uuid4()

        # Mock project with extraction schema
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = {
            "name": "drivetrain_company",
            "field_groups": [{"name": "manufacturing", "fields": []}],
        }
        mock_db.query.return_value.filter.return_value.first.return_value = mock_project

        job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={"project_id": str(project_id)},
        )

        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        # Mock the schema pipeline
        with patch.object(worker, "_process_with_schema_pipeline") as mock_schema:
            mock_schema.return_value = SchemaExtractionResult(
                sources_processed=5,
                sources_failed=0,
                total_extractions=35,  # 7 field groups * 5 sources
            )

            await worker.process_job(job)

            # Verify schema pipeline was used, not generic
            mock_schema.assert_called_once()
            mock_pipeline_service.process_project_pending.assert_not_called()

        assert job.status == "completed"
        assert job.result["total_extractions"] == 35

    async def test_worker_uses_generic_pipeline_when_no_schema(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Worker uses generic pipeline when project has no extraction_schema."""
        project_id = uuid4()

        # Mock project without extraction schema
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = None
        mock_db.query.return_value.filter.return_value.first.return_value = mock_project

        job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={"project_id": str(project_id)},
        )

        # Mock generic pipeline result
        mock_result = Mock()
        mock_result.sources_processed = 5
        mock_result.sources_failed = 0
        mock_result.total_extractions = 50
        mock_result.total_deduplicated = 5
        mock_result.total_entities = 100
        mock_pipeline_service.process_project_pending.return_value = mock_result

        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        await worker.process_job(job)

        # Verify generic pipeline was used
        mock_pipeline_service.process_project_pending.assert_called_once()

        assert job.status == "completed"
        assert job.result["total_extractions"] == 50

    async def test_worker_falls_back_to_generic_when_no_settings(
        self, mock_db, mock_pipeline_service
    ):
        """Worker falls back to generic extraction when settings not provided."""
        project_id = uuid4()

        # Mock project with extraction schema
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = {
            "name": "drivetrain_company",
            "field_groups": [{"name": "manufacturing", "fields": []}],
        }
        mock_db.query.return_value.filter.return_value.first.return_value = mock_project

        job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={"project_id": str(project_id)},
        )

        # Mock generic pipeline result
        mock_result = Mock()
        mock_result.sources_processed = 5
        mock_result.sources_failed = 0
        mock_result.total_extractions = 50
        mock_result.total_deduplicated = 5
        mock_result.total_entities = 100
        mock_pipeline_service.process_project_pending.return_value = mock_result

        # Worker WITHOUT settings
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=None,  # No settings
        )

        await worker.process_job(job)

        # Should fall back to generic pipeline
        mock_pipeline_service.process_project_pending.assert_called_once()
        assert job.status == "completed"
