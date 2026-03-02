"""LLM queue backpressure management with exponential backoff."""

import asyncio
from typing import TYPE_CHECKING

import structlog

from exceptions import QueueFullError

if TYPE_CHECKING:
    from services.llm.queue import LLMRequestQueue

logger = structlog.get_logger(__name__)

# Backpressure constants
BACKPRESSURE_WAIT_BASE = 2.0  # Base wait time in seconds
MAX_BACKPRESSURE_RETRIES = 10  # Max retries before raising QueueFullError


class BackpressureManager:
    """Manages LLM queue backpressure with exponential backoff.

    Args:
        llm_queue: Optional LLM request queue to monitor.
        wait_base: Base wait time in seconds for exponential backoff.
        max_retries: Maximum retries before raising QueueFullError.
    """

    def __init__(
        self,
        llm_queue: "LLMRequestQueue | None" = None,
        wait_base: float = BACKPRESSURE_WAIT_BASE,
        max_retries: int = MAX_BACKPRESSURE_RETRIES,
    ):
        self._llm_queue = llm_queue
        self._wait_base = wait_base
        self._max_retries = max_retries

    async def wait_for_capacity(self) -> None:
        """Wait for LLM queue to have capacity.

        Uses exponential backoff to poll queue status.

        Raises:
            QueueFullError: If queue remains full after max retries.
        """
        if self._llm_queue is None:
            return

        for attempt in range(self._max_retries):
            status = await self._llm_queue.get_backpressure_status()

            if not status.get("should_wait", False):
                return

            wait_time = self._wait_base * (1.5**attempt)
            logger.info(
                "pipeline_backpressure_wait",
                attempt=attempt + 1,
                max_retries=self._max_retries,
                wait_seconds=wait_time,
                queue_depth=status.get("queue_depth"),
                pressure=status.get("pressure"),
            )
            await asyncio.sleep(wait_time)

        # All retries exhausted
        raise QueueFullError(
            f"LLM queue persistently full after {self._max_retries} retries"
        )
