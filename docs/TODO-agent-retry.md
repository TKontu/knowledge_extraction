# TODO: Scraper Retry Logic

**Agent ID**: `agent-retry`
**Branch**: `feat/scraper-retry`
**Priority**: 2

## Objective

Add exponential backoff retry logic to the scraper worker for transient failures, improving reliability.

## Context

- `src/services/scraper/worker.py` processes scrape jobs
- Currently no retry on URL-level failures - they just increment `sources_failed`
- Rate limit exceeds are caught but not retried later
- Config in `src/config.py` has `SCRAPE_MAX_RETRIES` (default 3) but it's not used
- Jobs table has no `attempt_count` column

## Tasks

### 1. Add retry helper module

**File**: `src/services/scraper/retry.py` (new file)

```python
"""Retry logic with exponential backoff."""

import asyncio
import random
from collections.abc import Awaitable, Callable
from typing import TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class RetryConfig:
    """Configuration for retry behavior."""

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        """Initialize retry config.

        Args:
            max_retries: Maximum number of retry attempts.
            base_delay: Initial delay in seconds.
            max_delay: Maximum delay cap in seconds.
            exponential_base: Base for exponential backoff.
            jitter: Add random jitter to prevent thundering herd.
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def get_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt number.

        Args:
            attempt: Current attempt number (0-indexed).

        Returns:
            Delay in seconds.
        """
        delay = self.base_delay * (self.exponential_base ** attempt)
        delay = min(delay, self.max_delay)

        if self.jitter:
            # Add up to 25% jitter
            delay = delay * (0.75 + random.random() * 0.5)

        return delay


async def retry_with_backoff(
    func: Callable[[], Awaitable[T]],
    config: RetryConfig,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
    operation_name: str = "operation",
) -> T:
    """Execute function with exponential backoff retry.

    Args:
        func: Async function to execute.
        config: Retry configuration.
        retryable_exceptions: Tuple of exception types to retry on.
        operation_name: Name for logging.

    Returns:
        Result of successful function call.

    Raises:
        Last exception if all retries exhausted.
    """
    last_exception: Exception | None = None

    for attempt in range(config.max_retries + 1):
        try:
            return await func()
        except retryable_exceptions as e:
            last_exception = e

            if attempt < config.max_retries:
                delay = config.get_delay(attempt)
                logger.warning(
                    "retry_scheduled",
                    operation=operation_name,
                    attempt=attempt + 1,
                    max_retries=config.max_retries,
                    delay_seconds=round(delay, 2),
                    error=str(e),
                )
                await asyncio.sleep(delay)
            else:
                logger.error(
                    "retry_exhausted",
                    operation=operation_name,
                    attempts=config.max_retries + 1,
                    error=str(e),
                )

    # Should never reach here, but type checker needs this
    raise last_exception  # type: ignore[misc]
```

### 2. Update ScraperWorker to use retry

**File**: `src/services/scraper/worker.py`

Import and use retry for URL scraping:

```python
from services.scraper.retry import RetryConfig, retry_with_backoff

# In __init__, add retry config
def __init__(
    self,
    db: Session,
    firecrawl_client: FirecrawlClient,
    rate_limiter: DomainRateLimiter | None = None,
    retry_config: RetryConfig | None = None,
) -> None:
    # ... existing code ...
    self.retry_config = retry_config or RetryConfig(
        max_retries=3,
        base_delay=2.0,
        max_delay=60.0,
    )

# In process_job, wrap scrape call with retry
async def _scrape_url_with_retry(self, url: str, domain: str) -> ScrapeResult | None:
    """Scrape URL with retry logic.

    Args:
        url: URL to scrape.
        domain: Domain for rate limiting.

    Returns:
        ScrapeResult on success, None on failure after retries.
    """
    async def do_scrape():
        if self.rate_limiter:
            await self.rate_limiter.acquire(domain)
        return await self.client.scrape(url)

    try:
        return await retry_with_backoff(
            func=do_scrape,
            config=self.retry_config,
            retryable_exceptions=(httpx.HTTPError, TimeoutError, ConnectionError),
            operation_name=f"scrape:{domain}",
        )
    except Exception as e:
        logger.error("scrape_failed_after_retries", url=url, error=str(e))
        return None
```

Then update the URL processing loop to use the new method:

```python
# In process_job, replace direct scrape call
for url in urls:
    try:
        domain = self._extract_domain(url)

        # Scrape with retry
        result = await self._scrape_url_with_retry(url, domain)

        if result and result.success and result.markdown:
            # ... existing storage code ...
            sources_scraped += 1
        else:
            sources_failed += 1

    except RateLimitExceeded:
        # ... existing handling ...
```

### 3. Add retry settings to config

**File**: `src/config.py`

Add/update retry-related settings:

```python
# Scraper Retry Configuration
scrape_retry_max_attempts: int = Field(
    default=3,
    description="Maximum retry attempts for failed scrapes",
)
scrape_retry_base_delay: float = Field(
    default=2.0,
    description="Base delay between retries in seconds",
)
scrape_retry_max_delay: float = Field(
    default=60.0,
    description="Maximum delay between retries in seconds",
)
```

### 4. Update scheduler to pass retry config

**File**: `src/services/scraper/scheduler.py`

Pass retry config when creating worker:

```python
from config import settings
from services.scraper.retry import RetryConfig

retry_config = RetryConfig(
    max_retries=settings.scrape_retry_max_attempts,
    base_delay=settings.scrape_retry_base_delay,
    max_delay=settings.scrape_retry_max_delay,
)

worker = ScraperWorker(
    db=db,
    firecrawl_client=client,
    rate_limiter=limiter,
    retry_config=retry_config,
)
```

### 5. Write tests

**File**: `tests/test_scraper_retry.py`

```python
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
```

## Constraints

- Do NOT change job status handling logic
- Do NOT modify rate limiter behavior
- Retry only for transient errors (network, timeout), not for 4xx responses
- Keep existing logging format
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_scraper_retry.py tests/test_scraper_worker.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/scraper/retry.py src/services/scraper/worker.py src/services/scraper/scheduler.py src/config.py
```

## Verification

Before creating PR, run ONLY the scoped commands above:

1. `pytest tests/test_scraper_retry.py tests/test_scraper_worker.py -v` - Must pass
2. `ruff check src/services/scraper/retry.py src/services/scraper/worker.py src/services/scraper/scheduler.py src/config.py` - Must be clean
3. All tasks above completed

## Definition of Done

- [ ] `src/services/scraper/retry.py` created with RetryConfig and retry_with_backoff
- [ ] ScraperWorker updated to use retry for URL scraping
- [ ] Retry config settings added to config.py
- [ ] Scheduler passes retry config to worker
- [ ] Tests written and passing
- [ ] PR created with title: `feat: add scraper retry with exponential backoff`
