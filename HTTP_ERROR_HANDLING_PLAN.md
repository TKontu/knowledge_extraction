# HTTP Error Handling - Architectural Plan

## Problem Summary

**Current broken behavior:**
1. **Camoufox fails headers test** - Gets HTTP 400 on `gotcha=headers` page even though Camoufox should spoof as real browser
2. **HTTP errors treated as success** - 400/404/500 responses marked as "completed" and stored in database
3. **Error content contamination** - Error pages stored alongside real content, polluting extraction results
4. **No failure tracking** - Jobs with HTTP errors don't get marked as failed, preventing retry logic

## Test Results

```
Page: /pages/ajax-javascript/
- Individual test: ✅ 4.4 seconds, 200 OK
- Bulk crawl: ⏱️ 180s timeout

Page: /pages/advanced/?gotcha=headers
- Individual test: ❌ 400 Bad Request (3 engine attempts)
- Completed as "success" with 1 source created
- Error page HTML stored in database
```

## Root Causes

### 1. Missing/Incorrect Headers
**Location**: Camoufox scraper (`src/services/camoufox/scraper.py:520-522`)

```python
# Headers only applied if request.headers provided
if request.headers:
    await page.set_extra_http_headers(request.headers)
```

**Problem**:
- Camoufox `geoip=True` provides fingerprints but may not set all required headers
- Firecrawl doesn't send custom headers to Camoufox for regular crawls
- Site-specific header requirements (like `gotcha=headers`) not handled

### 2. No HTTP Status Validation
**Location**: Firecrawl crawl worker (`/mnt/c/code/firecrawl/apps/api/src/services/worker/scrape-worker.ts`)

**Problem**:
- Scrape job returns `pageStatusCode: 400` but job still marked as `completed`
- No check before calling `_store_pages()` to filter HTTP errors
- Error pages get stored as valid sources

### 3. Orchestrator Doesn't Filter Errors
**Location**: Orchestrator crawl worker (`src/services/scraper/crawl_worker.py:144-186`)

```python
async def _store_pages(self, job: Job, pages: list[dict]) -> int:
    for page in pages:
        markdown = page.get("markdown", "")
        # ❌ No status code check here!
        await self.source_repo.create(...)
```

**Problem**: Blindly stores all pages returned by Firecrawl, even error pages

---

## Architectural Solutions

### Fix 1: Add Standard Browser Headers to Camoufox ⭐ **PRIORITY**

**Goal**: Make Camoufox send realistic browser headers automatically

**Location**: `src/services/camoufox/scraper.py`

**Implementation**:
```python
# Add standard browser headers that all contexts should have
STANDARD_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

async def _do_scrape(self, request: ScrapeRequest) -> dict[str, Any]:
    # ...
    context = await self._browser.new_context(**context_options)
    page = await context.new_page()

    # Merge standard headers with custom headers
    headers_to_apply = {**STANDARD_BROWSER_HEADERS}
    if request.headers:
        headers_to_apply.update(request.headers)

    # Always apply headers (not conditional)
    await page.set_extra_http_headers(headers_to_apply)
```

**Why this works**:
- Camoufox User-Agent already spoofed via fingerprinting
- These additional headers complete the browser profile
- Matches what real Firefox sends
- Should pass `gotcha=headers` test

**Testing**:
```bash
# Re-test gotcha=headers page
curl -X POST http://192.168.0.136:8742/api/v1/crawl ...
# Should get 200 OK instead of 400
```

---

### Fix 2: Filter HTTP Errors Before Storing Sources ⭐ **CRITICAL**

**Goal**: Don't store error pages as valid content

**Location**: `src/services/scraper/crawl_worker.py:144-186`

**Implementation**:
```python
async def _store_pages(self, job: Job, pages: list[dict]) -> int:
    project_id = job.payload["project_id"]
    company = job.payload["company"]
    sources_created = 0

    for page in pages:
        metadata = page.get("metadata", {})
        markdown = page.get("markdown", "")
        url = metadata.get("url") or metadata.get("sourceURL", "")

        if not markdown or not url:
            logger.warning(...)
            continue

        # ✅ NEW: Check HTTP status code
        status_code = metadata.get("statusCode")
        if status_code and (status_code >= 400):
            logger.warning(
                "page_http_error_skipped",
                job_id=str(job.id),
                url=url,
                status_code=status_code,
                reason="HTTP error pages not stored as sources",
            )
            continue  # Skip storing error pages

        # Check for duplicate URL...
        # Store source...
```

**Why this is critical**:
- Prevents error page HTML from entering extraction pipeline
- Keeps database clean - only real content
- Error pages have no useful facts to extract

---

### Fix 3: Mark Jobs as Failed for HTTP Errors

**Goal**: Jobs with HTTP errors should fail, not complete successfully

**Location**: `src/services/scraper/crawl_worker.py:100-134`

**Implementation**:
```python
if status.status == "completed":
    # Step 3: Store all pages as sources
    pages_with_errors = []
    valid_pages = []

    for page in status.pages:
        metadata = page.get("metadata", {})
        status_code = metadata.get("statusCode")
        if status_code and status_code >= 400:
            pages_with_errors.append((page, status_code))
        else:
            valid_pages.append(page)

    sources_created = await self._store_pages(job, valid_pages)

    # Determine job status based on results
    if sources_created == 0 and len(pages_with_errors) > 0:
        # All pages failed with HTTP errors
        job.status = "failed"
        job.error = f"{len(pages_with_errors)} pages returned HTTP errors (no valid content)"
        job.completed_at = datetime.now(UTC)
        self.db.commit()

        logger.error(
            "crawl_failed_all_http_errors",
            job_id=str(job.id),
            pages_with_errors=len(pages_with_errors),
            error_codes=[sc for _, sc in pages_with_errors],
        )
        return

    # Some valid pages - mark as completed
    job.status = "completed"
    # ... rest of success handling
```

**Why this is robust**:
- Jobs with only HTTP errors → marked as `failed`
- Jobs with mixed results → marked as `completed` but only valid content stored
- Enables proper retry logic (failed jobs can be retried)
- Clear logging of what happened

---

### Fix 4: Add HTTP Status to Metadata

**Goal**: Track HTTP status codes for observability

**Location**: Same as Fix 2

**Implementation**:
```python
await self.source_repo.create(
    project_id=project_id,
    uri=url,
    source_group=company,
    source_type="web",
    title=metadata.get("title", ""),
    content=markdown,
    meta_data={
        "domain": domain,
        "http_status": status_code,  # ✅ NEW
        **metadata
    },
    status="pending",
)
```

**Why this helps**:
- Debugging: Can query sources by HTTP status
- Monitoring: Track crawl health (how many 200s vs errors)
- Future: Could implement retry logic for specific status codes

---

## Testing Plan

### Step 1: Test Header Fix
```bash
# Test gotcha=headers page with new headers
curl -X POST http://192.168.0.136:8742/api/v1/crawl \
  -H "Content-Type: application/json" \
  -H "X-API-Key: thisismyapikey3215215632" \
  -d '{
    "url": "https://www.scrapethissite.com/pages/advanced/?gotcha=headers",
    "project_id": "...",
    "company": "test-headers-fix",
    "max_depth": 1,
    "limit": 1
  }'

# Expected: 200 OK instead of 400
```

### Step 2: Test Error Filtering
```bash
# Create a crawl that includes both valid and invalid URLs
# Check database - error pages should NOT be stored
```

### Step 3: Test Failure Marking
```bash
# Crawl URL that returns only 404s
# Check job status - should be "failed", not "completed"
```

### Step 4: Full Integration Test
```bash
# Re-run the problematic bulk crawl
curl -X POST http://192.168.0.136:8742/api/v1/crawl \
  -d '{"url": "https://www.scrapethissite.com/pages/", "max_depth": 5, "limit": 100, ...}'

# Verify:
# 1. No 400 errors from gotcha pages
# 2. No error pages in database
# 3. Jobs fail gracefully when appropriate
```

---

## Implementation Order

1. **Fix 1 (Headers)** - Quick win, solves gotcha=headers immediately
2. **Fix 2 (Filtering)** - Critical for data quality
3. **Fix 3 (Failure marking)** - Important for retry logic
4. **Fix 4 (Metadata)** - Nice to have for observability

---

## Migration Strategy

**Database cleanup** (after Fix 2 deployed):
```python
# Script to remove error pages from existing data
# DELETE FROM sources WHERE meta_data->>'http_status' >= 400
```

---

## Open Questions

1. **Should we retry HTTP errors?**
   - 4xx errors (client errors): Usually permanent, don't retry
   - 5xx errors (server errors): Often transient, could retry
   - **Recommendation**: Don't retry 4xx, consider retrying 5xx with backoff

2. **What about soft 404s?**
   - Pages that return 200 but show "Page Not Found" content
   - **Recommendation**: Out of scope for now, would need content analysis

3. **Headers for other gotcha tests?**
   - `gotcha=login` - needs authentication (out of scope)
   - `gotcha=csrf` - needs CSRF token handling (complex)
   - **Recommendation**: Focus on headers first, document others as limitations

---

## Success Criteria

✅ `gotcha=headers` page returns 200 OK instead of 400
✅ No error pages (status >= 400) stored in database
✅ Jobs with only HTTP errors marked as "failed"
✅ HTTP status tracked in source metadata
✅ Clean separation: valid content vs errors

---

**Next session should**: Implement Fix 1 (headers) as immediate win, then Fix 2 (filtering) for data quality.
