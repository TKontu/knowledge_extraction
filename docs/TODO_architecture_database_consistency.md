# TODO: Architecture - Database Consistency & Worker Robustness

Review date: 2026-01-28

## Summary

Architecture review of worker/job handling and multi-database coordination. The system uses **three databases in parallel** (PostgreSQL, Redis, Qdrant) without distributed transaction coordination. This document tracks identified issues and recommended improvements.

**Overall Assessment**: Reasonably robust for knowledge extraction pipelines where eventual consistency is acceptable. Critical for systems requiring strict transactional guarantees.

---

## High Priority

### 1. No Distributed Transactions Across Databases

- **Severity:** HIGH
- **Files:** `src/services/extraction/pipeline.py:164-206`
- **Issue:** Extraction pipeline writes to PostgreSQL, then Qdrant sequentially without coordination

```python
# PostgreSQL write (line 164)
extraction = await self._extraction_repo.create(...)

# Qdrant write (lines 188-205) - can fail independently
embeddings = await self._embedding_service.embed_batch(facts_to_embed)
await self._qdrant_repo.upsert_batch(items)
```

- **Failure Mode:** If Qdrant fails after PostgreSQL commit:
  - Extractions exist in PostgreSQL but aren't searchable
  - Source status still marked `"extracted"` (line 261)
  - No automated recovery mechanism

- **Current Mitigation:** Logs warnings, skips entity extraction, sets `entities_extracted=False`

- **Recommended Actions:**
  - [x] ~~Track `embedding_id` in PostgreSQL after Qdrant upsert~~ (DONE - 2026-01-28)
  - [ ] Add background task to find orphaned extractions (`embedding_id IS NULL`) and retry embedding
  - [ ] Consider transactional outbox pattern for critical consistency
  - [ ] Add alerting for partial-failure states

---

### 2. Stale Job Recovery Window Too Aggressive

- **Severity:** HIGH
- **Files:** `src/services/scraper/scheduler.py:185-198`, `scheduler.py:262-275`, `scheduler.py:325-338`
- **Issue:** Jobs considered "stale" after only 5 seconds (`poll_interval`)

```python
stale_threshold = datetime.now(UTC) - timedelta(seconds=self.poll_interval)  # 5 seconds!
```

- **Problem:** LLM extraction jobs can take minutes; slow jobs may be "recovered" while still running
- **Risk:** Duplicate processing, race conditions

- **Recommended Actions:**
  - [ ] Increase stale threshold to 5-10 minutes for extraction jobs
  - [ ] Consider per-job-type thresholds (scrape: 2min, extract: 10min, crawl: 30min)
  - [ ] Add heartbeat mechanism for long-running jobs

---

## Medium Priority

### 3. Async/Sync Mismatch in Repositories

- **Severity:** MEDIUM
- **Files:** `src/services/storage/repositories/extraction.py`, `source.py`, `entity.py`
- **Issue:** Repository methods declared `async` but use synchronous SQLAlchemy operations

```python
async def create(self, ...) -> Extraction:
    extraction = Extraction(...)
    self._session.add(extraction)
    self._session.flush()  # SYNC operation - blocks event loop!
    return extraction
```

- **Impact:** Performance degradation under load; limited to `pool_size` (5) concurrent DB operations despite async architecture

- **Recommended Actions:**
  - [ ] Option A: Migrate to SQLAlchemy async engine with `AsyncSession`
  - [ ] Option B: Remove `async` keywords to clarify sync behavior
  - [ ] Document chosen approach in CLAUDE.md

---

### 4. Inconsistent Transaction Boundaries

- **Severity:** MEDIUM
- **Files:**
  - `src/services/scraper/worker.py:85,152,181` (3 commits per job)
  - `src/services/extraction/worker.py:63,131` (2 commits per job)
  - `src/services/extraction/pipeline.py` (relies on implicit flush)

- **Issue:** Pipeline flushes individual extractions immediately (line 82) but no explicit commit

- **Problem:** Partial pipeline completion (50 of 100 extractions) + failure = inconsistent state:
  - Flushed extractions survive in session
  - Some embeddings in Qdrant, others not

- **Recommended Actions:**
  - [ ] Document transaction boundary strategy
  - [ ] Consider batch commits with explicit savepoints
  - [ ] Add transaction boundary comments in code

---

### 5. LLM Response Polling Inefficiency

- **Severity:** MEDIUM
- **Files:** `src/services/llm/queue.py:131-150`
- **Issue:** Polling Redis every 100ms for up to 300 seconds = 3000 round-trips per request

```python
while time.time() < deadline:
    result = await self.redis.get(response_key)
    if result:
        return response
    await asyncio.sleep(self.poll_interval)  # 0.1s polling
```

- **Impact:** Unnecessary Redis load, especially under high concurrency

- **Recommended Actions:**
  - [ ] Replace with Redis pub/sub for response notification
  - [ ] Alternative: Use Redis BLPOP/blocking read patterns
  - [ ] Increase poll interval for longer-running requests

---

## Low Priority

### 6. Database Pool Sizing Review

- **Severity:** LOW
- **File:** `src/database.py:17`
- **Current:** `pool_size=5, max_overflow=10`
- **Issue:** May be insufficient for high concurrency, especially with sync operations blocking

- **Recommended Actions:**
  - [ ] Load test to determine optimal pool size
  - [ ] Consider dynamic pool sizing based on worker count
  - [ ] Monitor connection exhaustion in production

---

### 7. Qdrant Repository Sync Operations

- **Severity:** LOW
- **File:** `src/services/storage/qdrant/repository.py`
- **Issue:** Uses sync Qdrant client in async context

```python
async def upsert_batch(self, items: list[EmbeddingItem]) -> list[str]:
    # ...
    self.client.upsert(...)  # Sync call
```

- **Recommended Actions:**
  - [ ] Evaluate async Qdrant client
  - [ ] Or wrap in `run_in_executor` for true async behavior

---

### 8. Job Duration Metrics Use PostgreSQL-Specific SQL

- **Severity:** LOW
- **File:** `src/services/metrics/collector.py:134-141`
- **Issue:** `extract("epoch", timestamp)` is PostgreSQL-specific syntax
- **Context:**
  - Tests use PostgreSQL (verified in `conftest.py:54-58`)
  - Production uses PostgreSQL
  - Other repository code already handles SQLite fallbacks (e.g., `extraction.py:223,251,286,307`)

```python
func.avg(
    extract("epoch", Job.completed_at) - extract("epoch", Job.started_at)
)
```

- **Recommended Actions:**
  - [ ] Add SQLite fallback using `julianday()` for consistency with other code
  - [ ] Or document PostgreSQL as a hard requirement

---

### 9. Missing Unit Tests for New Methods

- **Severity:** LOW
- **Files:**
  - `src/services/storage/repositories/extraction.py` - `update_embedding_id()`, `update_embedding_ids_batch()`
  - `src/services/metrics/collector.py` - `_job_duration_by_type()`
- **Issue:** New methods added 2026-01-28 lack dedicated unit tests
- **Impact:** Regression risk if these methods are modified

- **Recommended Actions:**
  - [ ] Add test for `update_embedding_ids_batch()` verifying single query execution
  - [ ] Add test for `_job_duration_by_type()` with mock job data
  - [ ] Add test for edge cases (empty list, NULL timestamps)

---

## Architecture Reference

### Database Interaction Matrix

| Source DB | Target DB | Coordination | Failure Mode |
|-----------|-----------|--------------|--------------|
| PostgreSQL | Qdrant | Sequential, no saga | Orphaned extractions without embeddings |
| PostgreSQL | Redis (LLM queue) | Async fire-and-forget | DLQ captures failures, extraction marked failed |
| Redis | PostgreSQL (response) | Polling with timeout | Request lost if response arrives after timeout |

### Current Strengths

1. **Job Locking** (`scheduler.py:172-180`): Uses `SELECT FOR UPDATE SKIP LOCKED` correctly
2. **Adaptive Concurrency** (`worker.py:576-641`): LLM worker adjusts based on timeout ratio
3. **Backpressure Handling** (`pipeline.py:272-303`): Checks queue capacity before submitting
4. **Dead Letter Queue**: Failed LLM requests preserved with full context for reprocessing

---

## Implementation Recommendations

### Phase 1: Critical Fixes (Before Next Major Release)
1. Increase stale job threshold
2. Add embedding retry background task

### Phase 2: Performance Improvements
3. Fix async/sync mismatch (choose strategy)
4. Improve LLM response pattern (pub/sub)

### Phase 3: Robustness
5. Document transaction boundary strategy
6. Add transactional outbox for critical paths

---

## Related Documents

- `docs/TODO_production_readiness.md` - Production checklist
- `docs/TODO_extraction_reliability.md` - Extraction-specific improvements
- `docs/PLAN-crawl-improvements.md` - Crawl pipeline enhancements
- `docs/pipeline_review_embedding_tracking_changes.md` - Pipeline review for 2026-01-28 changes

---

## Completed Items

### 2026-01-28: Foundational Fixes (Pre-Orchestration)

**1. Fixed `embedding_id` tracking** (was never set)
- **Files modified:**
  - `src/services/storage/repositories/extraction.py` - Added `update_embedding_id()` and `update_embedding_ids_batch()` methods
  - `src/services/extraction/pipeline.py:208-211` - Now calls `update_embedding_ids_batch()` after Qdrant upsert
- **Impact:** Extractions now track whether they have embeddings in Qdrant

**2. Fixed cleanup service Qdrant deletion** (was silently skipping all deletes)
- **File:** `src/services/job/cleanup_service.py:97-110`
- **Change:** Now uses ALL extraction IDs for Qdrant deletion, not just those with `embedding_id` set
- **Impact:** Job cleanup now properly removes embeddings from Qdrant (handles both new and historical extractions)

**3. Added job duration metrics** (was missing - needed for stale threshold decision)
- **Files modified:**
  - `src/services/metrics/collector.py` - Added `JobDurationStats` dataclass and `_job_duration_by_type()` method
  - `src/services/metrics/prometheus.py` - Added Prometheus output for job duration metrics
- **New metrics:**
  - `scristill_job_duration_seconds_avg{type="..."}`
  - `scristill_job_duration_seconds_min{type="..."}`
  - `scristill_job_duration_seconds_max{type="..."}`
  - `scristill_jobs_completed_total{type="..."}`
- **Impact:** Can now make data-driven decisions about stale job thresholds

**4. Fixed N+1 query in `update_embedding_ids_batch()`**
- **File:** `src/services/storage/repositories/extraction.py:345-376`
- **Change:** Replaced N individual UPDATE statements with single UPDATE using `cast(Extraction.id, String)`
- **Impact:** O(1) database round-trips instead of O(n) for batch embedding updates

