"""Tests for embedding recovery service."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from orm_models import Extraction
from services.extraction.embedding_recovery import (
    EmbeddingRecoveryService,
)
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository
from services.storage.repositories.extraction import ExtractionRepository


@pytest.fixture
def mock_db():
    """Mock database session."""
    return MagicMock()


@pytest.fixture
def mock_embedding_service():
    """Mock embedding service."""
    service = AsyncMock(spec=EmbeddingService)
    service.embed_batch = AsyncMock(return_value=[[0.1] * 1024, [0.2] * 1024])
    return service


@pytest.fixture
def mock_qdrant_repo():
    """Mock Qdrant repository."""
    repo = AsyncMock(spec=QdrantRepository)
    repo.upsert_batch = AsyncMock(return_value=["id1", "id2"])
    return repo


@pytest.fixture
def mock_extraction_repo():
    """Mock extraction repository."""
    repo = MagicMock(spec=ExtractionRepository)
    repo.find_orphaned = MagicMock(return_value=[])
    repo.update_embedding_ids_batch = MagicMock(return_value=2)
    return repo


@pytest.fixture
def recovery_service(mock_db, mock_embedding_service, mock_qdrant_repo, mock_extraction_repo):
    """Create recovery service with mocks."""
    return EmbeddingRecoveryService(
        db=mock_db,
        embedding_service=mock_embedding_service,
        qdrant_repo=mock_qdrant_repo,
        extraction_repo=mock_extraction_repo,
        batch_size=50,
    )


class TestFindOrphanedExtractions:
    """Tests for finding orphaned extractions."""

    async def test_find_orphaned_extractions_returns_null_embedding_id(
        self, recovery_service, mock_extraction_repo
    ):
        """Should find extractions with embedding_id IS NULL."""
        # Arrange
        project_id = uuid4()
        orphaned = [
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact 1"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            ),
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact 2"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            ),
        ]
        mock_extraction_repo.find_orphaned = MagicMock(return_value=orphaned)

        # Act
        result = recovery_service.find_orphaned_extractions(
            project_id=project_id, limit=100
        )

        # Assert
        assert len(result) == 2
        assert all(e.embedding_id is None for e in result)
        mock_extraction_repo.find_orphaned.assert_called_once_with(
            project_id=project_id, limit=100
        )

    async def test_find_orphaned_excludes_embedded(
        self, recovery_service, mock_extraction_repo
    ):
        """Should not return extractions with embedding_id set."""
        # Arrange
        project_id = uuid4()
        orphaned_only = [
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            ),
        ]
        mock_extraction_repo.find_orphaned = MagicMock(return_value=orphaned_only)

        # Act
        result = recovery_service.find_orphaned_extractions(
            project_id=project_id, limit=100
        )

        # Assert
        assert len(result) == 1
        assert result[0].embedding_id is None

    async def test_recovery_respects_project_filter(
        self, recovery_service, mock_extraction_repo
    ):
        """Should filter by project_id when provided."""
        # Arrange
        project_id = uuid4()

        # Act
        recovery_service.find_orphaned_extractions(
            project_id=project_id, limit=50
        )

        # Assert
        mock_extraction_repo.find_orphaned.assert_called_once_with(
            project_id=project_id, limit=50
        )

    async def test_recovery_respects_limit(
        self, recovery_service, mock_extraction_repo
    ):
        """Should respect the limit parameter."""
        # Arrange
        project_id = uuid4()

        # Act
        recovery_service.find_orphaned_extractions(
            project_id=project_id, limit=25
        )

        # Assert
        mock_extraction_repo.find_orphaned.assert_called_once_with(
            project_id=project_id, limit=25
        )


class TestRecoverBatch:
    """Tests for recovering batches of extractions."""

    async def test_recover_batch_creates_embeddings(
        self, recovery_service, mock_embedding_service, mock_qdrant_repo
    ):
        """Should create embeddings for orphaned extractions."""
        # Arrange
        project_id = uuid4()
        extractions = [
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact 1"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            ),
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact 2"},
                extraction_type="feature",
                source_group="test",
                embedding_id=None,
            ),
        ]
        mock_embedding_service.embed_batch = AsyncMock(
            return_value=[[0.1] * 1024, [0.2] * 1024]
        )

        # Act
        result = await recovery_service.recover_batch(extractions)

        # Assert
        assert result.succeeded == 2
        assert result.failed == 0
        mock_embedding_service.embed_batch.assert_called_once()
        call_args = mock_embedding_service.embed_batch.call_args[0][0]
        assert call_args == ["Test fact 1", "Test fact 2"]

    async def test_recover_batch_updates_embedding_id(
        self, recovery_service, mock_embedding_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should update embedding_id after successful recovery."""
        # Arrange
        project_id = uuid4()
        extraction_id_1 = uuid4()
        extraction_id_2 = uuid4()
        extractions = [
            Extraction(
                id=extraction_id_1,
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact 1"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            ),
            Extraction(
                id=extraction_id_2,
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact 2"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            ),
        ]
        mock_embedding_service.embed_batch = AsyncMock(
            return_value=[[0.1] * 1024, [0.2] * 1024]
        )
        mock_qdrant_repo.upsert_batch = AsyncMock(
            return_value=[str(extraction_id_1), str(extraction_id_2)]
        )

        # Act
        result = await recovery_service.recover_batch(extractions)

        # Assert
        assert result.succeeded == 2
        mock_extraction_repo.update_embedding_ids_batch.assert_called_once()
        call_args = mock_extraction_repo.update_embedding_ids_batch.call_args[0][0]
        assert call_args == [extraction_id_1, extraction_id_2]

    async def test_recover_batch_handles_partial_failure(
        self, recovery_service, mock_embedding_service, mock_extraction_repo
    ):
        """Should handle partial failures gracefully."""
        # Arrange
        project_id = uuid4()
        extractions = [
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": "Test fact 1"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            ),
        ]
        # Simulate embedding failure
        mock_embedding_service.embed_batch = AsyncMock(
            side_effect=Exception("Embedding API error")
        )

        # Act
        result = await recovery_service.recover_batch(extractions)

        # Assert
        assert result.succeeded == 0
        assert result.failed == 1
        assert len(result.errors) == 1
        # Should not update embedding IDs on failure
        mock_extraction_repo.update_embedding_ids_batch.assert_not_called()


class TestRunRecovery:
    """Tests for the full recovery process."""

    async def test_run_recovery_processes_multiple_batches(
        self, recovery_service, mock_extraction_repo, mock_embedding_service, mock_qdrant_repo
    ):
        """Should process multiple batches until no orphans remain."""
        # Arrange
        project_id = uuid4()
        batch_1 = [
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": f"Test fact {i}"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            )
            for i in range(50)
        ]
        batch_2 = [
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": f"Test fact {i+50}"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            )
            for i in range(30)
        ]
        # First call returns batch_1, second call returns batch_2, third returns empty
        mock_extraction_repo.find_orphaned = MagicMock(
            side_effect=[batch_1, batch_2, []]
        )
        # Return correct number of embeddings for each batch
        mock_embedding_service.embed_batch = AsyncMock(
            side_effect=[
                [[0.1] * 1024] * 50,  # First batch: 50 embeddings
                [[0.2] * 1024] * 30,  # Second batch: 30 embeddings
            ]
        )

        # Act
        summary = await recovery_service.run_recovery(
            project_id=project_id, max_batches=10
        )

        # Assert
        assert summary.total_found == 80  # 50 + 30
        assert summary.total_recovered == 80
        assert summary.batches_processed == 2
        assert mock_extraction_repo.find_orphaned.call_count == 3

    async def test_run_recovery_respects_max_batches(
        self, recovery_service, mock_extraction_repo, mock_embedding_service
    ):
        """Should stop after max_batches even if orphans remain."""
        # Arrange
        project_id = uuid4()
        batch = [
            Extraction(
                id=uuid4(),
                project_id=project_id,
                source_id=uuid4(),
                data={"fact_text": f"Test fact {i}"},
                extraction_type="technical",
                source_group="test",
                embedding_id=None,
            )
            for i in range(50)
        ]
        # Always return a full batch (simulating many orphans)
        mock_extraction_repo.find_orphaned = MagicMock(return_value=batch)
        mock_embedding_service.embed_batch = AsyncMock(
            return_value=[[0.1] * 1024] * 50
        )

        # Act
        summary = await recovery_service.run_recovery(
            project_id=project_id, max_batches=3
        )

        # Assert
        assert summary.batches_processed == 3
        assert mock_extraction_repo.find_orphaned.call_count == 3
