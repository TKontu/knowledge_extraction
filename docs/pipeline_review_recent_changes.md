# Pipeline Review: Recent Changes (2026-01-27)

## Changes Reviewed
1. **DLQ Service** (PR #67) - Dead Letter Queue for scrape/extraction failures
2. **Embedding Batching** (PR #66) - Batch embedding in extraction pipeline
3. **Schema Improvements** (PR #63) - `entities_extracted` flag, `created_by_job_id` tracking
4. **Observability** (PR #65) - Enhanced logging and quality metrics

---

## Critical (must fix)

### 🔴 C1: DLQ import paths are wrong
**File:** `src/api/v1/dlq.py:6-7`
**Status:** ✅ VERIFIED - Will crash on startup

```python
from src.api.dependencies import get_dlq_service  # WRONG
from src.services.dlq.service import DLQService    # WRONG
```

**Evidence:** All other API files use `from api.dependencies import ...` without `src.` prefix. `main.py` imports via `from api.v1.dlq import router`.

**Fix:** Change to:
```python
from api.dependencies import get_dlq_service
from services.dlq.service import DLQService
```

### 🔴 C2: DLQ pop race condition - duplicate returns
**File:** `src/services/dlq/service.py:133-140, 152-159`
**Status:** ✅ VERIFIED - Can return same item to multiple callers

```python
items_json = await self._redis.lrange(SCRAPE_DLQ_KEY, 0, -1)  # Read all
for item_json in items_json:
    if item_data["id"] == item_id:
        await self._redis.lrem(SCRAPE_DLQ_KEY, 1, item_json)   # Remove
        return DLQItem(**item_data)
```

**Issue:** Two concurrent `/retry` requests for same item:
1. Both read item in `lrange`
2. First `lrem` removes it (returns 1)
3. Second `lrem` returns 0 (not found) - **but code doesn't check return value**
4. Both return the same item as "successfully popped"

**Fix:** Check `lrem` return value, or use Lua script for atomicity.

---

## Important (should fix)

### 🟠 I1: Batch embedding failure leaves orphaned extractions
**File:** `src/services/extraction/pipeline.py:164-226`
**Status:** ✅ VERIFIED

**Flow when batch embedding fails:**
1. Line 164-172: Extractions created in DB ✓
2. Line 187: `embed_batch()` fails
3. Line 206-208: Error caught, logged, **execution continues**
4. Line 211-226: Entity extraction proceeds, sets `entities_extracted=True`

**Result:** Extractions exist in DB with `entities_extracted=True` but have NO embeddings in Qdrant. They are unsearchable via vector similarity.

**Fix:** Either rollback extractions on embedding failure, or add `embeddings_created` flag.

### 🟠 I2: Scrape worker doesn't track job_id
**File:** `src/services/scraper/worker.py:117-129`
**Status:** ✅ VERIFIED

```python
await self.source_repo.create(
    project_id=project_id,
    uri=result.url,
    # ... no created_by_job_id
)
```

**Issue:**
- `create()` method doesn't accept `created_by_job_id` parameter
- `upsert()` does accept it and `crawl_worker.py:286` uses it
- Sources from scrape worker have no job traceability

**Fix:** Add `created_by_job_id` parameter to `create()` and pass `job.id`.

### 🟠 I3: DLQ retry endpoints don't actually retry
**File:** `src/api/v1/dlq.py:78-125`
**Status:** ✅ VERIFIED - By design but misleading

The `/retry` endpoints only pop items and return them. They don't re-queue work for processing. API consumers expect "retry" to trigger reprocessing.

**Fix:** Either rename to `/pop` or implement actual re-queueing.

---

## Minor

### 🟡 M1: DLQ limit parameter unbounded
**File:** `src/api/v1/dlq.py:46, 63`
**Status:** ✅ VERIFIED

```python
limit: int = 100,  # No max validation
```

User can pass `limit=1000000` and pull entire DLQ into memory.

**Fix:** Add `Query(le=1000)` or similar max constraint.

### 🟡 M2: Redis connection pool created per request
**File:** `src/redis_client.py:19-30`
**Status:** ✅ VERIFIED

```python
async def get_async_redis() -> aioredis.Redis:
    return aioredis.from_url(...)  # Creates NEW pool each call
```

Each DLQ endpoint call creates new connection pool. Inefficient under load.

**Fix:** Use singleton pattern like sync `redis_client`.

### 🟡 M3: entities_extracted doesn't distinguish partial success
**File:** `src/services/extraction/pipeline.py:222-226`
**Status:** ✅ VERIFIED

Flag is set per-extraction after entity extraction succeeds. If some entities fail within an extraction (caught line 228), we can't tell.

**Impact:** Low - individual extraction either fully succeeds or fails for entities.

---

## False Positives (removed from original review)

| Finding | Reason Invalid |
|---------|----------------|
| NULL extraction_type in metrics | `extraction_type` is `nullable=False` in ORM |
| Migration not idempotent | Normal Alembic behavior, not specific to this PR |
| strict=True partial failure | Embedding API returns all-or-nothing, not partial |

---

## Summary

| Severity | Count | Issues |
|----------|-------|--------|
| 🔴 Critical | 2 | C1: Import paths, C2: Race condition |
| 🟠 Important | 3 | I1: Orphaned extractions, I2: Missing job_id, I3: No retry logic |
| 🟡 Minor | 3 | M1: Unbounded limit, M2: Connection pool, M3: Partial flag |

## Recommended Fix Priority

1. **C1: Fix DLQ imports** - App won't start with DLQ enabled
2. **C2: Fix race condition** - Data integrity issue
3. **I1: Handle embedding failure** - Silent data quality issue
4. **I2: Add job_id to create()** - Audit trail gap
5. **M1: Add limit validation** - Prevent OOM
