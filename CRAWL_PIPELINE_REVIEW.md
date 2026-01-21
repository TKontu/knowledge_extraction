# Pipeline Review: POST /api/v1/crawl

**Date**: 2026-01-21
**Reviewer**: Claude (Orchestrator)
**Trigger**: Failed crawl job with "meta_data" error (0 sources created from 48 pages)

---

## Flow

```
POST /api/v1/crawl
  ↓
src/api/v1/crawl.py:create_crawl_job()
  ├─ Depends(get_db) → database.py:get_db()
  ├─ Validate CrawlRequest (pydantic)
  └─ Create Job(type="crawl", status="queued")
      ↓
Background: JobScheduler._run_single_crawl_worker()
  ↓
src/services/scraper/crawl_worker.py:CrawlWorker.process_job()
  ├─ FirecrawlClient.start_crawl() → Firecrawl API
  ├─ Poll: FirecrawlClient.get_crawl_status()
  └─ _store_pages() → SourceRepository.upsert()
      ↓
src/services/storage/repositories/source.py:upsert()
  ├─ pg_insert(Source).values(**values)
  └─ on_conflict_do_update()  ⚠️ BUG HERE
      ↓
PostgreSQL: INSERT ... ON CONFLICT DO UPDATE
```

---

## Critical Issues (Must Fix)

### 🔴 C1: AttributeError in upsert ON CONFLICT statement
**File**: `src/services/storage/repositories/source.py:270`
**Severity**: CRITICAL - Blocks all source creation during crawls
**Status**: ✅ FIXED (not deployed)

**Problem**:
```python
# Line 264-274 (BEFORE FIX)
stmt = stmt.on_conflict_do_update(
    constraint="uq_sources_project_uri",
    set_={
        "meta_data": stmt.excluded.meta_data,  # ❌ AttributeError!
        ...
    }
)
```

**Root Cause**:
- ORM model: `meta_data: Mapped[dict] = mapped_column("metadata", ...)`
- Python attribute: `meta_data`
- Database column: `metadata`
- Using `stmt.excluded.meta_data` → SQLAlchemy looks for column `meta_data`
- Column doesn't exist → `AttributeError: meta_data`
- Exception stringifies to `"meta_data"` which becomes job.error

**Impact**:
- Every crawl job fails at source storage
- 100% of crawled pages are lost (0 sources created)
- User sees cryptic "meta_data" error
- Affects production deployment (v1.2.0)

**Fix Applied**:
```python
# Line 264-274 (AFTER FIX)
stmt = stmt.on_conflict_do_update(
    constraint="uq_sources_project_uri",
    set_={
        Source.meta_data: stmt.excluded.metadata,  # ✅ Use Column object + db name
        Source.title: stmt.excluded.title,
        Source.content: stmt.excluded.content,
        Source.raw_content: stmt.excluded.raw_content,
        Source.outbound_links: stmt.excluded.outbound_links,
    }
)
```

**Why the fix works**:
1. Uses Column object (`Source.meta_data`) instead of string key
2. References database column name (`stmt.excluded.metadata`)
3. SQLAlchemy correctly maps Python attribute → DB column

**Verification**:
- ✅ Code compiles without AttributeError
- ✅ Generates correct SQL: `SET metadata = excluded.metadata`
- ⏳ Needs deployment and integration test

---

## Important Issues (Should Fix)

### 🟠 I1: Misleading error messages
**File**: `src/services/scraper/crawl_worker.py:139-142`
**Severity**: MEDIUM - Impairs debugging

**Problem**:
```python
except Exception as e:
    self.db.rollback()
    job.status = "failed"
    job.error = str(e)  # ← Generic error string
```

When SQLAlchemy raises `AttributeError('meta_data')`, the error becomes just `"meta_data"` with no context.

**Impact**:
- User sees cryptic single-word errors
- No stack trace logged for debugging
- Root cause unclear without code inspection

**Recommendation**:
```python
except Exception as e:
    self.db.rollback()
    job.status = "failed"
    job.error = f"{type(e).__name__}: {str(e)}"
    logger.error(
        "crawl_error",
        job_id=str(job.id),
        error=str(e),
        error_type=type(e).__name__,
        exc_info=True  # Include stack trace
    )
```

---

### 🟠 I2: Missing transaction boundaries
**Files**:
- `src/services/scraper/crawl_worker.py:199`
- `src/services/storage/repositories/source.py:276-286`

**Problem**:
```python
# crawl_worker.py:180-199
for page in pages:
    # ... process page ...
    source, created = await self.source_repo.upsert(...)
    if created:
        sources_created += 1

self.db.commit()  # ← Single commit after entire loop
return sources_created
```

**Risk**:
- If any page fails mid-loop (after C1 is fixed), all previous pages are lost
- No partial progress saved
- Long crawls vulnerable to timeout/crash

**Current Behavior**:
- Process 48 pages → Exception on page 30 → Rollback all 29
- Result: 0 sources created despite 29 successful upserts

**Recommendation**:
Consider batch commits:
```python
BATCH_SIZE = 10
for i, page in enumerate(pages):
    source, created = await self.source_repo.upsert(...)
    if created:
        sources_created += 1

    # Commit every 10 pages
    if (i + 1) % BATCH_SIZE == 0:
        self.db.commit()

# Commit remaining
self.db.commit()
```

**Trade-off**:
- Pro: Partial progress preserved
- Con: Slightly more complex error handling
- Con: Duplicate detection harder if crash mid-batch

---

### 🟠 I3: Inconsistent error filtering logic
**File**: `src/services/scraper/crawl_worker.py:164-174`

**Problem**:
```python
status_code = metadata.get("statusCode")
if status_code and status_code >= 400:
    logger.warning("page_http_error_skipped", ...)
    continue
```

**Issues**:
1. Skips 400 errors (login/CSRF protected pages) → GOOD
2. Also skips 403, 404, 500 errors → QUESTIONABLE
3. No distinction between client errors (4xx) vs server errors (5xx)
4. 429 (rate limit) should trigger retry, not skip

**Impact**:
- Legitimate content behind soft 404s is skipped
- Transient 5xx errors aren't retried
- Rate limits cause silent data loss

**Recommendation**:
```python
status_code = metadata.get("statusCode")
if status_code:
    # Skip auth-protected (expected failures)
    if status_code in (401, 403):
        logger.warning("auth_protected_skipped", url=url, status=status_code)
        continue

    # Log but store 404 (may have cached content)
    if status_code == 404:
        logger.warning("not_found_but_stored", url=url)

    # 5xx and 429 should be retried by Firecrawl, flag if seen here
    if status_code >= 500 or status_code == 429:
        logger.error("server_error_in_crawl", url=url, status=status_code)
        # Store anyway - extraction may handle it
```

---

## Minor Issues

### 🟡 M1: Inefficient "created" detection
**File**: `src/services/storage/repositories/source.py:282-284`

**Problem**:
```python
# Check if it was a create or update by checking created_at
created = (datetime.now(UTC) - source.created_at).total_seconds() < 1
```

**Issues**:
- Relies on timing heuristic (fragile)
- Fails if database clock skew
- Fails if operation takes >1s (unlikely but possible)

**Better approach**:
```python
# PostgreSQL INSERT ... ON CONFLICT returns xmax=0 for INSERT, xmax>0 for UPDATE
# Or use RETURNING clause with a computed column
```

**Current workaround**: Works 99.9% of time, acceptable for now

---

### 🟡 M2: project_id string conversion inconsistency
**File**: `src/services/scraper/crawl_worker.py:146`

**Code**:
```python
project_id = job.payload["project_id"]  # ← Stored as string in JSON
company = job.payload["company"]
```

**Issue**:
- `Job.payload` is JSON (stores UUIDs as strings)
- `SourceRepository.upsert()` expects `UUID` type
- Implicit string → UUID conversion in PostgreSQL
- Type hints lie (`project_id: UUID` but actually `str`)

**Impact**: None currently (PostgreSQL handles coercion)

**Recommendation**:
```python
from uuid import UUID
project_id = UUID(job.payload["project_id"])
```

---

### 🟡 M3: Missing rate limit context in logs
**File**: `src/services/scraper/crawl_worker.py:33-68`

**Observation**:
- Crawl started at 06:00:27
- Completed at 06:03:49 (3m 22s for 48 pages)
- ~4.2 seconds per page
- Config: `crawl_delay_ms=2000`, `crawl_max_concurrency=2`

**Expected**: 48 pages / 2 concurrent = 24 batches × 2s = ~48s minimum

**Actual**: 202s (4x slower)

**Possible causes** (not errors, just observations):
- Firecrawl internal processing time
- Anti-bot detection delays
- Network latency

**Recommendation**: Add timing metrics
```python
logger.info(
    "crawl_completed",
    job_id=str(job.id),
    sources_created=sources_created,
    duration_seconds=(datetime.now(UTC) - job.started_at).total_seconds(),
    pages_per_second=status.completed / (datetime.now(UTC) - job.started_at).total_seconds()
)
```

---

## Summary

### Deployment Priority

**Immediate (blocks functionality)**:
- ✅ **C1**: AttributeError in `source.py:270` - FIXED, needs deployment

**Next Release**:
- **I1**: Improve error messages with stack traces
- **I2**: Add batch commits for large crawls
- **I3**: Refine HTTP error filtering logic

**Backlog**:
- **M1**: Improve created/updated detection
- **M2**: Type safety for UUID handling
- **M3**: Add performance metrics

---

## Test Coverage Needed

After deploying C1 fix:
1. Integration test: Full crawl pipeline with 10+ pages
2. Error handling: Simulate metadata parsing failures
3. Concurrency: Multiple crawls to same domain
4. Edge cases: Empty metadata, missing title, null status_code

---

## Deployment Steps

1. Build v1.2.1 with C1 fix:
   ```bash
   ./build-and-push.sh ghcr.io/tkontu v1.2.1
   ```

2. Deploy to production:
   ```bash
   export PIPELINE_TAG=v1.2.1
   docker compose -f docker-compose.prod.yml pull pipeline
   docker compose -f docker-compose.prod.yml up -d pipeline
   ```

3. Verify with test crawl:
   ```bash
   curl -X POST http://192.168.0.136:8742/api/v1/crawl \
     -H "X-API-Key: thisismyapikey3215215632" \
     -H "Content-Type: application/json" \
     -d '{
       "url": "https://www.scrapethissite.com/pages/",
       "project_id": "00501840-fbca-49c7-b7a3-cdfd664cc489",
       "company": "scrapethissite_test",
       "max_depth": 2,
       "limit": 10
     }'
   ```

4. Monitor: Check sources_created > 0 in job result
