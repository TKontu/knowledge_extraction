"""Retry logic with exponential backoff."""

import asyncio
import random
from collections.abc import Awaitable, Callable

import structlog

logger = structlog.get_logger(__name__)


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
        delay = self.base_delay * (self.exponential_base**attempt)
        delay = min(delay, self.max_delay)

        if self.jitter:
            # Add up to 25% jitter
            delay = delay * (0.75 + random.random() * 0.5)

        return delay


async def retry_with_backoff[T](
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
