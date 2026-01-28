"""Tests for LLM queue pub/sub notification system."""

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from redis.asyncio import Redis

from services.llm.models import LLMRequest, LLMResponse
from services.llm.queue import LLMRequestQueue
from services.llm.worker import LLMWorker


@pytest.fixture
async def redis_client():
    """Create Redis client for testing."""
    redis = Redis(host="localhost", port=6379, db=0, decode_responses=False)
    yield redis
    await redis.aclose()


@pytest.fixture
async def llm_queue(redis_client: Redis):
    """Create LLM request queue for testing."""
    queue = LLMRequestQueue(
        redis=redis_client,
        stream_key="llm:requests:test",
        response_ttl=300,
        poll_interval=0.1,
    )
    yield queue


class TestPubSubNotification:
    """Tests for pub/sub notification functionality."""

    async def test_wait_for_result_receives_pubsub_notification(
        self, llm_queue: LLMRequestQueue, redis_client: Redis
    ):
        """Test that wait_for_result receives pub/sub notification."""
        request_id = "test-req-123"
        response = LLMResponse(
            request_id=request_id,
            status="success",
            result={"data": "test"},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        # Simulate worker publishing response and notification in background
        async def publish_response():
            await asyncio.sleep(0.2)  # Small delay to ensure subscription is ready
            response_key = f"llm:response:{request_id}"
            channel = llm_queue._response_channel(request_id)

            # Store response
            await redis_client.setex(
                response_key,
                300,
                response.to_json(),
            )

            # Publish notification
            await redis_client.publish(channel, "ready")

        # Start background task
        asyncio.create_task(publish_response())

        # Wait for result - should receive via pub/sub
        result = await llm_queue.wait_for_result(request_id, timeout=5.0)

        assert result.request_id == request_id
        assert result.status == "success"
        assert result.result == {"data": "test"}

    async def test_wait_for_result_returns_cached_response(
        self, llm_queue: LLMRequestQueue, redis_client: Redis
    ):
        """Test that wait_for_result returns immediately if response already exists."""
        request_id = "test-req-cached"
        response = LLMResponse(
            request_id=request_id,
            status="success",
            result={"data": "cached"},
            error=None,
            processing_time_ms=50,
            completed_at=datetime.now(UTC),
        )

        # Pre-store response in Redis
        response_key = f"llm:response:{request_id}"
        await redis_client.setex(response_key, 300, response.to_json())

        # Should return immediately without waiting for pub/sub
        start_time = asyncio.get_event_loop().time()
        result = await llm_queue.wait_for_result(request_id, timeout=5.0)
        elapsed = asyncio.get_event_loop().time() - start_time

        assert result.request_id == request_id
        assert result.status == "success"
        assert elapsed < 0.5  # Should be near-instant

    async def test_wait_for_result_timeout(
        self, llm_queue: LLMRequestQueue, redis_client: Redis
    ):
        """Test that wait_for_result times out properly."""
        request_id = "test-req-timeout"

        # Don't publish anything - should timeout
        from services.llm.queue import RequestTimeoutError

        with pytest.raises(RequestTimeoutError) as exc_info:
            await llm_queue.wait_for_result(request_id, timeout=1.0)

        assert request_id in str(exc_info.value)

    async def test_wait_for_result_cleans_up_subscription(
        self, llm_queue: LLMRequestQueue, redis_client: Redis
    ):
        """Test that subscriptions are cleaned up properly."""
        request_id = "test-req-cleanup"
        response = LLMResponse(
            request_id=request_id,
            status="success",
            result={"data": "cleanup_test"},
            error=None,
            processing_time_ms=75,
            completed_at=datetime.now(UTC),
        )

        # Publish response after short delay
        async def publish_response():
            await asyncio.sleep(0.2)
            response_key = f"llm:response:{request_id}"
            channel = llm_queue._response_channel(request_id)
            await redis_client.setex(response_key, 300, response.to_json())
            await redis_client.publish(channel, "ready")

        asyncio.create_task(publish_response())

        # Wait for result
        await llm_queue.wait_for_result(request_id, timeout=5.0)

        # Check that no subscriptions remain
        # Note: Redis PUBSUB NUMSUB returns subscriber count per channel
        channel = llm_queue._response_channel(request_id)
        result = await redis_client.execute_command("PUBSUB", "NUMSUB", channel)

        # Result is [channel_name, count, ...]
        assert result[1] == 0, "Subscription should be cleaned up"

    async def test_concurrent_requests_isolated(
        self, llm_queue: LLMRequestQueue, redis_client: Redis
    ):
        """Test that multiple concurrent requests don't interfere with each other."""
        request_ids = [f"test-req-concurrent-{i}" for i in range(3)]

        async def wait_and_publish(req_id: str, delay: float):
            """Wait for result while publishing response after delay."""
            # Publish response in background
            async def publish():
                await asyncio.sleep(delay)
                response = LLMResponse(
                    request_id=req_id,
                    status="success",
                    result={"id": req_id},
                    error=None,
                    processing_time_ms=int(delay * 1000),
                    completed_at=datetime.now(UTC),
                )
                response_key = f"llm:response:{req_id}"
                channel = llm_queue._response_channel(req_id)
                await redis_client.setex(response_key, 300, response.to_json())
                await redis_client.publish(channel, "ready")

            asyncio.create_task(publish())
            return await llm_queue.wait_for_result(req_id, timeout=5.0)

        # Start all requests concurrently with different delays
        results = await asyncio.gather(
            wait_and_publish(request_ids[0], 0.3),
            wait_and_publish(request_ids[1], 0.1),
            wait_and_publish(request_ids[2], 0.2),
        )

        # Verify each result matches its request
        for i, result in enumerate(results):
            assert result.request_id == request_ids[i]
            assert result.result == {"id": request_ids[i]}

    async def test_response_channel_naming(self, llm_queue: LLMRequestQueue):
        """Test response channel naming convention."""
        request_id = "test-123"
        channel = llm_queue._response_channel(request_id)

        assert channel == "llm:response:notify:test-123"
        assert "llm:response:notify:" in channel
        assert request_id in channel


class TestWorkerNotification:
    """Tests for worker pub/sub notification functionality."""

    async def test_worker_publishes_notification(self):
        """Test that worker publishes notification after storing response."""
        # Create mock Redis client
        mock_redis = AsyncMock()
        mock_redis.setex = AsyncMock()
        mock_redis.publish = AsyncMock()
        mock_redis.xack = AsyncMock()

        # Create mock LLM client
        mock_llm = MagicMock()
        mock_completion = MagicMock()
        mock_completion.choices = [MagicMock(message=MagicMock(content='{"result": "test"}'))]
        mock_llm.chat.completions.create = AsyncMock(return_value=mock_completion)

        # Create worker
        worker = LLMWorker(
            redis=mock_redis,
            llm_client=mock_llm,
            worker_id="test-worker",
            stream_key="llm:requests:test",
        )

        # Create test request
        request = LLMRequest(
            request_id="test-req-notify",
            request_type="extract_facts",
            payload={
                "content": "test content",
                "categories": ["test"],
                "profile_name": "test",
            },
            priority=1,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
            retry_count=0,
        )

        # Process request
        entry_id = "test-entry-123"
        data = {"data": request.to_json()}
        await worker._process_request(entry_id, data)

        # Verify setex was called (response stored)
        assert mock_redis.setex.called
        setex_call = mock_redis.setex.call_args
        assert setex_call[0][0] == f"llm:response:{request.request_id}"

        # Verify publish was called (notification sent)
        assert mock_redis.publish.called
        publish_call = mock_redis.publish.call_args
        assert publish_call[0][0] == f"llm:response:notify:{request.request_id}"
        assert publish_call[0][1] == "ready"
