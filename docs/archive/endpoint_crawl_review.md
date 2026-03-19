# Pipeline Review: /crawl with Camoufox

**Date**: 2025-01-29
**Reviewer**: Orchestrator (Opus)
**Scope**: Full crawl request lifecycle with Camoufox integration

## Flow

```
POST /api/v1/crawl (crawl.py)
    → CrawlRequest validation
    → Job record created (status=queued)

JobScheduler.start() (scheduler.py)
    → Polls for queued/stale jobs
    → SELECT FOR UPDATE SKIP LOCKED
    → Spawns CrawlWorker instances

CrawlWorker.process_job() (crawl_worker.py)
    → FirecrawlClient.start_crawl()
    → Poll get_crawl_status() until complete
    → _store_pages() with language filtering
    → Auto-create extraction job if enabled

FirecrawlClient (client.py)
    → HTTP requests to Firecrawl API
    → OR proxied to Camoufox service

CamoufoxScraper (src/services/camoufox/scraper.py)
    → Browser pool management
    → Per-request context pattern
    → AJAX discovery, iframe inlining
```

---

## Critical (Must Fix)

### 1. AsyncCamoufox API Misuse - Will Crash on Start
- [ ] `src/services/camoufox/scraper.py:197-198`

```python
camoufox = AsyncCamoufox(geoip=True, **launch_options)
browser = await camoufox.start()  # ❌ WRONG
```

**Problem**: `AsyncCamoufox` is a context manager, not a class with `.start()`. Correct usage:
```python
async with AsyncCamoufox(geoip=True, **launch_options) as browser:
    # Use browser
```

**Impact**: `AttributeError` when browser pool starts. Entire Camoufox integration broken.

**Evidence**: Reference at `/projects/camoufox-ref/pythonlib/camoufox/async_api.py` shows `AsyncCamoufox` extends `PlaywrightContextManager`.

---

### 2. Response Null Check Missing in JSON Handler
- [ ] `src/services/camoufox/scraper.py:642-656`

```python
if response:
    headers = await response.all_headers()
    content_type = next(...)

if content_type and ("application/json" in content_type.lower()):
    body = await response.body()  # ❌ response could be None here
```

**Problem**: `content_type` set inside `if response:` block, but `response.body()` called outside after checking only `content_type`.

**Impact**: `AttributeError: 'NoneType' has no attribute 'body'` on navigation errors.

---

### 3. Firecrawl Pagination Never Fetched - Silent Data Loss
- [ ] `src/services/scraper/client.py:412-450`

```python
next_url = data.get("next")  # Firecrawl pagination cursor
if next_url:
    logger.info("firecrawl_pagination_detected", ...)
# ❌ BUT: Never fetches the next page!
return CrawlStatus(..., pages=pages)  # Only first batch
```

**Problem**: Firecrawl returns `next` cursor when >50-100 pages exist. Client logs detection but never fetches remaining pages.

**Impact**:
- Crawls silently lose data beyond first batch
- Page count mismatch logged but continues with incomplete data
- Likely root cause of "crawl_completed_zero_sources" issues

---

## Important (Should Fix)

### 4. User-Agent Header Logic Inverted
- [ ] `src/services/scraper/client.py:324-328`

```python
if user_agent or not ignore_robots_txt:  # ❌ Logic backwards
    scrape_options["headers"] = {"User-Agent": user_agent or DEFAULT_USER_AGENT}
```

**Problem**: Sets User-Agent when `ignore_robots_txt=False`. Should be opposite - set when ignoring robots.txt.

**Correct**:
```python
if ignore_robots_txt or user_agent:
```

---

### 5. Camoufox/Firecrawl Timeout Mismatch
- [ ] `src/services/scraper/crawl_worker.py:71` vs `src/services/camoufox/config.py:45`

| Setting | Value | Location |
|---------|-------|----------|
| Firecrawl timeout | 180s (180000ms) | crawl_worker.py:71 |
| Camoufox networkidle | 5s (5000ms) | config.py:45 |

**Problem**: Camoufox gives up on networkidle after 5s, but Firecrawl wrapper allows 3 minutes. Pages taking >5s for network idle will fail prematurely.

---

### 6. Protected Headers Filter Incomplete
- [ ] `src/services/camoufox/scraper.py:592-605`

```python
protected_headers = {"user-agent", "accept-language", "accept-encoding"}
# STANDARD_BROWSER_HEADERS (line 91-101) includes Accept, DNT, Connection
```

**Problem**: `STANDARD_BROWSER_HEADERS` are applied to every page and may conflict with Camoufox's C++-level header injection, breaking fingerprint consistency.

---

### 7. Language Detection Timeout Non-Blocking
- [ ] `src/services/scraper/crawl_worker.py:273-302`

**Problem**: `asyncio.wait_for()` wraps detection, but on timeout the page is stored anyway without language confirmation. Low severity due to fallback behavior.

---

## Minor

### 8. AJAX Discovery Hard-Limited to 20 Clicks
- [ ] `src/services/camoufox/scraper.py:335`

```python
for i, element in enumerate(elements[:20]):
```

May miss AJAX content on complex SPAs.

---

### 9. Log Truncation Hides Debug Info
- [ ] `src/services/scraper/client.py:437`

```python
next_url=next_url[:100] if next_url else None
```

Pagination cursor truncation may complicate debugging.

---

## Summary

| Severity | Count | Key Issues |
|----------|-------|------------|
| 🔴 Critical | 3 | AsyncCamoufox API, Response null, Pagination loss |
| 🟠 Important | 4 | User-Agent logic, Timeout mismatch, Headers, Language timeout |
| 🟡 Minor | 2 | AJAX limit, Log truncation |

---

## Investigation Recommendations

1. **Test Camoufox browser pool startup** - Verify if `.start()` actually works or crashes
2. **Audit Firecrawl pagination in production logs** - Check how often `next` cursor appears
3. **Load test with networkidle >5s pages** - Verify timeout behavior
4. **Review Camoufox fingerprint with applied headers** - Ensure no conflicts
