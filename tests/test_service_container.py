"""Tests for ServiceContainer lifecycle."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestServiceContainerStart:
    """Tests for ServiceContainer.start()."""

    @pytest.fixture
    def _patch_all(self):
        """Patch all external dependencies used by ServiceContainer."""
        with (
            patch("services.scraper.service_container.settings") as mock_settings,
            patch("services.scraper.service_container.redis_client"),
            patch("services.scraper.service_container.qdrant_client"),
            patch("services.scraper.service_container.get_async_redis", new_callable=AsyncMock) as mock_get_redis,
            patch("services.scraper.service_container.FirecrawlClient") as mock_fc,
            patch("services.scraper.service_container.DomainRateLimiter"),
            patch("services.scraper.service_container.RateLimitConfig"),
            patch("services.scraper.service_container.RetryConfig"),
            patch("services.scraper.service_container.EmbeddingService"),
            patch("services.scraper.service_container.QdrantRepository"),
            patch("services.scraper.service_container.ExtractionEmbeddingService"),
            patch("services.scraper.service_container.LLMRequestQueue"),
            patch("services.scraper.service_container.LLMWorker") as mock_worker_cls,
            patch("services.scraper.service_container.AsyncOpenAI"),
        ):
            # Configure mock settings
            mock_settings.firecrawl_url = "http://localhost:3002"
            mock_settings.scrape_timeout = 60
            mock_settings.scrape_delay_min = 1
            mock_settings.scrape_delay_max = 3
            mock_settings.scrape_daily_limit_per_domain = 500
            mock_settings.scrape_retry_max_attempts = 3
            mock_settings.scrape_retry_base_delay = 1.0
            mock_settings.scrape_retry_max_delay = 30.0
            mock_settings.openai_base_url = "http://localhost:9003/v1"
            mock_settings.openai_api_key = "test"
            mock_settings.llm_http_timeout = 60
            mock_settings.llm_model = "test-model"
            mock_settings.llm_max_tokens = 8192
            mock_settings.llm_response_ttl = 300
            mock_settings.llm_queue_stream_key = "llm:requests"
            mock_settings.llm_queue_max_depth = 1000
            mock_settings.llm_queue_backpressure_threshold = 500
            mock_settings.llm_worker_concurrency = 10
            mock_settings.llm_worker_max_concurrency = 50
            mock_settings.llm_worker_min_concurrency = 5

            # Configure worker mock
            worker_instance = AsyncMock()
            worker_instance.initialize = AsyncMock()
            worker_instance.start = AsyncMock()
            worker_instance.stop = AsyncMock()
            mock_worker_cls.return_value = worker_instance

            # Configure firecrawl mock
            mock_fc.return_value.close = AsyncMock()

            # Configure redis mock
            mock_redis_instance = AsyncMock()
            mock_get_redis.return_value = mock_redis_instance

            yield {
                "settings": mock_settings,
                "worker_cls": mock_worker_cls,
                "worker_instance": worker_instance,
                "firecrawl_cls": mock_fc,
                "redis_instance": mock_redis_instance,
            }

    @pytest.mark.asyncio
    async def test_start_creates_all_services(self, _patch_all):
        """After start(), all service properties should be accessible."""
        from services.scraper.service_container import ServiceContainer

        container = ServiceContainer()
        await container.start()

        # All properties should work without RuntimeError
        assert container.firecrawl_client is not None
        assert container.rate_limiter is not None
        assert container.retry_config is not None
        assert container.embedding_service is not None
        assert container.extraction_embedding is not None
        assert container.llm_queue is not None

        await container.stop()

    @pytest.mark.asyncio
    async def test_stop_tears_down_services(self, _patch_all):
        """stop() should call close/stop on teardown-capable services."""
        from services.scraper.service_container import ServiceContainer

        container = ServiceContainer()
        await container.start()
        await container.stop()

        # LLM worker should be stopped
        _patch_all["worker_instance"].stop.assert_called_once()
        # Firecrawl client should be closed
        _patch_all["firecrawl_cls"].return_value.close.assert_called_once()
        # Async redis should be closed
        _patch_all["redis_instance"].close.assert_called_once()

    @pytest.mark.asyncio
    async def test_property_before_start_raises(self):
        """Accessing any property before start() should raise RuntimeError."""
        from services.scraper.service_container import ServiceContainer

        container = ServiceContainer()

        properties = [
            "firecrawl_client",
            "rate_limiter",
            "retry_config",
            "embedding_service",
            "extraction_embedding",
            "llm_queue",
        ]

        for prop in properties:
            with pytest.raises(RuntimeError, match="not started"):
                getattr(container, prop)

    @pytest.mark.asyncio
    async def test_async_context_manager(self, _patch_all):
        """async with ServiceContainer() should start and stop."""
        from services.scraper.service_container import ServiceContainer

        container = ServiceContainer()
        async with container:
            assert container.firecrawl_client is not None

        # After exit, _started should be False
        assert container._started is False
