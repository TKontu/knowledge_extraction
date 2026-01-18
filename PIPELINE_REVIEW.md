# Pipeline Review: /crawl Endpoint (End-to-End)

## Executive Summary

**ROOT CAUSE IDENTIFIED**: 60-second timeouts are caused by requests waiting indefinitely in the Camoufox browser page semaphore queue, NOT by slow page loads or NO_PROXY misconfiguration.

## Critical Findings

### 🔴 CRITICAL: Semaphore Blocking Without Timeout

**File**: `src/services/camoufox/scraper.py:479-481`

```python
async with self._semaphore:  # ⚠️ BLOCKS INDEFINITELY
    async with self._acquire_page():
        return await self._do_scrape(request)
```

**Problem**:
- The semaphore acquisition has NO timeout
- Requests wait indefinitely for a free browser page slot
- When pool is exhausted (40 pages), new requests queue and wait
- Timeout only starts AFTER acquiring semaphore, not during the wait
- With scheduler bug creating 8+ duplicate jobs, the 40-page pool was quickly exhausted
- Subsequent requests waited ~60s in queue before timeout

**Impact**:
- Requests appear to "timeout" but they're actually waiting in queue
- The configured `scrape_timeout` (180s) is irrelevant if spent waiting in queue
- This matches the observed behavior: consistent 60s delays regardless of page complexity

**Fix Required**: Add timeout to semaphore acquisition or implement request-level timeout that includes queue wait time

---

### 🔴 CRITICAL: Hardcoded 60s Timeouts in Firecrawl Queue Jobs

**File**: `/mnt/c/code/firecrawl/apps/api/src/services/queue-jobs.ts`

Multiple hardcoded 60-second timeouts that don't respect configured timeout:

- **Line 138, 147, 179, 188**: `60 * 1000` hardcoded for concurrency limit active jobs
- **Line 62, 77, 102, 119**: Uses `scrapeOptions?.timeout ?? 60 * 1000` as fallback

**Problem**:
- When `scrapeOptions.timeout` is undefined (shouldn't happen but possible), falls back to 60s
- Concurrency tracking jobs always expire after 60s regardless of config
- May cause premature job expiration from Redis tracking

**Fix Required**: Use configured timeout consistently, no hardcoded fallbacks

---

### 🟡 WARNING: Scheduler Bug Amplification Effect

**File**: `src/services/scraper/scheduler.py` (FIXED in last session)

The scheduler bug (8+ duplicate jobs) amplified the semaphore queueing issue:

1. Bug created 8+ concurrent jobs for same crawl
2. Each job tried to acquire browser page slots
3. 40-page pool exhausted quickly
4. New legitimate requests queued and timed out
5. Symptom: "INF INF INF..." log duplication confirmed this

**Status**: Fixed, but the semaphore issue remains

---

## Complete Pipeline Flow

### 1. Orchestrator → Firecrawl Client

**Entry Point**: `src/api/v1/crawl.py:23-76`
```python
@router.post("/crawl", status_code=status.HTTP_202_ACCEPTED)
async def create_crawl_job(request: CrawlRequest, db: Session = Depends(get_db))
```

**Flow**:
1. Creates `Job` record in DB with `status="queued"`
2. Stores crawl config in `job.payload`
3. Returns immediately (202 Accepted)

**Timeout Config**: `settings.scrape_timeout` (180s) stored in payload

---

**Scheduler Pickup**: `src/services/scraper/scheduler.py:159-209`
```python
async def _run_single_crawl_worker(self, worker_id: int)
```

**Flow**:
1. 6 parallel workers poll for `Job.type="crawl"` and `status in ["queued", "running"]`
2. Each worker processes one job to completion
3. Calls `CrawlWorker.process_job(job)`

---

**Crawl Worker**: `src/services/scraper/crawl_worker.py:31-142`

**Flow**:
1. **Start Crawl** (lines 40-69): Calls Firecrawl API `/v1/crawl`
   - Converts timeout: `scrape_timeout_ms = settings.scrape_timeout * 1000` (line 43)
   - Passes to `client.start_crawl(..., scrape_timeout=scrape_timeout_ms)` (line 52)

2. **Poll Status** (lines 71-89): Polls `/v1/crawl/{id}` every 5s

3. **Store Results** (lines 99-116): Saves pages as Source records

**Timeout Propagation**: ✅ Correctly passed as milliseconds

---

**Firecrawl Client**: `src/services/scraper/client.py:263-363`

```python
async def start_crawl(
    url: str,
    scrape_timeout: int = 60000,  # milliseconds
    ...
) -> str:
```

**Flow** (lines 318-350):
1. Builds `scrape_options = {"timeout": scrape_timeout}` (line 321)
2. POSTs to `{base_url}/v1/crawl` with crawl request
3. Returns Firecrawl job ID

**Timeout Propagation**: ✅ Sent as `scrapeOptions.timeout` in request body

---

### 2. Firecrawl API → BullMQ Queue

**Crawl Controller**: `/mnt/c/code/firecrawl/apps/api/src/controllers/v1/crawl.ts:24-190`

**Flow** (lines 83-87):
```typescript
const { scrapeOptions, internalOptions } = fromV1ScrapeOptions(
  bodyScrapeOptions,
  bodyScrapeOptions.timeout,  // ✅ Passed through
  req.auth.team_id,
);
```

**Flow** (lines 118-160):
1. Creates `StoredCrawl` with `scrapeOptions` (includes timeout)
2. Saves to Redis
3. Adds job to BullMQ via `_addScrapeJobToBullMQ()` (line 164)

**Timeout Propagation**: ✅ Part of scrapeOptions in stored crawl

---

**Queue Job Addition**: `/mnt/c/code/firecrawl/apps/api/src/services/queue-jobs.ts:124-160`

**Flow**:
1. Adds job to NUQ (BullMQ) scrape queue
2. ⚠️ **Line 138**: Hardcoded 60s for concurrency tracking
3. Job includes full `scrapeOptions` with timeout

**Issues**:
- Hardcoded 60s for Redis TTL on concurrency tracking (line 138, 147)
- Falls back to 60s if timeout undefined (lines 62, 77, 102, 119)

---

### 3. Scrape Worker → Engine Selection

**Scrape Worker**: `/mnt/c/code/firecrawl/apps/api/src/services/worker/scrape-worker.ts:163-315`

**Flow** (lines 174-176):
```typescript
const remainingTime = job.data.scrapeOptions.timeout
  ? job.data.scrapeOptions.timeout - (Date.now() - start)
  : undefined;
```

**Flow** (lines 181-190):
1. Creates AbortController with `remainingTime` timeout
2. Sets up Promise.race with timeout (lines 208-224)

**Flow** (lines 209-212):
```typescript
pipeline = await Promise.race([
  startWebScraperPipeline({ job, costTracking }),
  ...(remainingTime ? [timeoutPromise] : []),
]);
```

**Timeout Propagation**: ✅ Used for race condition and abort controller

---

**Web Scraper Pipeline**: `/mnt/c/code/firecrawl/apps/api/src/main/runWebScraper.ts:8-40`

**Flow**:
1. Passes `scrapeOptions` (with timeout) to `scrapeURL()`
2. Retries up to 3 times for crawls

---

**scrapeURL**: `/mnt/c/code/firecrawl/apps/api/src/scraper/scrapeURL/index.ts:277-287`

**Flow** (lines 279-287):
```typescript
abort: new AbortManager(
  internalOptions.externalAbort,
  options.timeout !== undefined
    ? {
        signal: abortController.signal,
        tier: "scrape",
        timesOutAt: new Date(Date.now() + options.timeout),  // ✅
        throwable() { return new ScrapeJobTimeoutError("Scrape timed out"); },
      }
    : undefined
)
```

**Flow**:
1. Creates `AbortManager` with timeout from options
2. Delegates to engine (Playwright for Camoufox)

**Timeout Propagation**: ✅ Converted to `timesOutAt` Date

---

### 4. Playwright Engine → Camoufox Service

**Playwright Engine**: `/mnt/c/code/firecrawl/apps/api/src/scraper/scrapeURL/engines/playwright/index.ts:8-51`

**Flow** (lines 11-23):
```typescript
const response = await robustFetch({
  url: config.PLAYWRIGHT_MICROSERVICE_URL!,  // Camoufox service URL
  body: {
    url: meta.rewrittenUrl ?? meta.url,
    timeout: meta.abort.scrapeTimeout(),  // ✅ Calculated from remaining time
    wait_after_load: meta.options.waitFor,
    headers: meta.options.headers,
    ...
  },
  method: "POST",
});
```

**AbortManager.scrapeTimeout()**: `/mnt/c/code/firecrawl/apps/api/src/scraper/scrapeURL/lib/abortManager.ts:103-112`

```typescript
scrapeTimeout(): number | undefined {
  const timeouts = this.aborts
    .filter(x => x.tier === "scrape")
    .map(x => x.timesOutAt)
    .filter(x => x !== undefined);
  if (timeouts.length === 0) return undefined;
  return Math.min(...timeouts.map(x => x.getTime())) - Date.now();  // Remaining ms
}
```

**Timeout Propagation**: ✅ Sent as `timeout` in POST body to Camoufox

---

### 5. Camoufox Service → Browser Execution

**Camoufox Server**: `src/services/camoufox/server.py:152-211`

```python
@app.post("/scrape")
async def scrape_url(request: ScrapeRequest) -> JSONResponse:
    result = await scraper.scrape(request)  # request.timeout = 180000
```

**Request Model**: `src/services/camoufox/models.py:21-24`

```python
timeout: int = Field(
    default=180000,  # 3 minutes
    description="Page load timeout in milliseconds",
)
```

**Timeout Propagation**: ✅ Received from Firecrawl

---

**Camoufox Scraper**: `src/services/camoufox/scraper.py:463-481`

```python
async def scrape(self, request: ScrapeRequest) -> dict[str, Any]:
    if self._browser is None:
        return {"error": "Browser not started"}

    async with self._semaphore:  # 🔴 BLOCKS INDEFINITELY HERE
        async with self._acquire_page():
            return await self._do_scrape(request)
```

**Semaphore Init**: `src/services/camoufox/scraper.py:114`

```python
self._semaphore = asyncio.Semaphore(self.config.max_concurrent_pages)  # Default: 10, Configured: 40
```

**🔴 CRITICAL ISSUE**:
- Line 479: `async with self._semaphore` has **NO timeout**
- If semaphore is full (40 pages active), request waits indefinitely
- Timeout only applies AFTER acquiring semaphore
- Queue wait time is NOT counted against timeout

---

**Actual Scraping**: `src/services/camoufox/scraper.py:483-570`

```python
async def _do_scrape(self, request: ScrapeRequest) -> dict[str, Any]:
    # ... setup ...

    # Navigate to URL
    response: Response | None = await page.goto(
        request.url,
        timeout=request.timeout,  # ✅ Used here (but only if we got this far)
        wait_until="load",
    )

    # Wait for selector if needed
    if request.check_selector:
        await page.wait_for_selector(
            request.check_selector,
            timeout=min(request.timeout, 10000),  # ✅
        )
```

**Timeout Propagation**: ✅ Used in page.goto() and wait_for_selector()

---

## Timeout Configuration Summary

| Layer | File | Value | Status |
|-------|------|-------|--------|
| Orchestrator Config | `src/config.py:125-128` | `scrape_timeout = 180` (seconds) | ✅ |
| Crawl Worker | `crawl_worker.py:43` | `scrape_timeout * 1000` = 180000ms | ✅ |
| Firecrawl Client | `client.py:321` | `"timeout": scrape_timeout` | ✅ |
| Firecrawl Queue | `queue-jobs.ts:62,77,102,119` | `?? 60 * 1000` | ⚠️ Hardcoded fallback |
| Firecrawl Queue Tracking | `queue-jobs.ts:138,147,179,188` | `60 * 1000` | 🔴 Always 60s |
| Scrape Worker | `scrape-worker.ts:174-176` | Calculated remaining time | ✅ |
| AbortManager | `abortManager.ts:103-112` | Dynamic remaining time | ✅ |
| Playwright Engine | `engines/playwright/index.ts:19` | `meta.abort.scrapeTimeout()` | ✅ |
| Camoufox Request | `models.py:21-24` | `default=180000` | ✅ |
| Camoufox Semaphore | `scraper.py:479` | **NO TIMEOUT** | 🔴 **ROOT CAUSE** |
| Playwright goto() | `scraper.py:527` | `timeout=request.timeout` | ✅ |

---

## Resource Pool Configuration

| Resource | File | Default | Configured | Notes |
|----------|------|---------|------------|-------|
| Camoufox Pages | `camoufox/config.py:29-33` | 10 | 40 | `CAMOUFOX_POOL_SIZE` |
| Crawl Workers | `scheduler.py:96` | - | 6 | `settings.max_concurrent_crawls` |
| Firecrawl Concurrency | Firecrawl config | - | Variable | Per-crawl setting |

**Impact of Scheduler Bug**:
- 8+ duplicate jobs × 40 pages each = Pool exhaustion
- Legitimate requests queued at semaphore
- Wait time ~60s before timeout

---

## Identified Bottlenecks

### 1. Semaphore Queueing (PRIMARY)
- **Location**: `src/services/camoufox/scraper.py:479`
- **Impact**: High - Causes 60s delays
- **Severity**: Critical
- **Fix**: Add timeout to semaphore or request-level timeout

### 2. Hardcoded Timeouts in Firecrawl
- **Location**: `/mnt/c/code/firecrawl/apps/api/src/services/queue-jobs.ts`
- **Impact**: Medium - May cause premature expiration
- **Severity**: Important
- **Fix**: Use configured timeout consistently

### 3. Scheduler Duplicate Jobs
- **Location**: `src/services/scraper/scheduler.py`
- **Impact**: High - Amplified semaphore issue
- **Severity**: Critical
- **Status**: ✅ FIXED in commit f44a3fc

---

## Recommendations

### Immediate Actions

1. **Add Semaphore Timeout** (Critical)
   ```python
   # src/services/camoufox/scraper.py:479
   try:
       await asyncio.wait_for(
           self._semaphore.acquire(),
           timeout=request.timeout / 1000.0  # Convert ms to seconds
       )
       try:
           async with self._acquire_page():
               return await self._do_scrape(request)
       finally:
           self._semaphore.release()
   except asyncio.TimeoutError:
       return {"error": "Browser page pool exhausted - request timed out in queue"}
   ```

2. **Fix Hardcoded Timeouts in Firecrawl** (Important)
   - Replace all `60 * 1000` with `scrapeOptions?.timeout ?? 180 * 1000`
   - Use higher default to match config (180s)

3. **Add Queue Wait Metrics** (Important)
   - Log time spent waiting in semaphore queue
   - Monitor pool exhaustion events
   - Alert when wait time > threshold

### Monitoring

1. **Add Metrics**:
   - Semaphore queue wait time
   - Active vs available page slots
   - Timeout location (queue vs page load)

2. **Add Logging**:
   ```python
   queue_start = time.time()
   async with self._semaphore:
       queue_wait = time.time() - queue_start
       log.info("semaphore_acquired", queue_wait_ms=queue_wait * 1000)
   ```

3. **Health Checks**:
   - Current queue depth
   - Pool utilization percentage
   - Average wait time

---

## Conclusion

The 60-second timeout issue is NOT caused by:
- ❌ NO_PROXY misconfiguration
- ❌ Slow page loads
- ❌ Anti-bot challenges

The issue IS caused by:
- ✅ **Indefinite queueing at Camoufox semaphore** (primary)
- ✅ **Scheduler bug exhausting page pool** (amplifier, now fixed)
- ✅ **Hardcoded 60s timeouts in Firecrawl queue** (secondary)

The timeout configuration is correctly propagated through all layers, but the semaphore acquisition happens BEFORE the timeout is enforced, allowing requests to wait indefinitely in queue.

**User's intuition was correct**: The NO_PROXY fix alone would not resolve the 60s delays. The real issue is resource pool exhaustion and unbounded queue waiting.
