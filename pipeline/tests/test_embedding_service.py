"""Tests for EmbeddingService."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Settings


@pytest.fixture
def embedding_service():
    """Create EmbeddingService with test settings."""
    from services.storage.embedding import EmbeddingService

    settings = Settings()
    return EmbeddingService(settings)


class TestEmbeddingServiceInit:
    """Test EmbeddingService initialization."""

    def test_initializes_with_settings(self, embedding_service):
        """Should initialize with Settings object."""
        assert embedding_service is not None
        assert hasattr(embedding_service, "client")
        assert hasattr(embedding_service, "model")

    def test_dimension_property_returns_1024(self, embedding_service):
        """Should expose dimension property with value 1024."""
        assert embedding_service.dimension == 1024


class TestEmbeddingServiceEmbed:
    """Test EmbeddingService.embed() method."""

    async def test_embed_returns_1024_dimension_vector(self, embedding_service):
        """Should return vector with 1024 dimensions."""
        # Mock the API response
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
        embedding_service.client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await embedding_service.embed("test text")

        assert len(result) == 1024

    async def test_embed_returns_list_of_floats(self, embedding_service):
        """Should return list containing float values."""
        # Mock the API response
        mock_response = MagicMock()
        mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]
        embedding_service.client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await embedding_service.embed("test text")

        assert isinstance(result, list)
        assert all(isinstance(x, float) for x in result)


class TestEmbeddingServiceEmbedBatch:
    """Test EmbeddingService.embed_batch() method."""

    async def test_embed_batch_with_empty_list(self, embedding_service):
        """Should return empty list when given empty input."""
        result = await embedding_service.embed_batch([])

        assert result == []

    async def test_embed_batch_returns_correct_count(self, embedding_service):
        """Should return same number of embeddings as input texts."""
        # Mock the API response
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=[0.1] * 1024),
            MagicMock(embedding=[0.2] * 1024),
            MagicMock(embedding=[0.3] * 1024),
        ]
        embedding_service.client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await embedding_service.embed_batch(["text1", "text2", "text3"])

        assert len(result) == 3
        assert all(len(emb) == 1024 for emb in result)

    async def test_embed_batch_preserves_order(self, embedding_service):
        """Should return embeddings in same order as input texts."""
        # Mock the API response with distinct embeddings
        mock_response = MagicMock()
        mock_response.data = [
            MagicMock(embedding=[1.0, 0.0, 0.0]),
            MagicMock(embedding=[0.0, 2.0, 0.0]),
            MagicMock(embedding=[0.0, 0.0, 3.0]),
        ]
        embedding_service.client.embeddings.create = AsyncMock(return_value=mock_response)

        result = await embedding_service.embed_batch(["first", "second", "third"])

        assert result[0][0] == 1.0
        assert result[1][1] == 2.0
        assert result[2][2] == 3.0
