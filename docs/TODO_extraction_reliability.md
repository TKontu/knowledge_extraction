# TODO: Extraction Pipeline Reliability Fixes

**Status:** Issue #1 FIXED (2026-01-27)
**Priority:** Critical (blocking production extractions)
**Created:** 2026-01-27

## Context

The drivetrain companies crawl revealed multiple reliability issues:
- **32 failed jobs** due to duplicate entity constraint violations
- **231 queued jobs** stuck behind failures
- **4 running jobs** processing slowly
- **444 of 446 sources** still pending extraction

Root cause chain:
1. LLM returns truncated JSON → JSON parse fails
2. Job retries → same extraction runs again
3. Entity already linked from partial first run → `UniqueViolation`
4. `PendingRollbackError` cascades → job fails permanently

## Issues to Fix

---

### Issue 1: Duplicate Entity Link Insertion (CRITICAL) ✅ FIXED

**File:** `src/services/storage/repositories/entity.py`
**Method:** `link_to_extraction()` (lines 194-218)

**Problem:**
Method blindly inserts `ExtractionEntity` without checking if link already exists. The unique constraint `(extraction_id, entity_id, role)` causes failures on retry.

**Error:**
```
UniqueViolation: duplicate key value violates unique constraint
"extraction_entities_extraction_id_entity_id_role_key"
DETAIL: Key (extraction_id, entity_id, role)=(..., ..., mention) already exists.
```

**Fix:**
Add `get_or_create_link()` method that checks before inserting:

```python
async def link_to_extraction(
    self,
    extraction_id: UUID,
    entity_id: UUID,
    role: str = "mention",
) -> tuple[ExtractionEntity, bool]:
    """Create or get existing link between entity and extraction.

    Returns:
        Tuple of (ExtractionEntity, created) - created=False if already existed
    """
    # Check for existing link
    existing = self._session.execute(
        select(ExtractionEntity).where(
            and_(
                ExtractionEntity.extraction_id == extraction_id,
                ExtractionEntity.entity_id == entity_id,
                ExtractionEntity.role == role,
            )
        )
    ).scalar_one_or_none()

    if existing:
        return existing, False

    # Create new link
    link = ExtractionEntity(
        extraction_id=extraction_id,
        entity_id=entity_id,
        role=role,
    )
    self._session.add(link)
    self._session.flush()
    return link, True
```

**Caller Update:**
`src/services/knowledge/extractor.py` line 172 - update to handle tuple return:

```python
# Step 3: Link entities to extraction
entities = []
for entity, _created in stored_entities:
    link, link_created = await self._entity_repo.link_to_extraction(
        entity_id=entity.id,
        extraction_id=extraction_id,
    )
    if link_created:
        logger.debug("entity_linked", entity_id=entity.id, extraction_id=extraction_id)
    entities.append(entity)
```

**Tests to Add:**
- `test_link_to_extraction_idempotent` - calling twice returns same link
- `test_link_to_extraction_different_roles` - same entity with different roles creates separate links

---

### Issue 2: JSON Parsing Failures (HIGH)

**Files:**
- `src/services/llm/worker.py` (lines 399, 461, 511, 561)
- `src/services/llm/client.py` (lines 259, 551, 726)
- `src/services/extraction/schema_extractor.py` (line 250)

**Problem:**
LLM output truncated due to `max_tokens` limit → `json.loads()` fails → triggers retry.

**Error:**
```
Unterminated string starting at: line 22 column 15 (char 977)
```

**Fix:**
See `docs/TODO_json_repair.md` for full implementation plan.

Quick fix pattern:
```python
try:
    result = json.loads(text)
except json.JSONDecodeError as e:
    result = repair_json(text)  # Attempt repair before retry
```

---

### Issue 3: Session Rollback Not Handled (MEDIUM)

**Problem:**
After `UniqueViolation`, the SQLAlchemy session enters `PendingRollbackError` state. Subsequent operations fail without explicit rollback.

**Evidence:**
```
PendingRollbackError: This Session's transaction has been rolled back
due to a previous exception during flush. To begin a new transaction,
first issue Session.rollback().
```

**Fix:**
Ensure transaction boundaries handle rollback:

```python
try:
    await self._entity_repo.link_to_extraction(...)
except IntegrityError:
    self._session.rollback()
    # Re-fetch or skip - link already exists
```

Or better: fix Issue 1 so this error never occurs.

---

### Issue 4: Embedding Batching Not Used (HIGH - Performance)

**Files:**
- `src/services/extraction/pipeline.py` (line 173)
- `src/services/storage/deduplication.py` (line 59)
- `src/services/storage/embedding.py` (lines 59-78 - unused batch method)
- `src/services/storage/qdrant/repository.py` (lines 99-127 - unused batch method)

**Problem:**
Embedding pipeline processes facts ONE AT A TIME despite having batch APIs available. Unlike LLM extraction (which has Redis queue + worker batching), embeddings have no batching infrastructure.

**Current code (`pipeline.py:147-182`):**
```python
for fact in result.facts:  # 50 facts = 50 API calls!
    embedding = await self._embedding_service.embed(fact.fact)  # SINGLE
    await self._qdrant_repo.upsert(...)  # SINGLE
```

**Impact:**
| Metric | Current | With Batching |
|--------|---------|---------------|
| API calls per 100 facts | 100+ | ~10 (batch of 10) |
| 446 sources × 50 facts | 22,300 calls | ~2,230 calls |
| Parallelism | Sequential | Concurrent |

**Unused batch methods that exist:**
- `EmbeddingService.embed_batch(texts: list[str])` - NEVER CALLED
- `QdrantRepository.upsert_batch(items: list[EmbeddingItem])` - NEVER CALLED

**Fix Options:**

**Option A: Simple batch collection (minimal change)**
```python
# Collect all facts first
facts_to_embed = [fact.fact for fact in result.facts if not is_duplicate(fact)]

# Batch embed
embeddings = await self._embedding_service.embed_batch(facts_to_embed)

# Batch upsert
items = [EmbeddingItem(extraction_id=..., embedding=emb, payload=...)
         for emb in embeddings]
await self._qdrant_repo.upsert_batch(items)
```

**Option B: Redis queue + worker (like LLM extraction)**
- Create `EmbeddingWorker` similar to `LLMWorker`
- Add Redis stream for embedding requests
- Configurable concurrency and batching
- Dead letter queue for failures

**Recommendation:** Start with Option A (quick win), plan Option B for scale.

**Tests to Add:**
- `test_pipeline_uses_batch_embedding` - verify batch method called
- `test_embedding_batch_performance` - measure improvement

---

### Issue 5: Project Persistence Investigation (LOW)

**Symptom:**
Batch crawl log shows project `7e47d5a8-9924-44ae-9d6d-da65c0857636` ("Industrial Drivetrain Companies - Full Batch") was created, but project no longer exists.

**Possible Causes:**
1. Manual deletion
2. Database restore from backup
3. Transaction rollback during creation
4. Container restart lost in-memory state

**Action:**
- Check for deletion audit logs
- Verify database persistence configuration
- Add project creation logging with confirmation

---

## Implementation Order

### Phase 1: Unblock Current Crawl (Critical)
1. **Issue 1** - Fix duplicate entity links (~20 lines) - **BLOCKING 32 JOBS**
2. **Issue 3** - Session rollback handling

### Phase 1b: Crawl Investigation (High) ✅ LOGGING ADDED
3. **Issue 12** - Investigate why Firecrawl returns 0 pages for some crawls
   - ✅ Added verbose logging for 0-page crawls in `client.py`
   - ✅ Added pagination detection logging (`next` field)
   - ✅ Added page count mismatch warnings
   - ✅ Added `crawl_completed_zero_sources` warning in `crawl_worker.py`
   - Still need to observe on next crawl to diagnose root cause

### Phase 2: Reliability & Recovery (High)
3. **Issue 6** - Add scrape/extraction DLQ
4. **Issue 7** - Add checkpoint/resume system
5. **Issue 2** - JSON repair ✅ IMPLEMENTED (see `docs/TODO_json_repair.md`)

### Phase 3: Performance (High)
6. **Issue 4** - Embedding batching (10-20x speedup)

### Phase 4: Observability (Medium)
7. **Issue 8** - Entity extraction completeness flag
8. **Issue 9** - Source→job traceability (migration)
9. **Issue 10** - Improve error context logging
10. **Issue 11** - Add quality metrics

### Phase 5: Investigation (Low)
11. **Issue 5** - Missing project post-mortem

## Verification

After fixing Issue 1:

```bash
# Reset failed jobs to queued
curl -X POST "http://192.168.0.136:8742/api/v1/jobs/retry-failed?project_id=4cbfbcd2-218e-42a4-b591-df8aa69f277b" \
  -H "X-API-Key: thisismyapikey3215215632"

# Monitor job status
curl "http://192.168.0.136:8742/api/v1/jobs?project_id=4cbfbcd2-218e-42a4-b591-df8aa69f277b&status=failed&limit=1" \
  -H "X-API-Key: thisismyapikey3215215632"
```

Expected: Failed count should not increase after fix.

## Files to Modify

### Phase 1: Critical Fixes
| File | Change |
|------|--------|
| `src/services/storage/repositories/entity.py` | Add duplicate check to `link_to_extraction` |
| `src/services/knowledge/extractor.py` | Handle tuple return from link method |

### Phase 1b: Crawl Investigation
| File | Change |
|------|--------|
| `src/services/scraper/client.py` | Add pagination handling (defensive), improve logging |
| Firecrawl/Camoufox logs | Investigate why some crawls return 0 pages |

### Phase 2: Reliability
| File | Change |
|------|--------|
| `src/services/llm/worker.py` | Add JSON repair, DLQ for extractions |
| `src/services/extraction/pipeline.py` | Add checkpoint system |
| `src/services/extraction/worker.py` | Add DLQ push on failure |
| `src/api/routes/admin.py` | Add DLQ endpoints |

### Phase 3: Performance
| File | Change |
|------|--------|
| `src/services/extraction/pipeline.py` | Use `embed_batch()` in loop |
| `src/services/storage/deduplication.py` | Batch dedup checks |

### Phase 4: Observability
| File | Change |
|------|--------|
| `src/orm_models.py` | Add `entities_extracted` flag to Extraction |
| `alembic/versions/` | Migration for `source.created_by_job_id` |
| `src/services/extraction/pipeline.py` | Enhanced error logging |
| `src/services/metrics/prometheus.py` | Add quality metrics |

### Tests to Add
| File | Tests |
|------|-------|
| `tests/services/storage/test_entity_repository.py` | Idempotency tests |
| `tests/services/extraction/test_pipeline.py` | Batch embedding, checkpoint tests |
| `tests/api/test_dlq.py` | DLQ endpoint tests |

---

### Issue 6: No DLQ for Scraping/Extraction (HIGH - Data Recovery)

**Problem:**
LLM requests have a Dead Letter Queue (Redis `llm:dlq`), but failed scrape/extraction jobs have NO recovery mechanism. Failed sources stay in `pending` status forever.

**Current state:**
- LLM DLQ: ✅ `llm:dlq` with `get_dlq_stats()`, `reprocess_dlq_item()`
- Scrape DLQ: ❌ None
- Extraction DLQ: ❌ None

**Impact:**
- Failed sources require manual intervention to identify and retry
- No visibility into failure backlog
- Operators must query DB for stuck jobs

**Fix:**
```python
# Add Redis lists for failed items
SCRAPE_DLQ_KEY = "scrape:dlq"
EXTRACTION_DLQ_KEY = "extraction:dlq"

# On failure, push to DLQ with context
await redis.lpush(EXTRACTION_DLQ_KEY, json.dumps({
    "source_id": str(source_id),
    "job_id": str(job_id),
    "error": str(e),
    "failed_at": datetime.now(UTC).isoformat(),
    "retry_count": retry_count,
}))
```

**API endpoints to add:**
- `GET /api/v1/dlq/scrape` - list failed scrape items
- `GET /api/v1/dlq/extraction` - list failed extraction items
- `POST /api/v1/dlq/{type}/{id}/retry` - reprocess item

---

### Issue 7: No Checkpoint/Resume System (HIGH - Data Loss)

**Problem:**
If extraction fails mid-way, ALL progress is lost. No checkpoint system to resume from failure point.

**Scenario:**
```
Process 10 chunks:
  Chunk 1-5: ✓ extracted (not committed)
  Chunk 6: ✗ fails
  Result: Chunks 1-5 LOST, full re-extraction on retry
```

**Current code (`pipeline.py`):**
- Processes all chunks
- Only commits at the END (line 199)
- No intermediate checkpoints

**Fix:**
```python
# Add checkpoint table or Redis key
checkpoint = {
    "source_id": source_id,
    "chunks_processed": 5,
    "last_chunk_id": chunk_5_id,
    "extractions_created": [...],
    "timestamp": datetime.now(UTC),
}

# On retry, resume from checkpoint
if checkpoint := await self._get_checkpoint(source_id):
    start_chunk = checkpoint["chunks_processed"]
```

**Alternative:** Commit after each chunk (trade-off: more DB writes)

---

### Issue 8: Entity Extraction Failures Silent (MEDIUM - Data Consistency)

**Problem:**
When entity extraction fails, the extraction record exists but has NO entities attached. There's no flag indicating the extraction is incomplete.

**Current behavior:**
1. Extraction created in DB ✓
2. Entity extraction fails ✗
3. Exception logged, but extraction stays
4. No `entities_complete` flag
5. Silently inconsistent data

**Fix:**
Add `entities_extracted: bool = False` column to Extraction table:
```python
# After successful entity extraction
extraction.entities_extracted = True
session.commit()
```

**Query for incomplete extractions:**
```sql
SELECT * FROM extractions WHERE entities_extracted = false;
```

---

### Issue 9: Missing Source → Job Traceability (MEDIUM - Audit Trail)

**Problem:**
Sources have no reference to the job that created them. Can't answer: "Which sources came from job X?"

**Current schema:**
- `Source.project_id` ✓
- `Source.source_group` ✓
- `Source.job_id` ❌ MISSING

**Impact:**
- Can't trace source origin
- Limited audit trail
- Debugging requires source_group matching

**Fix:**
Migration to add:
```sql
ALTER TABLE sources ADD COLUMN created_by_job_id UUID REFERENCES jobs(id);
CREATE INDEX idx_sources_job_id ON sources(created_by_job_id);
```

---

### Issue 10: Incomplete Error Context (MEDIUM - Debugging)

**Problem:**
Extraction errors are logged but lack context for debugging:
- No chunk ID that failed
- No LLM response (even partial)
- No source content snippet

**Current logging:**
```python
logger.error("extraction_failed", error=str(e))  # Just the message
```

**Improved logging:**
```python
logger.error(
    "extraction_failed",
    error=str(e),
    error_type=type(e).__name__,
    source_id=str(source_id),
    chunk_index=chunk_idx,
    chunk_preview=content[:500],  # First 500 chars
    llm_response_preview=response[:500] if response else None,
    exc_info=True,  # Full stack trace
)
```

---

### Issue 12: Firecrawl Returns Zero Pages for Some Crawls (NEEDS INVESTIGATION)

**File:** `src/services/scraper/client.py`
**Method:** `get_crawl_status()` (lines 368-423)

**Status:** ⚠️ PARTIALLY VERIFIED - Pagination may not be root cause

#### Verification Results (2026-01-27)

Analyzed crawl jobs in `drivetraincompanies` project:

**Jobs that WORKED (Jan 26):**
| Company | pages_total | sources_created | Result |
|---------|-------------|-----------------|--------|
| Acmegear | 22 | 22 | ✅ All pages stored |
| Addn | 6 | 6 | ✅ All pages stored |
| Industrial Gears | 3 | 3 | ✅ All pages stored |

**Jobs that FAILED (Jan 25):**
| Company | pages_total | sources_created | Result |
|---------|-------------|-----------------|--------|
| Hofmannengineering | 0 | 0 | ❌ Firecrawl returned 0 |
| Smsmining | 0 | 0 | ❌ Firecrawl returned 0 |
| Stmteamaustralia | 0 | 0 | ❌ Firecrawl returned 0 |

**Key Finding:** When jobs succeed, `pages_total == sources_created` (no pagination loss).
The issue is that **Firecrawl returns 0 pages** for some crawls, not pagination truncation.

#### Possible Root Causes (Need Investigation)

1. **Anti-bot blocking** - Sites may block Firecrawl/Camoufox
2. **Network/timeout issues** - Crawl times out before completing
3. **Firecrawl configuration** - Something different between Jan 25 and Jan 26 runs
4. **Site-specific issues** - Some sites may not be crawlable
5. **Camoufox browser issues** - Browser automation failing silently

#### Original Pagination Concern

The Firecrawl API DOES support pagination with a `next` pointer:
```json
{
  "data": [...first batch...],
  "next": "http://...?skip=10"  // Pagination link
}
```

The client does NOT follow pagination (line 421):
```python
pages=data.get("data", [])  # Only first page if paginated
```

**However**, for jobs that work, all pages are returned without needing pagination.
Pagination handling should still be added as a defensive measure for large crawls.

#### Recommended Actions

1. **INVESTIGATE** why some Firecrawl crawls return 0 pages
   - Check Firecrawl logs for the failed job IDs
   - Test crawling the failed URLs manually
   - Compare Firecrawl/Camoufox configuration between dates

2. **ADD pagination handling** (defensive, ~30 lines):
   ```python
   async def get_crawl_status(self, crawl_id: str) -> CrawlStatus:
       all_pages = []
       next_url = f"{self.base_url}/v1/crawl/{crawl_id}"

       while next_url:
           response = await self._http_client.get(next_url)
           data = response.json()
           all_pages.extend(data.get("data", []))
           next_url = data.get("next")
           if len(all_pages) >= 10000:
               break

       return CrawlStatus(..., pages=all_pages)
   ```

3. **ADD better logging** for crawl status responses:
   - Log `next` field presence
   - Log when pages_total != len(data)
   - Log Firecrawl job ID for correlation

**Priority:** Downgraded from CRITICAL to HIGH - needs investigation first

---

### Issue 11: No Extraction Quality Metrics (LOW - Monitoring)

**Problem:**
No metrics to detect extraction quality issues:
- No histogram for extraction duration
- No counter for deduplicated facts
- No gauge for entities per extraction

**Current metrics (`/metrics`):**
```
scristill_jobs_total
scristill_sources_total
scristill_extractions_total
scristill_entities_total
```

**Missing metrics:**
```
extraction_duration_seconds{project, source_group} (histogram)
facts_deduplicated_total{project} (counter)
entities_per_extraction{project} (histogram)
extraction_failures_total{error_type} (counter)
```

---

## Related Documents

- `docs/TODO_json_repair.md` - JSON repair implementation plan
- `docs/PLAN-crawl-improvements.md` - Broader crawl pipeline improvements

---

## Summary of All Issues

| # | Issue | Priority | Impact | Fix Complexity |
|---|-------|----------|--------|----------------|
| 1 | Duplicate entity links | Critical | 32 failed jobs | Low (~20 lines) |
| 2 | JSON parsing failures | High | Causes retries | Medium |
| 3 | Session rollback | Medium | Cascade failures | Low |
| 4 | Embedding not batched | High | 10-20x slower | Medium |
| 5 | Missing project | Low | Data loss | Investigation |
| 6 | No scrape/extraction DLQ | High | No failure recovery | Medium |
| 7 | No checkpoint/resume | High | Data loss on failure | Medium |
| 8 | Entity extraction silent fail | Medium | Data inconsistency | Low |
| 9 | No source→job traceability | Medium | Limited audit trail | Low (migration) |
| 10 | Incomplete error context | Medium | Hard to debug | Low |
| 11 | No quality metrics | Low | Blind to issues | Medium |
| 12 | Firecrawl returns 0 pages | High | Some crawls fail | Investigation + Low fix |
