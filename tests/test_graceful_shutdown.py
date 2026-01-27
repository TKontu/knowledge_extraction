"""Tests for graceful shutdown functionality."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from src.shutdown import ShutdownManager, get_shutdown_manager


class TestShutdownManager:
    """Tests for ShutdownManager class."""

    def test_initial_state_not_shutting_down(self):
        """Test that manager starts in non-shutdown state."""
        manager = ShutdownManager()
        assert manager.is_shutting_down is False

    @pytest.mark.asyncio
    async def test_initiate_shutdown_sets_flag(self):
        """Test that initiating shutdown sets the flag."""
        manager = ShutdownManager()
        await manager.initiate_shutdown()
        assert manager.is_shutting_down is True

    @pytest.mark.asyncio
    async def test_initiate_shutdown_idempotent(self):
        """Test that calling shutdown multiple times is safe."""
        manager = ShutdownManager()
        await manager.initiate_shutdown()
        await manager.initiate_shutdown()  # Should not error
        assert manager.is_shutting_down is True

    @pytest.mark.asyncio
    async def test_cleanup_callbacks_called(self):
        """Test that registered cleanup callbacks are called."""
        manager = ShutdownManager()
        callback = AsyncMock()
        manager.register_cleanup(callback)

        await manager.initiate_shutdown()

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_callback_timeout_handled(self):
        """Test that slow callbacks are timed out properly."""
        manager = ShutdownManager()

        async def slow_callback():
            await asyncio.sleep(60)  # Will timeout at 30s

        manager.register_cleanup(slow_callback)

        # Should complete without hanging (30s timeout in implementation)
        # Allow 35s for the 30s timeout to fire and complete
        await asyncio.wait_for(manager.initiate_shutdown(), timeout=35.0)

    @pytest.mark.asyncio
    async def test_cleanup_callback_exception_handled(self):
        """Test that exceptions in callbacks don't break shutdown."""
        manager = ShutdownManager()

        async def failing_callback():
            raise ValueError("Test error")

        manager.register_cleanup(failing_callback)

        # Should not raise
        await manager.initiate_shutdown()
        assert manager.is_shutting_down is True

    def test_get_shutdown_manager_returns_singleton(self):
        """Test that get_shutdown_manager returns the same instance."""
        manager1 = get_shutdown_manager()
        manager2 = get_shutdown_manager()
        assert manager1 is manager2


class TestHealthEndpointShutdown:
    """Tests for health endpoint during shutdown."""

    def test_health_returns_503_during_shutdown(self):
        """Test that health endpoint returns 503 when shutting down."""
        from src.main import health_check

        # Mock the shutdown manager to return shutting_down=True
        with patch("src.main.get_shutdown_manager") as mock_get_shutdown:
            mock_shutdown = type("ShutdownManager", (), {"is_shutting_down": True})()
            mock_get_shutdown.return_value = mock_shutdown

            # Call the health check endpoint
            import asyncio

            response = asyncio.run(health_check())

            assert response.status_code == 503
            assert (
                response.body.decode()
                == '{"status":"shutting_down","service":"scristill-pipeline","timestamp":"'
                or "shutting_down" in response.body.decode()
            )
