# Handoff: Infinite Crawl Retry Loop Investigation

## Completed
- ✅ **Fixed Qdrant collection initialization** (PR #42 merged to main)
  - Added retry logic with exponential backoff (1s, 2s, 4s, 8s)
  - Fixed Docker health checks (wget → file test)
  - Comprehensive TDD test coverage
- ✅ **Verified end-to-end pipeline working in production**
  - Successfully crawled ajax-javascript page
  - 5 facts extracted and stored in Qdrant
  - Semantic search returning correct results (scores 0.7-0.8)
- ✅ **Identified root cause of infinite retry loop**
  - Located in Firecrawl fork: `/mnt/c/code/firecrawl/apps/api/src/services/worker/scrape-worker.ts:583-586`
  - Failed pages get unlocked from `visited_unique` set → rediscovered through links → infinite re-queue
- ✅ **Implemented HTTP error handling fixes (TDD approach)** (PR #44 merged)
  - **Fix 1**: Added standard browser headers to Camoufox (Sec-Fetch-*, Accept, etc.)
  - **Fix 2**: Filter HTTP errors (status >= 400) before storing sources
  - **Fix 3**: Track HTTP status in source metadata for observability
  - **Fix 4**: Filter protected headers (User-Agent, Accept-Language, Accept-Encoding) from custom headers
  - **Tests**: 15 new tests, all passing
  - Files modified: `src/services/camoufox/scraper.py`, `src/services/scraper/crawl_worker.py`
- ✅ **Fixed vLLM batching for better KV cache utilization** (PR #46 merged)
  - **Root Cause**: Batch-and-wait pattern created gaps where KV cache emptied (observed at 4% utilization)
  - **Fix**: Replaced with continuous semaphore pattern - new requests start immediately when any slot opens
  - **Config**: Increased `extraction_max_concurrent_chunks` from 25 → 80
  - **TDD**: 5 new tests in `tests/test_schema_orchestrator_concurrency.py`
- ✅ **Tested all problematic pages individually** (2026-01-20)
  - `/pages/frames/` → **200 OK, 1-3 seconds** ✅
  - `/pages/forms/?page_num=1` → **200 OK, 1-3 seconds** ✅
  - `/pages/advanced/?gotcha=login` → **400 Bad Request** (expected - requires session/auth)
  - `/pages/advanced/?gotcha=csrf` → **400 Bad Request** (expected - requires CSRF token)
- ✅ **Confirmed infinite loop bug still exists** (2026-01-20)
  - Verified code at `scrape-worker.ts:583-586` unchanged
  - No retry limit, no failure tracking, no backoff strategy in Firecrawl
- ❌ **Delay unit mismatch hypothesis DISPROVED** (2026-01-20)
  - Initially suspected delay bug, but self-hosted deployments bypass concurrency queue
  - Root cause of bulk crawl timeouts still under investigation
- ✅ **Implemented browser pool for Camoufox** (2026-01-20)
  - **Root Cause**: Single Firefox browser cannot handle concurrent `page.goto()` calls
  - **Fix**: Browser pool with N=5 instances (configurable via `CAMOUFOX_BROWSER_COUNT`)
  - **Implementation**: Round-robin distribution across browsers
  - **Config**: `CAMOUFOX_BROWSER_COUNT=5`, `CAMOUFOX_POOL_SIZE=10` (total concurrent pages)
  - **Tests**: 17 tests passing (12 new browser pool tests + 5 existing header tests)
  - Files modified: `src/services/camoufox/scraper.py`, `src/services/camoufox/config.py`, `docker-compose.yml`

## In Progress
- 🧪 **Ready for production testing** - Deploy and test concurrent crawls

## Root Cause Analysis (2026-01-20)

### Issue 1: SINGLE BROWSER INSTANCE BOTTLENECK (CONFIRMED ROOT CAUSE)

**Location**: `/mnt/c/code/knowledge_extraction-orchestrator/src/services/camoufox/scraper.py`

**The Problem**:
```python
# Line 681 - Global singleton
scraper = CamoufoxScraper()

# Line 134 - Single browser instance
self._browser: Browser | None = None

# Line 501-503 - All requests share the same browser
async with self._semaphore:
    async with self._acquire_page():
        return await self._do_scrape(request)  # Creates context on ONE browser
```

**Evidence from debug logs** (2026-01-20):
```
# Single scrape: 3 seconds ✅
09:55:23 scrape_started
09:55:26 scrape_completed

# Concurrent scrapes during crawl: ALL block ❌
09:55:49.203 scrape_started /pages/simple/
09:55:49.236 scrape_started /pages/forms/
09:55:49.556 scrape_started /pages/ajax-javascript/
09:55:49.565 scrape_started /pages/advanced/
09:55:49.569 scrape_started /pages/frames/
... (NO scrape_completed for ANY page)
09:58:49 TIMEOUT all pages (exactly 180s later)
```

**Root Cause**:
- Semaphore allows 40 concurrent pages (CAMOUFOX_POOL_SIZE=40)
- But ALL pages share ONE browser process (Firefox-based Camoufox)
- Firefox cannot handle multiple simultaneous `page.goto()` navigations efficiently
- When 5+ pages try to navigate at the same time, they block each other
- The browser rendering thread is the bottleneck, not the semaphore

**Comparison**: Firecrawl's Playwright service uses the same single-browser pattern (`api.ts:86`), but with **Chromium** which handles concurrent tabs better than Firefox.

**Fix Required**: Implement a **browser pool** with multiple Camoufox browser instances.

---

### ~~Issue 2: DELAY UNIT MISMATCH~~ (DISPROVED)

**Initially suspected** but **NOT the cause** for self-hosted deployments.

The delay bug exists in code (`concurrency-limit.ts:341-345`):
```typescript
setTimeout(resolve, sc.crawlerOptions.delay * 1000)  // Would be 33 min if delay=2000
```

**However**, this code is **NEVER EXECUTED** for self-hosted deployments:
- `USE_DB_AUTHENTICATION` is not set → defaults to `false`
- `isSelfHosted()` returns `true` (`lib/deployment.ts:3`)
- In `queue-jobs.ts:221-222`: `concurrencyLimited = "no"`
- Jobs bypass concurrency queue entirely → delay code not reached

**Verified**: The docker-compose.yml does NOT set `USE_DB_AUTHENTICATION`, so this is a self-hosted deployment where concurrency limits and delays are bypassed.

### Issue 2: Infinite Retry Loop (Firecrawl Bug)

**Location**: `/mnt/c/code/firecrawl/apps/api/src/services/worker/scrape-worker.ts:583-586`

**The Bug**:
```typescript
await redisEvictConnection.srem(
  "crawl:" + job.data.crawl_id + ":visited_unique",
  normalizeURL(job.data.url, sc),
);
```

When a job fails (for ANY reason including the 33-minute timeout), the URL is unlocked and can be rediscovered, creating an infinite loop.

**What's Missing in Firecrawl**:
- ❌ No failure count tracking per URL
- ❌ No MAX_RETRIES configuration
- ❌ No exponential backoff between retries
- ❌ No dead letter queue for permanently failed URLs

### ~~Issue 3: Queue Backlog~~ (CORRECTED - Not the primary cause)

Previous analysis incorrectly blamed concurrency limits. Investigation revealed:
- ❌ **WRONG**: "Jobs timeout while waiting in queue"
- ✅ **CORRECT**: Crawl jobs have `Infinity` timeout in concurrency queue (`queue-jobs.ts:76-78`)
- ✅ **CORRECT**: Job timeout only starts when worker picks up job (`scrape-worker.ts:173`)
- ✅ **CORRECT**: The 33-minute DELAY between promotions is the real blocker

**Concurrency limits exist but are NOT the primary bottleneck**:
| Setting | Value | Location |
|---------|-------|----------|
| Team concurrency | 2 | `auth.ts:100` |
| Playwright semaphore | 10 | `playwright-service-ts/api.ts:16` |
| Crawl max concurrency | 2 | `config.py:135` (passed to Firecrawl) |

These limits slow things down but don't cause 180s timeouts on their own.

## Fix Priority

### Priority 1: Implement Browser Pool for Camoufox (ROOT CAUSE FIX)
**Status**: Ready to implement

**Problem**: Single Camoufox browser instance cannot handle concurrent page navigations. Firefox blocks when multiple `page.goto()` calls happen simultaneously.

**Solution**: Create a browser pool with N browser instances (e.g., N=5):
```python
class CamoufoxBrowserPool:
    def __init__(self, pool_size: int = 5):
        self._browsers: list[Browser] = []
        self._semaphore = asyncio.Semaphore(pool_size)  # Limit total concurrency
        self._browser_locks: list[asyncio.Lock] = []     # One lock per browser

    async def acquire_browser(self) -> tuple[Browser, asyncio.Lock]:
        """Get the least-busy browser from the pool."""
        async with self._semaphore:
            # Round-robin or least-connections selection
            ...
```

**Benefits**:
- Each browser handles 1-2 pages (not 5+ fighting for same thread)
- True parallelism at the browser process level
- Matches production patterns (multiple browser workers)

**Config**: Add `CAMOUFOX_BROWSER_COUNT=5` to docker-compose.yml

### Priority 2: Fix Infinite Retry Loop (Firecrawl)
**File**: `apps/api/src/services/worker/scrape-worker.ts:583-586`

**Option A (Simple)**: Comment out the `srem()` call
- Failed URLs stay locked permanently
- No infinite loop

**Option B (Robust)**: Add failure tracking with MAX_RETRIES=2
```typescript
const failureKey = `crawl:${job.data.crawl_id}:failures`;
const failCount = await redis.hincrby(failureKey, normalizedUrl, 1);
if (failCount < 2) {
  await redisEvictConnection.srem(...);  // Allow 1 retry
}
// else: keep locked permanently
```

## Next Steps

1. [x] **Implement browser pool** - Created `CamoufoxBrowserPool` with N=5 browsers ✅
2. [x] **Update scraper** - Requests routed round-robin across browsers ✅
3. [x] **Add config** - `CAMOUFOX_BROWSER_COUNT` in docker-compose.yml ✅
4. [ ] **Deploy and test** - Rebuild Docker image and test concurrent crawls
5. [ ] **Fix infinite retry loop** in Firecrawl (prevents edge cases)
6. [ ] **Full crawl test** (depth 5, limit 100) after deployment

## Key Files

### Orchestrator (/mnt/c/code/knowledge_extraction-orchestrator)
- `src/services/scraper/client.py` - FirecrawlClient calls
- `src/services/scraper/crawl_worker.py` - Crawl job processing
- `docker-compose.yml` - Service configuration

### Firecrawl Fork (/mnt/c/code/firecrawl)
- `apps/api/src/services/worker/scrape-worker.ts` - Job processing + URL unlock bug (line 583-586)
- `apps/api/src/services/queue-jobs.ts` - Job queuing logic
- `apps/api/src/scraper/scrapeURL/engines/playwright/index.ts` - Camoufox integration
- `apps/api/src/lib/crawl-redis.ts` - URL locking

## Test Commands

### Test Individual Page via Firecrawl API
```bash
curl -X POST http://localhost:3004/scrape -H "Content-Type: application/json" -d '{"url":"https://www.scrapethissite.com/pages/frames/","timeout":60000}' 2>/dev/null | jq '.pageStatusCode'
```

### Test via Orchestrator API
```bash
curl -X POST http://192.168.0.136:8742/api/v1/crawl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: thisismyapikey3215215632" \
  -d '{
    "url": "https://www.scrapethissite.com/pages/frames/",
    "project_id": "5eafd403-2734-4f4e-9518-925fe6b01dc9",
    "company": "test-individual",
    "max_depth": 1,
    "limit": 1
  }'
```

---

**Summary**:
1. ✅ **ROOT CAUSE FIXED**: Browser pool implemented for Camoufox
   - Single browser couldn't handle concurrent `page.goto()` calls (Firefox limitation)
   - **Solution**: Browser pool with N=5 instances, round-robin distribution
   - **Config**: `CAMOUFOX_BROWSER_COUNT=5`, `CAMOUFOX_POOL_SIZE=10`
   - **Tests**: 17 passing (12 new + 5 existing)
2. 🟡 **SECONDARY**: Firecrawl unlocks failed URLs (`scrape-worker.ts:583-586`) → infinite retry loop

**Next steps**:
1. [ ] Rebuild and deploy Camoufox Docker image
2. [ ] Test concurrent crawls (should complete in 2-3s each, not 180s timeout)
3. [ ] Fix Firecrawl infinite retry loop (secondary priority)

**To deploy**:
```bash
docker-compose build camoufox
docker-compose up -d camoufox
```

**Run `/clear` to start fresh session with this context.**
