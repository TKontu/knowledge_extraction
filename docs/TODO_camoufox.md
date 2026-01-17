# TODO: Camoufox Browser Service Implementation

**Goal:** Replace Playwright microservice with Camoufox to enable HTTPS support for anti-bot protected sites (Akamai, Cloudflare).

**Branch:** `feature/camoufox-service`

**Why Camoufox?** After evaluating alternatives (nodriver, Botasaurus, DrissionPage, Patchright):
- 0% detection rate (best in class)
- Native Playwright integration (minimal code changes)
- HTTPS native (no proxy interception needed)
- Backup: Patchright (67% detection, drop-in Playwright replacement)

---

## Key Technical Notes

### HTML Response Contains JavaScript-Rendered Content
The service returns fully rendered DOM after JS execution, not raw HTML:
```typescript
content = await page.content();  // Returns DOM AFTER JavaScript has run
```

### How `/crawl` Uses This Service
1. `/crawl` API creates a crawl job
2. First page scraped via `PLAYWRIGHT_MICROSERVICE_URL` -> returns HTML
3. Firecrawl parses HTML to discover links
4. Each discovered link creates a new scrape job calling our `/scrape`
5. **No special crawl logic needed** - Firecrawl handles link discovery

### No Session Persistence Required
Firecrawl creates a **new browser context per request** and closes it immediately.
Our implementation must match this behavior (no browser pool needed).

---

## Phase 0: Documentation

- [x] Create `docs/ARCHITECTURE-camoufox.md`
- [x] Create `docs/TODO_camoufox.md`

---

## Phase 1: Core Service (MVP)

### Directory Structure
- [ ] Create `src/services/camoufox/__init__.py`
- [ ] Create `src/services/camoufox/config.py` - pydantic-settings configuration
- [ ] Create `src/services/camoufox/models.py` - Request/Response schemas (matching Firecrawl exactly)
- [ ] Create `src/services/camoufox/scraper.py` - Browser management + page scraping
- [ ] Create `src/services/camoufox/server.py` - FastAPI app with `/scrape` and `/health`

### Docker Files
- [ ] Create `Dockerfile.camoufox` (with `camoufox fetch`)
- [ ] Create `requirements-camoufox.txt`

### Verification
- [ ] Service starts without errors
- [ ] `/health` returns `{"status": "healthy", "maxConcurrentPages": N, "activePages": N}`
- [ ] `/scrape` returns HTML for simple HTTP site

---

## Phase 2: Docker Integration

- [ ] Build and test Docker image locally
- [ ] Add `camoufox` service to `docker-compose.yml`
- [ ] Configure Firecrawl: `PLAYWRIGHT_MICROSERVICE_URL=http://camoufox:3003/scrape`
- [ ] Test with simple HTTP site
- [ ] Test with blocked HTTP domain (http://www.weg.net)

---

## Phase 3: HTTPS Testing (Key Goal)

- [ ] Test with HTTPS protected domain (https://www.weg.net) **KEY TEST**
- [ ] Verify anti-bot bypass works (real content, not bot detection page)
- [ ] Test multi-page crawl via Firecrawl `/crawl` API with depth > 1
- [ ] Compare content quality vs FlareSolverr

---

## Phase 4: Production Hardening

- [ ] Add residential proxy support (`CAMOUFOX_PROXY` + `geoip=True`)
- [ ] Add retry logic with exponential backoff
- [ ] Performance tuning (MAX_CONCURRENT_PAGES)
- [ ] Update `docker-compose.prod.yml`
- [ ] Create `tests/test_camoufox_service.py`

### Verification
- [ ] 95%+ success rate for blocked domains (with residential proxy)
- [ ] Page load < 30s for protected sites
- [ ] Memory usage < 500MB per browser instance

---

## API Contract (Firecrawl-Compatible)

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

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CAMOUFOX_PORT` | 3003 | Service port |
| `MAX_CONCURRENT_PAGES` | 10 | Max concurrent browser contexts |
| `CAMOUFOX_TIMEOUT` | 15000 | Default page timeout (ms) |
| `CAMOUFOX_PROXY` | None | Optional residential proxy URL |
| `CAMOUFOX_HEADLESS` | true | Run headless |
| `LOG_LEVEL` | INFO | Logging level |

---

## Files Summary

### Create
1. `src/services/camoufox/__init__.py`
2. `src/services/camoufox/config.py` - pydantic-settings configuration
3. `src/services/camoufox/models.py` - Request/Response schemas
4. `src/services/camoufox/scraper.py` - Browser management + scraping logic
5. `src/services/camoufox/server.py` - FastAPI app with /scrape and /health
6. `Dockerfile.camoufox`
7. `requirements-camoufox.txt`
8. `tests/test_camoufox_service.py` (Phase 4)

**Note:** `browser_pool.py` NOT needed - Firecrawl creates new context per request.

### Modify
1. `docker-compose.yml` - Add camoufox service
2. `docker-compose.prod.yml` - Add camoufox service
3. `stack.env` - Add CAMOUFOX_* variables

---

## Success Criteria

1. **HTTPS Support:** Successfully scrape `https://www.weg.net` (not just `http://`)
2. **JS Rendering:** Content includes JavaScript-generated elements
3. **Multi-page Crawl:** Firecrawl crawl with depth=2 works
4. **Performance:** Page load < 30s for protected sites
5. **Reliability:** 95%+ success rate for blocked domains (with residential proxy)
6. **Compatibility:** Zero breaking changes to Firecrawl API contract

**Note:** Without residential proxies, success rate will be lower due to datacenter IP blocking.

---

## Source References

- **Firecrawl Playwright Service:** `/mnt/c/code/firecrawl-ref/apps/playwright-service-ts/api.ts`
- **Camoufox Python Package:** `/tmp/camoufox-pkg/camoufox-src/camoufox/`
- **Camoufox Async API:** `camoufox.async_api.AsyncCamoufox`
- **Camoufox Docs:** https://camoufox.com/python/
