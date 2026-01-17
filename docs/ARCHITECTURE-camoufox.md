# Camoufox Browser Service Architecture

## Problem Statement

The current Playwright + FlareSolverr stack cannot handle HTTPS traffic for anti-bot protected sites:

- **FlareSolverr** uses Selenium with undetected-chromedriver for HTTP anti-bot bypass
- **Limitation:** FlareSolverr acts as a proxy and cannot intercept HTTPS (TLS termination issue)
- **Impact:** Sites like `https://www.weg.net` (Akamai-protected) cannot be scraped via HTTPS

---

## Solution: Camoufox

**Camoufox** is a custom Firefox fork with C++-level fingerprint modifications:
- **0% detection rate** across CreepJS, BrowserScan, Cloudflare Turnstile, DataDome, Imperva
- Native HTTPS support (no proxy interception needed)
- C++ level modifications (undetectable via JavaScript inspection)
- Works with residential proxies for Akamai bypass

**Backup Option:** Patchright (67% detection reduction, drop-in Playwright replacement) if Camoufox proves unstable.

---

## Key Technical Details

### HTML Response Contains JavaScript-Rendered Content
The service returns **fully rendered DOM after JavaScript execution**, not raw HTML source:
```typescript
// From Firecrawl api.ts line 179
content = await page.content();  // Returns DOM AFTER JavaScript has run
```
JavaScript-generated content IS captured in the response.

### WSL vs Linux Compatibility
- **WSL limitation is for BUILDING from source only**
- The `pip install camoufox` downloads pre-built binaries
- **TrueNAS (remote server) is real Linux and fully supported**
- Supported architectures: x86_64, arm64, i686

### No Session Persistence Required
Firecrawl's Playwright service creates a **new browser context per request** and closes it immediately.
There is NO session persistence across requests. Our implementation must match this behavior.

### How `/crawl` Uses the Playwright Service
Firecrawl's crawling works by:
1. `/crawl` API creates a crawl job
2. First page is scraped via `PLAYWRIGHT_MICROSERVICE_URL` -> returns HTML
3. Firecrawl parses the HTML to discover links
4. Each discovered link creates a new scrape job
5. Each scrape job calls `PLAYWRIGHT_MICROSERVICE_URL` independently

**Implication:** Our `/scrape` endpoint handles ALL pages in a crawl. No special crawl-level logic needed.
Link discovery and depth control are handled by Firecrawl, not the browser service.

---

## Architecture Comparison

### Current State

```
Firecrawl API
    | POST /scrape (per page)
    v
Playwright Service (Node.js, port 3003)
    | via PROXY_SERVER env var
    v
Proxy-Adapter (port 8192)
    |-- Non-blocked domains -> Direct HTTP
    +-- Blocked domains -> FlareSolverr (HTTP only, no HTTPS)
```

**Limitation:** FlareSolverr cannot handle HTTPS traffic.

### Target State

```
Firecrawl API
    | POST /scrape (per page)
    v
Camoufox Service (Python/FastAPI, port 3003)
    | Native stealth browser (no proxy needed for blocked domains)
    +-- All domains (HTTP + HTTPS) -> Direct with anti-bot bypass
```

**Key Improvement:** Camoufox handles HTTPS natively with stealth fingerprints.

---

## Component Design

### Service Structure

```
src/services/camoufox/
|-- __init__.py
|-- server.py          # FastAPI app entry point
|-- scraper.py         # Browser management + page scraping
|-- config.py          # Service-specific settings
+-- models.py          # Request/Response schemas (matching Firecrawl exactly)
```

**Note:** `browser_pool.py` removed - unnecessary since Firecrawl creates new context per request.

### Architecture Pattern

```python
# Single browser instance, new context per request (matches Firecrawl pattern)
class CamoufoxScraper:
    """Manages a single Camoufox browser with per-request contexts."""

    def __init__(self, max_concurrent: int = 10):
        self.browser: Browser = None
        self.semaphore = asyncio.Semaphore(max_concurrent)

    async def scrape(self, url: str, timeout: int, ...) -> dict:
        async with self.semaphore:
            context = await self.browser.new_context(...)
            try:
                page = await context.new_page()
                response = await page.goto(url, timeout=timeout)
                content = await page.content()
                return {"content": content, "pageStatusCode": response.status, ...}
            finally:
                await page.close()
                await context.close()  # Context MUST be closed per request
```

### API Contract (Firecrawl-Compatible)

**CRITICAL: Must match exact Firecrawl Playwright service contract**

**Endpoint:** `POST /scrape`

**Request:**
```json
{
  "url": "https://www.weg.net",
  "wait_after_load": 0,
  "timeout": 15000,
  "headers": {"User-Agent": "..."},
  "check_selector": null,
  "skip_tls_verification": false
}
```

**Response (Success):**
```json
{
  "content": "<html>...</html>",
  "pageStatusCode": 200,
  "contentType": "text/html"
}
```

**Response (Page Error):**
```json
{
  "content": "<html>...</html>",
  "pageStatusCode": 403,
  "contentType": "text/html",
  "pageError": "Access Denied"
}
```

**Endpoint:** `GET /health`

**Response:**
```json
{
  "status": "healthy",
  "maxConcurrentPages": 10,
  "activePages": 2
}
```

**NOTE:** Service returns raw HTML only. Firecrawl handles markdown conversion internally.

---

## Docker Configuration

### Dockerfile.camoufox

```dockerfile
FROM python:3.12-slim

# Install Firefox dependencies (required by Camoufox's custom Firefox)
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libx11-xcb1 \
    libasound2 \
    libxcomposite1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements-camoufox.txt .
RUN pip install --no-cache-dir -r requirements-camoufox.txt

# CRITICAL: Download Camoufox's custom Firefox binary
RUN python -m camoufox fetch

# Copy service code
COPY src/services/camoufox/ ./camoufox/
COPY src/logging_config.py .

ENV PYTHONPATH=/app

EXPOSE 3003

CMD ["python", "-m", "camoufox.server"]
```

### requirements-camoufox.txt

```
# Camoufox with geoip support
camoufox[geoip]>=0.4.11

# Camoufox dependencies (explicitly listed for clarity)
browserforge>=1.2.0
orjson>=3.10.0
numpy>=1.26.0
screeninfo>=0.8.0
ua-parser>=1.0.0

# Web framework
fastapi>=0.115.0
uvicorn[standard]>=0.32.0

# Data validation
pydantic>=2.9.0
pydantic-settings>=2.6.0

# Logging
structlog>=24.4.0
```

**Note:** `markdownify` removed - Firecrawl handles markdown conversion internally.

### docker-compose.yml (addition)

```yaml
camoufox:
  build:
    context: .
    dockerfile: Dockerfile.camoufox
  container_name: camoufox-service
  ports:
    - "3003:3003"
  environment:
    - CAMOUFOX_PORT=3003
    - MAX_CONCURRENT_PAGES=${MAX_CONCURRENT_PAGES:-10}
    - CAMOUFOX_TIMEOUT=${CAMOUFOX_TIMEOUT:-15000}
    - CAMOUFOX_PROXY=${CAMOUFOX_PROXY:-}
    - LOG_LEVEL=${LOG_LEVEL:-INFO}
  deploy:
    resources:
      limits:
        memory: 2G
        cpus: '2'
  restart: unless-stopped
  networks:
    - scristill
```

**Firecrawl Update:**
```yaml
firecrawl-api:
  environment:
    - PLAYWRIGHT_MICROSERVICE_URL=http://camoufox:3003/scrape
    # Remove HTTP_PROXY/HTTPS_PROXY - Camoufox handles anti-bot directly
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `CAMOUFOX_PORT` | 3003 | Service port |
| `MAX_CONCURRENT_PAGES` | 10 | Max concurrent browser contexts |
| `CAMOUFOX_TIMEOUT` | 15000 | Default page timeout (ms) |
| `CAMOUFOX_PROXY` | None | Optional residential proxy URL |
| `CAMOUFOX_HEADLESS` | true | Run headless |
| `LOG_LEVEL` | INFO | Logging level |

---

## Alternatives Comparison

| Feature | Camoufox | Patchright | nodriver | Botasaurus | FlareSolverr |
|---------|----------|------------|----------|------------|--------------|
| **Detection Rate** | 0% (best) | 67% | Good | Good | Good |
| **HTTPS Support** | Yes | Yes | Yes | Yes | **No (HTTP only)** |
| **Firecrawl Compatible** | High | High | Low | None | Current |
| **Proxy Auth Support** | Yes | Yes | Issues | Excellent | Yes |
| **Docker/Linux** | Yes | Yes | Needs Xvfb | Needs Xvfb | Yes |
| **Migration Effort** | Medium | Low | High | High | N/A |
| **Production Ready** | Beta | Stable | No | Mixed | Stable |

**Recommendation:** Start with Camoufox. Fall back to Patchright if stability issues arise.

---

## Migration Path

### Step 1: Parallel Deployment
Run both services during testing:
```yaml
playwright:
  # Keep existing config
camoufox:
  ports:
    - "3004:3003"  # Test port
```

### Step 2: Switch Firecrawl
```yaml
firecrawl-api:
  environment:
    - PLAYWRIGHT_MICROSERVICE_URL=http://camoufox:3003/scrape
```

### Step 3: Remove Legacy
Once validated, remove:
- `playwright` service
- `proxy-adapter` service (if only used for anti-bot)
- `flaresolverr` service (if only used for anti-bot)

---

## Risk Mitigation

| Risk | Mitigation |
|------|------------|
| Camoufox detection by Akamai | **Residential proxies required** - datacenter IPs get blocked regardless of browser |
| Camoufox beta instability | Patchright as backup (drop-in replacement, 67% detection reduction) |
| Memory usage (browsers) | MAX_CONCURRENT_PAGES limit (default 10), context-per-request pattern |
| Service crashes | Graceful error handling, health checks, Docker restart policy |
| Breaking Firecrawl API | Exact API compatibility verified against source code |
| Firefox dependencies in Docker | Extended apt-get deps list, `camoufox fetch` in Dockerfile |

---

## Success Metrics

1. **HTTPS Support:** Successfully scrape `https://www.weg.net` (not just `http://`)
2. **JS Rendering:** Content includes JavaScript-generated elements
3. **Performance:** Page load < 30s for protected sites
4. **Reliability:** 95%+ success rate for blocked domains (with residential proxy)
5. **Resource Usage:** < 500MB per browser instance
6. **Compatibility:** Zero breaking changes to Firecrawl API contract

**Note:** Without residential proxies, success rate will be lower due to datacenter IP blocking.

---

## Source References

- **Firecrawl Playwright Service:** `/mnt/c/code/firecrawl-ref/apps/playwright-service-ts/api.ts`
- **Firecrawl Engine Selection:** `/mnt/c/code/firecrawl-ref/apps/api/src/scraper/scrapeURL/engines/index.ts`
- **Camoufox Python Package:** `/tmp/camoufox-pkg/camoufox-src/camoufox/`
- **Camoufox Async API:** `camoufox.async_api.AsyncCamoufox`
- **Camoufox Docs:** https://camoufox.com/python/
- **Crawlee + Camoufox Template:** https://crawlee.dev/python/docs/examples/playwright-crawler-with-camoufox
