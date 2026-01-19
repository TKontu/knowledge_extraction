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
  - Pages timing out: `/pages/frames/`, `/pages/ajax-javascript/`, `/pages/advanced/?gotcha=*`, `/pages/forms/?page_num=*`
- ✅ **Implemented HTTP error handling fixes (TDD approach)**
  - **Fix 1**: Added standard browser headers to Camoufox (Sec-Fetch-*, Accept, etc.)
  - **Fix 2**: Filter HTTP errors (status >= 400) before storing sources
  - **Fix 3**: Track HTTP status in source metadata for observability
  - **Tests**: 15 new tests (5 for headers + 10 for filtering), all passing
  - Files modified: `src/services/camoufox/scraper.py`, `src/services/scraper/crawl_worker.py`
- ✅ **Fixed header conflicts with Camoufox internal handling**
  - **Root Cause**: Our STANDARD_BROWSER_HEADERS included Accept-Language and Accept-Encoding
    which Camoufox handles internally via BrowserForge fingerprints
  - **The Conflict**: Using `page.set_extra_http_headers()` with these headers interfered with
    Camoufox's C++-level header injection, potentially causing User-Agent to not be set
  - **Fix**: Removed Accept-Language and Accept-Encoding from STANDARD_BROWSER_HEADERS
  - **Architectural Decision**: Let Camoufox handle User-Agent, Accept-Language, Accept-Encoding
    internally; only use `set_extra_http_headers()` for headers Camoufox doesn't handle
  - See: https://camoufox.com - HTTP Headers section

## In Progress
- ⚠️ **Infinite retry loop still occurring on production crawls**
- 🔍 **Analysis complete** - awaiting decision on fix approach
- ✅ **HTTP error handling fixes implemented via TDD** (ready to deploy)

## Next Steps
- [ ] **Choose fix approach** and implement in Firecrawl fork:

  **Option 1 (Simple)**: Don't unlock failed URLs
  - File: `/mnt/c/code/firecrawl/apps/api/src/services/worker/scrape-worker.ts`
  - Remove/comment lines 583-586 that unlock failed URLs
  - Pros: One-line fix, immediate resolution
  - Cons: Failed URLs never retry, even for transient failures

  **Option 2 (Robust - Recommended)**: Add failure count tracking
  - Add Redis hash `crawl:{id}:failures` to track per-URL failure counts
  - Allow 2 retry attempts (1 initial + 1 retry), then permanent lock
  - Example implementation:
    ```typescript
    const failureCount = await redis.hincrby(`crawl:${crawl_id}:failures`, normalizedUrl, 1);
    if (failureCount < MAX_RETRIES) {
      // Unlock for retry
      await redis.srem(`crawl:${crawl_id}:visited_unique`, normalizedUrl);
    }
    // else: keep locked permanently
    ```
  - Pros: Handles transient failures, prevents infinite loops
  - Cons: More complex, requires Firecrawl fork modification

- [ ] **Increase scrape timeout** from 180s to 300-600s
  - File: `src/config.py:125-128` → `scrape_timeout` field
  - These pages (iframes, ajax, anti-scraping challenges) need more time

- [ ] **Test fix with problematic pages individually**
  - Create project, test each page separately with new timeout
  - Pages: `/pages/frames/`, `/pages/ajax-javascript/`, `/pages/advanced/?gotcha=headers`

- [ ] **Full crawl test** with depth 5, limit 100
  - Verify no more infinite loops
  - Check deployment logs for repeated URLs

## Key Files

### Orchestrator (knowledge_extraction-orchestrator)
- `src/config.py:125-128` - `scrape_timeout: 180` setting (increase to 300-600)
- `src/main.py:89-116` - Qdrant initialization with retry logic (recently fixed)
- `src/services/scraper/client.py:263-346` - `FirecrawlClient.start_crawl()` passes timeout to Firecrawl

### Firecrawl Fork (/mnt/c/code/firecrawl)
- `apps/api/src/services/worker/scrape-worker.ts:161-625` - Job processing logic
  - **Line 173**: `start = job.data.startTime ?? Date.now()` (timeout starts at worker pickup - correct!)
  - **Lines 583-586**: 🔴 **THE BUG** - unlocks failed URLs:
    ```typescript
    await redisEvictConnection.srem(
      "crawl:" + job.data.crawl_id + ":visited_unique",
      normalizeURL(job.data.url, sc),
    );
    ```
- `apps/api/src/lib/crawl-redis.ts:405-514` - URL locking mechanisms (`lockURL`, `lockURLs`)
- `apps/api/src/lib/retry-utils.ts` - Generic retry utilities (4 attempts, 500ms/1.5s/3s delays)

## Context

### Problem Summary
**Infinite loop mechanism:**
1. Page A completes → discovers link to Page B
2. Lock Page B → create scrape job
3. Page B times out after 180s
4. Mark scrape job failed + **UNLOCK Page B** ← THE PROBLEM
5. Page C completes → discovers link to Page B
6. Check: is Page B locked? NO (unlocked in step 4)
7. Lock Page B → create NEW scrape job for Page B
8. → Infinite loop back to step 3

### Why These Pages Timeout - ⚠️ CORRECTED ANALYSIS

**🔴 PREVIOUS ANALYSIS WAS WRONG** - Pages are NOT slow to load!

**Test Results:**

1. **ajax-javascript page** (2026-01-19 18:43:46):
   - Individual test: ✅ 4.4 seconds, 200 OK, 13976 bytes
   - Bulk crawl: ⏱️ 180s timeout

2. **gotcha=headers page** (2026-01-19 18:46:52):
   - Individual test: ❌ 400 Bad Request (engine waterfall: 3 attempts in ~4 seconds)
   - Root cause: Missing standard browser headers
   - ✅ **FIXED**: Added STANDARD_BROWSER_HEADERS to Camoufox

**The pages load FAST when tested individually!** This means the timeout issue is **NOT** due to:
- ❌ Pages being intentionally difficult
- ❌ Heavy JavaScript/AJAX
- ❌ Anti-scraping challenges
- ❌ Page complexity

**Real cause must be something else:**
- 🔍 Concurrent crawling resource exhaustion?
- 🔍 Browser instance limits during bulk crawls?
- 🔍 Network connection pool exhaustion?
- 🔍 Playwright context/session issues under load?
- 🔍 Memory pressure with multiple pages?

**Pages that timed out at 180s during bulk crawl:**
- `/pages/frames/` - ⏳ Need to test individually
- `/pages/ajax-javascript/` - ✅ **4.4s individually** vs ⏱️ 180s timeout in bulk
- `/pages/advanced/?gotcha=headers` - ⏳ Need to test individually
- `/pages/advanced/?gotcha=login` - ⏳ Need to test individually
- `/pages/advanced/?gotcha=csrf` - ⏳ Need to test individually
- `/pages/forms/?page_num=*` - ⏳ Need to test individually

### Important Technical Findings
- ✅ **Timeout starts when worker picks up job**, NOT when queued
  - Verified in `scrape-worker.ts:173`: `const start = job.data.startTime ?? Date.now()`
  - For crawl-discovered pages, `startTime` is undefined → defaults to worker pickup time
- ✅ **Timeout calculation is per-job, not global**
  - Each scrape job gets full 180s when processing starts
- ⚠️ **Pages timeout at 180s during BULK crawls but complete in <5s individually**
  - This points to **resource exhaustion** or **concurrency issues**, not slow pages
  - Need to test all problematic pages individually to confirm pattern

### Architectural Decision Needed
**User question**: "How do we handle this in a robust and architecturally good manner?"

**Recommendation**:
1. **Short-term** (immediate): Option 1 (don't unlock) + increase timeout to 300s
2. **Long-term** (proper fix): Option 2 (failure tracking with MAX_RETRIES=2)

This provides:
- Immediate relief from infinite loops
- Handles transient failures (network issues, temporary site slowdowns)
- Prevents permanent resource waste on truly problematic pages
- Clean, observable failure tracking via Redis

### Deployment Status
- ✅ Main branch has working Qdrant fixes (PR #42 merged)
- ✅ Production stack at `192.168.0.136:8742` running latest code
- ⚠️ Crawl job with retry loop was stopped (user compose down'd it)
- ✅ Fresh databases deployed (no crawl history)
- 📍 Firecrawl fork available at `/mnt/c/code/firecrawl` for modifications

### Production Test Results
**Successful single-page crawl** (before full crawl):
- URL: `https://www.scrapethissite.com/pages/ajax-javascript/`
- Result: ✅ 1 page scraped, 5 facts extracted
- Qdrant storage: ✅ All facts stored successfully (no 404 errors)
- Search: ✅ Semantic search working (relevant results, good scores)

**Failed multi-page crawl** (infinite loop):
- URL: `https://www.scrapethissite.com/pages/` (depth 5, limit 100)
- Result: ⚠️ 33 pages completed, then infinite retries on 5-6 problematic pages
- Logs: Same URLs repeating indefinitely with 180s timeouts

---

## Quick Reference Commands

### Test Individual Problem Page
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

### Check Camoufox Logs for Timeouts
```bash
grep "Timeout.*exceeded" deployment_logs/_scristill-stack-camoufox-1_logs.txt | wc -l
```

### Verify Qdrant Collection
```bash
curl http://192.168.0.136:6333/collections/extractions | jq '.result.points_count'
```

---

**Next session should**:
1. Decide on Option 1 vs Option 2
2. Implement fix in Firecrawl fork
3. Increase timeout in orchestrator config
4. Test problematic pages individually
5. Verify no more infinite loops

**Run `/clear` to start fresh session with this context.**
