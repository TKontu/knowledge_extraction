"""Redis connection and client management."""

from typing import Generator

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
