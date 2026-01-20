"""Tests for LLM Worker Dead Letter Queue (DLQ) functionality.

TDD: These tests define the expected behavior for DLQ handling.
"""

import asyncio
import json
from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestDeadLetterQueue:
    """Tests for Dead Letter Queue functionality in LLMWorker."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        redis.xreadgroup = AsyncMock(return_value=[])
        redis.xack = AsyncMock()
        redis.xadd = AsyncMock()
        redis.setex = AsyncMock()
        redis.lpush = AsyncMock()
        redis.llen = AsyncMock(return_value=0)
        redis.lrange = AsyncMock(return_value=[])
        redis.lrem = AsyncMock()
        return redis

    @pytest.fixture
    def mock_llm_client(self):
        """Create mock LLM client that fails."""
        client = AsyncMock()
        client.chat = AsyncMock()
        client.chat.completions = AsyncMock()
        client.chat.completions.create = AsyncMock(
            side_effect=Exception("LLM processing failed")
        )
        return client

    @pytest.fixture
    def worker(self, mock_redis, mock_llm_client):
        """Create LLMWorker with mocks."""
        from src.services.llm.worker import LLMWorker

        return LLMWorker(
            redis=mock_redis,
            llm_client=mock_llm_client,
            worker_id="test-worker-dlq",
            max_retries=3,
        )

    @pytest.mark.asyncio
    async def test_failed_request_moves_to_dlq_after_max_retries(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that requests move to DLQ after exhausting retries."""
        from src.services.llm.models import LLMRequest

        # Create a request that has already been retried twice (retry_count=2)
        # Next failure should move it to DLQ
        request = LLMRequest(
            request_id="test-dlq",
            request_type="extract_facts",
            payload={"content": "test"},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
            retry_count=2,  # Already retried twice
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                ("llm:requests", [("entry-1", {"data": request.to_json()})])
            ]
        )

        await worker.process_batch()

        # Should have pushed to DLQ
        mock_redis.lpush.assert_called_once()
        call_args = mock_redis.lpush.call_args
        assert call_args[0][0] == "llm:dlq"  # DLQ key

        # Should have stored error response
        mock_redis.setex.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_request_requeued_if_retries_remaining(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that requests are requeued if retries remain."""
        from src.services.llm.models import LLMRequest

        # Request with no retries yet
        request = LLMRequest(
            request_id="test-retry",
            request_type="extract_facts",
            payload={"content": "test"},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
            retry_count=0,  # No retries yet
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                ("llm:requests", [("entry-1", {"data": request.to_json()})])
            ]
        )

        await worker.process_batch()

        # Should have requeued with incremented retry count
        mock_redis.xadd.assert_called()
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == "llm:requests"

        # Verify retry_count was incremented
        data = json.loads(call_args[0][1]["data"])
        assert data["retry_count"] == 1

        # Should NOT have pushed to DLQ
        mock_redis.lpush.assert_not_called()

    @pytest.mark.asyncio
    async def test_dlq_entry_contains_full_context(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that DLQ entry contains full error context."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-context",
            request_type="extract_entities",
            payload={"content": "test", "entity_types": ["plan"]},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
            retry_count=2,
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                ("llm:requests", [("entry-1", {"data": request.to_json()})])
            ]
        )

        await worker.process_batch()

        # Verify DLQ entry contains full context
        call_args = mock_redis.lpush.call_args
        dlq_data = json.loads(call_args[0][1])

        assert "request" in dlq_data
        assert "error" in dlq_data
        assert "failed_at" in dlq_data
        assert dlq_data["request"]["request_id"] == "test-context"
        assert "LLM processing failed" in dlq_data["error"]

    @pytest.mark.asyncio
    async def test_get_dlq_stats(self, worker, mock_redis):
        """Test that get_dlq_stats returns correct stats."""
        mock_redis.llen = AsyncMock(return_value=5)
        mock_redis.lrange = AsyncMock(
            return_value=[
                json.dumps({
                    "request": {"request_id": "test-1"},
                    "error": "Error 1",
                    "failed_at": datetime.now(UTC).isoformat(),
                }).encode(),
                json.dumps({
                    "request": {"request_id": "test-2"},
                    "error": "Error 2",
                    "failed_at": datetime.now(UTC).isoformat(),
                }).encode(),
            ]
        )

        stats = await worker.get_dlq_stats()

        assert stats["count"] == 5
        assert len(stats["recent"]) == 2
        assert stats["recent"][0]["request"]["request_id"] == "test-1"

    @pytest.mark.asyncio
    async def test_reprocess_dlq_item_with_reset(self, worker, mock_redis):
        """Test that reprocess_dlq_item moves item back to queue with reset retry count."""
        dlq_item = json.dumps({
            "request": {
                "request_id": "test-reprocess",
                "request_type": "extract_facts",
                "payload": {"content": "test"},
                "priority": 5,
                "created_at": datetime.now(UTC).isoformat(),
                "timeout_at": (datetime.now(UTC) + timedelta(seconds=300)).isoformat(),
                "retry_count": 3,
            },
            "error": "Previous error",
            "failed_at": datetime.now(UTC).isoformat(),
        })

        mock_redis.lrem = AsyncMock(return_value=1)

        await worker.reprocess_dlq_item(dlq_item)

        # Should remove from DLQ
        mock_redis.lrem.assert_called_once()
        call_args = mock_redis.lrem.call_args
        assert call_args[0][0] == "llm:dlq"

        # Should add back to queue with reset retry count
        mock_redis.xadd.assert_called_once()
        call_args = mock_redis.xadd.call_args
        data = json.loads(call_args[0][1]["data"])
        assert data["retry_count"] == 0  # Reset

    @pytest.mark.asyncio
    async def test_dlq_error_response_sent_to_caller(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that error response is still sent to caller when moving to DLQ."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-error-response",
            request_type="extract_facts",
            payload={"content": "test"},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
            retry_count=2,
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                ("llm:requests", [("entry-1", {"data": request.to_json()})])
            ]
        )

        await worker.process_batch()

        # Should have stored error response
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert "llm:response:test-error-response" in call_args[0][0]

        # Verify response has error status
        response_json = call_args[0][2]
        response_data = json.loads(response_json)
        assert response_data["status"] == "error"
        assert "LLM processing failed" in response_data["error"]
