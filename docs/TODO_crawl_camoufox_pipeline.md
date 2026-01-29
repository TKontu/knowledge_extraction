# TODO: Crawl/Camoufox Pipeline Issues

Review date: 2026-01-29
**Verified**: 2026-01-29 (code inspection completed)
**Fixed**: 2026-01-29

## Summary

Pipeline review identified issues in the crawl endpoint. After code inspection verification:
- **1 REAL issue** (pagination data loss) - ✅ FIXED
- **2 MINOR issues** (AJAX limit, resource leak) - ✅ FIXED
- **6 FALSE POSITIVES** (code is correct as-is)

Reference: `docs/endpoint_crawl_review.md`

---

## Fixed Issues

### 1. Firecrawl Pagination Never Fetched - DATA LOSS
- **File:** `src/services/scraper/client.py`
- **Status:** ✅ FIXED
- **Fix:** `get_crawl_status()` now follows pagination cursor to fetch all pages
  - Loops while `next_url` exists and status is "completed"
  - Max 100 page iterations to prevent infinite loops
  - Proper error handling and logging for pagination fetches

### 2. Camoufox Context Manager Not Properly Cleaned Up
- **File:** `src/services/camoufox/scraper.py`
- **Status:** ✅ FIXED
- **Fix:** `stop()` now calls `__aexit__()` on each AsyncCamoufox instance
  - Properly releases Playwright resources
  - Error handling for cleanup failures

### 3. AJAX Discovery Hard-Limited to 20 Clicks
- **File:** `src/services/camoufox/config.py`, `scraper.py`
- **Status:** ✅ FIXED
- **Fix:** Added `ajax_discovery_max_clicks` config option (default: 20)
  - Configurable via `CAMOUFOX_AJAX_DISCOVERY_MAX_CLICKS` env var

### 4. User-Agent Logic Simplified
- **File:** `src/services/scraper/client.py`
- **Status:** ✅ FIXED
- **Previous:** Only set User-Agent when respecting robots.txt (confusing)
- **Fix:** Always set User-Agent for transparency
  - Identifies crawler regardless of robots.txt handling

### 5. Language Detection Failure Not Tracked
- **File:** `src/services/scraper/crawl_worker.py`
- **Status:** ✅ FIXED
- **Previous:** On timeout/error, page stored with no indication
- **Fix:** Added metadata flags for downstream processing:
  - `language_detection_failed: true`
  - `language_detection_error: "timeout"` or error message

---

## False Positives (Code is Correct)

### ~~AsyncCamoufox API Misuse~~
- **Status:** ❌ FALSE POSITIVE
- **Reality:** `PlaywrightContextManager` has a `.start()` method (verified in `camoufox-ref/tests/async/test_asyncio.py:56`)
- **Evidence:** `playwright = await async_playwright().start()` is valid pattern
- `AsyncCamoufox` inherits from `PlaywrightContextManager`, so `.start()` works

### ~~Response Null Check Missing~~
- **Status:** ❌ FALSE POSITIVE
- **Reality:** Code is safe. When `response` is None:
  1. `content_type` stays None (initialized at line 643)
  2. `if content_type and (...)` at line 652 evaluates to False
  3. `response.body()` is never reached
- **Code flow verified:** Lines 643-656 are correctly guarded

### ~~User-Agent Header Logic Inverted~~
- **Status:** ❌ FALSE POSITIVE
- **Reality:** Logic is intentional:
  - When respecting robots.txt (`ignore_robots_txt=False`), identify yourself with User-Agent
  - When ignoring robots.txt (e.g., llms.txt allows AI), don't override default user-agent
- This is sensible behavior for ethical crawling

### ~~Timeout Mismatch~~
- **Status:** ❌ FALSE POSITIVE
- **Reality:** The 5-second networkidle timeout is intentional:
  - Config comment: "reduced for faster scraping"
  - Code has fallback: `_wait_for_content_stability()` called after networkidle timeout
  - This is a performance optimization, not a bug
- **Evidence:** Lines 428-437 in scraper.py show the fallback pattern

### ~~Protected Headers Conflict~~
- **Status:** ❌ FALSE POSITIVE
- **Reality:**
  - Protected headers (user-agent, accept-language, accept-encoding) are correctly filtered
  - STANDARD_BROWSER_HEADERS (Accept, DNT, etc.) are standard browser headers
  - These supplement Camoufox, they don't conflict
- **Evidence:** Lines 594-605 show proper filtering logic

### ~~Language Detection Timeout Non-Blocking~~
- **Status:** ❌ FALSE POSITIVE (By Design)
- **Reality:** Intentional behavior documented in code comments:
  - "Continue storing page (timeout shouldn't block crawl)"
  - This prevents language detection from breaking the entire crawl
- **Evidence:** Lines 295-311 show deliberate fallback

---

## Verification Steps

After fixing confirmed issues:

1. [ ] Crawls with >100 pages return complete data (pagination fix)
2. [ ] No process/memory leaks after repeated browser pool restarts (cleanup fix)
3. [ ] Complex SPAs with >20 interactive elements fully discovered (optional: AJAX config)

---

## Related Files

| Component | File | Purpose |
|-----------|------|---------|
| Firecrawl Client | `src/services/scraper/client.py` | HTTP client - **pagination fix needed** |
| Camoufox Scraper | `src/services/camoufox/scraper.py` | Browser automation - **cleanup fix needed** |
| Crawl Worker | `src/services/scraper/crawl_worker.py` | Job execution |
| Config | `src/services/camoufox/config.py` | Settings |
