"""Tests for async Redis client."""

import pytest


@pytest.mark.asyncio
async def test_get_async_redis_returns_async_client():
    """get_async_redis should return an async Redis client."""
    from redis_client import get_async_redis

    async_redis = await get_async_redis()

    # Verify it's an async Redis client by checking for async methods
    assert hasattr(async_redis, "get")
    assert hasattr(async_redis, "set")
    assert hasattr(async_redis, "xadd")  # For streams
    assert hasattr(async_redis, "xlen")

    # Close the connection
    await async_redis.close()


@pytest.mark.asyncio
async def test_queue_uses_async_redis():
    """Queue operations should work with async Redis."""
    import redis.asyncio as aioredis

    from services.llm.queue import LLMRequestQueue

    # Create async Redis client
    async_redis = aioredis.from_url("redis://localhost", decode_responses=True)

    try:
        queue = LLMRequestQueue(redis=async_redis)

        # Should not raise TypeError (async operations work)
        depth = await queue.get_queue_depth()
        assert isinstance(depth, int)
    finally:
        await async_redis.close()
