"""DLQ service for handling failed scrape and extraction jobs."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from redis.asyncio import Redis

SCRAPE_DLQ_KEY = "scrape:dlq"
EXTRACTION_DLQ_KEY = "extraction:dlq"


@dataclass
class DLQItem:
    """Represents an item in the Dead Letter Queue."""

    id: str  # UUID string
    source_id: str
    job_id: str | None
    error: str
    failed_at: str  # ISO format
    retry_count: int
    dlq_type: str  # "scrape" or "extraction"


class DLQService:
    """Service for managing Dead Letter Queues."""

    def __init__(self, redis: Redis) -> None:
        """Initialize DLQ service.

        Args:
            redis: Redis client instance.
        """
        self._redis = redis

    async def push_scrape_failure(
        self,
        source_id: UUID,
        error: str,
        job_id: UUID | None = None,
        retry_count: int = 0,
    ) -> None:
        """Push failed scrape to DLQ.

        Args:
            source_id: ID of the source that failed.
            error: Error message.
            job_id: Optional job ID.
            retry_count: Number of retries attempted.
        """
        item = {
            "id": str(uuid4()),
            "source_id": str(source_id),
            "job_id": str(job_id) if job_id else None,
            "error": error,
            "failed_at": datetime.now(UTC).isoformat(),
            "retry_count": retry_count,
            "dlq_type": "scrape",
        }
        await self._redis.lpush(SCRAPE_DLQ_KEY, json.dumps(item))

    async def push_extraction_failure(
        self,
        source_id: UUID,
        error: str,
        job_id: UUID | None = None,
        retry_count: int = 0,
    ) -> None:
        """Push failed extraction to DLQ.

        Args:
            source_id: ID of the source that failed.
            error: Error message.
            job_id: Optional job ID.
            retry_count: Number of retries attempted.
        """
        item = {
            "id": str(uuid4()),
            "source_id": str(source_id),
            "job_id": str(job_id) if job_id else None,
            "error": error,
            "failed_at": datetime.now(UTC).isoformat(),
            "retry_count": retry_count,
            "dlq_type": "extraction",
        }
        await self._redis.lpush(EXTRACTION_DLQ_KEY, json.dumps(item))

    async def get_scrape_dlq(self, limit: int = 100) -> list[DLQItem]:
        """Get items from scrape DLQ.

        Args:
            limit: Maximum number of items to return.

        Returns:
            List of DLQ items.
        """
        items_json = await self._redis.lrange(SCRAPE_DLQ_KEY, 0, limit - 1)
        return [DLQItem(**json.loads(item)) for item in items_json]

    async def get_extraction_dlq(self, limit: int = 100) -> list[DLQItem]:
        """Get items from extraction DLQ.

        Args:
            limit: Maximum number of items to return.

        Returns:
            List of DLQ items.
        """
        items_json = await self._redis.lrange(EXTRACTION_DLQ_KEY, 0, limit - 1)
        return [DLQItem(**json.loads(item)) for item in items_json]

    async def get_dlq_stats(self) -> dict:
        """Get counts for both DLQs.

        Returns:
            Dictionary with counts for scrape and extraction DLQs.
        """
        scrape_count = await self._redis.llen(SCRAPE_DLQ_KEY)
        extraction_count = await self._redis.llen(EXTRACTION_DLQ_KEY)
        return {"scrape": scrape_count, "extraction": extraction_count}

    async def pop_scrape_item(self, item_id: str) -> DLQItem | None:
        """Remove and return item from scrape DLQ for retry.

        Args:
            item_id: ID of the item to pop.

        Returns:
            The DLQ item if found, None otherwise.
        """
        # Get all items and find the matching one
        items_json = await self._redis.lrange(SCRAPE_DLQ_KEY, 0, -1)
        for item_json in items_json:
            item_data = json.loads(item_json)
            if item_data["id"] == item_id:
                # Remove the item from the list
                await self._redis.lrem(SCRAPE_DLQ_KEY, 1, item_json)
                return DLQItem(**item_data)
        return None

    async def pop_extraction_item(self, item_id: str) -> DLQItem | None:
        """Remove and return item from extraction DLQ for retry.

        Args:
            item_id: ID of the item to pop.

        Returns:
            The DLQ item if found, None otherwise.
        """
        # Get all items and find the matching one
        items_json = await self._redis.lrange(EXTRACTION_DLQ_KEY, 0, -1)
        for item_json in items_json:
            item_data = json.loads(item_json)
            if item_data["id"] == item_id:
                # Remove the item from the list
                await self._redis.lrem(EXTRACTION_DLQ_KEY, 1, item_json)
                return DLQItem(**item_data)
        return None
