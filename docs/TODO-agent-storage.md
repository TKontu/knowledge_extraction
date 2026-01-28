# TODO: Agent Storage - Async Improvements & Documentation

**Agent ID:** `agent-storage`
**Branch:** `feat/storage-improvements`
**Priority:** Low-Medium

---

## Context

The storage layer has several minor issues identified in the architecture review:

1. Qdrant repository uses sync client in async context
2. Database pool sizing may need adjustment
3. Metrics collector uses PostgreSQL-specific SQL
4. Missing tests for new methods added 2026-01-28

**Key files:**
- `src/services/storage/qdrant/repository.py` - Sync Qdrant operations
- `src/database.py` - Pool configuration
- `src/services/metrics/collector.py` - Job duration metrics
- `src/services/storage/repositories/extraction.py` - New methods

---

## Objective

Improve storage layer robustness through async wrappers, better configuration, and test coverage.

---

## Tasks

### 1. Wrap Qdrant Sync Operations in Executor

**File:** `src/services/storage/qdrant/repository.py`

The Qdrant client is synchronous but methods are declared async. Wrap sync calls properly:

```python
import asyncio
from functools import partial

class QdrantRepository:
    async def upsert_batch(self, items: list[EmbeddingItem]) -> list[str]:
        """Batch upsert - runs sync client in executor."""
        if not items:
            return []

        points = [...]  # Build points list

        # Run sync operation in thread pool
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,  # Default executor
            partial(
                self.client.upsert,
                collection_name=self.collection_name,
                points=points,
            ),
        )

        return [str(item.extraction_id) for item in items]
```

Apply to all methods: `upsert()`, `upsert_batch()`, `search()`, `delete()`, `delete_batch()`.

### 2. Add SQLite Fallback for Job Duration Metrics

**File:** `src/services/metrics/collector.py`

The `_job_duration_by_type()` method uses PostgreSQL-specific `extract("epoch", ...)`. Add SQLite fallback:

```python
def _job_duration_by_type(self) -> dict[str, JobDurationStats]:
    """Calculate job duration statistics by type."""
    try:
        dialect_name = self._db.bind.dialect.name
    except AttributeError:
        dialect_name = "sqlite"

    if dialect_name == "postgresql":
        # PostgreSQL: Use extract epoch
        duration_expr = (
            extract("epoch", Job.completed_at) - extract("epoch", Job.started_at)
        )
    else:
        # SQLite: Use julianday
        duration_expr = (
            (func.julianday(Job.completed_at) - func.julianday(Job.started_at)) * 86400
        )

    # ... rest of query using duration_expr
```

### 3. Make Database Pool Size Configurable

**File:** `src/config.py`

Add settings:

```python
# Database pool settings
db_pool_size: int = Field(default=5, description="Database connection pool size")
db_max_overflow: int = Field(default=10, description="Max overflow connections")
db_pool_timeout: int = Field(default=30, description="Pool connection timeout in seconds")
```

**File:** `src/database.py`

Use settings:

```python
from config import settings

engine = create_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
)
```

### 4. Add Missing Tests

**File:** `tests/test_extraction_repository_batch.py` (new file)

Tests for new repository methods:

```python
class TestUpdateEmbeddingIdsBatch:
    async def test_updates_single_extraction(self, extraction_repo, test_extraction):
        """Single extraction gets embedding_id set."""
        ...

    async def test_updates_multiple_extractions(self, extraction_repo, test_extractions):
        """Multiple extractions updated in single query."""
        ...

    async def test_empty_list_returns_zero(self, extraction_repo):
        """Empty list returns 0 without error."""
        ...

    async def test_nonexistent_ids_ignored(self, extraction_repo):
        """IDs not in database are safely ignored."""
        ...
```

**File:** `tests/test_metrics_job_duration.py` (new file)

Tests for job duration metrics:

```python
class TestJobDurationMetrics:
    def test_calculates_avg_duration(self, db_session, completed_jobs):
        """Average duration calculated correctly."""
        ...

    def test_handles_no_completed_jobs(self, db_session):
        """Returns empty dict when no completed jobs."""
        ...

    def test_groups_by_job_type(self, db_session, mixed_jobs):
        """Separate stats for each job type."""
        ...

    def test_excludes_jobs_without_timestamps(self, db_session):
        """Jobs with NULL started_at/completed_at excluded."""
        ...
```

---

## Tests Summary

New test files to create:
1. `tests/test_extraction_repository_batch.py` - Batch embedding_id updates
2. `tests/test_metrics_job_duration.py` - Job duration metrics
3. `tests/test_qdrant_async.py` - Async wrapper tests

---

## Constraints

- Do NOT change Qdrant client initialization (keep sync client)
- Do NOT change database schema
- Do NOT change the Qdrant collection configuration
- Keep all existing tests passing
- Pool settings should have sensible defaults

---

## Acceptance Criteria

- [ ] Qdrant sync operations wrapped in `run_in_executor`
- [ ] Metrics collector handles both PostgreSQL and SQLite
- [ ] Database pool size configurable via environment
- [ ] Tests exist for `update_embedding_ids_batch()`
- [ ] Tests exist for `_job_duration_by_type()`
- [ ] All new tests pass
- [ ] All existing tests pass
- [ ] `ruff check` passes

---

## Verification

```bash
# Run new tests
pytest tests/test_extraction_repository_batch.py -v
pytest tests/test_metrics_job_duration.py -v
pytest tests/test_qdrant_async.py -v

# Run all storage tests
pytest tests/test_*repository*.py -v
pytest tests/test_qdrant*.py -v

# Lint
ruff check src/services/storage/ src/database.py src/services/metrics/

# Verify pool settings work
DATABASE_POOL_SIZE=10 python -c "from src.config import settings; print(settings.db_pool_size)"
```
