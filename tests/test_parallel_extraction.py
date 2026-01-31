"""Tests for parallel extraction batch processing.

TDD: These tests verify that extraction processes sources in parallel,
not serially, to maximize vLLM throughput.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


class TestParallelBatchProcessing:
    """Test that process_batch runs sources in parallel."""

    @pytest.fixture
    def mock_dependencies(self):
        """Create mock dependencies for ExtractionPipelineService."""
        return {
            "orchestrator": AsyncMock(),
            "deduplicator": AsyncMock(),
            "entity_extractor": AsyncMock(),
            "extraction_repo": AsyncMock(),
            "source_repo": AsyncMock(),
            "project_repo": AsyncMock(),
            "qdrant_repo": AsyncMock(),
            "embedding_service": AsyncMock(),
        }

    @pytest.fixture
    def pipeline_service(self, mock_dependencies):
        """Create pipeline service with mocks."""
        from src.services.extraction.pipeline import ExtractionPipelineService

        return ExtractionPipelineService(**mock_dependencies)

    @pytest.mark.asyncio
    async def test_process_batch_runs_sources_concurrently(
        self, pipeline_service, mock_dependencies
    ):
        """Test that multiple sources are processed concurrently, not serially.

        This test verifies that process_batch doesn't wait for each source
        to complete before starting the next one.
        """
        source_ids = [uuid4() for _ in range(5)]
        project_id = uuid4()

        # Track concurrent execution
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        # Create mock source
        mock_source = MagicMock()
        mock_source.content = "Test content"
        mock_source.source_group = "test_company"
        mock_dependencies["source_repo"].get = AsyncMock(return_value=mock_source)

        # Mock project
        mock_project = MagicMock()
        mock_project.entity_types = []
        mock_dependencies["project_repo"].get = AsyncMock(return_value=mock_project)

        # Mock orchestrator to track concurrency
        original_extract = mock_dependencies["orchestrator"].extract

        async def tracking_extract(*args, **kwargs):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)

            # Simulate LLM processing time
            await asyncio.sleep(0.1)

            async with lock:
                current_concurrent -= 1

            # Return mock result
            result = MagicMock()
            result.facts = []
            return result

        mock_dependencies["orchestrator"].extract = tracking_extract

        # Run batch processing
        await pipeline_service.process_batch(
            source_ids=source_ids,
            project_id=project_id,
        )

        # With 5 sources and parallel processing, we should see > 1 concurrent
        # If serial, max_concurrent would be exactly 1
        assert max_concurrent > 1, (
            f"Expected concurrent processing but max_concurrent was {max_concurrent}. "
            "Sources are being processed serially!"
        )

    @pytest.mark.asyncio
    async def test_process_batch_respects_concurrency_limit(
        self, pipeline_service, mock_dependencies
    ):
        """Test that concurrency is bounded by a reasonable limit."""
        source_ids = [uuid4() for _ in range(20)]
        project_id = uuid4()

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        mock_source = MagicMock()
        mock_source.content = "Test content"
        mock_source.source_group = "test_company"
        mock_dependencies["source_repo"].get = AsyncMock(return_value=mock_source)

        mock_project = MagicMock()
        mock_project.entity_types = []
        mock_dependencies["project_repo"].get = AsyncMock(return_value=mock_project)

        async def tracking_extract(*args, **kwargs):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)

            await asyncio.sleep(0.05)

            async with lock:
                current_concurrent -= 1

            result = MagicMock()
            result.facts = []
            return result

        mock_dependencies["orchestrator"].extract = tracking_extract

        await pipeline_service.process_batch(
            source_ids=source_ids,
            project_id=project_id,
        )

        # Should not exceed a reasonable concurrency limit (e.g., 10)
        # This prevents overwhelming downstream services
        assert max_concurrent <= 10, (
            f"Concurrency too high: {max_concurrent}. "
            "Should be bounded to prevent resource exhaustion."
        )

    @pytest.mark.asyncio
    async def test_process_batch_processes_all_sources(
        self, pipeline_service, mock_dependencies
    ):
        """Test that all sources are processed even with parallel execution."""
        source_ids = [uuid4() for _ in range(10)]
        project_id = uuid4()

        processed_ids = []
        lock = asyncio.Lock()

        mock_source = MagicMock()
        mock_source.content = "Test content"
        mock_source.source_group = "test_company"

        async def tracking_get(source_id):
            async with lock:
                processed_ids.append(source_id)
            return mock_source

        mock_dependencies["source_repo"].get = tracking_get

        mock_project = MagicMock()
        mock_project.entity_types = []
        mock_dependencies["project_repo"].get = AsyncMock(return_value=mock_project)

        async def mock_extract(*args, **kwargs):
            result = MagicMock()
            result.facts = []
            return result

        mock_dependencies["orchestrator"].extract = mock_extract

        result = await pipeline_service.process_batch(
            source_ids=source_ids,
            project_id=project_id,
        )

        # All sources should be processed
        assert result.sources_processed == 10
        assert len(processed_ids) == 10
        assert set(processed_ids) == set(source_ids)

    @pytest.mark.asyncio
    async def test_process_batch_handles_failures_gracefully(
        self, pipeline_service, mock_dependencies
    ):
        """Test that one source failure doesn't stop others from processing."""
        source_ids = [uuid4() for _ in range(5)]
        project_id = uuid4()
        failing_id = source_ids[2]  # Middle one fails

        successful_count = 0
        lock = asyncio.Lock()

        async def conditional_get(source_id):
            if source_id == failing_id:
                return None  # Simulates source not found
            mock_source = MagicMock()
            mock_source.content = "Test content"
            mock_source.source_group = "test_company"
            return mock_source

        mock_dependencies["source_repo"].get = conditional_get

        mock_project = MagicMock()
        mock_project.entity_types = []
        mock_dependencies["project_repo"].get = AsyncMock(return_value=mock_project)

        async def mock_extract(*args, **kwargs):
            nonlocal successful_count
            async with lock:
                successful_count += 1
            result = MagicMock()
            result.facts = []
            return result

        mock_dependencies["orchestrator"].extract = mock_extract

        result = await pipeline_service.process_batch(
            source_ids=source_ids,
            project_id=project_id,
        )

        # Should still process all sources
        assert result.sources_processed == 5
        # 4 successful, 1 failed (not found)
        assert result.sources_failed == 1


class TestSchemaExtractionPipelineParallel:
    """Test SchemaExtractionPipeline parallel processing."""

    @pytest.mark.asyncio
    async def test_extract_project_runs_sources_concurrently(self):
        """Test that extract_project processes multiple sources concurrently."""
        from src.services.extraction.pipeline import SchemaExtractionPipeline

        mock_orchestrator = AsyncMock()
        mock_db = MagicMock()

        pipeline = SchemaExtractionPipeline(
            orchestrator=mock_orchestrator,
            db_session=mock_db,
        )

        # Create mock sources
        sources = []
        for i in range(10):
            source = MagicMock()
            source.id = uuid4()
            source.project_id = uuid4()
            source.content = f"Content {i}"
            source.source_group = "test_company"
            sources.append(source)

        # Mock the SQLAlchemy query chain correctly
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query  # Allow chaining
        mock_query.all.return_value = sources
        mock_db.query.return_value = mock_query

        # Track concurrency
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def tracking_extract(*args, **kwargs):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)

            await asyncio.sleep(0.05)

            async with lock:
                current_concurrent -= 1

            return [], None  # Return tuple (empty extractions list, no classification)

        mock_orchestrator.extract_all_groups = tracking_extract

        project_id = uuid4()
        await pipeline.extract_project(project_id=project_id)

        # Should see concurrent execution (current limit is 4)
        # After fix, should allow higher concurrency
        assert max_concurrent > 1, (
            f"Expected concurrent processing but max_concurrent was {max_concurrent}"
        )
