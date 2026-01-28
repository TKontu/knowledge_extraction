"""Tests for EmbeddingRecoveryService."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.extraction.embedding_recovery import (
    EmbeddingRecoveryService,
    RecoveryResult,
    RecoverySummary,
)


@pytest.fixture
def mock_db():
    """Mock database session."""
    return Mock()


@pytest.fixture
def mock_embedding_service():
    """Mock EmbeddingService."""
    return AsyncMock()


@pytest.fixture
def mock_qdrant_repo():
    """Mock QdrantRepository."""
    return AsyncMock()


@pytest.fixture
def mock_extraction_repo():
    """Mock ExtractionRepository."""
    return AsyncMock()


@pytest.fixture
def recovery_service(
    mock_db, mock_embedding_service, mock_qdrant_repo, mock_extraction_repo
):
    """Create EmbeddingRecoveryService with mocked dependencies."""
    return EmbeddingRecoveryService(
        db=mock_db,
        embedding_service=mock_embedding_service,
        qdrant_repo=mock_qdrant_repo,
        extraction_repo=mock_extraction_repo,
        batch_size=50,
    )


class TestFindOrphanedExtractions:
    """Tests for find_orphaned_extractions method."""

    async def test_find_orphaned_extractions_returns_null_embedding_id(
        self, recovery_service, mock_extraction_repo
    ):
        """Should find extractions with embedding_id IS NULL."""
        project_id = uuid4()

        # Create mock extractions (orphaned)
        orphan1 = Mock()
        orphan1.id = uuid4()
        orphan1.embedding_id = None
        orphan1.data = {"fact_text": "Orphaned fact 1"}

        orphan2 = Mock()
        orphan2.id = uuid4()
        orphan2.embedding_id = None
        orphan2.data = {"fact_text": "Orphaned fact 2"}

        mock_extraction_repo.find_orphaned.return_value = [orphan1, orphan2]

        result = await recovery_service.find_orphaned_extractions(
            project_id=project_id, limit=100
        )

        # Should call repository method with correct params
        mock_extraction_repo.find_orphaned.assert_called_once_with(
            project_id=project_id, limit=100
        )

        # Should return the orphaned extractions
        assert len(result) == 2
        assert result[0].embedding_id is None
        assert result[1].embedding_id is None

    async def test_find_orphaned_excludes_embedded(
        self, recovery_service, mock_extraction_repo
    ):
        """Should not return extractions with embedding_id set."""
        project_id = uuid4()

        # Mock repo returns only orphaned (repo should filter)
        orphaned = Mock()
        orphaned.id = uuid4()
        orphaned.embedding_id = None

        mock_extraction_repo.find_orphaned.return_value = [orphaned]

        result = await recovery_service.find_orphaned_extractions(
            project_id=project_id
        )

        # Should only return extraction without embedding_id
        assert len(result) == 1
        assert result[0].embedding_id is None


class TestRecoverBatch:
    """Tests for recover_batch method."""

    async def test_recover_batch_creates_embeddings(
        self, recovery_service, mock_embedding_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should create embeddings for orphaned extractions."""
        project_id = uuid4()

        # Create orphaned extractions
        extraction1 = Mock()
        extraction1.id = uuid4()
        extraction1.project_id = project_id
        extraction1.source_group = "company1"
        extraction1.data = {"fact_text": "Test fact 1"}
        extraction1.embedding_id = None

        extraction2 = Mock()
        extraction2.id = uuid4()
        extraction2.project_id = project_id
        extraction2.source_group = "company1"
        extraction2.data = {"fact_text": "Test fact 2"}
        extraction2.embedding_id = None

        # Mock embedding service returns embeddings
        mock_embedding_service.embed_batch.return_value = [
            [0.1] * 768,
            [0.2] * 768,
        ]

        # Mock Qdrant upsert batch succeeds
        mock_qdrant_repo.upsert_batch.return_value = None

        # Mock update_embedding_ids_batch returns count
        mock_extraction_repo.update_embedding_ids_batch.return_value = 2

        result = await recovery_service.recover_batch([extraction1, extraction2])

        # Should call embed_batch with fact texts
        mock_embedding_service.embed_batch.assert_called_once_with(
            ["Test fact 1", "Test fact 2"]
        )

        # Should call upsert_batch with point data
        mock_qdrant_repo.upsert_batch.assert_called_once()

        # Should return success result
        assert isinstance(result, RecoveryResult)
        assert result.succeeded == 2
        assert result.failed == 0

    async def test_recover_batch_updates_embedding_id(
        self, recovery_service, mock_embedding_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should set embedding_id after successful recovery."""
        extraction = Mock()
        extraction.id = uuid4()
        extraction.project_id = uuid4()
        extraction.source_group = "company1"
        extraction.data = {"fact_text": "Test fact"}
        extraction.embedding_id = None

        # Mock successful embedding + upsert
        mock_embedding_service.embed_batch.return_value = [[0.1] * 768]
        mock_qdrant_repo.upsert_batch.return_value = None

        await recovery_service.recover_batch([extraction])

        # Should update embedding_ids in batch
        mock_extraction_repo.update_embedding_ids_batch.assert_called_once()
        call_args = mock_extraction_repo.update_embedding_ids_batch.call_args[0][0]
        assert extraction.id in call_args

    async def test_recover_batch_handles_partial_failure(
        self, recovery_service, mock_embedding_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should handle case where some extractions succeed and some fail."""
        extraction1 = Mock()
        extraction1.id = uuid4()
        extraction1.project_id = uuid4()
        extraction1.source_group = "company1"
        extraction1.data = {"fact_text": "Test fact 1"}
        extraction1.embedding_id = None

        extraction2 = Mock()
        extraction2.id = uuid4()
        extraction2.project_id = uuid4()
        extraction2.source_group = "company1"
        extraction2.data = {"fact_text": "Test fact 2"}
        extraction2.embedding_id = None

        extraction3 = Mock()
        extraction3.id = uuid4()
        extraction3.project_id = uuid4()
        extraction3.source_group = "company1"
        extraction3.data = {}  # Missing fact_text - will fail
        extraction3.embedding_id = None

        # Mock embedding service - will only embed valid texts
        mock_embedding_service.embed_batch.return_value = [
            [0.1] * 768,
            [0.2] * 768,
        ]

        mock_qdrant_repo.upsert_batch.return_value = None

        # Mock update returns count of 2 (two successful)
        mock_extraction_repo.update_embedding_ids_batch.return_value = 2

        result = await recovery_service.recover_batch(
            [extraction1, extraction2, extraction3]
        )

        # Should report partial success
        assert result.succeeded >= 2
        assert result.failed >= 1


class TestRecoveryWithProjectFilter:
    """Tests for project_id filtering."""

    async def test_recovery_respects_project_filter(
        self, recovery_service, mock_extraction_repo
    ):
        """Should only recover extractions for specified project."""
        project_id = uuid4()

        mock_extraction_repo.find_orphaned.return_value = []

        await recovery_service.find_orphaned_extractions(
            project_id=project_id, limit=100
        )

        # Should pass project_id filter to repository
        mock_extraction_repo.find_orphaned.assert_called_once_with(
            project_id=project_id, limit=100
        )

    async def test_recovery_respects_limit(
        self, recovery_service, mock_extraction_repo
    ):
        """Should not exceed specified limit."""
        # Create 200 mock orphans
        orphans = []
        for i in range(200):
            orphan = Mock()
            orphan.id = uuid4()
            orphan.embedding_id = None
            orphan.data = {"fact_text": f"Fact {i}"}
            orphans.append(orphan)

        mock_extraction_repo.find_orphaned.return_value = orphans[:50]  # Repo enforces limit

        result = await recovery_service.find_orphaned_extractions(limit=50)

        # Should call with limit
        mock_extraction_repo.find_orphaned.assert_called_once_with(
            project_id=None, limit=50
        )

        # Should respect limit
        assert len(result) <= 50


class TestRunRecovery:
    """Tests for run_recovery method."""

    async def test_run_recovery_processes_multiple_batches(
        self, recovery_service, mock_extraction_repo, mock_embedding_service, mock_qdrant_repo
    ):
        """Should process multiple batches until complete."""
        # First batch - 50 orphans
        batch1 = []
        for i in range(50):
            extraction = Mock()
            extraction.id = uuid4()
            extraction.project_id = uuid4()
            extraction.source_group = "company1"
            extraction.data = {"fact_text": f"Fact {i}"}
            extraction.embedding_id = None
            batch1.append(extraction)

        # Second batch - 30 orphans
        batch2 = []
        for i in range(30):
            extraction = Mock()
            extraction.id = uuid4()
            extraction.project_id = uuid4()
            extraction.source_group = "company1"
            extraction.data = {"fact_text": f"Fact {i+50}"}
            extraction.embedding_id = None
            batch2.append(extraction)

        # Mock repo returns batch1, then batch2, then empty
        mock_extraction_repo.find_orphaned.side_effect = [batch1, batch2, []]

        # Mock successful embeddings
        mock_embedding_service.embed_batch.return_value = [[0.1] * 768] * 50
        mock_qdrant_repo.upsert_batch.return_value = None

        # Mock update returns count of processed items
        mock_extraction_repo.update_embedding_ids_batch.return_value = 50

        result = await recovery_service.run_recovery(max_batches=10)

        # Should call find_orphaned multiple times
        assert mock_extraction_repo.find_orphaned.call_count >= 2

        # Should return summary
        assert isinstance(result, RecoverySummary)
        assert result.total_processed >= 80
