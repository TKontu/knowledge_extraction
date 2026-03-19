# Implementation Plan: Crawl Pipeline Improvements

**Date**: 2026-01-21
**Based On**: CRAWL_PIPELINE_REVIEW.md
**Status**: Planning

---

## Overview

This document outlines the implementation plan for addressing 6 identified issues in the crawl pipeline, ranging from critical error handling improvements to performance observability enhancements.

---

## Issue Prioritization

### Priority 1: Foundation (Error Handling & Data Safety)
- **I1**: Misleading error messages
- **I2**: Missing batch commits

**Rationale**: These directly impact reliability and debuggability. Without good error messages, future bugs are hard to diagnose. Without batch commits, large crawls risk complete data loss.

### Priority 2: Correctness (Filtering & Type Safety)
- **I3**: HTTP error filtering
- **M2**: UUID type inconsistency

**Rationale**: I3 affects data quality (what gets crawled). M2 is a type safety issue that could cause subtle bugs.

### Priority 3: Observability (Metrics & Detection)
- **M3**: Performance metrics
- **M1**: Created/updated detection

**Rationale**: Nice-to-haves that improve monitoring and code quality but don't block functionality.

---

## Implementation Approach

### Strategy: Incremental Releases

**Option A: Single Big PR (NOT RECOMMENDED)**
- ❌ High risk of introducing new bugs
- ❌ Hard to review
- ❌ Difficult to rollback specific changes

**Option B: Phased Releases (RECOMMENDED)**
```
Phase 1: v1.2.2 - Error Handling Foundation (I1 + M2)
  ↓
Phase 2: v1.2.3 - Data Safety (I2)
  ↓
Phase 3: v1.3.0 - Smart Filtering & Metrics (I3 + M3 + M1)
```

**Rationale**:
- Phase 1 is low-risk, high-value (better errors, type safety)
- Phase 2 requires careful testing (transaction boundaries)
- Phase 3 bundles observability improvements

---

## Phase 1: Error Handling Foundation (v1.2.2)

**Target**: Week 1
**Risk**: LOW
**Dependencies**: None

### I1: Improve Error Messages

**Files to Modify**:
- `src/services/scraper/crawl_worker.py`
- `src/services/scraper/worker.py`
- `src/services/extraction/worker.py`

**Changes**:

```python
# BEFORE (crawl_worker.py:135-142)
except Exception as e:
    self.db.rollback()
    job.status = "failed"
    job.error = str(e)
    job.completed_at = datetime.now(UTC)
    self.db.commit()
    logger.error("crawl_error", job_id=str(job.id), error=str(e))

# AFTER
except Exception as e:
    self.db.rollback()

    # Format error with type and message
    error_msg = f"{type(e).__name__}: {str(e)}"
    job.status = "failed"
    job.error = error_msg
    job.completed_at = datetime.now(UTC)
    self.db.commit()

    # Log with full context and stack trace
    logger.error(
        "crawl_error",
        job_id=str(job.id),
        error=str(e),
        error_type=type(e).__name__,
        error_module=type(e).__module__,
        exc_info=True  # Include stack trace
    )
```

**Testing**:
- Unit test: Inject exceptions, verify error format
- Integration test: Trigger actual failures, check logs

**Rollout**: Safe to deploy independently

---

### M2: UUID Type Consistency

**Files to Modify**:
- `src/services/scraper/crawl_worker.py:146`
- `src/services/scraper/worker.py` (similar pattern)
- `src/services/extraction/worker.py` (similar pattern)

**Changes**:

```python
# BEFORE (crawl_worker.py:144-147)
async def _store_pages(self, job: Job, pages: list[dict]) -> int:
    """Store crawled pages as Source records."""
    project_id = job.payload["project_id"]  # ← String from JSON
    company = job.payload["company"]

# AFTER
from uuid import UUID

async def _store_pages(self, job: Job, pages: list[dict]) -> int:
    """Store crawled pages as Source records."""
    # Explicit UUID conversion with validation
    try:
        project_id = UUID(job.payload["project_id"])
    except (ValueError, KeyError) as e:
        logger.error("invalid_project_id", job_id=str(job.id), error=str(e))
        raise ValueError(f"Invalid project_id in job payload: {e}") from e

    company = job.payload["company"]
```

**Benefits**:
- Type safety at runtime
- Early failure if payload is corrupted
- Clear error messages

**Testing**:
- Unit test: Valid UUID, invalid UUID, missing key
- Type checking: `mypy src/services/scraper/`

**Rollout**: Safe to deploy independently

---

### Phase 1 Acceptance Criteria

- [ ] All exception handlers include error type
- [ ] All logs with errors include `exc_info=True`
- [ ] Job.error format: `"{ErrorType}: {message}"`
- [ ] UUID conversion explicit in all workers
- [ ] Tests pass: `pytest tests/test_*_worker.py -v`
- [ ] Type checking passes: `mypy src/services/`

---

## Phase 2: Data Safety with Batch Commits (v1.2.3)

**Target**: Week 2
**Risk**: MEDIUM (transaction boundaries are tricky)
**Dependencies**: Phase 1

### I2: Implement Batch Commits

**Files to Modify**:
- `src/services/scraper/crawl_worker.py:144-200`
- `src/config.py` (add config)

**Design Decisions**:

#### Decision 1: Batch Size
**Options**:
- A) Fixed size (e.g., 10 pages)
- B) Time-based (e.g., every 30s)
- C) Adaptive (based on page size)

**Recommendation**: **Option A (Fixed size)**
- Simpler to implement
- Predictable behavior
- Easy to tune

**Config**:
```python
# src/config.py
crawl_batch_commit_size: int = Field(
    default=10,
    ge=1,
    le=100,
    description="Commit sources every N pages during crawl"
)
```

#### Decision 2: Error Handling
**Question**: If page 15 fails, what happens to pages 11-14?

**Option A: Rollback batch, skip failed page, retry batch**
```python
for batch in batches:
    try:
        for page in batch:
            source, created = await self.upsert(page)
        self.db.commit()
    except Exception as e:
        self.db.rollback()
        # Retry batch one-by-one to isolate failure
        for page in batch:
            try:
                source, created = await self.upsert(page)
                self.db.commit()
            except Exception as e2:
                logger.error("page_failed", page=page, error=str(e2))
```

**Option B: Commit what worked, continue**
```python
for i, page in enumerate(pages):
    try:
        source, created = await self.upsert(page)
        if created:
            sources_created += 1

        # Commit every N pages
        if (i + 1) % batch_size == 0:
            self.db.commit()
    except Exception as e:
        logger.error("page_failed", page=page, error=str(e))
        self.db.rollback()
        # Continue with next page
```

**Recommendation**: **Option B (Continue on failure)**
- More resilient to individual page failures
- Better partial progress
- Simpler code

**Implementation**:

```python
async def _store_pages(self, job: Job, pages: list[dict]) -> int:
    """Store crawled pages as Source records with batch commits."""
    project_id = UUID(job.payload["project_id"])
    company = job.payload["company"]
    batch_size = settings.crawl_batch_commit_size
    sources_created = 0
    pages_failed = 0

    for i, page in enumerate(pages):
        try:
            metadata = page.get("metadata", {})
            markdown = page.get("markdown", "")
            url = metadata.get("url") or metadata.get("sourceURL", "")

            if not markdown or not url:
                logger.warning(
                    "crawl_page_skipped",
                    job_id=str(job.id),
                    url=url or "missing",
                    reason="missing_markdown" if not markdown else "missing_url",
                )
                continue

            # Filter HTTP errors (400+)
            status_code = metadata.get("statusCode")
            if status_code and status_code >= 400:
                logger.warning(
                    "page_http_error_skipped",
                    job_id=str(job.id),
                    url=url,
                    status_code=status_code,
                )
                continue

            domain = urlparse(url).netloc

            # Upsert source
            source, created = await self.source_repo.upsert(
                project_id=project_id,
                uri=url,
                source_group=company,
                source_type="web",
                title=metadata.get("title", ""),
                content=markdown,
                meta_data={
                    "domain": domain,
                    "http_status": status_code,
                    **metadata
                },
                status="pending",
            )

            if created:
                sources_created += 1

            # Batch commit every N pages
            if (i + 1) % batch_size == 0:
                self.db.commit()
                logger.debug(
                    "batch_committed",
                    job_id=str(job.id),
                    batch_num=(i + 1) // batch_size,
                    sources_in_batch=batch_size
                )

        except Exception as e:
            pages_failed += 1
            logger.error(
                "page_store_failed",
                job_id=str(job.id),
                page_index=i,
                url=page.get("metadata", {}).get("url", "unknown"),
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True
            )
            # Rollback failed page, continue with next
            self.db.rollback()

    # Commit any remaining pages
    try:
        self.db.commit()
        logger.debug(
            "final_batch_committed",
            job_id=str(job.id),
            remaining_pages=len(pages) % batch_size
        )
    except Exception as e:
        logger.error("final_batch_failed", job_id=str(job.id), error=str(e))
        self.db.rollback()

    logger.info(
        "pages_storage_complete",
        job_id=str(job.id),
        sources_created=sources_created,
        pages_failed=pages_failed,
        total_pages=len(pages)
    )

    return sources_created
```

**Testing**:
- Unit test: Mock upsert to fail on specific pages
- Integration test: Crawl with injected failures
- Stress test: 100+ page crawl
- Verify: Partial progress preserved on crash

**Rollback Plan**:
- Keep `crawl_batch_commit_size` configurable
- Default to `len(pages)` (single commit) if issues arise
- Monitor `pages_failed` metric

---

### Phase 2 Acceptance Criteria

- [ ] Config: `CRAWL_BATCH_COMMIT_SIZE` (default: 10)
- [ ] Commits happen every N pages
- [ ] Individual page failures don't stop crawl
- [ ] Logs show batch progress
- [ ] Tests verify partial progress on failure
- [ ] Performance: No significant slowdown (<5%)

---

## Phase 3: Smart Filtering & Observability (v1.3.0)

**Target**: Week 3-4
**Risk**: LOW-MEDIUM
**Dependencies**: Phase 1, 2

### I3: Nuanced HTTP Error Filtering

**Files to Modify**:
- `src/services/scraper/crawl_worker.py:164-174`
- `src/config.py` (add skip list)

**Current Behavior**:
```python
if status_code and status_code >= 400:
    # Skip ALL 4xx and 5xx
    continue
```

**Proposed Behavior**:

```python
# src/config.py
crawl_skip_status_codes: list[int] = Field(
    default=[401, 403, 429],
    description="HTTP status codes to skip during crawl (don't store)"
)

crawl_warn_status_codes: list[int] = Field(
    default=[404, 410, 500, 502, 503],
    description="HTTP status codes to warn about but still store"
)
```

```python
# crawl_worker.py
status_code = metadata.get("statusCode")

if status_code:
    # Skip auth/rate-limit errors (expected failures)
    if status_code in settings.crawl_skip_status_codes:
        logger.warning(
            "page_skipped_by_status",
            job_id=str(job.id),
            url=url,
            status_code=status_code,
            reason="Configured skip code"
        )
        continue

    # Log warnings for problematic codes but store anyway
    if status_code in settings.crawl_warn_status_codes:
        logger.warning(
            "page_problematic_status",
            job_id=str(job.id),
            url=url,
            status_code=status_code,
            reason="May have incomplete content"
        )
        # Add flag to metadata for extraction layer
        metadata["_warning"] = f"HTTP {status_code}"

# Continue to store...
```

**Rationale**:
- 401/403: Auth required → skip (no content accessible)
- 429: Rate limited → skip (will be retried by Firecrawl)
- 404: Not found → store (Firecrawl may have cached content)
- 5xx: Server error → store (may be transient, content might be partial)

**Testing**:
- Mock crawl responses with various status codes
- Verify skip behavior
- Verify warning flags in metadata

---

### M3: Performance Metrics

**Files to Modify**:
- `src/services/scraper/crawl_worker.py:100-121`

**Changes**:

```python
if status.status == "completed":
    # Timing metrics
    start_time = job.started_at
    end_time = datetime.now(UTC)
    duration = (end_time - start_time).total_seconds()

    # Step 3: Store all pages as sources
    sources_created = await self._store_pages(job, status.pages)

    job.status = "completed"
    job.completed_at = end_time
    job.result = {
        "pages_total": status.total,
        "pages_completed": status.completed,
        "sources_created": sources_created,
        # New metrics
        "duration_seconds": round(duration, 2),
        "pages_per_second": round(status.completed / duration, 3) if duration > 0 else 0,
        "avg_seconds_per_page": round(duration / status.completed, 2) if status.completed > 0 else 0,
    }
    self.db.commit()

    logger.info(
        "crawl_completed",
        job_id=str(job.id),
        sources_created=sources_created,
        duration_seconds=duration,
        pages_per_second=status.completed / duration if duration > 0 else 0,
        throughput_category="fast" if duration / status.completed < 3 else "slow"
    )
```

**Benefits**:
- Identify slow crawls
- Detect performance regressions
- Capacity planning (estimate time for large crawls)

---

### M1: Robust Created/Updated Detection

**Files to Modify**:
- `src/services/storage/repositories/source.py:262-287`

**Current (Fragile)**:
```python
created = (datetime.now(UTC) - source.created_at).total_seconds() < 1
```

**Option A: Use PostgreSQL xmax**
```python
# Add to RETURNING clause
stmt = stmt.returning(Source.id, text("(xmax = 0) as was_inserted"))
result = self._session.execute(stmt)
row = result.one()
source_id = row[0]
created = row[1]  # True if INSERT, False if UPDATE
```

**Option B: Add "last_modified" column**
```python
# Migration: Add updated_at column to sources table
# Compare created_at == updated_at to detect new records
```

**Option C: Use separate queries (cleaner but slower)**
```python
# Check if exists first
existing = await self.get_by_uri(project_id, uri)
if existing:
    # Update
    created = False
else:
    # Insert
    created = True
```

**Recommendation**: **Option A (xmax)**
- No schema change
- Fast (single query)
- Reliable

**Trade-off**: PostgreSQL-specific (not portable to MySQL/SQLite)

**Implementation**:
```python
async def upsert(...) -> tuple[Source, bool]:
    values = {...}

    stmt = pg_insert(Source).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_sources_project_uri",
        set_={...}
    ).returning(Source.id, text("(xmax = 0)::boolean"))

    result = self._session.execute(stmt)
    row = result.one()
    source_id = row[0]
    created = row[1]  # True if new, False if updated

    source = await self.get(source_id)
    self._session.flush()
    return source, created
```

---

### Phase 3 Acceptance Criteria

- [ ] HTTP status filtering is configurable
- [ ] 404/5xx stored with warning metadata
- [ ] Performance metrics in job.result
- [ ] Logs include throughput metrics
- [ ] Created/updated detection uses xmax
- [ ] All tests pass

---

## Testing Strategy

### Unit Tests
```bash
# Test individual functions
pytest tests/test_crawl_worker.py -v -k test_store_pages_batch_commit
pytest tests/test_crawl_worker.py -v -k test_error_formatting
pytest tests/test_source_repository.py -v -k test_upsert_created_detection
```

### Integration Tests
```bash
# Test full pipeline
pytest tests/integration/test_crawl_pipeline.py -v

# Test with real Firecrawl (local)
pytest tests/integration/test_crawl_with_firecrawl.py -v --run-integration
```

### Regression Tests
- Crawl scrapethissite.com with various depths
- Verify sources_created matches pages_completed
- Check logs for proper error formatting

---

## Rollout Plan

### v1.2.2 (Phase 1)
```bash
# 1. Deploy to staging
./build-and-push.sh ghcr.io/tkontu v1.2.2-rc1
# Test on staging
# 2. Deploy to production
./build-and-push.sh ghcr.io/tkontu v1.2.2
docker compose -f docker-compose.prod.yml up -d pipeline
```

### v1.2.3 (Phase 2)
```bash
# 1. Deploy with conservative batch size
export CRAWL_BATCH_COMMIT_SIZE=20  # Large batches first
docker compose -f docker-compose.prod.yml up -d pipeline

# 2. Monitor for 24 hours
# 3. Reduce to 10 if stable
```

### v1.3.0 (Phase 3)
```bash
# 1. Deploy with default skip codes
# 2. Monitor sources_created metric
# 3. Adjust skip/warn lists based on data quality
```

---

## Monitoring & Alerts

### Key Metrics to Track

**Error Rate**:
```
rate(crawl_error_total[5m]) > 0.1  # Alert if >10% fail
```

**Batch Commit Performance**:
```
histogram_quantile(0.95, crawl_batch_commit_duration_seconds) > 5
```

**Data Loss Detection**:
```
(pages_completed - sources_created) / pages_completed > 0.2  # Alert if >20% lost
```

**Throughput**:
```
avg(pages_per_second) < 0.1  # Alert if <0.1 pages/sec
```

---

## Risk Mitigation

### Phase 1 Risks
- **Risk**: Error formatting breaks log parsing
- **Mitigation**: Structured logging (JSON), validate format in tests

### Phase 2 Risks
- **Risk**: Batch commits cause deadlocks
- **Mitigation**: Keep batch size configurable, monitor lock wait times

- **Risk**: Partial commits complicate retry logic
- **Mitigation**: Job is idempotent (upsert handles duplicates)

### Phase 3 Risks
- **Risk**: Wrong status codes skipped, data loss
- **Mitigation**: Make skip list configurable, start conservative

- **Risk**: xmax detection fails on replicas
- **Mitigation**: Repository only runs on primary, document limitation

---

## Decision Log

| Decision | Options Considered | Chosen | Rationale |
|----------|-------------------|--------|-----------|
| Release Strategy | Single PR vs Phased | Phased | Lower risk, easier review |
| Batch Size | Fixed, Time-based, Adaptive | Fixed (10) | Simplicity, tunability |
| Batch Error Handling | Rollback vs Continue | Continue | Resilience, partial progress |
| HTTP Filter | Configurable vs Hardcoded | Configurable | Flexibility per deployment |
| Created Detection | Timing, xmax, Separate query | xmax | Fast, reliable, no schema change |

---

## Timeline Estimate

| Phase | Tasks | Estimated Effort | Target Completion |
|-------|-------|------------------|-------------------|
| Phase 1 | I1 + M2 | 4 hours dev + 2 hours test | Week 1 |
| Phase 2 | I2 | 8 hours dev + 4 hours test | Week 2 |
| Phase 3 | I3 + M3 + M1 | 6 hours dev + 4 hours test | Week 3 |
| **Total** | | **~28 hours** | **3 weeks** |

---

## Success Criteria

After all phases complete:

1. **Reliability**: Crawl failure rate < 5%
2. **Debuggability**: All errors have stack traces and context
3. **Data Safety**: Partial progress preserved on crash (>80% of pages)
4. **Observability**: Dashboard shows throughput, error types, status codes
5. **Type Safety**: `mypy` passes with no errors
6. **Performance**: No regression (within 5% of baseline)

---

## Open Questions

1. **Q**: Should batch size vary by domain (slow domains get smaller batches)?
   **A**: Not in v1.2.3, consider for v1.4.0

2. **Q**: Should we retry failed pages at the end of crawl?
   **A**: No, Firecrawl handles retries. Log failures for investigation.

3. **Q**: What's the acceptable sources_created vs pages_completed ratio?
   **A**: >80% ideal, alert if <70%

4. **Q**: Should we expose metrics via Prometheus /metrics endpoint?
   **A**: Yes, but separate task (not in this plan)

---

## References

- Original Issue: `CRAWL_PIPELINE_REVIEW.md`
- Related: LLM queue feature (backpressure handling)
- PostgreSQL Docs: [ON CONFLICT](https://www.postgresql.org/docs/current/sql-insert.html#SQL-ON-CONFLICT)
- SQLAlchemy Docs: [Insert ON CONFLICT](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html#insert-on-conflict-upsert)
