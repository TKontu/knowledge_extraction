"""Tests for ExtractionWorker."""

from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from orm_models import Job, Project
from services.extraction.pipeline import SchemaPipelineResult
from services.extraction.worker import ExtractionWorker


@pytest.fixture
def mock_db():
    """Mock database session."""
    db = Mock()
    db.query.return_value.filter.return_value.first.return_value = None
    return db


@pytest.fixture
def mock_llm():
    """Mock LLM config for schema extraction."""
    from config import LLMConfig

    return LLMConfig(
        base_url="http://localhost:8000/v1",
        embedding_base_url="http://localhost:8000/v1",
        api_key="test",
        model="test-model",
        embedding_model="bge-m3",
        embedding_dimension=1024,
        http_timeout=60,
        max_tokens=4096,
        max_retries=3,
        retry_backoff_min=2,
        retry_backoff_max=30,
        base_temperature=0.1,
        retry_temperature_increment=0.05,
    )


class TestSchemaExtractionSelection:
    """Tests for automatic schema-based extraction selection."""

    async def test_worker_uses_schema_pipeline_when_project_has_schema(
        self, mock_db, mock_llm
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
            llm=mock_llm,
        )

        # Mock the schema pipeline
        with patch.object(worker, "_process_with_schema_pipeline") as mock_schema:
            mock_schema.return_value = SchemaPipelineResult(
                project_id="test",
                sources_processed=5,
                sources_failed=0,
                total_extractions=35,  # 7 field groups * 5 sources
                field_groups=7,
                schema_name="test",
            )

            await worker.process_job(job)

            # Verify schema pipeline was used
            mock_schema.assert_called_once()

        assert job.status == "completed"
        assert job.result["total_extractions"] == 35
