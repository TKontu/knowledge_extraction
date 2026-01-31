"""Tests for EmbeddingService."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from config import Settings


@pytest.fixture
def embedding_service():
    """Create EmbeddingService with test settings."""
    from services.storage.embedding import EmbeddingService

    settings = Settings()
    return EmbeddingService(settings)


@pytest.fixture
def reset_embedding_service_state():
    """Reset EmbeddingService class-level state before and after test."""
    from services.storage.embedding import EmbeddingService

    # Store original state
    original_semaphore = EmbeddingService._semaphore
    original_max_concurrent = EmbeddingService._max_concurrent

    # Reset for test
    EmbeddingService._semaphore = None
    EmbeddingService._max_concurrent = 50

    yield

    # Restore original state
    EmbeddingService._semaphore = original_semaphore
    EmbeddingService._max_concurrent = original_max_concurrent


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


class TestEmbeddingServiceRerank:
    """Test EmbeddingService.rerank() method."""

    async def test_rerank_with_empty_documents(self, embedding_service):
        """Should return empty list when given empty documents."""
        result = await embedding_service.rerank("query", [])

        assert result == []

    async def test_rerank_returns_sorted_results(self, embedding_service):
        """Should return results sorted by relevance score descending."""
        mock_response_data = {
            "results": [
                {"index": 0, "relevance_score": 0.3},
                {"index": 1, "relevance_score": 0.9},
                {"index": 2, "relevance_score": 0.5},
            ]
        }

        # Mock the shared http client
        mock_response = MagicMock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        embedding_service._http_client = mock_client

        result = await embedding_service.rerank(
            query="test query",
            documents=["doc1", "doc2", "doc3"],
            model="bge-reranker-v2-m3",
        )

        # Should be sorted by score descending
        assert result[0] == (1, 0.9)  # doc2 has highest score
        assert result[1] == (2, 0.5)  # doc3 has second highest
        assert result[2] == (0, 0.3)  # doc1 has lowest score

    async def test_rerank_single_document(self, embedding_service):
        """Should handle single document correctly."""
        mock_response_data = {
            "results": [{"index": 0, "relevance_score": 0.8}]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        embedding_service._http_client = mock_client

        result = await embedding_service.rerank(
            query="test query",
            documents=["single doc"],
            model="bge-reranker-v2-m3",
        )

        assert len(result) == 1
        assert result[0] == (0, 0.8)

    async def test_rerank_uses_default_model(self, embedding_service):
        """Should use default reranker model from settings when not specified."""
        mock_response_data = {
            "results": [{"index": 0, "relevance_score": 0.7}]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        embedding_service._http_client = mock_client

        # Call without model parameter - should use default from settings
        result = await embedding_service.rerank(
            query="test query",
            documents=["doc"],
        )

        assert len(result) == 1
        # Verify post was called
        mock_client.post.assert_called_once()

    async def test_rerank_returns_index_score_tuples(self, embedding_service):
        """Should return list of (index, score) tuples."""
        mock_response_data = {
            "results": [
                {"index": 0, "relevance_score": 0.6},
                {"index": 1, "relevance_score": 0.8},
            ]
        }

        mock_response = MagicMock()
        mock_response.json.return_value = mock_response_data
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        embedding_service._http_client = mock_client

        result = await embedding_service.rerank(
            query="test",
            documents=["a", "b"],
            model="test-model",
        )

        for item in result:
            assert isinstance(item, tuple)
            assert len(item) == 2
            assert isinstance(item[0], int)
            assert isinstance(item[1], float)


class TestEmbeddingServiceConcurrency:
    """Test EmbeddingService concurrency control."""

    def test_configure_concurrency_sets_semaphore(self, reset_embedding_service_state):
        """Should configure semaphore with specified concurrency."""
        from services.storage.embedding import EmbeddingService

        EmbeddingService.configure_concurrency(25)

        assert EmbeddingService._max_concurrent == 25
        assert EmbeddingService._semaphore is not None
        # Semaphore internal value equals max_concurrent when no acquisitions
        assert EmbeddingService._semaphore._value == 25

    def test_get_semaphore_creates_default_if_none(self, reset_embedding_service_state):
        """Should create semaphore with default value if none exists."""
        from services.storage.embedding import EmbeddingService

        # Ensure semaphore is None
        assert EmbeddingService._semaphore is None

        semaphore = EmbeddingService._get_semaphore()

        assert semaphore is not None
        assert EmbeddingService._semaphore is semaphore
        assert semaphore._value == EmbeddingService._max_concurrent

    def test_init_configures_from_settings(self, reset_embedding_service_state):
        """Should configure concurrency from settings on first instance."""
        from services.storage.embedding import EmbeddingService

        settings = Settings()
        expected_concurrent = settings.embedding_max_concurrent

        # First instance should configure
        service = EmbeddingService(settings)

        assert EmbeddingService._max_concurrent == expected_concurrent
        assert EmbeddingService._semaphore._value == expected_concurrent

    def test_second_instance_reuses_semaphore(self, reset_embedding_service_state):
        """Should reuse existing semaphore for subsequent instances."""
        from services.storage.embedding import EmbeddingService

        settings = Settings()

        service1 = EmbeddingService(settings)
        semaphore_after_first = EmbeddingService._semaphore

        service2 = EmbeddingService(settings)
        semaphore_after_second = EmbeddingService._semaphore

        # Same semaphore instance
        assert semaphore_after_first is semaphore_after_second

    async def test_semaphore_limits_concurrent_requests(self, reset_embedding_service_state):
        """Should limit concurrent embed requests to configured value."""
        from services.storage.embedding import EmbeddingService

        # Configure with low concurrency for testing
        EmbeddingService.configure_concurrency(2)

        settings = Settings()
        service = EmbeddingService(settings)

        # Track concurrent executions
        max_concurrent_observed = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def mock_embed(*args, **kwargs):
            nonlocal max_concurrent_observed, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent_observed:
                    max_concurrent_observed = current_concurrent

            # Simulate API latency
            await asyncio.sleep(0.05)

            async with lock:
                current_concurrent -= 1

            mock_response = MagicMock()
            mock_response.data = [MagicMock(embedding=[0.1] * 1024)]
            return mock_response

        service.client.embeddings.create = mock_embed

        # Launch more requests than semaphore allows
        tasks = [service.embed(f"text {i}") for i in range(5)]
        await asyncio.gather(*tasks)

        # Should never exceed configured limit
        assert max_concurrent_observed <= 2
