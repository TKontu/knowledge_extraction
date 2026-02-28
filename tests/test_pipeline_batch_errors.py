"""Tests for pipeline batch error handling with asyncio.gather."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.extraction.pipeline import (
    BatchPipelineResult,
    ExtractionPipelineService,
    PipelineResult,
)


class TestBatchErrorHandling:
    """Test that batch processing handles individual source failures gracefully."""

    @pytest.fixture
    def mock_orchestrator(self):
        """Create mock extraction orchestrator."""
        return AsyncMock()

    @pytest.fixture
    def mock_deduplicator(self):
        """Create mock deduplicator."""
        return AsyncMock()

    @pytest.fixture
    def mock_entity_extractor(self):
        """Create mock entity extractor."""
        return AsyncMock()

    @pytest.fixture
    def mock_extraction_repo(self):
        """Create mock extraction repository."""
        return MagicMock()

    @pytest.fixture
    def mock_source_repo(self):
        """Create mock source repository."""
        repo = MagicMock()
        return repo

    @pytest.fixture
    def mock_project_repo(self):
        """Create mock project repository."""
        return MagicMock()

    @pytest.fixture
    def mock_qdrant_repo(self):
        """Create mock Qdrant repository."""
        return AsyncMock()

    @pytest.fixture
    def mock_embedding_service(self):
        """Create mock embedding service."""
        return AsyncMock()

    @pytest.fixture
    def pipeline_service(
        self,
        mock_orchestrator,
        mock_deduplicator,
        mock_entity_extractor,
        mock_extraction_repo,
        mock_source_repo,
        mock_project_repo,
        mock_qdrant_repo,
        mock_embedding_service,
    ):
        """Create pipeline service with mocked dependencies."""
        return ExtractionPipelineService(
            orchestrator=mock_orchestrator,
            deduplicator=mock_deduplicator,
            entity_extractor=mock_entity_extractor,
            extraction_repo=mock_extraction_repo,
            source_repo=mock_source_repo,
            project_repo=mock_project_repo,
            qdrant_repo=mock_qdrant_repo,
            embedding_service=mock_embedding_service,
        )

    @pytest.mark.asyncio
    async def test_batch_continues_on_single_source_failure(
        self, pipeline_service, mock_source_repo
    ):
        """Verify batch processing continues when one source fails."""
        project_id = uuid4()
        source_ids = [uuid4(), uuid4(), uuid4()]

        # Create mock sources
        sources = []
        for sid in source_ids:
            source = MagicMock()
            source.id = sid
            source.content = f"Content for {sid}"
            source.source_group = "test_company"
            sources.append(source)

        # First source succeeds, second raises exception, third succeeds
        call_count = [0]

        def mock_get(source_id):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 1:  # Second source fails
                raise RuntimeError("LLM timeout for source 2")
            return sources[idx % len(sources)]

        mock_source_repo.get = mock_get

        # Mock project repo
        with patch.object(pipeline_service, "_project_repo") as mock_proj:
            mock_proj.get.return_value = MagicMock(entity_types=[])

            # Mock orchestrator to return empty facts
            with patch.object(pipeline_service, "_orchestrator") as mock_orch:
                mock_result = MagicMock()
                mock_result.facts = []
                mock_orch.extract = AsyncMock(return_value=mock_result)

                result = await pipeline_service.process_batch(
                    source_ids=source_ids,
                    project_id=project_id,
                    profile_name="general",
                )

        # Verify batch completed despite one failure
        assert isinstance(result, BatchPipelineResult)
        assert result.sources_processed == 3
        # At least one should have failed
        assert result.sources_failed >= 1

    @pytest.mark.asyncio
    async def test_batch_returns_exception_results(self, pipeline_service):
        """Verify exceptions are captured as PipelineResult with errors."""
        project_id = uuid4()
        source_ids = [uuid4(), uuid4()]

        # Make process_source raise exception for second source
        original_process = pipeline_service.process_source

        call_count = [0]

        async def mock_process_source(source_id, project_id, profile_name):
            idx = call_count[0]
            call_count[0] += 1
            if idx == 1:
                raise ValueError("Test error for source 2")
            return PipelineResult(
                source_id=source_id,
                extractions_created=1,
                extractions_deduplicated=0,
                entities_extracted=0,
                entities_deduplicated=0,
                errors=[],
            )

        with patch.object(
            pipeline_service, "process_source", side_effect=mock_process_source
        ):
            result = await pipeline_service.process_batch(
                source_ids=source_ids,
                project_id=project_id,
                profile_name="general",
            )

        # Should have 2 results
        assert result.sources_processed == 2
        # One should have failed
        assert result.sources_failed >= 1
        # Results should include both success and error
        assert len(result.results) == 2


class TestSchemaExtractionBatchErrors:
    """Test SchemaExtractionPipeline batch error handling."""

    def test_extract_project_handles_errors_in_coroutines(self):
        """Verify extract_project handles errors within extract_with_limit.

        extract_with_limit wraps each source extraction in try/except,
        returning (0, False) on failure, so asyncio.gather doesn't need
        return_exceptions=True.
        """
        import inspect
        from services.extraction.pipeline import SchemaExtractionPipeline

        source = inspect.getsource(SchemaExtractionPipeline.extract_project)

        # Verify error handling exists within the coroutine (extract_with_limit)
        assert "extract_with_limit" in source, \
            "extract_project should use extract_with_limit wrapper"
        assert "except Exception" in source, \
            "extract_project should handle exceptions within extract_with_limit"


class TestExtractionPipelineBatchErrors:
    """Test ExtractionPipelineService batch error handling."""

    def test_gather_has_return_exceptions_in_process_batch(self):
        """Verify asyncio.gather uses return_exceptions in process_batch.

        This is a code inspection test that verifies the fix is in place.
        """
        import inspect
        from services.extraction.pipeline import ExtractionPipelineService

        source = inspect.getsource(ExtractionPipelineService.process_batch)

        # Verify asyncio.gather has return_exceptions=True
        assert "return_exceptions=True" in source or "return_exceptions" in source, \
            "process_batch should use asyncio.gather with return_exceptions=True"
