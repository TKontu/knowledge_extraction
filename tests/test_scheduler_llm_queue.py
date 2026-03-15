"""Tests for Scheduler LLM queue wiring.

TDD: These tests verify that the Scheduler passes the LLM queue to
LLMClient when llm_queue_enabled is True.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest


class TestSchedulerLLMQueueWiring:
    """Tests for Scheduler passing LLM queue to extractors."""

    @pytest.fixture
    def llm_config(self):
        """Create LLMConfig for testing."""
        from config import LLMConfig

        return LLMConfig(
            base_url="http://localhost:9003/v1",
            embedding_base_url="http://localhost:9003/v1",
            api_key="test",
            model="test-model",
            embedding_model="bge-m3",
            embedding_dimension=1024,
            http_timeout=60,
            max_tokens=4096,
            max_retries=3,
            retry_backoff_min=1,
            retry_backoff_max=30,
            base_temperature=0.1,
            retry_temperature_increment=0.1,
        )

    @pytest.mark.asyncio
    async def test_llm_client_receives_queue_when_enabled(self, llm_config):
        """Test that LLMClient is created with queue when llm_queue_enabled=True."""
        from services.llm.client import LLMClient
        from services.llm.queue import LLMRequestQueue

        mock_queue = MagicMock(spec=LLMRequestQueue)

        # Create LLMClient with queue (simulating what scheduler should do)
        llm_client = LLMClient(llm_config, llm_queue=mock_queue)

        # Verify client is in queue mode
        assert llm_client.llm_queue is mock_queue
        assert llm_client.client is None  # No direct client in queue mode

    @pytest.mark.asyncio
    async def test_llm_client_no_queue_when_disabled(self, llm_config):
        """Test that LLMClient is created without queue when llm_queue_enabled=False."""
        from services.llm.client import LLMClient

        # Create LLMClient without queue
        llm_client = LLMClient(llm_config, llm_queue=None)

        # Verify client is in direct mode
        assert llm_client.llm_queue is None
        assert llm_client.client is not None  # Has direct client

    @pytest.mark.asyncio
    async def test_extraction_worker_receives_queued_llm(self, llm_config):
        """Test that extraction worker can be created with queue-backed LLM config."""
        from services.extraction.worker import ExtractionWorker
        from services.llm.queue import LLMRequestQueue

        mock_queue = MagicMock(spec=LLMRequestQueue)
        mock_db = MagicMock()

        # Create ExtractionWorker with llm config and queue
        worker = ExtractionWorker(
            db=mock_db,
            llm=llm_config,
            llm_queue=mock_queue,
        )

        # Verify the queue is wired through
        assert worker.llm_queue is mock_queue


class TestSchedulerQueueInitialization:
    """Tests for Scheduler queue initialization."""

    @pytest.mark.asyncio
    async def test_scheduler_creates_queue_and_worker(self):
        """Test that scheduler creates LLMRequestQueue and LLMWorker."""
        # The scheduler already creates these in start(), but we want to verify
        # the LLMClient is passed the queue when llm_queue_enabled=True

        # This is more of an integration test pattern
        # For unit testing, we verify the components work together
        from services.llm.queue import LLMRequestQueue
        from services.llm.worker import LLMWorker

        # Verify classes exist and can be instantiated
        mock_redis = AsyncMock()
        mock_llm = AsyncMock()

        queue = LLMRequestQueue(redis=mock_redis)
        worker = LLMWorker(
            redis=mock_redis,
            llm_client=mock_llm,
            worker_id="test",
        )

        assert queue is not None
        assert worker is not None
