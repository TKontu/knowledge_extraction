"""Tests for extraction pipeline checkpointing and resume functionality."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest

from orm_models import Job, Project
from src.services.extraction.pipeline import CheckpointCallback, SchemaExtractionPipeline
from src.services.extraction.worker import ExtractionWorker, SchemaExtractionResult


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
    settings.smart_classification_enabled = False
    settings.extraction_max_concurrent_sources = 5
    return settings


class TestCheckpointCallback:
    """Tests for checkpoint callback creation in ExtractionWorker."""

    def test_create_checkpoint_callback_returns_callable(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """_create_checkpoint_callback returns a callable."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload={"project_id": str(uuid4())},
        )

        callback = worker._create_checkpoint_callback(job)

        assert callable(callback)

    def test_checkpoint_callback_updates_job_payload(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Checkpoint callback updates job.payload with checkpoint data."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload={"project_id": str(uuid4())},
        )

        callback = worker._create_checkpoint_callback(job)

        # Simulate checkpoint after processing 3 sources
        processed_ids = [str(uuid4()) for _ in range(3)]
        callback(processed_ids, 15, 5)

        # Verify checkpoint was added to payload
        assert "checkpoint" in job.payload
        checkpoint = job.payload["checkpoint"]
        assert checkpoint["processed_source_ids"] == processed_ids
        assert checkpoint["total_extractions"] == 15
        assert checkpoint["total_entities"] == 5
        assert "last_checkpoint_at" in checkpoint

        # Note: Callback does NOT commit - pipeline commits after calling callback
        # This ensures checkpoint and extractions are committed atomically

    def test_checkpoint_callback_preserves_existing_payload(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Checkpoint callback preserves existing payload fields."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        existing_payload = {
            "project_id": str(uuid4()),
            "source_ids": ["id1", "id2"],
            "force": True,
        }
        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload=existing_payload.copy(),
        )

        callback = worker._create_checkpoint_callback(job)
        callback(["source1"], 10, 2)

        # Verify existing fields preserved
        assert job.payload["project_id"] == existing_payload["project_id"]
        assert job.payload["source_ids"] == existing_payload["source_ids"]
        assert job.payload["force"] is True
        # And checkpoint added
        assert "checkpoint" in job.payload


class TestResumeState:
    """Tests for resume state detection in ExtractionWorker."""

    def test_get_resume_state_returns_none_for_no_payload(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """_get_resume_state returns None when job has no payload."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload=None,
        )

        result = worker._get_resume_state(job)
        assert result is None

    def test_get_resume_state_returns_none_for_no_checkpoint(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """_get_resume_state returns None when payload has no checkpoint."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload={"project_id": str(uuid4())},
        )

        result = worker._get_resume_state(job)
        assert result is None

    def test_get_resume_state_returns_set_of_processed_ids(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """_get_resume_state returns set of processed source IDs."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        processed_ids = [str(uuid4()), str(uuid4()), str(uuid4())]
        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload={
                "project_id": str(uuid4()),
                "checkpoint": {
                    "processed_source_ids": processed_ids,
                    "last_checkpoint_at": "2024-01-15T10:30:00Z",
                    "total_extractions": 45,
                    "total_entities": 12,
                },
            },
        )

        result = worker._get_resume_state(job)

        assert result is not None
        assert isinstance(result, set)
        assert len(result) == 3
        assert all(pid in result for pid in processed_ids)

    def test_get_resume_state_returns_none_for_empty_processed_ids(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """_get_resume_state returns None when processed_source_ids is empty."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload={
                "project_id": str(uuid4()),
                "checkpoint": {
                    "processed_source_ids": [],
                    "last_checkpoint_at": "2024-01-15T10:30:00Z",
                    "total_extractions": 0,
                    "total_entities": 0,
                },
            },
        )

        result = worker._get_resume_state(job)
        assert result is None


class TestSchemaExtractionPipelineCheckpointing:
    """Tests for checkpoint functionality in SchemaExtractionPipeline."""

    @pytest.fixture
    def mock_orchestrator(self):
        """Mock SchemaExtractionOrchestrator."""
        orchestrator = AsyncMock()
        orchestrator.extract_all_groups.return_value = ([], None)
        return orchestrator

    @pytest.fixture
    def mock_source(self):
        """Create a mock source."""

        def _create(source_id=None):
            source = Mock()
            source.id = source_id or uuid4()
            source.project_id = uuid4()
            source.content = "Test content"
            source.source_group = "TestCompany"
            source.uri = "https://example.com"
            source.title = "Test Page"
            source.status = "pending"
            source.page_type = None
            return source

        return _create

    def _setup_db_mock(self, mock_db, mock_project, sources):
        """Set up database mock with proper query chain for sources."""
        # Project query: db.query(Project).filter(...).first()
        project_query = Mock()
        project_query.first.return_value = mock_project

        # Source query: db.query(Source).filter(...).filter(...).all()
        source_query = Mock()
        source_query.all.return_value = sources
        # Handle the second .filter() call for source_groups
        source_query.filter.return_value = source_query

        def query_side_effect(model):
            model_name = model.__name__ if hasattr(model, "__name__") else str(model)
            if "Project" in model_name:
                return Mock(filter=Mock(return_value=project_query))
            return Mock(filter=Mock(return_value=source_query))

        mock_db.query.side_effect = query_side_effect

    async def test_checkpoint_callback_called_after_chunk(
        self, mock_db, mock_orchestrator, mock_source
    ):
        """Checkpoint callback is called after each chunk is processed."""
        # Create pipeline
        pipeline = SchemaExtractionPipeline(mock_orchestrator, mock_db)

        # Create mock sources (more than chunk_size=20)
        sources = [mock_source() for _ in range(25)]

        # Mock project with schema
        mock_project = Mock()
        mock_project.extraction_schema = {
            "name": "test_schema",
            "field_groups": [{"name": "test", "fields": []}],
        }

        self._setup_db_mock(mock_db, mock_project, sources)

        # Track checkpoint calls
        checkpoint_calls = []

        def checkpoint_callback(processed_ids, extractions, entities):
            checkpoint_calls.append(
                {
                    "processed_ids": processed_ids.copy(),
                    "extractions": extractions,
                    "entities": entities,
                }
            )

        # Run extraction with checkpoint callback
        with patch(
            "src.services.extraction.pipeline.SchemaAdapter"
        ) as mock_adapter_class:
            mock_adapter = Mock()
            mock_adapter.validate_extraction_schema.return_value = Mock(is_valid=True)
            mock_adapter.convert_to_field_groups.return_value = []
            mock_adapter_class.return_value = mock_adapter

            await pipeline.extract_project(
                project_id=uuid4(),
                checkpoint_callback=checkpoint_callback,
            )

        # Verify checkpoint was called at least once (after chunk processing)
        assert len(checkpoint_calls) >= 1

    async def test_resume_from_skips_already_processed_sources(
        self, mock_db, mock_orchestrator, mock_source
    ):
        """Resume skips sources that were already processed."""
        pipeline = SchemaExtractionPipeline(mock_orchestrator, mock_db)

        # Create 5 sources
        sources = [mock_source() for _ in range(5)]
        source_ids = [str(s.id) for s in sources]

        # Mark first 2 as already processed
        resume_from = set(source_ids[:2])

        # Mock project
        mock_project = Mock()
        mock_project.extraction_schema = {
            "name": "test_schema",
            "field_groups": [{"name": "test", "fields": []}],
        }

        self._setup_db_mock(mock_db, mock_project, sources)

        with patch(
            "src.services.extraction.pipeline.SchemaAdapter"
        ) as mock_adapter_class:
            mock_adapter = Mock()
            mock_adapter.validate_extraction_schema.return_value = Mock(is_valid=True)
            mock_adapter.convert_to_field_groups.return_value = []
            mock_adapter_class.return_value = mock_adapter

            result = await pipeline.extract_project(
                project_id=uuid4(),
                resume_from=resume_from,
            )

        # Only 3 sources should be processed (5 total - 2 already done)
        assert result["sources_processed"] == 3

    async def test_batch_commit_happens_after_each_chunk(
        self, mock_db, mock_orchestrator, mock_source
    ):
        """Database commit is called after each chunk."""
        pipeline = SchemaExtractionPipeline(mock_orchestrator, mock_db)

        # Create sources for 2 chunks (chunk_size=20)
        sources = [mock_source() for _ in range(25)]

        mock_project = Mock()
        mock_project.extraction_schema = {
            "name": "test_schema",
            "field_groups": [{"name": "test", "fields": []}],
        }

        self._setup_db_mock(mock_db, mock_project, sources)

        with patch(
            "src.services.extraction.pipeline.SchemaAdapter"
        ) as mock_adapter_class:
            mock_adapter = Mock()
            mock_adapter.validate_extraction_schema.return_value = Mock(is_valid=True)
            mock_adapter.convert_to_field_groups.return_value = []
            mock_adapter_class.return_value = mock_adapter

            # Reset commit call count before extraction
            mock_db.commit.reset_mock()

            await pipeline.extract_project(project_id=uuid4())

        # Should have committed at least twice (once per chunk)
        assert mock_db.commit.call_count >= 2

    async def test_failed_sources_not_added_to_checkpoint(
        self, mock_db, mock_orchestrator, mock_source
    ):
        """Failed sources should NOT be added to checkpoint (can be retried on resume)."""
        pipeline = SchemaExtractionPipeline(mock_orchestrator, mock_db)

        # Create 5 sources
        sources = [mock_source() for _ in range(5)]

        mock_project = Mock()
        mock_project.extraction_schema = {
            "name": "test_schema",
            "field_groups": [{"name": "test", "fields": [{"name": "f1"}]}],
        }

        self._setup_db_mock(mock_db, mock_project, sources)

        # Make orchestrator fail on sources 1 and 3 (0-indexed)
        fail_indices = {1, 3}
        call_count = [0]

        async def extract_all_groups_side_effect(*args, **kwargs):
            idx = call_count[0]
            call_count[0] += 1
            if idx in fail_indices:
                raise Exception(f"Simulated failure for source {idx}")
            return ([], None)

        mock_orchestrator.extract_all_groups.side_effect = extract_all_groups_side_effect

        # Track checkpoint calls
        checkpoint_calls = []

        def checkpoint_callback(processed_ids, extractions, entities):
            checkpoint_calls.append(processed_ids.copy())

        # Create a mock FieldGroup that the adapter will return
        mock_field_group = Mock()
        mock_field_group.name = "test"

        with patch(
            "src.services.extraction.pipeline.SchemaAdapter"
        ) as mock_adapter_class:
            mock_adapter = Mock()
            mock_adapter.validate_extraction_schema.return_value = Mock(is_valid=True)
            # Return non-empty field_groups so extract_source proceeds to orchestrator
            mock_adapter.convert_to_field_groups.return_value = [mock_field_group]
            mock_adapter_class.return_value = mock_adapter

            result = await pipeline.extract_project(
                project_id=uuid4(),
                checkpoint_callback=checkpoint_callback,
            )

        # Should have 2 failed sources
        assert result["sources_failed"] == 2

        # Checkpoint should only contain successful source IDs (indices 0, 2, 4)
        if checkpoint_calls:
            final_checkpoint = checkpoint_calls[-1]
            successful_source_ids = {str(sources[i].id) for i in [0, 2, 4]}
            failed_source_ids = {str(sources[i].id) for i in [1, 3]}

            # All successful sources should be in checkpoint
            for sid in successful_source_ids:
                assert sid in final_checkpoint, f"Successful source {sid} missing from checkpoint"

            # No failed sources should be in checkpoint
            for sid in failed_source_ids:
                assert sid not in final_checkpoint, f"Failed source {sid} should not be in checkpoint"


class TestWorkerProcessJobWithCheckpointing:
    """Tests for process_job with checkpoint support."""

    async def test_process_job_passes_checkpoint_callback_to_pipeline(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Worker passes checkpoint callback to schema pipeline."""
        project_id = uuid4()

        # Mock project with schema
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = {
            "name": "test_schema",
            "field_groups": [{"name": "test", "fields": []}],
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

        # Mock the schema pipeline creation and extraction
        with patch.object(
            worker, "_create_schema_pipeline", new_callable=AsyncMock
        ) as mock_create:
            mock_pipeline = AsyncMock()
            mock_pipeline.extract_project.return_value = {
                "sources_processed": 5,
                "sources_failed": 0,
                "extractions_created": 15,
            }
            mock_create.return_value = mock_pipeline

            await worker.process_job(job)

            # Verify extract_project was called with checkpoint_callback
            mock_pipeline.extract_project.assert_called_once()
            call_kwargs = mock_pipeline.extract_project.call_args.kwargs
            assert "checkpoint_callback" in call_kwargs
            assert call_kwargs["checkpoint_callback"] is not None

    async def test_process_job_resumes_from_checkpoint(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Worker resumes from checkpoint when restarting a failed job."""
        project_id = uuid4()
        already_processed = [str(uuid4()), str(uuid4())]

        # Mock project with schema
        mock_project = Mock(spec=Project)
        mock_project.extraction_schema = {
            "name": "test_schema",
            "field_groups": [{"name": "test", "fields": []}],
        }
        mock_db.query.return_value.filter.return_value.first.return_value = mock_project

        # Job with existing checkpoint from previous run
        job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={
                "project_id": str(project_id),
                "checkpoint": {
                    "processed_source_ids": already_processed,
                    "last_checkpoint_at": "2024-01-15T10:30:00Z",
                    "total_extractions": 10,
                    "total_entities": 3,
                },
            },
        )

        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        with patch.object(
            worker, "_create_schema_pipeline", new_callable=AsyncMock
        ) as mock_create:
            mock_pipeline = AsyncMock()
            mock_pipeline.extract_project.return_value = {
                "sources_processed": 3,
                "sources_failed": 0,
                "extractions_created": 9,
            }
            mock_create.return_value = mock_pipeline

            await worker.process_job(job)

            # Verify extract_project was called with resume_from
            call_kwargs = mock_pipeline.extract_project.call_args.kwargs
            assert "resume_from" in call_kwargs
            assert call_kwargs["resume_from"] == set(already_processed)


class TestCheckpointDataStructure:
    """Tests for checkpoint data structure integrity."""

    def test_checkpoint_has_required_fields(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Checkpoint data contains all required fields."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload={},
        )

        callback = worker._create_checkpoint_callback(job)
        callback(["id1", "id2"], 10, 5)

        checkpoint = job.payload["checkpoint"]

        # Verify all required fields present
        assert "processed_source_ids" in checkpoint
        assert "last_checkpoint_at" in checkpoint
        assert "total_extractions" in checkpoint
        assert "total_entities" in checkpoint

    def test_checkpoint_timestamp_is_iso_format(
        self, mock_db, mock_pipeline_service, mock_settings
    ):
        """Checkpoint timestamp is in ISO 8601 format."""
        worker = ExtractionWorker(
            db=mock_db,
            pipeline_service=mock_pipeline_service,
            settings=mock_settings,
        )

        job = Job(
            id=uuid4(),
            type="extract",
            status="running",
            payload={},
        )

        callback = worker._create_checkpoint_callback(job)
        callback(["id1"], 5, 2)

        timestamp = job.payload["checkpoint"]["last_checkpoint_at"]

        # Should be parseable as ISO format
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert parsed is not None
