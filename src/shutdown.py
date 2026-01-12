"""Shutdown state management for graceful termination."""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class ShutdownManager:
    """Manages graceful shutdown state and callbacks."""

    def __init__(self):
        self._shutting_down = False
        self._shutdown_event = asyncio.Event()
        self._cleanup_callbacks: list[Callable[[], Coroutine[Any, Any, None]]] = []

    @property
    def is_shutting_down(self) -> bool:
        return self._shutting_down

    def register_cleanup(self, callback: Callable[[], Coroutine[Any, Any, None]]) -> None:
        """Register an async cleanup callback to run on shutdown."""
        self._cleanup_callbacks.append(callback)

    async def initiate_shutdown(self) -> None:
        """Begin graceful shutdown process."""
        if self._shutting_down:
            return

        self._shutting_down = True
        logger.info("shutdown_initiated", callbacks=len(self._cleanup_callbacks))

        # Run all cleanup callbacks
        for callback in self._cleanup_callbacks:
            try:
                await asyncio.wait_for(callback(), timeout=30.0)
            except TimeoutError:
                logger.warning("cleanup_callback_timeout", callback=callback.__name__)
            except Exception as e:
                logger.error("cleanup_callback_failed", callback=callback.__name__, error=str(e))

        self._shutdown_event.set()
        logger.info("shutdown_complete")

    async def wait_for_shutdown(self) -> None:
        """Wait until shutdown is complete."""
        await self._shutdown_event.wait()


# Global singleton
shutdown_manager = ShutdownManager()


def get_shutdown_manager() -> ShutdownManager:
    """Get the global shutdown manager instance."""
    return shutdown_manager
