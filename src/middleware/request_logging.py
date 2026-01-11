"""Middleware for request logging."""

import time

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = structlog.get_logger(__name__)


class RequestLoggingMiddleware:
    """Middleware to log all HTTP requests."""

    def __init__(self, app: ASGIApp) -> None:
        """Initialize middleware."""
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Process request and log it."""
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"]
        method = scope["method"]

        # Skip logging for health endpoints
        if path.startswith("/health"):
            await self.app(scope, receive, send)
            return

        # Get client IP
        client = scope.get("client")
        client_ip = client[0] if client else None

        # Log request start
        start_time = time.perf_counter()
        logger.info(
            "request_started",
            method=method,
            path=path,
            client_ip=client_ip,
        )

        # Capture status code from response
        status_code = None

        async def send_wrapper(message: Message) -> None:
            """Wrap send to capture status code."""
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        # Process request
        await self.app(scope, receive, send_wrapper)

        # Log response
        duration_ms = (time.perf_counter() - start_time) * 1000
        logger.info(
            "request_completed",
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=round(duration_ms, 2),
        )
