# TODO: Architecture - Database Consistency & Worker Robustness

Review date: 2026-01-29 (verified)

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
  - [x] ~~Add service to find orphaned extractions and retry embedding~~ (DONE - 2026-01-29)
    - Service: `src/services/extraction/embedding_recovery.py`
    - API endpoint: `POST /projects/{project_id}/extractions/recover`
    - Tests: `tests/test_embedding_recovery.py`
    - **Note**: Manual trigger via API, not automatic background task
  - [ ] Consider transactional outbox pattern for critical consistency
  - [ ] Add alerting for partial-failure states

---

## Medium Priority

### 2. Async/Sync Mismatch in Repositories

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

### 3. Inconsistent Transaction Boundaries

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

### ~~4. LLM Response Polling Inefficiency~~ (DONE)

- **Severity:** MEDIUM
- **Files:** `src/services/llm/queue.py:176-256`
- **Status:** ✅ COMPLETED (verified 2026-01-29)
- **Implementation:** Redis pub/sub with fallback polling

```python
# Subscribe and wait for notification
pubsub = self.redis.pubsub()
await pubsub.subscribe(channel)

# Wait for message with timeout and fallback polling
message = await asyncio.wait_for(
    pubsub.get_message(ignore_subscribe_messages=True),
    timeout=0.1,
)
```

- **Features:**
  - Primary: Redis pub/sub for instant notification
  - Fallback: Periodic polling every `poll_fallback_interval` seconds (reliability)
  - Proper cleanup with `pubsub.unsubscribe()` and `pubsub.aclose()`

---

## Low Priority

### 5. Database Pool Sizing Review

- **Severity:** LOW
- **File:** `src/database.py:17`
- **Current:** `pool_size=5, max_overflow=10`
- **Issue:** May be insufficient for high concurrency, especially with sync operations blocking

- **Recommended Actions:**
  - [ ] Load test to determine optimal pool size
  - [ ] Consider dynamic pool sizing based on worker count
  - [ ] Monitor connection exhaustion in production

---

### 6. Qdrant Repository Sync Operations

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

### ~~7. Job Duration Metrics Use PostgreSQL-Specific SQL~~ (DONE)

- **Severity:** LOW
- **File:** `src/services/metrics/collector.py:131-148`
- **Status:** ✅ COMPLETED (verified 2026-01-29)
- **Implementation:** SQLite fallback using `julianday()` already exists at lines 145-148

```python
# PostgreSQL: Use extract epoch
if dialect_name == "postgresql":
    duration_expr = (
        extract("epoch", Job.completed_at) - extract("epoch", Job.started_at)
    )
else:
    # SQLite: Use julianday
    duration_expr = (
        (func.julianday(Job.completed_at) - func.julianday(Job.started_at))
        * 86400
    )
```

---

### ~~8. Missing Unit Tests for New Methods~~ (DONE)

- **Severity:** LOW
- **Status:** ✅ COMPLETED (verified 2026-01-29)
- **Test Files:**
  - `tests/test_extraction_repository_batch.py` - Tests for `update_embedding_ids_batch()`
    - `test_updates_single_extraction`
    - `test_updates_multiple_extractions`
    - `test_empty_list_returns_zero`
    - `test_nonexistent_ids_ignored`
  - `tests/test_metrics_job_duration.py` - Tests for `_job_duration_by_type()`
    - `test_calculates_avg_duration`
    - `test_handles_no_completed_jobs`
    - `test_groups_by_job_type`
    - `test_excludes_jobs_without_timestamps`
    - `test_excludes_non_completed_jobs`
  - `tests/test_embedding_recovery.py` - Tests for embedding recovery service
    - Full test coverage for `find_orphaned_extractions`, `recover_batch`, `run_recovery`

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
1. ~~Increase stale job threshold~~ (DONE)
2. ~~Add embedding retry service~~ (DONE - manual API endpoint exists)

### Phase 2: Performance Improvements
1. Fix async/sync mismatch (choose strategy)
2. ~~Improve LLM response pattern (pub/sub)~~ (DONE)

### Phase 3: Robustness
1. Document transaction boundary strategy
2. Add transactional outbox for critical paths
3. Add alerting for partial-failure states

---

## Related Documents

- `docs/TODO_production_readiness.md` - Production checklist
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

**5. Fixed stale job recovery thresholds** (was 5 seconds for all job types)
- **Files modified:**
  - `src/config.py:313-324` - Added configurable per-job-type thresholds
  - `src/services/scraper/scheduler.py:43-52` - Added `get_stale_thresholds()` function
- **New settings:**
  - `job_stale_threshold_scrape`: 300s (5 minutes)
  - `job_stale_threshold_extract`: 900s (15 minutes)
  - `job_stale_threshold_crawl`: 1800s (30 minutes)
- **Impact:** Eliminates duplicate processing risk for long-running extraction jobs

