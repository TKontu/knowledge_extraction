"""Tests for DomainRateLimiter."""

import asyncio
import time
from unittest.mock import Mock

import pytest

from services.scraper.rate_limiter import (
    DomainRateLimiter,
    RateLimitConfig,
    RateLimitExceeded,
)


class TestRateLimitConfig:
    """Test suite for RateLimitConfig."""

    def test_config_initialization_with_defaults(self):
        """Test RateLimitConfig initializes with default values."""
        config = RateLimitConfig()
        assert config.delay_min == 2
        assert config.delay_max == 5
        assert config.daily_limit == 500

    def test_config_initialization_with_custom_values(self):
        """Test RateLimitConfig accepts custom values."""
        config = RateLimitConfig(delay_min=1, delay_max=3, daily_limit=100)
        assert config.delay_min == 1
        assert config.delay_max == 3
        assert config.daily_limit == 100


class TestDomainRateLimiter:
    """Test suite for DomainRateLimiter."""

    @pytest.fixture
    def redis_mock(self):
        """Mock Redis client with stateful behavior."""
        mock = Mock()
        # Use a dict to simulate Redis state
        mock._data = {}

        def get_impl(key):
            return mock._data.get(key)

        def setex_impl(key, ttl, value):
            mock._data[key] = value
            return True

        def incr_impl(key):
            current = int(mock._data.get(key, 0))
            new_value = current + 1
            mock._data[key] = str(new_value)
            return new_value

        def delete_impl(key):
            mock._data.pop(key, None)
            return True

        def ttl_impl(key):
            # Return -1 if key doesn't exist or no TTL
            return -1

        mock.get.side_effect = get_impl
        mock.setex.side_effect = setex_impl
        mock.incr.side_effect = incr_impl
        mock.delete.side_effect = delete_impl
        mock.ttl.side_effect = ttl_impl
        mock.expire.return_value = True

        return mock

    @pytest.fixture
    def config(self):
        """Rate limit configuration."""
        return RateLimitConfig(delay_min=1, delay_max=2, daily_limit=10)

    @pytest.fixture
    def rate_limiter(self, redis_mock, config):
        """Create DomainRateLimiter instance."""
        return DomainRateLimiter(redis_client=redis_mock, config=config)

    @pytest.mark.asyncio
    async def test_rate_limiter_initialization(self, rate_limiter, redis_mock, config):
        """Test DomainRateLimiter initializes correctly."""
        assert rate_limiter.redis == redis_mock
        assert rate_limiter.config == config

    @pytest.mark.asyncio
    async def test_wait_if_needed_enforces_delay_on_second_request(
        self, rate_limiter, redis_mock
    ):
        """Test that second request to same domain is delayed."""
        domain = "example.com"

        # First request - no delay
        start = time.time()
        await rate_limiter.wait_if_needed(domain)
        first_duration = time.time() - start

        # Second request - should be delayed
        start = time.time()
        await rate_limiter.wait_if_needed(domain)
        second_duration = time.time() - start

        # Second request should take longer (at least delay_min seconds)
        assert second_duration >= 1.0  # delay_min is 1 second
        assert first_duration < 0.1  # First request should be immediate

    @pytest.mark.asyncio
    async def test_wait_if_needed_uses_different_keys_for_different_domains(
        self, rate_limiter, redis_mock
    ):
        """Test that different domains don't interfere with each other."""
        domain1 = "example.com"
        domain2 = "another.com"

        # Both should complete quickly (no waiting)
        start = time.time()
        await rate_limiter.wait_if_needed(domain1)
        await rate_limiter.wait_if_needed(domain2)
        duration = time.time() - start

        # Should not wait since different domains
        assert duration < 0.5

    @pytest.mark.asyncio
    async def test_check_daily_limit_returns_true_when_under_limit(
        self, rate_limiter, redis_mock
    ):
        """Test check_daily_limit returns True when under limit."""
        domain = "example.com"
        # Manually set count to 5 (under limit of 10)
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "5"

        result = await rate_limiter.check_daily_limit(domain)

        assert result is True

    @pytest.mark.asyncio
    async def test_check_daily_limit_returns_false_when_at_limit(
        self, rate_limiter, redis_mock
    ):
        """Test check_daily_limit returns False when at limit."""
        domain = "example.com"
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "10"  # At limit (10/10)

        result = await rate_limiter.check_daily_limit(domain)

        assert result is False

    @pytest.mark.asyncio
    async def test_check_daily_limit_returns_false_when_over_limit(
        self, rate_limiter, redis_mock
    ):
        """Test check_daily_limit returns False when over limit."""
        domain = "example.com"
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "15"  # Over limit (15/10)

        result = await rate_limiter.check_daily_limit(domain)

        assert result is False

    @pytest.mark.asyncio
    async def test_check_daily_limit_returns_true_when_no_requests_yet(
        self, rate_limiter, redis_mock
    ):
        """Test check_daily_limit returns True when no requests made yet."""
        domain = "example.com"
        # Don't set any data - simulates no requests yet

        result = await rate_limiter.check_daily_limit(domain)

        assert result is True

    @pytest.mark.asyncio
    async def test_increment_daily_count_increments_counter(
        self, rate_limiter, redis_mock
    ):
        """Test increment_daily_count increments Redis counter."""
        domain = "example.com"

        count = await rate_limiter.increment_daily_count(domain)

        assert count == 1
        # Verify Redis incr was called with correct key
        call_args = redis_mock.incr.call_args[0][0]
        assert domain in call_args
        assert "daily_count" in call_args

    @pytest.mark.asyncio
    async def test_increment_daily_count_sets_expiry_on_first_request(
        self, rate_limiter, redis_mock
    ):
        """Test that expiry is set on first request of the day."""
        domain = "example.com"

        await rate_limiter.increment_daily_count(domain)

        # Verify expire was called to set TTL
        redis_mock.expire.assert_called()

    @pytest.mark.asyncio
    async def test_get_daily_count_returns_current_count(
        self, rate_limiter, redis_mock
    ):
        """Test get_daily_count returns current count from Redis."""
        domain = "example.com"
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "42"

        count = await rate_limiter.get_daily_count(domain)

        assert count == 42

    @pytest.mark.asyncio
    async def test_get_daily_count_returns_zero_when_no_count(
        self, rate_limiter, redis_mock
    ):
        """Test get_daily_count returns 0 when no count exists."""
        domain = "example.com"
        # Don't set any data

        count = await rate_limiter.get_daily_count(domain)

        assert count == 0

    @pytest.mark.asyncio
    async def test_acquire_checks_limit_and_increments(self, rate_limiter, redis_mock):
        """Test acquire checks limit and increments count on success."""
        domain = "example.com"
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "5"  # Under limit

        await rate_limiter.acquire(domain)

        # Count should be incremented
        assert redis_mock._data[key] == "6"

    @pytest.mark.asyncio
    async def test_acquire_raises_exception_when_limit_exceeded(
        self, rate_limiter, redis_mock
    ):
        """Test acquire raises RateLimitExceeded when limit reached."""
        domain = "example.com"
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "10"  # At limit

        # Mock ttl to return 3600
        redis_mock.ttl.side_effect = lambda k: 3600

        with pytest.raises(RateLimitExceeded) as exc_info:
            await rate_limiter.acquire(domain)

        assert domain in str(exc_info.value)
        assert "10" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_acquire_enforces_delay_between_requests(
        self, rate_limiter, redis_mock
    ):
        """Test acquire enforces delay between requests to same domain."""
        domain = "example.com"
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "5"  # Under limit

        # First request
        start = time.time()
        await rate_limiter.acquire(domain)
        first_duration = time.time() - start

        # Second request should be delayed
        start = time.time()
        await rate_limiter.acquire(domain)
        second_duration = time.time() - start

        assert second_duration >= 1.0  # delay_min

    @pytest.mark.asyncio
    async def test_reset_daily_count_resets_counter_to_zero(
        self, rate_limiter, redis_mock
    ):
        """Test reset_daily_count resets the counter."""
        domain = "example.com"

        await rate_limiter.reset_daily_count(domain)

        # Should delete the key
        call_args = redis_mock.delete.call_args[0][0]
        assert domain in call_args
        assert "daily_count" in call_args

    @pytest.mark.asyncio
    async def test_get_time_until_reset_returns_seconds_until_midnight(
        self, rate_limiter, redis_mock
    ):
        """Test get_time_until_reset returns time until midnight."""
        domain = "example.com"
        redis_mock.ttl.side_effect = lambda k: 3600  # 1 hour until reset

        seconds = await rate_limiter.get_time_until_reset(domain)

        assert seconds == 3600

    @pytest.mark.asyncio
    async def test_get_time_until_reset_returns_zero_when_no_ttl(
        self, rate_limiter, redis_mock
    ):
        """Test get_time_until_reset returns 0 when no TTL set."""
        domain = "example.com"
        redis_mock.ttl.side_effect = lambda k: -1  # No TTL

        seconds = await rate_limiter.get_time_until_reset(domain)

        assert seconds == 0

    @pytest.mark.asyncio
    async def test_concurrent_requests_to_same_domain_are_serialized(
        self, rate_limiter, redis_mock
    ):
        """Test concurrent requests to same domain are properly serialized."""
        domain = "example.com"
        key = rate_limiter._daily_count_key(domain)
        redis_mock._data[key] = "5"  # Under limit

        # Launch 3 concurrent requests
        start = time.time()
        await asyncio.gather(
            rate_limiter.acquire(domain),
            rate_limiter.acquire(domain),
            rate_limiter.acquire(domain),
        )
        duration = time.time() - start

        # Should take at least 2 seconds (2 delays of 1 second minimum each)
        assert duration >= 2.0

    @pytest.mark.asyncio
    async def test_concurrent_requests_to_different_domains_run_parallel(
        self, rate_limiter, redis_mock
    ):
        """Test concurrent requests to different domains run in parallel."""
        # Set counts for each domain
        for domain in ["example.com", "another.com", "third.com"]:
            key = rate_limiter._daily_count_key(domain)
            redis_mock._data[key] = "5"

        # Launch concurrent requests to different domains
        start = time.time()
        await asyncio.gather(
            rate_limiter.acquire("example.com"),
            rate_limiter.acquire("another.com"),
            rate_limiter.acquire("third.com"),
        )
        duration = time.time() - start

        # Should complete quickly since different domains
        assert duration < 1.0


class TestRateLimitExceeded:
    """Test suite for RateLimitExceeded exception."""

    def test_exception_message_includes_domain_and_limit(self):
        """Test exception message is informative."""
        exc = RateLimitExceeded(domain="example.com", limit=100, reset_in=3600)

        message = str(exc)
        assert "example.com" in message
        assert "100" in message
        assert "3600" in message

    def test_exception_stores_domain_attribute(self):
        """Test exception stores domain as attribute."""
        exc = RateLimitExceeded(domain="example.com", limit=100, reset_in=3600)

        assert exc.domain == "example.com"
        assert exc.limit == 100
        assert exc.reset_in == 3600
