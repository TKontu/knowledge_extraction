"""Tests for BackpressureManager."""

from unittest.mock import AsyncMock

import pytest

from exceptions import QueueFullError
from services.extraction.backpressure import BackpressureManager


class TestBackpressureManager:
    """Tests for BackpressureManager."""

    async def test_no_queue_returns_immediately(self):
        """No LLM queue means no waiting."""
        manager = BackpressureManager(llm_queue=None)
        await manager.wait_for_capacity()  # Should not raise

    async def test_healthy_queue_returns_immediately(self):
        """Healthy queue (should_wait=False) returns immediately."""
        queue = AsyncMock()
        queue.get_backpressure_status.return_value = {
            "pressure": 0.2,
            "should_wait": False,
            "queue_depth": 100,
            "max_depth": 1000,
        }
        manager = BackpressureManager(llm_queue=queue)
        await manager.wait_for_capacity()

        queue.get_backpressure_status.assert_called_once()

    async def test_waits_then_proceeds_when_pressure_clears(self):
        """Waits on backpressure, then proceeds when it clears."""
        queue = AsyncMock()
        queue.get_backpressure_status.side_effect = [
            {
                "pressure": 0.9,
                "should_wait": True,
                "queue_depth": 900,
                "max_depth": 1000,
            },
            {
                "pressure": 0.3,
                "should_wait": False,
                "queue_depth": 300,
                "max_depth": 1000,
            },
        ]
        manager = BackpressureManager(llm_queue=queue, wait_base=0.001)
        await manager.wait_for_capacity()

        assert queue.get_backpressure_status.call_count == 2

    async def test_raises_queue_full_after_max_retries(self):
        """Raises QueueFullError after max retries exhausted."""
        queue = AsyncMock()
        queue.get_backpressure_status.return_value = {
            "pressure": 0.95,
            "should_wait": True,
            "queue_depth": 950,
            "max_depth": 1000,
        }
        manager = BackpressureManager(llm_queue=queue, wait_base=0.001, max_retries=3)

        with pytest.raises(QueueFullError):
            await manager.wait_for_capacity()

        assert queue.get_backpressure_status.call_count == 3

    async def test_custom_wait_base_and_retries(self):
        """Custom wait_base and max_retries are respected."""
        queue = AsyncMock()
        queue.get_backpressure_status.return_value = {
            "pressure": 0.95,
            "should_wait": True,
            "queue_depth": 950,
            "max_depth": 1000,
        }
        manager = BackpressureManager(llm_queue=queue, wait_base=0.001, max_retries=2)

        with pytest.raises(QueueFullError):
            await manager.wait_for_capacity()

        assert queue.get_backpressure_status.call_count == 2
