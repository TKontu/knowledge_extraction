"""Tests for scraper retry logic."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from services.scraper.retry import RetryConfig, retry_with_backoff


class TestRetryConfig:
    def test_default_config(self):
        """Default config should have sensible values."""
        config = RetryConfig()
        assert config.max_retries == 3
        assert config.base_delay == 1.0
        assert config.max_delay == 60.0

    def test_get_delay_exponential(self):
        """Delay should increase exponentially."""
        config = RetryConfig(base_delay=1.0, exponential_base=2.0, jitter=False)

        assert config.get_delay(0) == 1.0
        assert config.get_delay(1) == 2.0
        assert config.get_delay(2) == 4.0
        assert config.get_delay(3) == 8.0

    def test_get_delay_capped_at_max(self):
        """Delay should not exceed max_delay."""
        config = RetryConfig(base_delay=10.0, max_delay=30.0, jitter=False)

        assert config.get_delay(0) == 10.0
        assert config.get_delay(1) == 20.0
        assert config.get_delay(2) == 30.0  # Capped
        assert config.get_delay(3) == 30.0  # Still capped

    def test_get_delay_with_jitter(self):
        """Jitter should add randomness."""
        config = RetryConfig(base_delay=10.0, jitter=True)

        delays = [config.get_delay(0) for _ in range(10)]
        # Should have variation
        assert len(set(delays)) > 1


class TestRetryWithBackoff:
    @pytest.mark.asyncio
    async def test_succeeds_on_first_try(self):
        """Should return immediately on success."""
        func = AsyncMock(return_value="success")
        config = RetryConfig(max_retries=3)

        result = await retry_with_backoff(func, config)

        assert result == "success"
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_failure(self):
        """Should retry on transient failure."""
        func = AsyncMock(side_effect=[ValueError("fail"), ValueError("fail"), "success"])
        config = RetryConfig(max_retries=3, base_delay=0.01)

        result = await retry_with_backoff(
            func,
            config,
            retryable_exceptions=(ValueError,)
        )

        assert result == "success"
        assert func.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        """Should raise after exhausting retries."""
        func = AsyncMock(side_effect=ValueError("persistent failure"))
        config = RetryConfig(max_retries=2, base_delay=0.01)

        with pytest.raises(ValueError, match="persistent failure"):
            await retry_with_backoff(
                func,
                config,
                retryable_exceptions=(ValueError,)
            )

        assert func.call_count == 3  # Initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_retryable_exception_raises_immediately(self):
        """Non-retryable exceptions should not retry."""
        func = AsyncMock(side_effect=KeyError("not retryable"))
        config = RetryConfig(max_retries=3, base_delay=0.01)

        with pytest.raises(KeyError):
            await retry_with_backoff(
                func,
                config,
                retryable_exceptions=(ValueError,)  # KeyError not included
            )

        assert func.call_count == 1


class TestScraperWorkerRetry:
    @pytest.mark.asyncio
    async def test_worker_retries_failed_scrape(self):
        """Worker should retry failed scrapes."""
        from services.scraper.worker import ScraperWorker
        from services.scraper.retry import RetryConfig

        # Mock dependencies
        db = MagicMock()
        client = AsyncMock()

        # First call fails, second succeeds
        success_result = MagicMock()
        success_result.success = True
        success_result.markdown = "# Content"
        success_result.url = "https://example.com"
        success_result.title = "Example"
        success_result.domain = "example.com"
        success_result.metadata = {}

        client.scrape = AsyncMock(
            side_effect=[TimeoutError("timeout"), success_result]
        )

        retry_config = RetryConfig(max_retries=2, base_delay=0.01)
        worker = ScraperWorker(
            db=db,
            firecrawl_client=client,
            retry_config=retry_config
        )

        # Mock repositories
        worker.source_repo = AsyncMock()
        worker.project_repo = AsyncMock()

        result = await worker._scrape_url_with_retry(
            "https://example.com",
            "example.com"
        )

        assert result is not None
        assert result.success is True
        assert client.scrape.call_count == 2
