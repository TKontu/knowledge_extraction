"""Redis connection and client management."""

from collections.abc import Generator

import redis

from config import settings

# Create Redis client
redis_client = redis.from_url(
    settings.redis_url,
    decode_responses=True,
    socket_connect_timeout=5,
    socket_timeout=5,
)


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
    except Exception:
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
    except Exception:
        return False
