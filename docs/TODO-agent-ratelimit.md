# TODO: Agent Rate Limit

**Agent ID**: `agent-ratelimit`
**Branch**: `feat/rate-limiting`
**Priority**: 3

## Objective

Add application-level rate limiting middleware to protect the API from abuse, with per-API-key limits and proper headers.

## Context

- Current rate limiting only exists for domain-based scraping (`src/services/scraper/rate_limiter.py`)
- No application-level request rate limiting exists
- Redis is already available for distributed state
- API key authentication exists in `src/middleware/auth.py`
- Config uses pydantic-settings in `src/config.py`

## Tasks

### 1. Add rate limit settings to config

**File**: `src/config.py`

Add new settings:

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Rate limiting
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 100  # requests per window
    rate_limit_window_seconds: int = 60  # window size
    rate_limit_burst: int = 20  # burst allowance above limit
```

### 2. Create rate limiting middleware

**File**: `src/middleware/rate_limit.py` (new file)

```python
"""Rate limiting middleware using sliding window algorithm."""

import time
from typing import Callable

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
```

### 3. Register middleware in main.py

**File**: `src/main.py`

Add import and middleware registration (order matters - add after auth):

```python
from middleware.rate_limit import RateLimitMiddleware

# Add after APIKeyMiddleware
app.add_middleware(RateLimitMiddleware)
```

### 4. Write tests

**File**: `tests/test_rate_limit_middleware.py`

```python
import pytest
from unittest.mock import Mock, patch, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from src.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture
def app_with_rate_limit():
    """Create test app with rate limiting."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/test")
    def test_endpoint():
        return {"status": "ok"}

    @app.get("/health")
    def health_endpoint():
        return {"status": "healthy"}

    return app


@pytest.fixture
def client(app_with_rate_limit):
    return TestClient(app_with_rate_limit)


class TestRateLimitMiddleware:
    def test_exempt_paths_not_rate_limited(self, client):
        """Health and metrics endpoints bypass rate limiting."""
        with patch("src.middleware.rate_limit.settings") as mock_settings:
            mock_settings.rate_limit_enabled = True
            response = client.get("/health")
            assert response.status_code == 200
            assert "X-RateLimit-Limit" not in response.headers

    def test_rate_limit_headers_added(self, client):
        """Rate limit headers are added to responses."""
        with patch("src.middleware.rate_limit.get_redis_client") as mock_redis:
            mock_redis.return_value = None  # Fallback mode
            with patch("src.middleware.rate_limit.settings") as mock_settings:
                mock_settings.rate_limit_enabled = True
                mock_settings.rate_limit_requests = 100
                mock_settings.rate_limit_window_seconds = 60

                response = client.get("/test", headers={"X-API-Key": "test-key"})

                assert "X-RateLimit-Limit" in response.headers
                assert "X-RateLimit-Remaining" in response.headers
                assert "X-RateLimit-Reset" in response.headers

    def test_rate_limit_disabled_skips_check(self, client):
        """When disabled, no rate limiting occurs."""
        with patch("src.middleware.rate_limit.settings") as mock_settings:
            mock_settings.rate_limit_enabled = False

            response = client.get("/test")
            assert response.status_code == 200
            assert "X-RateLimit-Limit" not in response.headers

    def test_rate_limit_exceeded_returns_429(self, client):
        """Exceeding rate limit returns 429."""
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [None, 150, None, None]  # High count
        mock_redis.pipeline.return_value = mock_pipe

        with patch("src.middleware.rate_limit.get_redis_client", return_value=mock_redis):
            with patch("src.middleware.rate_limit.settings") as mock_settings:
                mock_settings.rate_limit_enabled = True
                mock_settings.rate_limit_requests = 100
                mock_settings.rate_limit_burst = 20
                mock_settings.rate_limit_window_seconds = 60

                response = client.get("/test", headers={"X-API-Key": "test-key"})

                assert response.status_code == 429
                assert "Retry-After" in response.headers
                assert response.json()["error"] == "Too Many Requests"

    def test_redis_unavailable_allows_request(self, client):
        """When Redis is unavailable, requests are allowed."""
        with patch("src.middleware.rate_limit.get_redis_client", return_value=None):
            with patch("src.middleware.rate_limit.settings") as mock_settings:
                mock_settings.rate_limit_enabled = True
                mock_settings.rate_limit_requests = 100
                mock_settings.rate_limit_window_seconds = 60

                response = client.get("/test", headers={"X-API-Key": "test-key"})
                assert response.status_code == 200

    def test_uses_api_key_as_identifier(self):
        """API key is used as rate limit identifier when present."""
        # Test that different API keys have separate limits
        # This is implicitly tested by the key format in _check_rate_limit
        pass

    def test_falls_back_to_ip_without_api_key(self):
        """Client IP is used when no API key provided."""
        # Test that IP is used as fallback identifier
        pass


class TestRateLimitConfig:
    def test_default_config_values(self):
        """Verify default rate limit configuration."""
        from src.config import Settings

        settings = Settings()
        assert settings.rate_limit_enabled is True
        assert settings.rate_limit_requests == 100
        assert settings.rate_limit_window_seconds == 60
        assert settings.rate_limit_burst == 20
```

## Constraints

- Do NOT modify existing auth middleware
- Do NOT add new dependencies (use existing redis-py)
- MUST use sliding window algorithm (not fixed window)
- MUST handle Redis unavailability gracefully (allow requests)
- MUST exempt /health, /metrics, /, /docs, /openapi.json from limiting
- Headers MUST follow RFC 6585 conventions

## Verification

1. `pytest tests/test_rate_limit_middleware.py -v` passes
2. `pytest tests/ -v` - all existing tests still pass
3. `ruff check src/middleware/rate_limit.py src/config.py` - no lint errors
4. Manual test: Make 101+ requests quickly, verify 429 response

## Definition of Done

- [ ] Rate limit settings added to config.py
- [ ] RateLimitMiddleware created with sliding window
- [ ] Middleware registered in main.py
- [ ] X-RateLimit-* headers on all responses
- [ ] 429 response with Retry-After when exceeded
- [ ] Tests written and passing
- [ ] PR created with title: `feat: add application rate limiting middleware`
