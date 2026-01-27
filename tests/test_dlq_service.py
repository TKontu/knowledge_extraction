"""Tests for DLQ service."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.services.dlq.service import (
    EXTRACTION_DLQ_KEY,
    SCRAPE_DLQ_KEY,
    DLQService,
)


@pytest.fixture
def mock_redis():
    """Create a mock Redis client."""
    redis = AsyncMock()
    redis.lpush = AsyncMock()
    redis.lrange = AsyncMock()
    redis.llen = AsyncMock()
    redis.lrem = AsyncMock()
    return redis


@pytest.fixture
def dlq_service(mock_redis):
    """Create a DLQService instance with mocked Redis."""
    return DLQService(mock_redis)


class TestDLQService:
    """Test DLQService methods."""

    async def test_push_scrape_failure(self, dlq_service, mock_redis):
        """Test pushing a scrape failure to DLQ."""
        source_id = uuid4()
        job_id = uuid4()
        error = "Connection timeout"
        retry_count = 0

        await dlq_service.push_scrape_failure(source_id, error, job_id, retry_count)

        # Verify lpush was called with correct key
        assert mock_redis.lpush.call_count == 1
        call_args = mock_redis.lpush.call_args
        assert call_args[0][0] == SCRAPE_DLQ_KEY

        # Verify the JSON payload
        json_data = call_args[0][1]
        item = json.loads(json_data)
        assert item["source_id"] == str(source_id)
        assert item["job_id"] == str(job_id)
        assert item["error"] == error
        assert item["retry_count"] == retry_count
        assert item["dlq_type"] == "scrape"
        assert "id" in item
        assert "failed_at" in item

    async def test_push_extraction_failure(self, dlq_service, mock_redis):
        """Test pushing an extraction failure to DLQ."""
        source_id = uuid4()
        error = "LLM request failed"
        retry_count = 2

        await dlq_service.push_extraction_failure(source_id, error, None, retry_count)

        # Verify lpush was called with correct key
        assert mock_redis.lpush.call_count == 1
        call_args = mock_redis.lpush.call_args
        assert call_args[0][0] == EXTRACTION_DLQ_KEY

        # Verify the JSON payload
        json_data = call_args[0][1]
        item = json.loads(json_data)
        assert item["source_id"] == str(source_id)
        assert item["job_id"] is None
        assert item["error"] == error
        assert item["retry_count"] == retry_count
        assert item["dlq_type"] == "extraction"

    async def test_get_scrape_dlq(self, dlq_service, mock_redis):
        """Test getting items from scrape DLQ."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        job_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        mock_item = json.dumps(
            {
                "id": item_id,
                "source_id": source_id,
                "job_id": job_id,
                "error": "Timeout",
                "failed_at": failed_at,
                "retry_count": 1,
                "dlq_type": "scrape",
            }
        )

        mock_redis.lrange.return_value = [mock_item]

        items = await dlq_service.get_scrape_dlq(limit=100)

        # Verify lrange was called correctly
        mock_redis.lrange.assert_called_once_with(SCRAPE_DLQ_KEY, 0, 99)

        # Verify returned items
        assert len(items) == 1
        assert items[0].id == item_id
        assert items[0].source_id == source_id
        assert items[0].job_id == job_id
        assert items[0].error == "Timeout"
        assert items[0].retry_count == 1
        assert items[0].dlq_type == "scrape"

    async def test_get_extraction_dlq(self, dlq_service, mock_redis):
        """Test getting items from extraction DLQ."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        mock_item = json.dumps(
            {
                "id": item_id,
                "source_id": source_id,
                "job_id": None,
                "error": "Parse error",
                "failed_at": failed_at,
                "retry_count": 0,
                "dlq_type": "extraction",
            }
        )

        mock_redis.lrange.return_value = [mock_item]

        items = await dlq_service.get_extraction_dlq(limit=50)

        # Verify lrange was called correctly
        mock_redis.lrange.assert_called_once_with(EXTRACTION_DLQ_KEY, 0, 49)

        # Verify returned items
        assert len(items) == 1
        assert items[0].id == item_id
        assert items[0].dlq_type == "extraction"

    async def test_get_dlq_stats(self, dlq_service, mock_redis):
        """Test getting DLQ statistics."""
        mock_redis.llen.side_effect = [5, 3]  # scrape count, extraction count

        stats = await dlq_service.get_dlq_stats()

        assert stats["scrape"] == 5
        assert stats["extraction"] == 3
        assert mock_redis.llen.call_count == 2

    async def test_pop_scrape_item(self, dlq_service, mock_redis):
        """Test popping an item from scrape DLQ."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        mock_item = json.dumps(
            {
                "id": item_id,
                "source_id": source_id,
                "job_id": None,
                "error": "Failed",
                "failed_at": failed_at,
                "retry_count": 0,
                "dlq_type": "scrape",
            }
        )

        # Mock lrange to return the item
        mock_redis.lrange.return_value = [mock_item]
        # Mock lrem to return 1 (item removed)
        mock_redis.lrem.return_value = 1

        item = await dlq_service.pop_scrape_item(item_id)

        # Verify the item was found and returned
        assert item is not None
        assert item.id == item_id
        assert item.source_id == source_id

        # Verify lrem was called to remove the item
        mock_redis.lrem.assert_called_once_with(SCRAPE_DLQ_KEY, 1, mock_item)

    async def test_pop_scrape_item_not_found(self, dlq_service, mock_redis):
        """Test popping a non-existent item returns None."""
        mock_redis.lrange.return_value = []

        item = await dlq_service.pop_scrape_item("non-existent-id")

        assert item is None
        mock_redis.lrem.assert_not_called()

    async def test_pop_extraction_item(self, dlq_service, mock_redis):
        """Test popping an item from extraction DLQ."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        mock_item = json.dumps(
            {
                "id": item_id,
                "source_id": source_id,
                "job_id": None,
                "error": "Failed",
                "failed_at": failed_at,
                "retry_count": 1,
                "dlq_type": "extraction",
            }
        )

        mock_redis.lrange.return_value = [mock_item]
        mock_redis.lrem.return_value = 1

        item = await dlq_service.pop_extraction_item(item_id)

        # Verify the item was found and returned
        assert item is not None
        assert item.id == item_id
        assert item.dlq_type == "extraction"

        # Verify lrem was called to remove the item
        mock_redis.lrem.assert_called_once_with(EXTRACTION_DLQ_KEY, 1, mock_item)
