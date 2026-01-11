# TODO: Structured Logging & Request Tracing

**Agent:** logging
**Branch:** `feat/structured-logging`
**Priority:** MEDIUM-HIGH
**Assigned:** 2026-01-11

## Context

The system has `structlog` as a dependency but it's not configured globally. Only one file (`services/knowledge/extractor.py`) uses it. We need proper logging infrastructure for production operations.

**Current state:**
- structlog imported in one file
- No global configuration
- No request ID tracing
- No structured log format configured
- LOG_LEVEL and LOG_FORMAT env vars exist but aren't used

**Needed for operations:**
1. Global structlog configuration
2. Request ID tracing (correlation)
3. Structured JSON logging for production
4. Console logging for development
5. Logging in key services

## Objective

Configure structlog for production-ready logging with request tracing, and add logging to key system components.

## Tasks

### 1. Create logging configuration module

**File:** `src/logging_config.py` (new file)

**Requirements:**
- Configure structlog globally
- Support JSON format (production) and console format (development)
- Use LOG_LEVEL and LOG_FORMAT from config
- Add standard processors (timestamp, log level, etc.)

```python
import logging
import sys
import structlog
from config import get_settings

def configure_logging() -> None:
    """Configure structlog for the application."""
    settings = get_settings()

    # Determine if we're in development or production
    is_json = settings.log_format.lower() == "json"

    # Shared processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_json:
        # Production: JSON output
        structlog.configure(
            processors=shared_processors + [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, settings.log_level.upper())
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Development: colored console output
        structlog.configure(
            processors=shared_processors + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, settings.log_level.upper())
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

    # Also configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper()),
    )
```

**Test cases:**
- `test_configure_logging_json_format`
- `test_configure_logging_console_format`
- `test_configure_logging_respects_log_level`

### 2. Add config settings for logging

**File:** `src/config.py` (modify existing)

**Requirements:**
- Add LOG_LEVEL setting (default: INFO)
- Add LOG_FORMAT setting (default: json)
- Validate LOG_LEVEL is valid

```python
class Settings(BaseSettings):
    # ... existing settings ...

    # Logging
    log_level: str = Field(default="INFO", description="Log level (DEBUG, INFO, WARNING, ERROR)")
    log_format: str = Field(default="json", description="Log format (json, console)")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v.upper()
```

**Test cases:**
- `test_settings_default_log_level`
- `test_settings_validates_log_level`

### 3. Create request ID middleware

**File:** `src/middleware/request_id.py` (new file)

**Requirements:**
- Generate unique request ID for each request
- Add request ID to structlog context
- Add X-Request-ID header to response
- Accept X-Request-ID from client if provided

```python
import uuid
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import structlog

REQUEST_ID_HEADER = "X-Request-ID"

class RequestIDMiddleware(BaseHTTPMiddleware):
    """Middleware to add request ID to all requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        # Get or generate request ID
        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())

        # Bind to structlog context for this request
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request_id)

        # Process request
        response = await call_next(request)

        # Add request ID to response headers
        response.headers[REQUEST_ID_HEADER] = request_id

        return response
```

**Test cases:**
- `test_middleware_generates_request_id`
- `test_middleware_uses_client_request_id`
- `test_middleware_adds_response_header`
- `test_middleware_binds_to_structlog_context`

### 4. Create request logging middleware

**File:** `src/middleware/request_logging.py` (new file)

**Requirements:**
- Log incoming requests (method, path, client IP)
- Log response status and duration
- Don't log sensitive headers (Authorization, API key)

```python
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
import structlog

logger = structlog.get_logger(__name__)

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all HTTP requests."""

    async def dispatch(self, request: Request, call_next) -> Response:
        start_time = time.perf_counter()

        # Log request (excluding sensitive paths like /health for noise reduction)
        if not request.url.path.startswith("/health"):
            logger.info(
                "request_started",
                method=request.method,
                path=request.url.path,
                client_ip=request.client.host if request.client else None,
            )

        # Process request
        response = await call_next(request)

        # Calculate duration
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Log response
        if not request.url.path.startswith("/health"):
            logger.info(
                "request_completed",
                method=request.method,
                path=request.url.path,
                status_code=response.status_code,
                duration_ms=round(duration_ms, 2),
            )

        return response
```

**Test cases:**
- `test_middleware_logs_request`
- `test_middleware_logs_response_with_duration`
- `test_middleware_skips_health_endpoint`

### 5. Initialize logging in main.py

**File:** `src/main.py` (modify existing)

**Requirements:**
- Call configure_logging() at startup
- Add request ID middleware
- Add request logging middleware
- Add startup/shutdown logging

```python
from logging_config import configure_logging
from middleware.request_id import RequestIDMiddleware
from middleware.request_logging import RequestLoggingMiddleware

# Configure logging before creating app
configure_logging()

logger = structlog.get_logger(__name__)

# Create FastAPI app
app = FastAPI(...)

# Add middleware (order matters - request ID first)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RequestIDMiddleware)

@app.on_event("startup")
async def startup_event():
    logger.info("application_startup", version="1.0.0")

@app.on_event("shutdown")
async def shutdown_event():
    logger.info("application_shutdown")
```

**Test cases:**
- `test_app_configures_logging`
- `test_app_has_request_id_middleware`
- `test_app_logs_startup`

### 6. Add logging to key services

**Files to modify:**
- `src/services/scraper/worker.py`
- `src/services/scraper/client.py`
- `src/services/llm/client.py`
- `src/services/storage/repositories/extraction.py`
- `src/api/v1/scrape.py`
- `src/api/v1/extraction.py`

**Requirements:**
- Import structlog and create logger in each file
- Log key operations (job processing, API calls, errors)
- Include relevant context (job_id, source_id, etc.)
- Log errors with exception info

**Example patterns:**
```python
import structlog

logger = structlog.get_logger(__name__)

# In service methods
async def process_job(self, job_id: UUID):
    logger.info("job_processing_started", job_id=str(job_id))
    try:
        result = await self._do_work()
        logger.info("job_processing_completed", job_id=str(job_id), result_count=len(result))
        return result
    except Exception as e:
        logger.error("job_processing_failed", job_id=str(job_id), error=str(e), exc_info=True)
        raise

# In API endpoints
@router.post("/scrape")
async def create_scrape_job(...):
    logger.info("scrape_job_created", job_id=str(job_id), url_count=len(urls))
```

**Test cases:**
- `test_scraper_worker_logs_job_start`
- `test_llm_client_logs_api_call`
- `test_api_logs_request_processing`

### 7. Create comprehensive test suite

**File:** `tests/test_logging.py` (new file)

**Requirements:**
- Test logging configuration
- Test middleware behavior
- Test log output format
- Use log capture fixtures

```python
import pytest
import structlog
from logging_config import configure_logging

@pytest.fixture
def captured_logs(caplog):
    """Fixture to capture structlog output."""
    configure_logging()
    return caplog

class TestLoggingConfiguration:
    def test_json_format_produces_valid_json(self, captured_logs):
        ...

class TestRequestIDMiddleware:
    ...

class TestRequestLoggingMiddleware:
    ...
```

## Constraints

- Do NOT change log messages in existing tests (may break assertions)
- Do NOT add excessive logging that impacts performance
- Do NOT log sensitive data (API keys, passwords, full request bodies)
- Skip logging for /health and /metrics endpoints (noise reduction)
- Use TDD: write tests first, then implement
- Keep log messages concise but informative

## Verification

Before creating PR, confirm:
- [ ] All 7 tasks above completed
- [ ] `pytest tests/test_logging.py -v` - All tests pass
- [ ] `pytest` - All 493+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings
- [ ] Logs appear in JSON format when LOG_FORMAT=json
- [ ] Logs appear in console format when LOG_FORMAT=console
- [ ] Request IDs appear in all log entries
- [ ] X-Request-ID header in responses

## Notes

**Structlog Context Variables:**
```python
# Bind context for all logs in current request
structlog.contextvars.bind_contextvars(
    request_id=request_id,
    user_id=user_id,
)

# Clear at end of request
structlog.contextvars.clear_contextvars()
```

**Log Levels:**
- DEBUG: Detailed diagnostic info (not for production)
- INFO: Normal operations (job started, completed)
- WARNING: Unexpected but handled (retry, fallback)
- ERROR: Failures requiring attention

**File Structure:**
```
src/
├── logging_config.py           # Global structlog configuration
├── middleware/
│   ├── __init__.py
│   ├── auth.py                 # Existing
│   ├── request_id.py           # NEW
│   └── request_logging.py      # NEW
└── main.py                     # Initialize logging here
```

**Example JSON Log Output:**
```json
{
  "event": "request_completed",
  "timestamp": "2026-01-11T15:30:45.123456Z",
  "level": "info",
  "request_id": "abc123",
  "method": "POST",
  "path": "/api/v1/scrape",
  "status_code": 202,
  "duration_ms": 45.67
}
```
