"""Tests for extract-schema endpoint async Redis usage.

TDD: These tests verify that the /extract-schema endpoint uses async Redis
when llm_queue_enabled is True.

The bug is that extraction.py:285 passes sync redis_client to LLMRequestQueue,
but LLMRequestQueue methods use async (await self.redis.xadd, etc.).
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestExtractSchemaAsyncRedisRequirement:
    """Test that extract-schema uses async Redis when queue enabled."""

    def test_extraction_endpoint_imports_get_async_redis(self):
        """extract_schema must import get_async_redis to use async Redis.

        This test verifies the fix is in place by checking that the
        function's source code imports get_async_redis when queue is enabled.
        """
        from api.v1.extraction import extract_schema

        # Get the source code of the function
        source = inspect.getsource(extract_schema)

        # When llm_queue_enabled is True, the code should:
        # 1. Import get_async_redis
        # 2. Call await get_async_redis()
        # 3. Pass the result to LLMRequestQueue

        # Check that get_async_redis is imported (the fix)
        assert "get_async_redis" in source, (
            "extract_schema must import get_async_redis for async Redis client"
        )

        # Check that await get_async_redis() is called
        assert (
            "await get_async_redis()" in source
            or "await get_async_redis()" in source.replace(" ", "")
        ), "extract_schema must call 'await get_async_redis()' to get async client"

    def test_llm_request_queue_requires_async_redis(self):
        """LLMRequestQueue methods use await, so redis must be async.

        This test documents the requirement that LLMRequestQueue needs
        an async Redis client because its methods use await.
        """
        from services.llm.queue import LLMRequestQueue

        # Check that key methods are async (use await on self.redis)
        source = inspect.getsource(LLMRequestQueue)

        # These are the Redis operations that require async client
        async_redis_calls = [
            "await self.redis.xadd",
            "await self.redis.xlen",
            "await self.redis.get",
            "await self.redis.delete",
            "await self.redis.setex",
        ]

        for call in async_redis_calls:
            assert call in source, (
                f"LLMRequestQueue uses '{call}' - requires async Redis client"
            )


class TestAsyncRedisVsSyncRedis:
    """Tests demonstrating why async Redis is required."""

    @pytest.mark.asyncio
    async def test_async_redis_client_methods_are_coroutines(self):
        """Async Redis client methods should be awaitable coroutines."""
        # When using AsyncMock without spec, methods are automatically async
        async_redis = AsyncMock()

        # Call a method - should be awaitable
        result = await async_redis.xadd("key", {"field": "value"})
        # This works because AsyncMock makes methods return coroutines
        async_redis.xadd.assert_called_once()

    def test_sync_redis_client_methods_are_not_coroutines(self):
        """Sync Redis client methods are NOT coroutines.

        This demonstrates why using sync redis_client with LLMRequestQueue
        (which uses await) will fail.
        """
        import redis

        # Check that sync Redis methods are not coroutines
        # This proves that passing sync client to LLMRequestQueue is wrong
        sync_redis = MagicMock(spec=redis.Redis)

        # Sync Redis .xadd() is not a coroutine - can't be awaited
        result = sync_redis.xadd("key", {"field": "value"})
        assert not hasattr(result, "__await__"), (
            "Sync Redis methods are not coroutines - cannot be awaited"
        )
