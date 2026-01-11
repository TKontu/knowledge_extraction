# TODO: Agent Shutdown

**Agent ID**: `agent-shutdown`
**Branch**: `feat/graceful-shutdown`
**Priority**: 2

## Objective

Implement graceful shutdown handling so the application properly drains connections and completes in-flight work before stopping.

## Context

- Current `main.py` has basic lifespan with `start_scheduler()`/`stop_scheduler()`
- The scraper worker runs background jobs that should complete before shutdown
- Health endpoint at `/health` should return 503 during shutdown
- Application runs in Docker with SIGTERM for graceful stops

## Tasks

### 1. Create shutdown state manager

**File**: `src/shutdown.py` (new file)

```python
"""Shutdown state management for graceful termination."""

import asyncio
import signal
from contextlib import asynccontextmanager
from typing import Callable, Coroutine, Any

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
            except asyncio.TimeoutError:
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
```

### 2. Add signal handlers to main.py

**File**: `src/main.py`

Update the lifespan to include signal handling:

```python
import signal
from shutdown import shutdown_manager, get_shutdown_manager

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    loop = asyncio.get_event_loop()

    # Register signal handlers
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(
            sig,
            lambda s=sig: asyncio.create_task(handle_signal(s))
        )

    # Startup
    logger.info("application_startup", version="0.1.0")
    await start_scheduler()

    # Register cleanup callbacks
    shutdown_manager.register_cleanup(stop_scheduler)

    yield

    # Shutdown
    logger.info("application_shutdown")
    await shutdown_manager.initiate_shutdown()


async def handle_signal(sig: signal.Signals) -> None:
    """Handle shutdown signals."""
    logger.info("signal_received", signal=sig.name)
    await shutdown_manager.initiate_shutdown()
```

### 3. Update worker to check shutdown state

**File**: `src/services/scraper/worker.py`

Add shutdown awareness to the worker loop:

```python
from shutdown import get_shutdown_manager

# In the worker's run loop, add check:
async def process_jobs(self) -> None:
    shutdown = get_shutdown_manager()
    while not shutdown.is_shutting_down:
        job = await self.get_next_job()
        if job:
            await self.process_job(job)  # Complete current job
        else:
            await asyncio.sleep(1)

    logger.info("worker_shutdown", reason="graceful_shutdown")
```

### 4. Update health endpoint for shutdown status

**File**: `src/main.py`

Modify health check to return 503 during shutdown:

```python
from shutdown import get_shutdown_manager

@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint - returns service status."""
    shutdown = get_shutdown_manager()

    if shutdown.is_shutting_down:
        return JSONResponse(
            status_code=503,
            content={
                "status": "shutting_down",
                "service": "scristill-pipeline",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

    # ... rest of existing health check logic
```

### 5. Write tests

**File**: `tests/test_graceful_shutdown.py`

```python
import pytest
import asyncio
from unittest.mock import AsyncMock, patch

from src.shutdown import ShutdownManager, get_shutdown_manager


class TestShutdownManager:
    def test_initial_state_not_shutting_down(self):
        manager = ShutdownManager()
        assert manager.is_shutting_down is False

    @pytest.mark.asyncio
    async def test_initiate_shutdown_sets_flag(self):
        manager = ShutdownManager()
        await manager.initiate_shutdown()
        assert manager.is_shutting_down is True

    @pytest.mark.asyncio
    async def test_initiate_shutdown_idempotent(self):
        manager = ShutdownManager()
        await manager.initiate_shutdown()
        await manager.initiate_shutdown()  # Should not error
        assert manager.is_shutting_down is True

    @pytest.mark.asyncio
    async def test_cleanup_callbacks_called(self):
        manager = ShutdownManager()
        callback = AsyncMock()
        manager.register_cleanup(callback)

        await manager.initiate_shutdown()

        callback.assert_called_once()

    @pytest.mark.asyncio
    async def test_cleanup_callback_timeout_handled(self):
        manager = ShutdownManager()

        async def slow_callback():
            await asyncio.sleep(60)  # Will timeout

        manager.register_cleanup(slow_callback)

        # Should complete without hanging (30s timeout in implementation)
        with patch.object(manager, '_cleanup_callbacks', [slow_callback]):
            # Use shorter timeout for test
            await asyncio.wait_for(manager.initiate_shutdown(), timeout=2.0)

    @pytest.mark.asyncio
    async def test_cleanup_callback_exception_handled(self):
        manager = ShutdownManager()

        async def failing_callback():
            raise ValueError("Test error")

        manager.register_cleanup(failing_callback)

        # Should not raise
        await manager.initiate_shutdown()
        assert manager.is_shutting_down is True

    def test_get_shutdown_manager_returns_singleton(self):
        manager1 = get_shutdown_manager()
        manager2 = get_shutdown_manager()
        assert manager1 is manager2


class TestHealthEndpointShutdown:
    @pytest.mark.asyncio
    async def test_health_returns_503_during_shutdown(self, client):
        from src.shutdown import shutdown_manager

        # Simulate shutdown state
        shutdown_manager._shutting_down = True

        try:
            response = await client.get("/health")
            assert response.status_code == 503
            assert response.json()["status"] == "shutting_down"
        finally:
            # Reset for other tests
            shutdown_manager._shutting_down = False
```

## Constraints

- Do NOT change the scheduler implementation itself
- Do NOT add external dependencies
- Cleanup callbacks must have 30-second timeout
- Signal handlers must work on Linux (Docker environment)
- Keep existing health check logic, just add shutdown check

## Verification

1. `pytest tests/test_graceful_shutdown.py -v` passes
2. `pytest tests/ -v` - all existing tests still pass
3. `ruff check src/shutdown.py src/main.py` - no lint errors
4. Manual test: Start server, send SIGTERM, verify clean shutdown in logs

## Definition of Done

- [ ] `src/shutdown.py` created with ShutdownManager class
- [ ] Signal handlers (SIGTERM, SIGINT) registered in main.py
- [ ] Worker checks shutdown state before processing new jobs
- [ ] Health endpoint returns 503 during shutdown
- [ ] Tests written and passing
- [ ] PR created with title: `feat: add graceful shutdown handling`
