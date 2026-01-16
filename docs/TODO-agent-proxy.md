# TODO: FlareSolverr Proxy Adapter Implementation

**Agent:** agent-proxy
**Branch:** `feat/flaresolverr-proxy`
**Priority:** High

## Context

Many industrial company websites (WEG, Siemens, Wattdrive) use Cloudflare anti-bot protection, causing Firecrawl crawls to fail with "Access Denied" and 0 pages scraped. FlareSolverr is already deployed in the docker stack but not integrated.

This task implements a transparent HTTP proxy adapter that sits between Firecrawl and target websites, intelligently routing requests through FlareSolverr when accessing blocked domains, while passing through non-blocked domains directly for performance.

**Key Architecture Decision:** Use HTTP proxy protocol (HTTP_PROXY env var) rather than modifying Firecrawl code. The proxy adapter inspects target domains and routes accordingly.

## Objective

Build a lightweight async HTTP proxy service that routes Firecrawl requests through FlareSolverr for Cloudflare-protected domains, enabling successful crawling of blocked sites without modifying Firecrawl's code.

## Tasks

### 1. Create Proxy Module Structure

**File:** `src/services/proxy/__init__.py`

**Requirements:**
- Create empty `__init__.py` with module docstring
- Docstring should explain: "HTTP proxy adapter for routing Firecrawl requests through FlareSolverr when accessing anti-bot protected domains"
- Export main classes: `from .flaresolverr_client import FlareSolverrClient` and `from .flaresolverr_adapter import ProxyAdapter`

### 2. Implement FlareSolverr Client

**File:** `src/services/proxy/flaresolverr_client.py`

**Requirements:**

Create dataclasses and exception:
```python
from dataclasses import dataclass

@dataclass
class FlareSolverrResponse:
    """Response from FlareSolverr API."""
    url: str
    status: int
    cookies: list[dict]
    headers: dict[str, str]
    html: str
    user_agent: str

class FlareSolverrError(Exception):
    """Error from FlareSolverr API."""
    pass
```

Create `FlareSolverrClient` class:
- `__init__(self, base_url: str, max_timeout: int, http_client: httpx.AsyncClient)`
- `async solve_request(self, url: str, method: str = "GET") -> FlareSolverrResponse`
  - POST to `{base_url}/v1` with JSON body:
    ```json
    {
      "cmd": "request.get",
      "url": "<target_url>",
      "maxTimeout": <max_timeout_ms>
    }
    ```
  - Check response `status == "ok"`, raise `FlareSolverrError` if not
  - Extract `solution` object from response
  - Return `FlareSolverrResponse` with: `solution["url"]`, `solution["status"]`, `solution["cookies"]`, `solution["headers"]`, `solution["response"]` (html), `solution["userAgent"]`
  - Use structlog for logging: `logger.info("flaresolverr_request", url=url, status=response_status, duration=duration)`
  - Handle `httpx.TimeoutException` and `httpx.ConnectError` with specific error messages
- `async close(self) -> None` - close http_client
- Implement context manager: `async __aenter__` and `async __aexit__`

### 3. Implement Proxy Adapter Core

**File:** `src/services/proxy/flaresolverr_adapter.py`

**Requirements:**

Create `ProxyAdapter` class:
- `__init__(self, flaresolverr_url: str, blocked_domains: list[str], max_timeout: int)`
  - Initialize `FlareSolverrClient` with `httpx.AsyncClient(timeout=max_timeout/1000)`
  - Store `blocked_domains` as a set (lowercase)
- `should_use_flaresolverr(self, domain: str) -> bool`
  - Extract domain from URL if full URL provided
  - Convert domain to lowercase
  - Return `True` if domain in `blocked_domains` set, else `False`
- `async handle_request(self, request: aiohttp.web.Request) -> aiohttp.web.Response`
  - Extract target URL from request path (strip leading `/`)
  - Parse domain from URL using `urllib.parse.urlparse`
  - If `should_use_flaresolverr(domain)`:
    - Log: `logger.info("proxy_routing", url=url, method="flaresolverr")`
    - Call `flaresolverr_client.solve_request(url)`
    - Return `aiohttp.web.Response(text=response.html, status=response.status, headers=response.headers)`
  - Else (direct passthrough):
    - Log: `logger.info("proxy_routing", url=url, method="direct")`
    - Use `httpx.AsyncClient` to make direct GET request to URL
    - Return `aiohttp.web.Response(body=response.content, status=response.status_code, headers=dict(response.headers))`
  - Handle exceptions: return `aiohttp.web.Response(text=str(error), status=500)` with error logging
- `async health_check(self, request: aiohttp.web.Request) -> aiohttp.web.Response`
  - Return JSON: `{"status": "ok", "flaresolverr_url": self.flaresolverr_url, "blocked_domains": list(self.blocked_domains)}`

### 4. Implement Proxy Server Entry Point

**File:** `src/services/proxy/server.py`

**Requirements:**

Create `start_proxy_server()` function:
- Import config: `from config import settings`
- Create `ProxyAdapter` instance with:
  - `flaresolverr_url=settings.flaresolverr_url`
  - `blocked_domains=settings.flaresolverr_blocked_domains`
  - `max_timeout=settings.flaresolverr_max_timeout`
- Create `aiohttp.web.Application()`
- Add routes:
  - `app.router.add_get('/health', adapter.health_check)`
  - `app.router.add_route('*', '/{path:.*}', adapter.handle_request)`
- Create runner: `aiohttp.web.AppRunner(app)`
- `await runner.setup()`
- Create site: `aiohttp.web.TCPSite(runner, '0.0.0.0', settings.proxy_adapter_port)`
- `await site.start()`
- Log: `logger.info("proxy_server_started", port=settings.proxy_adapter_port, flaresolverr_url=settings.flaresolverr_url)`
- Wait indefinitely: `await asyncio.Event().wait()`

Add `if __name__ == "__main__"` block:
```python
if __name__ == "__main__":
    asyncio.run(start_proxy_server())
```

Add graceful shutdown:
- Register signal handler for SIGTERM/SIGINT
- Call `runner.cleanup()` on shutdown

### 5. Add Configuration

**File:** `src/config.py`

**Requirements:**

Add these fields to the `Settings` class (after the scraper configuration section):

```python
# FlareSolverr Proxy Adapter
proxy_adapter_enabled: bool = Field(
    default=True,
    description="Enable proxy adapter service",
)
proxy_adapter_port: int = Field(
    default=8192,
    description="Port for proxy adapter service",
)
flaresolverr_url: str = Field(
    default="http://flaresolverr:8191",
    description="FlareSolverr service URL",
)
flaresolverr_max_timeout: int = Field(
    default=60000,
    description="FlareSolverr timeout in milliseconds",
)
flaresolverr_blocked_domains: list[str] = Field(
    default_factory=lambda: ["weg.net", "siemens.com", "wattdrive.com"],
    description="Domains requiring FlareSolverr proxy",
)
```

### 6. Create Proxy Dockerfile

**File:** `Dockerfile.proxy` (in repo root)

**Requirements:**

```dockerfile
FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir \
    aiohttp==3.9.1 \
    structlog==24.1.0 \
    pydantic==2.5.3 \
    pydantic-settings==2.1.0 \
    httpx==0.26.0

# Copy only necessary files
COPY src/services/proxy/ /app/proxy/
COPY src/config.py /app/
COPY src/logging_config.py /app/

# Run server
CMD ["python", "-m", "proxy.server"]
```

### 7. Update Docker Compose

**File:** `docker-compose.prod.yml`

**Requirements:**

Add proxy-adapter service (after flaresolverr service):
```yaml
proxy-adapter:
  build:
    context: .
    dockerfile: Dockerfile.proxy
  container_name: proxy-adapter
  environment:
    - PROXY_ADAPTER_PORT=8192
    - FLARESOLVERR_URL=http://flaresolverr:8191
    - FLARESOLVERR_MAX_TIMEOUT=60000
    - FLARESOLVERR_BLOCKED_DOMAINS=weg.net,siemens.com,wattdrive.com
    - LOG_LEVEL=INFO
  depends_on:
    - flaresolverr
  networks:
    - scristill
  deploy:
    resources:
      limits:
        memory: 512M
        cpus: '0.5'
  restart: unless-stopped
```

Update firecrawl-api service (add to environment section):
```yaml
- HTTP_PROXY=http://proxy-adapter:8192
- HTTPS_PROXY=http://proxy-adapter:8192
- NO_PROXY=localhost,127.0.0.1,redis,postgres,qdrant
```

Add to firecrawl-api depends_on:
```yaml
depends_on:
  - proxy-adapter
```

Apply the same changes to `docker-compose.yml` for dev environment.

### 8. Update Environment Variables

**File:** `.env.example`

**Requirements:**

Add section at the end:
```bash
# FlareSolverr Proxy Adapter
PROXY_ADAPTER_ENABLED=true
PROXY_ADAPTER_PORT=8192
FLARESOLVERR_URL=http://flaresolverr:8191
FLARESOLVERR_MAX_TIMEOUT=60000
FLARESOLVERR_BLOCKED_DOMAINS=weg.net,siemens.com,wattdrive.com
```

### 9. Write Tests

**File:** `tests/test_flaresolverr_client.py`

**Requirements:**

Test cases to implement:
- `test_solve_request_success` - Mock httpx response with valid FlareSolverr response, verify FlareSolverrResponse fields
- `test_solve_request_flaresolverr_error` - Mock response with `status != "ok"`, verify FlareSolverrError raised
- `test_solve_request_timeout` - Mock httpx.TimeoutException, verify FlareSolverrError raised with timeout message
- `test_solve_request_connection_error` - Mock httpx.ConnectError, verify FlareSolverrError raised
- `test_context_manager` - Verify client can be used as async context manager

Use pytest-asyncio and pytest-mock for mocking.

**File:** `tests/test_proxy_adapter.py`

**Requirements:**

Test cases to implement:
- `test_should_use_flaresolverr_blocked_domain` - Assert True for "weg.net"
- `test_should_use_flaresolverr_non_blocked_domain` - Assert False for "example.com"
- `test_should_use_flaresolverr_case_insensitive` - Assert True for "WEG.NET"
- `test_handle_request_flaresolverr_routing` - Mock FlareSolverrClient.solve_request, verify correct response
- `test_handle_request_direct_passthrough` - Mock httpx.AsyncClient.get, verify correct response
- `test_health_check` - Verify JSON response with correct structure

Use aiohttp.test_utils for testing aiohttp handlers.

### 10. Verification

**Manual Testing Steps:**

Build and start stack:
```bash
docker compose -f docker-compose.prod.yml up -d --build proxy-adapter
```

Check proxy health:
```bash
curl http://localhost:8192/health
```

Expected: `{"status": "ok", "flaresolverr_url": "http://flaresolverr:8191", "blocked_domains": [...]}`

Test direct passthrough:
```bash
curl -x http://localhost:8192 https://www.brevinipowertransmission.com/
```

Expected: HTML content returned quickly (~1-2 seconds)

Test FlareSolverr routing:
```bash
curl -x http://localhost:8192 https://www.weg.net/
```

Expected: HTML content returned after ~10-15 seconds (browser rendering)

## Constraints

- **Do NOT modify Firecrawl source code** - only configure via environment variables
- **Do NOT modify existing scraper/crawler logic** - proxy is transparent to the application
- **Keep proxy lightweight** - use async I/O throughout
- **Fail gracefully** - if FlareSolverr is down, log error and return appropriate HTTP error (do not attempt direct connection as fallback)
- **Follow existing code style** - use structlog, type hints, async/await
- **Do NOT run full test suite** - only run tests in Test Scope below
- **Do NOT lint entire codebase** - only lint files in Lint Scope below

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_flaresolverr_client.py tests/test_proxy_adapter.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/proxy/flaresolverr_client.py \
  src/services/proxy/flaresolverr_adapter.py \
  src/services/proxy/server.py \
  src/services/proxy/__init__.py \
  src/config.py
```

## Verification

Before creating PR, run ONLY the scoped commands above:

1. `pytest tests/test_flaresolverr_client.py tests/test_proxy_adapter.py -v` - Must pass
2. `ruff check <files listed above>` - Must be clean
3. All 10 tasks above completed
4. Dockerfile.proxy builds successfully: `docker build -f Dockerfile.proxy -t test-proxy .`

## Definition of Done

- [ ] All 10 tasks completed
- [ ] Tests written and passing (scoped)
- [ ] Lint clean (scoped)
- [ ] Dockerfile.proxy builds without errors
- [ ] Manual verification: proxy health check returns 200 OK
- [ ] PR created with title: `feat: add FlareSolverr proxy adapter for anti-bot bypass`

## Success Criteria

- Proxy adapter service builds and starts successfully
- Health check endpoint returns 200 OK with correct JSON structure
- Tests pass with 100% coverage for new code
- No ruff violations in new files
- Blocked domains (weg.net) route through FlareSolverr
- Non-blocked domains pass through directly

## Notes

**FlareSolverr API Endpoint:**
- Correct: `POST http://flaresolverr:8191/v1`
- NOT: `/v1/request` (that's an old version)

**HTTP Proxy Protocol:**
- The proxy receives requests with target URL in the path: `GET /https://example.com`
- Or as absolute URI: `GET https://example.com`
- Handle both formats by extracting the URL correctly

**Performance Expectations:**
- Direct passthrough: ~1-5ms additional latency
- FlareSolverr routing: ~5-15s (browser rendering overhead)
- Only use FlareSolverr for domains in blocked list

**Blocked Domains List:**
- Start with: weg.net, siemens.com, wattdrive.com
- Can be expanded via FLARESOLVERR_BLOCKED_DOMAINS env var (comma-separated)
