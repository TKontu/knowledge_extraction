# TODO: Architecture - Database Consistency & Worker Robustness

Review date: 2026-01-31 (updated)

## Summary

Architecture review of worker/job handling and multi-database coordination. The system uses **three databases in parallel** (PostgreSQL, Redis, Qdrant) without distributed transaction coordination.

**Overall Assessment**: Reasonably robust for knowledge extraction pipelines where eventual consistency is acceptable.

---

## High Priority

### 1. Distributed Transaction Coordination
- **Severity:** HIGH (but acceptable for current use case)
- **Files:** `src/services/extraction/pipeline.py:164-206`
- **Issue:** Extraction pipeline writes to PostgreSQL, then Qdrant sequentially without coordination

- **Current Mitigations (all implemented):**
  - [x] Track `embedding_id` in PostgreSQL after Qdrant upsert
  - [x] Embedding recovery service (`POST /projects/{project_id}/extractions/recover`)
  - [x] Logs warnings, skips entity extraction, sets `entities_extracted=False`

- **Remaining (optional, for stricter consistency):**
  - [ ] Consider transactional outbox pattern for critical consistency
  - [ ] Add alerting for partial-failure states

---

## Medium Priority

### 2. Async/Sync Mismatch in Repositories
- **Severity:** MEDIUM
- **Files:** `src/services/storage/repositories/extraction.py`, `source.py`, `entity.py`
- **Issue:** Repository methods declared `async` but use synchronous SQLAlchemy operations

- **Recommended Actions:**
  - [ ] Option A: Migrate to SQLAlchemy async engine with `AsyncSession`
  - [ ] Option B: Remove `async` keywords to clarify sync behavior
  - [ ] Document chosen approach in CLAUDE.md

### 3. Inconsistent Transaction Boundaries
- **Severity:** MEDIUM
- **Files:**
  - `src/services/scraper/worker.py:85,152,181` (3 commits per job)
  - `src/services/extraction/worker.py:63,131` (2 commits per job)
  - `src/services/extraction/pipeline.py` (relies on implicit flush)

- **Recommended Actions:**
  - [ ] Document transaction boundary strategy
  - [ ] Consider batch commits with explicit savepoints
  - [ ] Add transaction boundary comments in code

---

## Low Priority

### 4. Database Pool Sizing Review
- **Severity:** LOW
- **File:** `src/database.py:17`
- **Current:** `pool_size=5, max_overflow=10`

- **Recommended Actions:**
  - [ ] Load test to determine optimal pool size
  - [ ] Consider dynamic pool sizing based on worker count
  - [ ] Monitor connection exhaustion in production

### 5. Qdrant Repository Sync Operations
- **Severity:** LOW
- **File:** `src/services/storage/qdrant/repository.py`
- **Issue:** Uses sync Qdrant client in async context

- **Recommended Actions:**
  - [ ] Evaluate async Qdrant client
  - [ ] Or wrap in `run_in_executor` for true async behavior

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
5. **Embedding Recovery**: Manual API endpoint for recovering orphaned extractions

---

## Completed Items

- [x] Fixed `embedding_id` tracking (was never set) - 2026-01-28
- [x] Fixed cleanup service Qdrant deletion - 2026-01-28
- [x] Added job duration metrics - 2026-01-28
- [x] Fixed N+1 query in `update_embedding_ids_batch()` - 2026-01-28
- [x] Fixed stale job recovery thresholds - 2026-01-28
- [x] LLM response polling (Redis pub/sub with fallback) - 2026-01-29
- [x] Job duration metrics SQLite fallback - 2026-01-29
- [x] Unit tests for new methods - 2026-01-29
- [x] Embedding recovery service - 2026-01-29

---

## Related Documents

- `docs/TODO_production_readiness.md` - Production checklist
- `docs/TODO_high_concurrency_tuning.md` - Concurrency configuration
- `docs/PLAN-crawl-improvements.md` - Crawl pipeline enhancements
