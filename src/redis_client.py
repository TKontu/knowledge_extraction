"""Redis connection and client management."""

from collections.abc import Generator

import redis
import redis.asyncio as aioredis

from config import settings

# Create Redis client (sync)
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
)


# Singleton async Redis client (lazily initialized)
_async_redis_client: aioredis.Redis | None = None


async def get_async_redis() -> aioredis.Redis:
    """Get async Redis client for queue operations.

    Uses a singleton pattern to reuse the connection pool across requests.

    Returns:
        Async Redis client instance.
    """
    global _async_redis_client
    if _async_redis_client is None:
        _async_redis_client = aioredis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=5,
            socket_timeout=5,
        )
    return _async_redis_client


def get_redis() -> Generator[redis.Redis, None, None]:
    """
    Dependency for getting Redis client.

    Yields:
        Redis client instance.
    """
    yield redis_client


def get_redis_client() -> redis.Redis | None:
    """
    Get Redis client instance.

    Returns:
        Redis client instance or None if unavailable.
    """
    try:
        redis_client.ping()
        return redis_client
    except (redis.ConnectionError, redis.TimeoutError, OSError):
        return None


def check_redis_connection() -> bool:
    """
    Check if Redis connection is working.

    Returns:
        True if connection succeeds, False otherwise.
    """
    try:
        # Ping Redis to verify connection
        redis_client.ping()
        return True
    except (redis.ConnectionError, redis.TimeoutError, OSError):
        return False
