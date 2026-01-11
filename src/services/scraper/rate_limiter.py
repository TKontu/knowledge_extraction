"""Domain-based rate limiter using Redis."""

import asyncio
import random
import time
from dataclasses import dataclass
from datetime import datetime, date, timedelta
from typing import Optional

import redis


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting.

    Attributes:
        delay_min: Minimum delay between requests in seconds.
        delay_max: Maximum delay between requests in seconds.
        daily_limit: Maximum requests per domain per day.
    """

    delay_min: int = 2
    delay_max: int = 5
    daily_limit: int = 500


class RateLimitExceeded(Exception):
    """Exception raised when rate limit is exceeded.

    Attributes:
        domain: The domain that exceeded the limit.
        limit: The daily limit that was exceeded.
        reset_in: Seconds until the limit resets.
    """

    def __init__(self, domain: str, limit: int, reset_in: int) -> None:
        """Initialize RateLimitExceeded exception.

        Args:
            domain: Domain that exceeded the limit.
            limit: Daily limit value.
            reset_in: Seconds until reset.
        """
        self.domain = domain
        self.limit = limit
        self.reset_in = reset_in
        super().__init__(
            f"Rate limit exceeded for {domain}: {limit} requests per day. "
            f"Resets in {reset_in} seconds."
        )


class DomainRateLimiter:
    """Rate limiter for controlling request frequency per domain.

    Uses Redis to track:
    - Last request timestamp per domain (for delay enforcement)
    - Daily request count per domain (for limit enforcement)

    Example:
        limiter = DomainRateLimiter(redis_client, config)

        # Before making request
        await limiter.acquire("example.com")
        # Make request here
    """

    def __init__(
        self, redis_client: redis.Redis, config: RateLimitConfig
    ) -> None:
        """Initialize DomainRateLimiter.

        Args:
            redis_client: Redis client for distributed state.
            config: Rate limiting configuration.
        """
        self.redis = redis_client
        self.config = config
        self._locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, domain: str) -> asyncio.Lock:
        """Get or create lock for domain.

        Args:
            domain: Domain name.

        Returns:
            Asyncio lock for the domain.
        """
        if domain not in self._locks:
            self._locks[domain] = asyncio.Lock()
        return self._locks[domain]

    def _last_request_key(self, domain: str) -> str:
        """Generate Redis key for last request timestamp.

        Args:
            domain: Domain name.

        Returns:
            Redis key string.
        """
        return f"ratelimit:{domain}:last_request"

    def _daily_count_key(self, domain: str) -> str:
        """Generate Redis key for daily request count.

        Args:
            domain: Domain name.

        Returns:
            Redis key string.
        """
        today = date.today().isoformat()
        return f"ratelimit:{domain}:daily_count:{today}"

    async def wait_if_needed(self, domain: str) -> None:
        """Wait if necessary to enforce delay between requests.

        Args:
            domain: Domain to check.
        """
        lock = self._get_lock(domain)
        async with lock:
            key = self._last_request_key(domain)
            last_request_str = self.redis.get(key)

            if last_request_str:
                last_request = float(last_request_str)
                elapsed = time.time() - last_request
                delay = random.uniform(self.config.delay_min, self.config.delay_max)

                if elapsed < delay:
                    wait_time = delay - elapsed
                    await asyncio.sleep(wait_time)

            # Update last request time
            self.redis.setex(key, 3600, str(time.time()))  # Expire after 1 hour

    async def check_daily_limit(self, domain: str) -> bool:
        """Check if domain is under daily limit.

        Args:
            domain: Domain to check.

        Returns:
            True if under limit, False if at or over limit.
        """
        count = await self.get_daily_count(domain)
        return count < self.config.daily_limit

    async def get_daily_count(self, domain: str) -> int:
        """Get current daily request count for domain.

        Args:
            domain: Domain to check.

        Returns:
            Current request count for today.
        """
        key = self._daily_count_key(domain)
        count_str = self.redis.get(key)
        return int(count_str) if count_str else 0

    async def increment_daily_count(self, domain: str) -> int:
        """Increment daily request count for domain.

        Args:
            domain: Domain to increment.

        Returns:
            New count after increment.
        """
        key = self._daily_count_key(domain)
        count = self.redis.incr(key)

        # Set expiry to end of day if this is first request
        ttl = self.redis.ttl(key)
        if ttl == -1:  # No expiry set
            # Calculate seconds until midnight
            now = datetime.now()
            midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time())
            seconds_until_midnight = int((midnight - now).total_seconds())
            self.redis.expire(key, seconds_until_midnight)

        return count

    async def get_time_until_reset(self, domain: str) -> int:
        """Get seconds until daily count resets.

        Args:
            domain: Domain to check.

        Returns:
            Seconds until reset (0 if no TTL set).
        """
        key = self._daily_count_key(domain)
        ttl = self.redis.ttl(key)
        return max(0, ttl) if ttl > 0 else 0

    async def reset_daily_count(self, domain: str) -> None:
        """Reset daily count for domain (mainly for testing).

        Args:
            domain: Domain to reset.
        """
        key = self._daily_count_key(domain)
        self.redis.delete(key)

    async def acquire(self, domain: str) -> None:
        """Acquire permission to make request to domain.

        Checks daily limit and enforces delay between requests.

        Args:
            domain: Domain to request.

        Raises:
            RateLimitExceeded: If daily limit has been reached.
        """
        # Check daily limit
        if not await self.check_daily_limit(domain):
            reset_in = await self.get_time_until_reset(domain)
            raise RateLimitExceeded(
                domain=domain,
                limit=self.config.daily_limit,
                reset_in=reset_in,
            )

        # Wait if needed to enforce delay
        await self.wait_if_needed(domain)

        # Increment daily count
        await self.increment_daily_count(domain)
