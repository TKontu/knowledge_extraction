"""Tests for Qdrant repository async wrappers."""

from unittest.mock import Mock, patch
from uuid import uuid4

import pytest

from src.services.storage.qdrant.repository import (
    EmbeddingItem,
    QdrantRepository,
)


@pytest.fixture
def mock_qdrant_client():
    """Create a mock Qdrant client."""
    return Mock()


@pytest.fixture
def qdrant_repo(mock_qdrant_client):
    """Create a QdrantRepository with mock client."""
    return QdrantRepository(mock_qdrant_client)


class TestQdrantAsyncWrappers:
    """Tests for async executor wrappers in Qdrant operations."""

    @pytest.mark.asyncio
    async def test_upsert_uses_executor(self, qdrant_repo, mock_qdrant_client):
        """Upsert operation runs in executor."""
        extraction_id = uuid4()
        embedding = [0.1] * 1024
        payload = {"test": "data"}

        # Call upsert
        result = await qdrant_repo.upsert(extraction_id, embedding, payload)

        # Should return point ID
        assert result == str(extraction_id)

        # Mock client should have been called
        mock_qdrant_client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_batch_uses_executor(self, qdrant_repo, mock_qdrant_client):
        """Batch upsert runs in executor."""
        items = [
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.1] * 1024,
                payload={"test": "data1"},
            ),
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.2] * 1024,
                payload={"test": "data2"},
            ),
        ]

        # Call batch upsert
        result = await qdrant_repo.upsert_batch(items)

        # Should return list of IDs
        assert len(result) == 2
        assert result[0] == str(items[0].extraction_id)
        assert result[1] == str(items[1].extraction_id)

        # Mock client should have been called
        mock_qdrant_client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_upsert_batch_empty_list(self, qdrant_repo, mock_qdrant_client):
        """Empty batch returns empty list without calling client."""
        result = await qdrant_repo.upsert_batch([])

        assert result == []
        mock_qdrant_client.upsert.assert_not_called()

    @pytest.mark.asyncio
    async def test_search_uses_executor(self, qdrant_repo, mock_qdrant_client):
        """Search operation runs in executor."""
        query_embedding = [0.1] * 1024

        # Mock search results
        mock_result = Mock()
        mock_result.id = str(uuid4())
        mock_result.score = 0.95
        mock_result.payload = {"test": "data"}
        mock_qdrant_client.search.return_value = [mock_result]

        # Call search
        results = await qdrant_repo.search(query_embedding, limit=10)

        # Should return search results
        assert len(results) == 1
        assert results[0].score == 0.95

        # Mock client should have been called
        mock_qdrant_client.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_uses_executor(self, qdrant_repo, mock_qdrant_client):
        """Delete operation runs in executor."""
        extraction_id = uuid4()

        # Call delete
        result = await qdrant_repo.delete(extraction_id)

        # Should return True
        assert result is True

        # Mock client should have been called
        mock_qdrant_client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_collection_uses_executor(self, qdrant_repo, mock_qdrant_client):
        """Init collection runs in executor."""
        # Mock collection list (collection doesn't exist)
        mock_collections = Mock()
        mock_collections.collections = []
        mock_qdrant_client.get_collections.return_value = mock_collections

        # Call init
        await qdrant_repo.init_collection()

        # Should have checked collections and created one
        mock_qdrant_client.get_collections.assert_called_once()
        mock_qdrant_client.create_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_collection_skips_if_exists(
        self, qdrant_repo, mock_qdrant_client
    ):
        """Init collection skips creation if collection exists."""
        # Mock collection list (collection exists)
        mock_collection = Mock()
        mock_collection.name = "extractions"
        mock_collections = Mock()
        mock_collections.collections = [mock_collection]
        mock_qdrant_client.get_collections.return_value = mock_collections

        # Call init
        await qdrant_repo.init_collection()

        # Should have checked collections but NOT created
        mock_qdrant_client.get_collections.assert_called_once()
        mock_qdrant_client.create_collection.assert_not_called()
