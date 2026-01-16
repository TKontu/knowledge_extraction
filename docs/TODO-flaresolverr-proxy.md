# TODO: FlareSolverr Proxy Adapter Implementation

**Objective:** Build a lightweight HTTP proxy adapter that routes Firecrawl requests through FlareSolverr when sites have anti-bot protection, enabling successful crawling of Cloudflare-protected domains.

**Context:** Currently, Firecrawl fails to crawl sites with Cloudflare/anti-bot protection (e.g., weg.net returns "Access Denied", 0 pages crawled). FlareSolverr is deployed but not integrated. This task implements a proxy adapter service that sits between Firecrawl and target sites, intelligently routing through FlareSolverr when needed.

---

## Tasks

### 1. Create Proxy Adapter Module Structure
- [ ] Create `src/services/proxy/__init__.py`
- [ ] Create module docstring explaining proxy adapter purpose
- [ ] Export main classes from `__init__.py`

### 2. Implement FlareSolverr Client (`src/services/proxy/flaresolverr_client.py`)
- [ ] Create `FlareSolverrResponse` dataclass with fields: url, status, cookies, headers, html, user_agent
- [ ] Create `FlareSolverrError` exception class
- [ ] Implement `FlareSolverrClient` class:
  - `__init__(base_url: str, max_timeout: int, http_client: httpx.AsyncClient)`
  - `async solve_request(url: str, method: str = "GET") -> FlareSolverrResponse`
    - POST to `/v1/request` with JSON: `{"cmd": "request.get", "url": url, "maxTimeout": max_timeout}`
    - Parse response, raise `FlareSolverrError` if `status != "ok"`
    - Return structured response with solution data
  - `async close()` for cleanup
  - Context manager support (`__aenter__`, `__aexit__`)
- [ ] Add structured logging for all requests (url, status, duration)
- [ ] Add error handling for timeouts and connection errors

### 3. Implement Proxy Adapter Core (`src/services/proxy/flaresolverr_adapter.py`)
- [ ] Create `ProxyAdapter` class with:
  - `__init__(flaresolverr_url: str, blocked_domains: list[str], max_timeout: int)`
  - Initialize FlareSolverrClient
  - Initialize blocked domains set
- [ ] Implement `should_use_flaresolverr(domain: str) -> bool`:
  - Check if domain in blocked_domains list
  - Return True for blocked, False otherwise
- [ ] Implement `async handle_request(request: aiohttp.web.Request) -> aiohttp.web.Response`:
  - Extract target URL from request
  - Parse domain from URL
  - **If should_use_flaresolverr(domain):**
    - Call `flaresolverr_client.solve_request(url)`
    - Convert FlareSolverr HTML response to HTTP response
    - Return with appropriate headers and status code
  - **Else (direct passthrough):**
    - Make direct HTTP request to target URL
    - Return response as-is
  - Log routing decision (direct vs FlareSolverr)
  - Handle errors and return appropriate HTTP error responses
- [ ] Implement `async health_check(request: aiohttp.web.Request) -> aiohttp.web.Response`:
  - Return JSON: `{"status": "ok", "flaresolverr_url": self.flaresolverr_url}`

### 4. Implement Proxy Server Entry Point (`src/services/proxy/server.py`)
- [ ] Create `start_proxy_server()` async function:
  - Load settings from config
  - Create ProxyAdapter instance
  - Create aiohttp web application
  - Add routes: `*` → `adapter.handle_request`, `/health` → `adapter.health_check`
  - Start TCPSite on configured port (default 8192)
  - Log startup message with port and FlareSolverr URL
  - Wait indefinitely (keep server running)
- [ ] Add `if __name__ == "__main__"` block to run server
- [ ] Add graceful shutdown handling

### 5. Add Configuration (`src/config.py`)
- [ ] Add `proxy_adapter_enabled: bool = Field(default=True, description="...")`
- [ ] Add `proxy_adapter_port: int = Field(default=8192, description="...")`
- [ ] Add `flaresolverr_url: str = Field(default="http://flaresolverr:8191", description="...")`
- [ ] Add `flaresolverr_max_timeout: int = Field(default=60000, description="...")`
- [ ] Add `flaresolverr_blocked_domains: list[str] = Field(default=["weg.net", "siemens.com"], description="...")`

### 6. Create Proxy Dockerfile (`Dockerfile.proxy`)
- [ ] Base image: `python:3.12-slim`
- [ ] Workdir: `/app`
- [ ] Install dependencies: `aiohttp`, `structlog`, `pydantic`, `httpx`
- [ ] Copy `src/services/proxy/` to `/app/proxy/`
- [ ] Copy `src/config.py` to `/app/`
- [ ] CMD: `["python", "-m", "proxy.server"]`

### 7. Update Docker Compose (`docker-compose.prod.yml`)
- [ ] Add `proxy-adapter` service:
  - Build from `Dockerfile.proxy`
  - Environment: `PROXY_PORT`, `FLARESOLVERR_URL`, `LOG_LEVEL`, `FLARESOLVERR_BLOCKED_DOMAINS`
  - Depends on: `flaresolverr`
  - Networks: `scristill`
  - Resources: 512M memory, 0.5 CPU
  - Restart: `unless-stopped`
- [ ] Update `firecrawl-api` service:
  - Add environment: `HTTP_PROXY=http://proxy-adapter:8192`
  - Add environment: `HTTPS_PROXY=http://proxy-adapter:8192`
  - Add environment: `NO_PROXY=localhost,127.0.0.1`
  - Add `depends_on`: `proxy-adapter`
- [ ] Apply same changes to `docker-compose.yml` for dev environment

### 8. Update Environment Variables (`.env.example`)
- [ ] Add section: `# FlareSolverr Proxy Adapter`
- [ ] Document: `PROXY_ADAPTER_ENABLED=true`
- [ ] Document: `PROXY_ADAPTER_PORT=8192`
- [ ] Document: `FLARESOLVERR_URL=http://flaresolverr:8191`
- [ ] Document: `FLARESOLVERR_MAX_TIMEOUT=60000`
- [ ] Document: `FLARESOLVERR_BLOCKED_DOMAINS=weg.net,siemens.com`

### 9. Write Tests
- [ ] Create `tests/test_flaresolverr_client.py`:
  - Test successful solve_request
  - Test error handling (timeout, connection error, FlareSolverr error)
  - Test response parsing
- [ ] Create `tests/test_proxy_adapter.py`:
  - Test should_use_flaresolverr with blocked domain
  - Test should_use_flaresolverr with non-blocked domain
  - Test handle_request with FlareSolverr routing (mock)
  - Test handle_request with direct passthrough (mock)
  - Test health_check endpoint

### 10. Verification & Testing
- [ ] Build and start stack: `docker compose -f docker-compose.prod.yml up -d --build`
- [ ] Check proxy adapter health: `curl http://localhost:8192/health`
- [ ] Test direct passthrough: `curl -x http://localhost:8192 https://www.brevinipowertransmission.com/`
- [ ] Test FlareSolverr routing: `curl -x http://localhost:8192 https://www.weg.net/`
- [ ] Test Firecrawl integration:
  - Create crawl job for blocked domain (weg.net)
  - Verify sources_created > 0
  - Check logs for FlareSolverr routing messages
- [ ] Test Firecrawl with non-blocked domain:
  - Create crawl job for clean domain
  - Verify fast completion without FlareSolverr
- [ ] Monitor resource usage (memory, CPU)

---

## Constraints

- **Do NOT modify Firecrawl source code** - only configure via environment variables
- **Do NOT modify existing scraper/crawler logic** - proxy is transparent
- **Keep proxy lightweight** - use async I/O, minimal memory footprint
- **Fail gracefully** - if FlareSolverr is down, log error and attempt direct connection
- **Respect existing rate limits** - proxy doesn't bypass per-domain rate limiting
- **Follow existing code style** - use structlog, async/await, type hints

---

## Test Cases

1. **Direct passthrough for non-blocked domain:**
   - Input: Request to brevinipowertransmission.com
   - Expected: Direct HTTP request, no FlareSolverr call, fast response

2. **FlareSolverr routing for blocked domain:**
   - Input: Request to weg.net
   - Expected: FlareSolverr API call, browser-rendered response, slower (~10s)

3. **Health check endpoint:**
   - Input: GET http://localhost:8192/health
   - Expected: JSON response with `{"status": "ok", ...}`

4. **Firecrawl integration with blocked domain:**
   - Input: Crawl job for weg.net with max_depth=2, limit=10
   - Expected: Job completes with sources_created > 0

5. **Error handling:**
   - Input: FlareSolverr service down, request to blocked domain
   - Expected: Log error, attempt direct connection, return appropriate error if both fail

---

## Files to Create

- `src/services/proxy/__init__.py`
- `src/services/proxy/flaresolverr_client.py`
- `src/services/proxy/flaresolverr_adapter.py`
- `src/services/proxy/server.py`
- `Dockerfile.proxy`
- `tests/test_flaresolverr_client.py`
- `tests/test_proxy_adapter.py`

## Files to Modify

- `src/config.py` (add proxy configuration fields)
- `docker-compose.prod.yml` (add proxy service, update firecrawl env)
- `docker-compose.yml` (same changes for dev)
- `.env.example` (document new env vars)

---

## Success Criteria

- [ ] Proxy adapter service builds and starts successfully
- [ ] Health check endpoint returns 200 OK
- [ ] Non-blocked domains crawl without FlareSolverr (fast)
- [ ] Blocked domains crawl successfully via FlareSolverr (sources > 0)
- [ ] No increase in failed crawl jobs
- [ ] Logs clearly show routing decisions
- [ ] Resource usage within limits (512MB, 0.5 CPU)
- [ ] All tests pass

---

## Notes

- **FlareSolverr API format:**
  ```json
  POST /v1/request
  {
    "cmd": "request.get",
    "url": "https://example.com",
    "maxTimeout": 60000
  }

  Response:
  {
    "status": "ok",
    "solution": {
      "url": "...",
      "status": 200,
      "response": "<html>...</html>",
      "headers": {...},
      "cookies": [...]
    }
  }
  ```

- **HTTP Proxy Protocol:**
  - Firecrawl's Playwright will send standard HTTP/HTTPS requests
  - For HTTPS: CONNECT method for tunneling
  - Proxy must handle both GET/POST requests and CONNECT tunneling

- **Blocked Domains List:**
  - Start with known blockers: weg.net, siemens.com
  - Can expand based on observed 403 errors
  - Make configurable via FLARESOLVERR_BLOCKED_DOMAINS env var

- **Performance:**
  - Direct passthrough adds ~1-5ms latency
  - FlareSolverr routing adds ~5-15s (browser rendering)
  - Only use FlareSolverr when necessary

---

**Reference Plan:** See `/home/linux/.claude/plans/lexical-growing-nova.md` for detailed architecture and implementation strategy.
