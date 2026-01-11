"""Tests for ExtractionPipelineService."""

import pytest
from uuid import UUID, uuid4
from unittest.mock import AsyncMock, Mock

from src.services.extraction.pipeline import (
    ExtractionPipelineService,
    PipelineResult,
    BatchPipelineResult,
)


@pytest.fixture
def mock_orchestrator():
    """Mock ExtractionOrchestrator."""
    return AsyncMock()


@pytest.fixture
def mock_deduplicator():
    """Mock ExtractionDeduplicator."""
    return AsyncMock()


@pytest.fixture
def mock_entity_extractor():
    """Mock EntityExtractor."""
    return AsyncMock()


@pytest.fixture
def mock_extraction_repo():
    """Mock ExtractionRepository."""
    return AsyncMock()


@pytest.fixture
def mock_source_repo():
    """Mock SourceRepository."""
    return AsyncMock()


@pytest.fixture
def mock_project_repo():
    """Mock ProjectRepository."""
    return AsyncMock()


@pytest.fixture
def mock_qdrant_repo():
    """Mock QdrantRepository."""
    return AsyncMock()


@pytest.fixture
def mock_embedding_service():
    """Mock EmbeddingService."""
    return AsyncMock()


@pytest.fixture
def pipeline_service(
    mock_orchestrator,
    mock_deduplicator,
    mock_entity_extractor,
    mock_extraction_repo,
    mock_source_repo,
    mock_project_repo,
    mock_qdrant_repo,
    mock_embedding_service,
):
    """Create ExtractionPipelineService with all mocked dependencies."""
    # Mock project with entity types
    mock_project = Mock()
    mock_project.entity_types = [
        {"name": "plan", "description": "Subscription plan"},
        {"name": "feature", "description": "Product feature"},
    ]
    mock_project_repo.get.return_value = mock_project

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


class TestExtractionPipelineServiceInit:
    """Tests for ExtractionPipelineService initialization."""

    def test_init_accepts_all_dependencies(self, pipeline_service):
        """Pipeline service accepts all required dependencies."""
        assert pipeline_service is not None
        assert hasattr(pipeline_service, "_orchestrator")
        assert hasattr(pipeline_service, "_deduplicator")
        assert hasattr(pipeline_service, "_entity_extractor")
        assert hasattr(pipeline_service, "_extraction_repo")
        assert hasattr(pipeline_service, "_source_repo")
        assert hasattr(pipeline_service, "_project_repo")
        assert hasattr(pipeline_service, "_qdrant_repo")
        assert hasattr(pipeline_service, "_embedding_service")


class TestProcessSource:
    """Tests for process_source method."""

    async def test_process_source_returns_pipeline_result(self, pipeline_service):
        """process_source returns a PipelineResult."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source with content
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator returns no facts
        mock_result = Mock()
        mock_result.facts = []
        pipeline_service._orchestrator.extract.return_value = mock_result

        result = await pipeline_service.process_source(source_id, project_id)

        assert isinstance(result, PipelineResult)
        assert result.source_id == source_id

    async def test_process_source_extracts_facts(self, pipeline_service):
        """Mocked orchestrator returns facts, they get stored."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source
        mock_source = Mock()
        mock_source.content = "Test content with facts"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator returns 2 facts
        mock_fact1 = Mock()
        mock_fact1.fact = "Fact 1 text"
        mock_fact1.category = "category1"
        mock_fact1.confidence = 0.95

        mock_fact2 = Mock()
        mock_fact2.fact = "Fact 2 text"
        mock_fact2.category = "category2"
        mock_fact2.confidence = 0.85

        mock_result = Mock()
        mock_result.facts = [mock_fact1, mock_fact2]
        pipeline_service._orchestrator.extract.return_value = mock_result

        # Mock deduplicator returns not duplicate
        mock_dedup = Mock()
        mock_dedup.is_duplicate = False
        pipeline_service._deduplicator.check_duplicate.return_value = mock_dedup

        # Mock extraction creation
        mock_extraction = Mock()
        mock_extraction.id = uuid4()
        pipeline_service._extraction_repo.create.return_value = mock_extraction

        # Mock embedding
        pipeline_service._embedding_service.embed.return_value = [0.1] * 768

        result = await pipeline_service.process_source(source_id, project_id)

        assert result.extractions_created == 2
        assert pipeline_service._extraction_repo.create.call_count == 2

    async def test_process_source_deduplicates(self, pipeline_service):
        """Duplicate facts are skipped."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator returns 2 facts
        mock_fact1 = Mock()
        mock_fact1.fact = "Fact 1"
        mock_fact1.category = "cat1"
        mock_fact1.confidence = 0.9

        mock_fact2 = Mock()
        mock_fact2.fact = "Fact 2"
        mock_fact2.category = "cat2"
        mock_fact2.confidence = 0.8

        mock_result = Mock()
        mock_result.facts = [mock_fact1, mock_fact2]
        pipeline_service._orchestrator.extract.return_value = mock_result

        # Mock deduplicator: first is duplicate, second is not
        def dedup_side_effect(*args, **kwargs):
            if kwargs.get("text_content") == "Fact 1":
                m = Mock()
                m.is_duplicate = True
                return m
            else:
                m = Mock()
                m.is_duplicate = False
                return m

        pipeline_service._deduplicator.check_duplicate.side_effect = dedup_side_effect

        # Mock extraction creation
        mock_extraction = Mock()
        mock_extraction.id = uuid4()
        pipeline_service._extraction_repo.create.return_value = mock_extraction

        # Mock embedding
        pipeline_service._embedding_service.embed.return_value = [0.1] * 768

        result = await pipeline_service.process_source(source_id, project_id)

        assert result.extractions_deduplicated == 1
        assert result.extractions_created == 1

    async def test_process_source_stores_embeddings(self, pipeline_service):
        """Embeddings stored in Qdrant."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator returns 1 fact
        mock_fact = Mock()
        mock_fact.fact = "Test fact"
        mock_fact.category = "test_cat"
        mock_fact.confidence = 0.9

        mock_result = Mock()
        mock_result.facts = [mock_fact]
        pipeline_service._orchestrator.extract.return_value = mock_result

        # Mock deduplicator
        mock_dedup = Mock()
        mock_dedup.is_duplicate = False
        pipeline_service._deduplicator.check_duplicate.return_value = mock_dedup

        # Mock extraction creation
        mock_extraction = Mock()
        mock_extraction.id = uuid4()
        pipeline_service._extraction_repo.create.return_value = mock_extraction

        # Mock embedding
        test_embedding = [0.1] * 768
        pipeline_service._embedding_service.embed.return_value = test_embedding

        result = await pipeline_service.process_source(source_id, project_id)

        # Verify embedding was generated
        pipeline_service._embedding_service.embed.assert_called_once_with("Test fact")

        # Verify embedding was stored in Qdrant
        pipeline_service._qdrant_repo.upsert.assert_called_once()

    async def test_process_source_extracts_entities(self, pipeline_service):
        """EntityExtractor called for each extraction."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator returns 1 fact
        mock_fact = Mock()
        mock_fact.fact = "Test fact"
        mock_fact.category = "test_cat"
        mock_fact.confidence = 0.9

        mock_result = Mock()
        mock_result.facts = [mock_fact]
        pipeline_service._orchestrator.extract.return_value = mock_result

        # Mock deduplicator
        mock_dedup = Mock()
        mock_dedup.is_duplicate = False
        pipeline_service._deduplicator.check_duplicate.return_value = mock_dedup

        # Mock extraction creation
        mock_extraction = Mock()
        mock_extraction.id = uuid4()
        pipeline_service._extraction_repo.create.return_value = mock_extraction

        # Mock embedding
        pipeline_service._embedding_service.embed.return_value = [0.1] * 768

        # Mock entity extractor returns entities
        mock_entities = [Mock(), Mock()]
        pipeline_service._entity_extractor.extract.return_value = mock_entities

        result = await pipeline_service.process_source(source_id, project_id)

        # Verify entity extraction was called
        pipeline_service._entity_extractor.extract.assert_called_once()
        assert result.entities_extracted == 2

    async def test_process_source_updates_source_status(self, pipeline_service):
        """Source marked as 'extracted'."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator
        mock_result = Mock()
        mock_result.facts = []
        pipeline_service._orchestrator.extract.return_value = mock_result

        result = await pipeline_service.process_source(source_id, project_id)

        # Verify status update was called
        pipeline_service._source_repo.update_status.assert_called_once_with(
            source_id, "extracted"
        )

    async def test_process_source_handles_empty_source(self, pipeline_service):
        """Returns gracefully for empty content."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source with no content
        mock_source = Mock()
        mock_source.content = None
        pipeline_service._source_repo.get.return_value = mock_source

        result = await pipeline_service.process_source(source_id, project_id)

        assert result.extractions_created == 0
        assert "Source not found or empty" in result.errors

    async def test_process_source_handles_errors(self, pipeline_service):
        """Errors captured in result, processing continues."""
        source_id = uuid4()
        project_id = uuid4()

        # Mock source
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator returns 2 facts
        mock_fact1 = Mock()
        mock_fact1.fact = "Fact 1"
        mock_fact1.category = "cat1"
        mock_fact1.confidence = 0.9

        mock_fact2 = Mock()
        mock_fact2.fact = "Fact 2"
        mock_fact2.category = "cat2"
        mock_fact2.confidence = 0.8

        mock_result = Mock()
        mock_result.facts = [mock_fact1, mock_fact2]
        pipeline_service._orchestrator.extract.return_value = mock_result

        # Mock deduplicator: first raises exception, second succeeds
        def dedup_side_effect(*args, **kwargs):
            if kwargs.get("text_content") == "Fact 1":
                raise Exception("Deduplication error")
            else:
                m = Mock()
                m.is_duplicate = False
                return m

        pipeline_service._deduplicator.check_duplicate.side_effect = dedup_side_effect

        # Mock extraction creation
        mock_extraction = Mock()
        mock_extraction.id = uuid4()
        pipeline_service._extraction_repo.create.return_value = mock_extraction

        # Mock embedding
        pipeline_service._embedding_service.embed.return_value = [0.1] * 768

        result = await pipeline_service.process_source(source_id, project_id)

        # Should have 1 error but still process the second fact
        assert len(result.errors) > 0
        assert result.extractions_created == 1


class TestProcessBatch:
    """Tests for process_batch method."""

    async def test_process_batch_returns_batch_result(self, pipeline_service):
        """process_batch returns a BatchPipelineResult."""
        source_ids = [uuid4(), uuid4()]
        project_id = uuid4()

        # Mock sources
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator
        mock_result = Mock()
        mock_result.facts = []
        pipeline_service._orchestrator.extract.return_value = mock_result

        result = await pipeline_service.process_batch(source_ids, project_id)

        assert isinstance(result, BatchPipelineResult)
        assert result.sources_processed == 2

    async def test_process_batch_processes_all_sources(self, pipeline_service):
        """All sources in batch are processed."""
        source_ids = [uuid4(), uuid4(), uuid4()]
        project_id = uuid4()

        # Mock sources
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator
        mock_result = Mock()
        mock_result.facts = []
        pipeline_service._orchestrator.extract.return_value = mock_result

        result = await pipeline_service.process_batch(source_ids, project_id)

        assert result.sources_processed == 3
        assert len(result.results) == 3
        assert pipeline_service._source_repo.get.call_count == 3

    async def test_process_batch_aggregates_results(self, pipeline_service):
        """Batch results are aggregated correctly."""
        source_ids = [uuid4(), uuid4()]
        project_id = uuid4()

        # Mock sources
        mock_source = Mock()
        mock_source.content = "Test content"
        mock_source.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source

        # Mock orchestrator returns 2 facts for each source
        mock_fact1 = Mock()
        mock_fact1.fact = "Fact 1"
        mock_fact1.category = "cat1"
        mock_fact1.confidence = 0.9

        mock_fact2 = Mock()
        mock_fact2.fact = "Fact 2"
        mock_fact2.category = "cat2"
        mock_fact2.confidence = 0.8

        mock_result = Mock()
        mock_result.facts = [mock_fact1, mock_fact2]
        pipeline_service._orchestrator.extract.return_value = mock_result

        # Mock deduplicator - no duplicates
        mock_dedup = Mock()
        mock_dedup.is_duplicate = False
        pipeline_service._deduplicator.check_duplicate.return_value = mock_dedup

        # Mock extraction creation
        mock_extraction = Mock()
        mock_extraction.id = uuid4()
        pipeline_service._extraction_repo.create.return_value = mock_extraction

        # Mock embedding
        pipeline_service._embedding_service.embed.return_value = [0.1] * 768

        result = await pipeline_service.process_batch(source_ids, project_id)

        # 2 sources × 2 facts = 4 total extractions
        assert result.total_extractions == 4
        assert result.sources_failed == 0

    async def test_process_batch_continues_on_failure(self, pipeline_service):
        """Batch continues processing even if individual sources fail."""
        source_ids = [uuid4(), uuid4(), uuid4()]
        project_id = uuid4()

        # Mock source_repo: first returns None (fails), others succeed
        def get_side_effect(source_id):
            if source_id == source_ids[0]:
                return None
            else:
                mock_source = Mock()
                mock_source.content = "Test content"
                mock_source.source_group = "test-group"
                return mock_source

        pipeline_service._source_repo.get.side_effect = get_side_effect

        # Mock orchestrator
        mock_result = Mock()
        mock_result.facts = []
        pipeline_service._orchestrator.extract.return_value = mock_result

        result = await pipeline_service.process_batch(source_ids, project_id)

        # All 3 sources processed, but 1 failed
        assert result.sources_processed == 3
        assert result.sources_failed == 1
        assert len(result.results) == 3


class TestProcessProjectPending:
    """Tests for process_project_pending method."""

    async def test_process_project_pending_finds_pending_sources(
        self, pipeline_service
    ):
        """Finds all pending sources for a project."""
        project_id = uuid4()
        pending_source_ids = [uuid4(), uuid4()]

        # Mock source_repo to return pending sources
        pending_sources = []
        for source_id in pending_source_ids:
            mock_source = Mock()
            mock_source.id = source_id
            mock_source.status = "pending"
            pending_sources.append(mock_source)

        pipeline_service._source_repo.get_by_project_and_status.return_value = (
            pending_sources
        )

        # Mock source retrieval for processing
        mock_source_with_content = Mock()
        mock_source_with_content.content = "Test content"
        mock_source_with_content.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source_with_content

        # Mock orchestrator
        mock_result = Mock()
        mock_result.facts = []
        pipeline_service._orchestrator.extract.return_value = mock_result

        result = await pipeline_service.process_project_pending(project_id)

        # Verify the method called get_by_project_and_status
        pipeline_service._source_repo.get_by_project_and_status.assert_called_once_with(
            project_id, "pending"
        )

        # Verify it processed both sources
        assert result.sources_processed == 2

    async def test_process_project_pending_processes_all(self, pipeline_service):
        """Processes all pending sources found."""
        project_id = uuid4()
        pending_source_ids = [uuid4(), uuid4(), uuid4()]

        # Mock source_repo to return pending sources
        pending_sources = []
        for source_id in pending_source_ids:
            mock_source = Mock()
            mock_source.id = source_id
            mock_source.status = "pending"
            pending_sources.append(mock_source)

        pipeline_service._source_repo.get_by_project_and_status.return_value = (
            pending_sources
        )

        # Mock source retrieval for processing
        mock_source_with_content = Mock()
        mock_source_with_content.content = "Test content"
        mock_source_with_content.source_group = "test-group"
        pipeline_service._source_repo.get.return_value = mock_source_with_content

        # Mock orchestrator
        mock_result = Mock()
        mock_result.facts = []
        pipeline_service._orchestrator.extract.return_value = mock_result

        result = await pipeline_service.process_project_pending(project_id)

        # Verify all 3 sources were processed
        assert result.sources_processed == 3
        assert len(result.results) == 3
