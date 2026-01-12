"""Rate limiting middleware using sliding window algorithm."""

import time
from collections.abc import Callable

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

from config import settings
from redis_client import get_redis_client

logger = structlog.get_logger(__name__)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Sliding window rate limiter with Redis backend."""

    # Endpoints exempt from rate limiting
    EXEMPT_PATHS = {"/health", "/metrics", "/", "/docs", "/openapi.json"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Skip if disabled
        if not settings.rate_limit_enabled:
            return await call_next(request)

        # Skip exempt paths
        if request.url.path in self.EXEMPT_PATHS:
            return await call_next(request)

        # Get identifier (API key or IP)
        api_key = request.headers.get("X-API-Key", "")
        identifier = api_key if api_key else request.client.host

        # Check rate limit
        allowed, remaining, reset_at = await self._check_rate_limit(identifier)

        if not allowed:
            logger.warning(
                "rate_limit_exceeded",
                identifier=identifier[:8] + "..." if len(identifier) > 8 else identifier,
                path=request.url.path,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "Too Many Requests",
                    "detail": "Rate limit exceeded. Please retry later.",
                    "retry_after": reset_at - int(time.time()),
                },
                headers={
                    "X-RateLimit-Limit": str(settings.rate_limit_requests),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_at),
                    "Retry-After": str(reset_at - int(time.time())),
                },
            )

        # Process request
        response = await call_next(request)

        # Add rate limit headers
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_requests)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_at)

        return response

    async def _check_rate_limit(self, identifier: str) -> tuple[bool, int, int]:
        """
        Check if request is allowed using sliding window.

        Returns:
            (allowed, remaining, reset_timestamp)
        """
        redis = get_redis_client()
        if redis is None:
            # Redis unavailable - allow request but log warning
            logger.warning("rate_limit_redis_unavailable")
            return True, settings.rate_limit_requests, int(time.time()) + settings.rate_limit_window_seconds

        now = int(time.time())
        window_start = now - settings.rate_limit_window_seconds
        key = f"ratelimit:{identifier}"

        pipe = redis.pipeline()

        # Remove old entries outside window
        pipe.zremrangebyscore(key, 0, window_start)

        # Count requests in current window
        pipe.zcard(key)

        # Add current request with timestamp as score
        pipe.zadd(key, {f"{now}:{id(self)}": now})

        # Set expiry on key
        pipe.expire(key, settings.rate_limit_window_seconds + 10)

        results = pipe.execute()
        request_count = results[1]  # zcard result

        limit = settings.rate_limit_requests + settings.rate_limit_burst
        remaining = max(0, limit - request_count - 1)
        reset_at = now + settings.rate_limit_window_seconds

        allowed = request_count < limit

        return allowed, remaining, reset_at
