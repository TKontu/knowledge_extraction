"""Tests for scheduler LLM worker integration.

TDD: Tests for starting and stopping LLM workers from the scheduler.
Now tests that ServiceContainer creates/stops LLM workers, and that
JobScheduler accesses them via the container.
"""

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestServiceContainerLLMWorkerStartup:
    """Tests for LLM worker startup in ServiceContainer."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.firecrawl_url = "http://localhost:3000"
        settings.scrape_timeout = 60
        settings.scrape_delay_min = 1.0
        settings.scrape_delay_max = 3.0
        settings.scrape_daily_limit_per_domain = 1000
        settings.scrape_retry_max_attempts = 3
        settings.scrape_retry_base_delay = 1.0
        settings.scrape_retry_max_delay = 30.0
        settings.max_concurrent_crawls = 2
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_model = "test-model"
        settings.llm_http_timeout = 60
        settings.llm_max_tokens = 8192
        settings.llm_response_ttl = 300
        settings.llm_queue_stream_key = "llm:requests"
        settings.llm_queue_max_depth = 1000
        settings.llm_queue_backpressure_threshold = 500
        settings.llm_worker_concurrency = 10
        settings.llm_worker_max_concurrency = 50
        settings.llm_worker_min_concurrency = 5
        return settings

    @pytest.fixture
    def mock_firecrawl_client(self):
        """Create mock FirecrawlClient with async close."""
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        return mock_client

    @pytest.mark.asyncio
    async def test_creates_llm_request_queue_on_start(self, mock_settings, mock_firecrawl_client):
        """Test that container creates LLM request queue on start."""
        from services.scraper.service_container import ServiceContainer

        with patch("services.scraper.service_container.settings", mock_settings), \
             patch("services.scraper.service_container.redis_client"), \
             patch("services.scraper.service_container.qdrant_client"), \
             patch("services.scraper.service_container.get_async_redis", new_callable=AsyncMock), \
             patch("services.scraper.service_container.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.service_container.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.service_container.LLMWorker") as MockWorker, \
             patch("services.scraper.service_container.AsyncOpenAI"), \
             patch("services.scraper.service_container.EmbeddingService"), \
             patch("services.scraper.service_container.QdrantRepository"), \
             patch("services.scraper.service_container.ExtractionEmbeddingService"), \
             patch("services.scraper.service_container.ExtractionDeduplicator"):

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            mock_queue_instance = MagicMock()
            MockQueue.return_value = mock_queue_instance

            container = ServiceContainer()
            await container.start()
            await container.stop()

            MockQueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_starts_llm_worker_on_start(self, mock_settings, mock_firecrawl_client):
        """Test that container starts LLM worker on start."""
        from services.scraper.service_container import ServiceContainer

        with patch("services.scraper.service_container.settings", mock_settings), \
             patch("services.scraper.service_container.redis_client"), \
             patch("services.scraper.service_container.qdrant_client"), \
             patch("services.scraper.service_container.get_async_redis", new_callable=AsyncMock), \
             patch("services.scraper.service_container.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.service_container.LLMRequestQueue"), \
             patch("services.scraper.service_container.LLMWorker") as MockWorker, \
             patch("services.scraper.service_container.AsyncOpenAI"), \
             patch("services.scraper.service_container.EmbeddingService"), \
             patch("services.scraper.service_container.QdrantRepository"), \
             patch("services.scraper.service_container.ExtractionEmbeddingService"), \
             patch("services.scraper.service_container.ExtractionDeduplicator"):

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            container = ServiceContainer()
            await container.start()
            await container.stop()

            MockWorker.assert_called_once()
            mock_worker_instance.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_stops_llm_worker_on_stop(self, mock_settings, mock_firecrawl_client):
        """Test that container stops LLM worker on stop."""
        from services.scraper.service_container import ServiceContainer

        with patch("services.scraper.service_container.settings", mock_settings), \
             patch("services.scraper.service_container.redis_client"), \
             patch("services.scraper.service_container.qdrant_client"), \
             patch("services.scraper.service_container.get_async_redis", new_callable=AsyncMock), \
             patch("services.scraper.service_container.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.service_container.LLMRequestQueue"), \
             patch("services.scraper.service_container.LLMWorker") as MockWorker, \
             patch("services.scraper.service_container.AsyncOpenAI"), \
             patch("services.scraper.service_container.EmbeddingService"), \
             patch("services.scraper.service_container.QdrantRepository"), \
             patch("services.scraper.service_container.ExtractionEmbeddingService"), \
             patch("services.scraper.service_container.ExtractionDeduplicator"):

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            container = ServiceContainer()
            await container.start()
            await container.stop()

            mock_worker_instance.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_queue_available_via_property(self, mock_settings, mock_firecrawl_client):
        """Test that LLM queue is accessible via container property."""
        from services.scraper.service_container import ServiceContainer

        with patch("services.scraper.service_container.settings", mock_settings), \
             patch("services.scraper.service_container.redis_client"), \
             patch("services.scraper.service_container.qdrant_client"), \
             patch("services.scraper.service_container.get_async_redis", new_callable=AsyncMock), \
             patch("services.scraper.service_container.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.service_container.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.service_container.LLMWorker") as MockWorker, \
             patch("services.scraper.service_container.AsyncOpenAI"), \
             patch("services.scraper.service_container.EmbeddingService"), \
             patch("services.scraper.service_container.QdrantRepository"), \
             patch("services.scraper.service_container.ExtractionEmbeddingService"), \
             patch("services.scraper.service_container.ExtractionDeduplicator"):

            mock_queue_instance = MagicMock()
            MockQueue.return_value = mock_queue_instance

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            container = ServiceContainer()
            await container.start()

            assert container.llm_queue is mock_queue_instance

            await container.stop()

    @pytest.mark.asyncio
    async def test_llm_worker_uses_correct_configuration(self, mock_settings, mock_firecrawl_client):
        """Test that LLM worker is configured with settings values."""
        from services.scraper.service_container import ServiceContainer

        with patch("services.scraper.service_container.settings", mock_settings), \
             patch("services.scraper.service_container.redis_client"), \
             patch("services.scraper.service_container.qdrant_client"), \
             patch("services.scraper.service_container.get_async_redis", new_callable=AsyncMock), \
             patch("services.scraper.service_container.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.service_container.LLMRequestQueue"), \
             patch("services.scraper.service_container.LLMWorker") as MockWorker, \
             patch("services.scraper.service_container.AsyncOpenAI"), \
             patch("services.scraper.service_container.EmbeddingService"), \
             patch("services.scraper.service_container.QdrantRepository"), \
             patch("services.scraper.service_container.ExtractionEmbeddingService"), \
             patch("services.scraper.service_container.ExtractionDeduplicator"):

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            container = ServiceContainer()
            await container.start()
            await container.stop()

            call_kwargs = MockWorker.call_args.kwargs
            assert call_kwargs["initial_concurrency"] == 10
            assert call_kwargs["max_concurrency"] == 50
            assert call_kwargs["min_concurrency"] == 5


class TestServiceContainerLLMWorkerLifecycle:
    """Tests for LLM worker lifecycle in ServiceContainer."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.firecrawl_url = "http://localhost:3000"
        settings.scrape_timeout = 60
        settings.scrape_delay_min = 1.0
        settings.scrape_delay_max = 3.0
        settings.scrape_daily_limit_per_domain = 1000
        settings.scrape_retry_max_attempts = 3
        settings.scrape_retry_base_delay = 1.0
        settings.scrape_retry_max_delay = 30.0
        settings.max_concurrent_crawls = 2
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_model = "test-model"
        settings.llm_http_timeout = 60
        settings.llm_max_tokens = 8192
        settings.llm_response_ttl = 300
        settings.llm_queue_stream_key = "llm:requests"
        settings.llm_queue_max_depth = 1000
        settings.llm_queue_backpressure_threshold = 500
        settings.llm_worker_concurrency = 10
        settings.llm_worker_max_concurrency = 50
        settings.llm_worker_min_concurrency = 5
        return settings

    @pytest.fixture
    def mock_firecrawl_client(self):
        """Create mock FirecrawlClient with async close."""
        mock_client = MagicMock()
        mock_client.close = AsyncMock()
        return mock_client

    @pytest.mark.asyncio
    async def test_llm_worker_task_is_created(self, mock_settings, mock_firecrawl_client):
        """Test that LLM worker is started as a background task."""
        from services.scraper.service_container import ServiceContainer

        with patch("services.scraper.service_container.settings", mock_settings), \
             patch("services.scraper.service_container.redis_client"), \
             patch("services.scraper.service_container.qdrant_client"), \
             patch("services.scraper.service_container.get_async_redis", new_callable=AsyncMock), \
             patch("services.scraper.service_container.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.service_container.LLMRequestQueue"), \
             patch("services.scraper.service_container.LLMWorker") as MockWorker, \
             patch("services.scraper.service_container.AsyncOpenAI"), \
             patch("services.scraper.service_container.EmbeddingService"), \
             patch("services.scraper.service_container.QdrantRepository"), \
             patch("services.scraper.service_container.ExtractionEmbeddingService"), \
             patch("services.scraper.service_container.ExtractionDeduplicator"):

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            container = ServiceContainer()
            await container.start()

            assert container._llm_worker_task is not None
            assert container.llm_worker is mock_worker_instance

            await container.stop()
