"""Tests for Redis-based LLM request queue.

TDD: These tests define the expected behavior for the LLM queue system.
"""

import asyncio
import json
from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest


class TestLLMRequestModel:
    """Tests for LLMRequest data model."""

    def test_llm_request_has_required_fields(self):
        """Test that LLMRequest has all required fields."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-123",
            request_type="extract_field_group",
            payload={"content": "test content"},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        assert request.request_id == "test-123"
        assert request.request_type == "extract_field_group"
        assert request.payload == {"content": "test content"}
        assert request.priority == 5
        assert request.created_at is not None
        assert request.timeout_at is not None

    def test_llm_request_serializes_to_json(self):
        """Test that LLMRequest can be serialized to JSON."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-123",
            request_type="extract_field_group",
            payload={"content": "test"},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        json_str = request.to_json()
        assert isinstance(json_str, str)

        # Should be valid JSON
        data = json.loads(json_str)
        assert data["request_id"] == "test-123"
        assert data["request_type"] == "extract_field_group"

    def test_llm_request_deserializes_from_json(self):
        """Test that LLMRequest can be deserialized from JSON."""
        from src.services.llm.models import LLMRequest

        original = LLMRequest(
            request_id="test-456",
            request_type="extract_facts",
            payload={"categories": ["tech"]},
            priority=10,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        json_str = original.to_json()
        restored = LLMRequest.from_json(json_str)

        assert restored.request_id == original.request_id
        assert restored.request_type == original.request_type
        assert restored.payload == original.payload
        assert restored.priority == original.priority

    def test_llm_request_validates_request_type(self):
        """Test that LLMRequest validates request_type."""
        from src.services.llm.models import LLMRequest, InvalidRequestTypeError

        with pytest.raises(InvalidRequestTypeError):
            LLMRequest(
                request_id="test",
                request_type="invalid_type",  # Not a valid type
                payload={},
                priority=5,
                created_at=datetime.now(UTC),
                timeout_at=datetime.now(UTC) + timedelta(seconds=300),
            )

    def test_llm_request_is_expired(self):
        """Test that LLMRequest can check if it's expired."""
        from src.services.llm.models import LLMRequest

        # Expired request
        expired = LLMRequest(
            request_id="test",
            request_type="extract_field_group",
            payload={},
            priority=5,
            created_at=datetime.now(UTC) - timedelta(seconds=600),
            timeout_at=datetime.now(UTC) - timedelta(seconds=300),
        )
        assert expired.is_expired() is True

        # Valid request
        valid = LLMRequest(
            request_id="test",
            request_type="extract_field_group",
            payload={},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )
        assert valid.is_expired() is False


class TestLLMResponseModel:
    """Tests for LLMResponse data model."""

    def test_llm_response_has_required_fields(self):
        """Test that LLMResponse has all required fields."""
        from src.services.llm.models import LLMResponse

        response = LLMResponse(
            request_id="test-123",
            status="success",
            result={"facts": []},
            error=None,
            processing_time_ms=150,
            completed_at=datetime.now(UTC),
        )

        assert response.request_id == "test-123"
        assert response.status == "success"
        assert response.result == {"facts": []}
        assert response.error is None
        assert response.processing_time_ms == 150

    def test_llm_response_serializes_to_json(self):
        """Test that LLMResponse can be serialized to JSON."""
        from src.services.llm.models import LLMResponse

        response = LLMResponse(
            request_id="test-123",
            status="success",
            result={"data": "test"},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        json_str = response.to_json()
        data = json.loads(json_str)
        assert data["request_id"] == "test-123"
        assert data["status"] == "success"

    def test_llm_response_deserializes_from_json(self):
        """Test that LLMResponse can be deserialized from JSON."""
        from src.services.llm.models import LLMResponse

        original = LLMResponse(
            request_id="test-789",
            status="error",
            result=None,
            error="Timeout",
            processing_time_ms=5000,
            completed_at=datetime.now(UTC),
        )

        json_str = original.to_json()
        restored = LLMResponse.from_json(json_str)

        assert restored.request_id == original.request_id
        assert restored.status == original.status
        assert restored.error == original.error

    def test_llm_response_validates_status(self):
        """Test that LLMResponse validates status values."""
        from src.services.llm.models import LLMResponse, InvalidStatusError

        with pytest.raises(InvalidStatusError):
            LLMResponse(
                request_id="test",
                status="invalid_status",  # Not valid
                result=None,
                error=None,
                processing_time_ms=0,
                completed_at=datetime.now(UTC),
            )


class TestLLMRequestQueue:
    """Tests for LLMRequestQueue Redis operations."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.xlen = AsyncMock(return_value=0)
        redis.xadd = AsyncMock(return_value="1234567890-0")
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()
        redis.xgroup_create = AsyncMock()
        redis.delete = AsyncMock(return_value=1)
        redis.publish = AsyncMock(return_value=1)

        # Mock pubsub object
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_pubsub.get_message = AsyncMock(return_value=None)
        redis.pubsub = MagicMock(return_value=mock_pubsub)

        return redis

    @pytest.fixture
    def queue(self, mock_redis):
        """Create LLMRequestQueue with mock Redis."""
        from src.services.llm.queue import LLMRequestQueue

        return LLMRequestQueue(
            redis=mock_redis,
            max_queue_depth=1000,
            backpressure_threshold=500,
            poll_fallback_interval=0.5,  # Fast fallback for tests
        )

    @pytest.mark.asyncio
    async def test_submit_adds_request_to_stream(self, queue, mock_redis):
        """Test that submit adds request to Redis stream."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-submit",
            request_type="extract_field_group",
            payload={"content": "test"},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        request_id = await queue.submit(request)

        assert request_id == "test-submit"
        mock_redis.xadd.assert_called_once()

        # Check the stream key
        call_args = mock_redis.xadd.call_args
        assert call_args[0][0] == "llm:requests"  # Stream key

    @pytest.mark.asyncio
    async def test_submit_rejects_when_queue_full(self, queue, mock_redis):
        """Test that submit raises error when queue is full."""
        from src.services.llm.models import LLMRequest
        from src.services.llm.queue import QueueFullError

        # Simulate full queue
        mock_redis.xlen = AsyncMock(return_value=1000)

        request = LLMRequest(
            request_id="test-full",
            request_type="extract_field_group",
            payload={},
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        with pytest.raises(QueueFullError) as exc_info:
            await queue.submit(request)

        assert "1000" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_get_queue_depth_returns_stream_length(self, queue, mock_redis):
        """Test that get_queue_depth returns correct value."""
        mock_redis.xlen = AsyncMock(return_value=42)

        depth = await queue.get_queue_depth()

        assert depth == 42
        mock_redis.xlen.assert_called_with("llm:requests")

    @pytest.mark.asyncio
    async def test_get_backpressure_status_ok(self, queue, mock_redis):
        """Test backpressure status when queue is healthy."""
        mock_redis.xlen = AsyncMock(return_value=100)  # Well below threshold

        status = await queue.get_backpressure_status()

        # Must return dict with should_wait key for pipeline.py compatibility
        assert isinstance(status, dict)
        assert status["should_wait"] is False
        assert status["status"] == "ok"
        assert status["queue_depth"] == 100
        assert status["threshold"] == 500

    @pytest.mark.asyncio
    async def test_get_backpressure_status_slow(self, queue, mock_redis):
        """Test backpressure status when queue is getting full."""
        mock_redis.xlen = AsyncMock(return_value=400)  # 80% of 500 threshold

        status = await queue.get_backpressure_status()

        # 80% of threshold should trigger should_wait
        assert isinstance(status, dict)
        assert status["should_wait"] is True
        assert status["status"] == "slow"
        assert status["queue_depth"] == 400

    @pytest.mark.asyncio
    async def test_get_backpressure_status_full(self, queue, mock_redis):
        """Test backpressure status when queue is full."""
        mock_redis.xlen = AsyncMock(return_value=600)  # Above 500 threshold

        status = await queue.get_backpressure_status()

        assert isinstance(status, dict)
        assert status["should_wait"] is True
        assert status["status"] == "full"
        assert status["queue_depth"] == 600

    @pytest.mark.asyncio
    async def test_wait_for_result_returns_response(self, queue, mock_redis):
        """Test that wait_for_result returns response when available."""
        from src.services.llm.models import LLMResponse

        # Mock response available immediately
        response = LLMResponse(
            request_id="test-wait",
            status="success",
            result={"facts": ["fact1"]},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )
        mock_redis.get = AsyncMock(return_value=response.to_json())

        result = await queue.wait_for_result("test-wait", timeout=5.0)

        assert result.request_id == "test-wait"
        assert result.status == "success"
        assert result.result == {"facts": ["fact1"]}

    @pytest.mark.asyncio
    async def test_wait_for_result_polls_until_available(self, queue, mock_redis):
        """Test that wait_for_result polls until response is available."""
        from src.services.llm.models import LLMResponse

        # First two calls return None, third returns response
        call_count = 0

        async def mock_get(key):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                return None
            return LLMResponse(
                request_id="test-poll",
                status="success",
                result={},
                error=None,
                processing_time_ms=100,
                completed_at=datetime.now(UTC),
            ).to_json()

        mock_redis.get = mock_get

        result = await queue.wait_for_result("test-poll", timeout=5.0)

        assert result.status == "success"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_wait_for_result_times_out(self, queue, mock_redis):
        """Test that wait_for_result raises TimeoutError."""
        from src.services.llm.queue import RequestTimeoutError

        # Never return a response
        mock_redis.get = AsyncMock(return_value=None)

        with pytest.raises(RequestTimeoutError):
            await queue.wait_for_result("test-timeout", timeout=0.5)


class TestWaitForResultCleanup:
    """Tests for response key cleanup after wait_for_result."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.xlen = AsyncMock(return_value=0)
        redis.get = AsyncMock(return_value=None)
        redis.delete = AsyncMock(return_value=1)
        redis.publish = AsyncMock(return_value=1)

        # Mock pubsub object
        mock_pubsub = MagicMock()
        mock_pubsub.subscribe = AsyncMock()
        mock_pubsub.unsubscribe = AsyncMock()
        mock_pubsub.aclose = AsyncMock()
        mock_pubsub.get_message = AsyncMock(return_value=None)
        redis.pubsub = MagicMock(return_value=mock_pubsub)

        return redis

    @pytest.fixture
    def queue(self, mock_redis):
        """Create LLMRequestQueue with mock Redis."""
        from src.services.llm.queue import LLMRequestQueue

        return LLMRequestQueue(
            redis=mock_redis,
            max_queue_depth=1000,
            backpressure_threshold=500,
            poll_interval=0.01,  # Fast polling for tests
            poll_fallback_interval=0.5,  # Fast fallback for tests
        )

    @pytest.mark.asyncio
    async def test_wait_for_result_deletes_response_key_after_read(self, queue, mock_redis):
        """Test that wait_for_result deletes the response key after reading."""
        from src.services.llm.models import LLMResponse

        response = LLMResponse(
            request_id="test-cleanup",
            status="success",
            result={"facts": []},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )
        mock_redis.get = AsyncMock(return_value=response.to_json())

        result = await queue.wait_for_result("test-cleanup", timeout=5.0)

        assert result.request_id == "test-cleanup"
        mock_redis.delete.assert_called_once_with("llm:response:test-cleanup")

    @pytest.mark.asyncio
    async def test_wait_for_result_deletes_key_on_error_response(self, queue, mock_redis):
        """Test that wait_for_result deletes key even when response has error status."""
        from src.services.llm.models import LLMResponse

        response = LLMResponse(
            request_id="test-error",
            status="error",
            result=None,
            error="LLM processing failed",
            processing_time_ms=50,
            completed_at=datetime.now(UTC),
        )
        mock_redis.get = AsyncMock(return_value=response.to_json())

        result = await queue.wait_for_result("test-error", timeout=5.0)

        assert result.status == "error"
        mock_redis.delete.assert_called_once_with("llm:response:test-error")

    @pytest.mark.asyncio
    async def test_wait_for_result_no_delete_on_timeout(self, queue, mock_redis):
        """Test that wait_for_result does NOT delete key when timeout occurs."""
        from src.services.llm.queue import RequestTimeoutError

        mock_redis.get = AsyncMock(return_value=None)

        with pytest.raises(RequestTimeoutError):
            await queue.wait_for_result("test-timeout", timeout=0.05)

        # Should NOT have called delete since no response was read
        mock_redis.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_wait_for_result_cleanup_failure_does_not_affect_return(self, queue, mock_redis):
        """Test that delete failure does not affect the returned response."""
        from src.services.llm.models import LLMResponse

        response = LLMResponse(
            request_id="test-delete-fail",
            status="success",
            result={"data": "test"},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )
        mock_redis.get = AsyncMock(return_value=response.to_json())
        mock_redis.delete = AsyncMock(side_effect=Exception("Redis delete failed"))

        # Should still return the response despite delete failure
        result = await queue.wait_for_result("test-delete-fail", timeout=5.0)

        assert result.request_id == "test-delete-fail"
        assert result.status == "success"
        mock_redis.delete.assert_called_once()


class TestLLMWorker:
    """Tests for LLMWorker processing."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        redis.xreadgroup = AsyncMock(return_value=[])
        redis.xack = AsyncMock()
        redis.setex = AsyncMock()
        redis.publish = AsyncMock(return_value=1)
        return redis

    @pytest.fixture
    def mock_llm_client(self):
        """Create mock LLM client."""
        client = AsyncMock()
        client.chat = AsyncMock()
        client.chat.completions = AsyncMock()
        client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"facts": []}'))]
            )
        )
        return client

    @pytest.fixture
    def worker(self, mock_redis, mock_llm_client):
        """Create LLMWorker with mocks."""
        from src.services.llm.worker import LLMWorker

        return LLMWorker(
            redis=mock_redis,
            llm_client=mock_llm_client,
            worker_id="test-worker-1",
            initial_concurrency=10,
            max_concurrency=50,
            min_concurrency=5,
        )

    @pytest.mark.asyncio
    async def test_worker_creates_consumer_group(self, worker, mock_redis):
        """Test that worker creates Redis consumer group on init."""
        await worker.initialize()

        mock_redis.xgroup_create.assert_called_once()
        call_args = mock_redis.xgroup_create.call_args
        assert call_args[0][0] == "llm:requests"  # Stream name
        assert call_args[0][1] == "llm-workers"  # Group name

    @pytest.mark.asyncio
    async def test_worker_reads_from_stream(self, worker, mock_redis):
        """Test that worker reads requests from stream."""
        # Return empty batch
        mock_redis.xreadgroup = AsyncMock(return_value=[])

        await worker.process_batch()

        mock_redis.xreadgroup.assert_called_once()
        call_kwargs = mock_redis.xreadgroup.call_args[1]
        assert call_kwargs["groupname"] == "llm-workers"
        assert call_kwargs["consumername"] == "test-worker-1"

    @pytest.mark.asyncio
    async def test_worker_processes_request(self, worker, mock_redis, mock_llm_client):
        """Test that worker processes a request and stores response."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-process",
            request_type="extract_field_group",
            payload={
                "content": "Test content",
                "field_group": {"name": "test"},
                "company_name": "Test Co",
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        # Mock stream read returning one message
        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                ("llm:requests", [("entry-1", {"data": request.to_json()})])
            ]
        )

        await worker.process_batch()

        # Should call LLM
        mock_llm_client.chat.completions.create.assert_called_once()

        # Should store response
        mock_redis.setex.assert_called_once()
        call_args = mock_redis.setex.call_args
        assert "llm:response:test-process" in call_args[0][0]

        # Should acknowledge message
        mock_redis.xack.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_skips_expired_requests(self, worker, mock_redis, mock_llm_client):
        """Test that worker skips expired requests."""
        from src.services.llm.models import LLMRequest

        # Expired request
        request = LLMRequest(
            request_id="test-expired",
            request_type="extract_field_group",
            payload={},
            priority=5,
            created_at=datetime.now(UTC) - timedelta(seconds=600),
            timeout_at=datetime.now(UTC) - timedelta(seconds=300),  # Already expired
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[
                ("llm:requests", [("entry-1", {"data": request.to_json()})])
            ]
        )

        await worker.process_batch()

        # Should NOT call LLM
        mock_llm_client.chat.completions.create.assert_not_called()

        # Should still store response (with timeout status)
        mock_redis.setex.assert_called_once()

        # Should acknowledge message
        mock_redis.xack.assert_called_once()

    @pytest.mark.asyncio
    async def test_worker_adaptive_concurrency_backs_off(self, worker):
        """Test that worker backs off when timeout rate is high."""
        # Simulate high timeout rate
        worker.success_count = 5
        worker.timeout_count = 5  # 50% timeout rate
        worker.last_adjustment = 0  # Force adjustment

        old_concurrency = worker.concurrency
        await worker.maybe_adjust_concurrency()

        # Should have backed off
        assert worker.concurrency < old_concurrency
        assert worker.concurrency >= worker.min_concurrency

    @pytest.mark.asyncio
    async def test_worker_adaptive_concurrency_scales_up(self, worker):
        """Test that worker scales up when performing well."""
        # Simulate good performance
        worker.success_count = 100
        worker.timeout_count = 1  # 1% timeout rate
        worker.last_adjustment = 0  # Force adjustment

        old_concurrency = worker.concurrency
        await worker.maybe_adjust_concurrency()

        # Should have scaled up
        assert worker.concurrency > old_concurrency
        assert worker.concurrency <= worker.max_concurrency

    @pytest.mark.asyncio
    async def test_worker_processes_multiple_requests_concurrently(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that worker processes multiple requests in parallel."""
        from src.services.llm.models import LLMRequest

        # Track concurrent executions
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def slow_llm_call(*args, **kwargs):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)

            await asyncio.sleep(0.1)  # Simulate LLM latency

            async with lock:
                current_concurrent -= 1

            return MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"facts": []}'))]
            )

        mock_llm_client.chat.completions.create = slow_llm_call

        # Create 5 requests
        requests = []
        for i in range(5):
            req = LLMRequest(
                request_id=f"test-concurrent-{i}",
                request_type="extract_field_group",
                payload={"content": f"content {i}"},
                priority=5,
                created_at=datetime.now(UTC),
                timeout_at=datetime.now(UTC) + timedelta(seconds=300),
            )
            requests.append(("entry-{i}", {"data": req.to_json()}))

        mock_redis.xreadgroup = AsyncMock(
            return_value=[("llm:requests", requests)]
        )

        await worker.process_batch()

        # Should have processed concurrently
        assert max_concurrent > 1, f"Expected concurrent processing but max was {max_concurrent}"
