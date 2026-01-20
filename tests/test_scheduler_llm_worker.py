"""Tests for scheduler LLM worker integration.

TDD: Tests for starting and stopping LLM workers from the scheduler.
"""

import asyncio
from datetime import datetime, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestSchedulerLLMWorkerStartup:
    """Tests for LLM worker startup in JobScheduler."""

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
        """Test that scheduler creates LLM request queue on start."""
        from services.scraper.scheduler import JobScheduler

        with patch("services.scraper.scheduler.settings", mock_settings), \
             patch("services.scraper.scheduler.redis_client") as mock_redis, \
             patch("services.scraper.scheduler.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.scheduler.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.scheduler.LLMWorker") as MockWorker, \
             patch("services.scraper.scheduler.AsyncOpenAI"):

            # Setup mocks - use AsyncMock for async methods
            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            mock_queue_instance = MagicMock()
            MockQueue.return_value = mock_queue_instance

            scheduler = JobScheduler(poll_interval=5)

            # Start but stop immediately to test initialization
            await scheduler.start()
            await scheduler.stop()

            # Should have created the queue
            MockQueue.assert_called_once()

    @pytest.mark.asyncio
    async def test_starts_llm_worker_on_start(self, mock_settings, mock_firecrawl_client):
        """Test that scheduler starts LLM worker on start."""
        from services.scraper.scheduler import JobScheduler

        with patch("services.scraper.scheduler.settings", mock_settings), \
             patch("services.scraper.scheduler.redis_client") as mock_redis, \
             patch("services.scraper.scheduler.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.scheduler.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.scheduler.LLMWorker") as MockWorker, \
             patch("services.scraper.scheduler.AsyncOpenAI"):

            # Setup mocks
            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            scheduler = JobScheduler(poll_interval=5)

            await scheduler.start()
            await scheduler.stop()

            # Should have created and initialized the worker
            MockWorker.assert_called_once()
            mock_worker_instance.initialize.assert_called_once()

    @pytest.mark.asyncio
    async def test_stops_llm_worker_on_stop(self, mock_settings, mock_firecrawl_client):
        """Test that scheduler stops LLM worker on stop."""
        from services.scraper.scheduler import JobScheduler

        with patch("services.scraper.scheduler.settings", mock_settings), \
             patch("services.scraper.scheduler.redis_client") as mock_redis, \
             patch("services.scraper.scheduler.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.scheduler.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.scheduler.LLMWorker") as MockWorker, \
             patch("services.scraper.scheduler.AsyncOpenAI"):

            # Setup mocks
            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            scheduler = JobScheduler(poll_interval=5)

            await scheduler.start()
            await scheduler.stop()

            # Should have stopped the worker
            mock_worker_instance.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_llm_queue_available_for_extraction_worker(self, mock_settings, mock_firecrawl_client):
        """Test that LLM queue is available for extraction worker."""
        from services.scraper.scheduler import JobScheduler

        with patch("services.scraper.scheduler.settings", mock_settings), \
             patch("services.scraper.scheduler.redis_client") as mock_redis, \
             patch("services.scraper.scheduler.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.scheduler.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.scheduler.LLMWorker") as MockWorker, \
             patch("services.scraper.scheduler.AsyncOpenAI"):

            mock_queue_instance = MagicMock()
            MockQueue.return_value = mock_queue_instance

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            scheduler = JobScheduler(poll_interval=5)

            await scheduler.start()

            # Scheduler should have the queue available
            assert scheduler._llm_queue is mock_queue_instance

            await scheduler.stop()

    @pytest.mark.asyncio
    async def test_llm_worker_uses_correct_configuration(self, mock_settings, mock_firecrawl_client):
        """Test that LLM worker is configured with settings values."""
        from services.scraper.scheduler import JobScheduler

        with patch("services.scraper.scheduler.settings", mock_settings), \
             patch("services.scraper.scheduler.redis_client") as mock_redis, \
             patch("services.scraper.scheduler.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.scheduler.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.scheduler.LLMWorker") as MockWorker, \
             patch("services.scraper.scheduler.AsyncOpenAI"):

            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            scheduler = JobScheduler(poll_interval=5)

            await scheduler.start()
            await scheduler.stop()

            # Check the worker was created with correct settings
            call_kwargs = MockWorker.call_args.kwargs
            assert call_kwargs["initial_concurrency"] == 10
            assert call_kwargs["max_concurrency"] == 50
            assert call_kwargs["min_concurrency"] == 5


class TestSchedulerLLMWorkerLifecycle:
    """Tests for LLM worker lifecycle in JobScheduler."""

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
        from services.scraper.scheduler import JobScheduler

        with patch("services.scraper.scheduler.settings", mock_settings), \
             patch("services.scraper.scheduler.redis_client") as mock_redis, \
             patch("services.scraper.scheduler.FirecrawlClient", return_value=mock_firecrawl_client), \
             patch("services.scraper.scheduler.LLMRequestQueue") as MockQueue, \
             patch("services.scraper.scheduler.LLMWorker") as MockWorker, \
             patch("services.scraper.scheduler.AsyncOpenAI"):

            # Setup worker mock
            mock_worker_instance = AsyncMock()
            mock_worker_instance.initialize = AsyncMock()
            mock_worker_instance.start = AsyncMock()
            mock_worker_instance.stop = AsyncMock()
            MockWorker.return_value = mock_worker_instance

            scheduler = JobScheduler(poll_interval=5)

            await scheduler.start()

            # Should have an LLM worker task
            assert scheduler._llm_worker_task is not None
            assert scheduler._llm_worker is mock_worker_instance

            await scheduler.stop()
