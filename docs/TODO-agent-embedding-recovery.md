# TODO: Agent Embedding Recovery - Orphan Detection & Retry

**Agent ID:** `agent-embedding-recovery`
**Branch:** `feat/embedding-recovery`
**Priority:** High

---

## Context

The extraction pipeline writes to PostgreSQL first, then Qdrant. If Qdrant fails, extractions exist in PostgreSQL but aren't searchable. As of 2026-01-28, we now track this via `embedding_id`:

- `embedding_id IS NOT NULL` → Extraction has embedding in Qdrant
- `embedding_id IS NULL` → Extraction failed to get embedding (orphaned)

**Key files:**
- `src/services/extraction/pipeline.py` - Sets `embedding_id` after Qdrant upsert
- `src/services/storage/repositories/extraction.py` - `update_embedding_ids_batch()` method
- `src/orm_models.py` - `Extraction.embedding_id` field

---

## Objective

Create a background service that finds orphaned extractions (`embedding_id IS NULL`) and retries the embedding/Qdrant upsert operation.

---

## Tasks

### 1. Create Embedding Recovery Service

**File:** `src/services/extraction/embedding_recovery.py` (new file)

```python
class EmbeddingRecoveryService:
    """Recovers orphaned extractions by retrying embedding generation."""

    def __init__(
        self,
        db: Session,
        embedding_service: EmbeddingService,
        qdrant_repo: QdrantRepository,
        extraction_repo: ExtractionRepository,
        batch_size: int = 50,
    ):
        ...

    async def find_orphaned_extractions(
        self,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[Extraction]:
        """Find extractions with embedding_id IS NULL."""
        ...

    async def recover_batch(
        self,
        extractions: list[Extraction],
    ) -> RecoveryResult:
        """Retry embedding for a batch of extractions."""
        ...

    async def run_recovery(
        self,
        project_id: UUID | None = None,
        max_batches: int = 10,
    ) -> RecoverySummary:
        """Run full recovery process."""
        ...
```

### 2. Add Repository Method for Finding Orphans

**File:** `src/services/storage/repositories/extraction.py`

Add method:

```python
async def find_orphaned(
    self,
    project_id: UUID | None = None,
    limit: int = 100,
) -> list[Extraction]:
    """Find extractions without embeddings (embedding_id IS NULL)."""
    query = select(Extraction).where(Extraction.embedding_id.is_(None))
    if project_id:
        query = query.where(Extraction.project_id == project_id)
    query = query.order_by(Extraction.created_at.asc()).limit(limit)
    result = self._session.execute(query)
    return list(result.scalars().all())
```

### 3. Add API Endpoint for Manual Recovery

**File:** `src/api/v1/extraction.py`

Add endpoint:

```python
@router.post("/projects/{project_id}/extractions/recover")
async def recover_orphaned_extractions(
    project_id: UUID,
    max_batches: int = Query(default=10, le=100),
    db: Session = Depends(get_db),
) -> RecoverySummary:
    """Manually trigger recovery of orphaned extractions."""
    ...
```

### 4. Add Scheduled Recovery Task (Optional)

**File:** `src/services/scraper/scheduler.py`

Add optional periodic recovery task that runs every hour:

```python
async def _recovery_task(self) -> None:
    """Periodic task to recover orphaned extractions."""
    while not self._shutdown_event.is_set():
        try:
            await self._run_embedding_recovery()
        except Exception as e:
            logger.error("recovery_task_error", error=str(e))
        await asyncio.sleep(3600)  # Run hourly
```

Make this configurable via settings (`embedding_recovery_enabled`, `embedding_recovery_interval`).

### 5. Add Metrics

**File:** `src/services/metrics/collector.py`

Add metric:

```python
orphaned_extractions_total: int  # Count of extractions with embedding_id IS NULL
```

---

## Tests

**File:** `tests/test_embedding_recovery.py` (new file)

### Test cases:

1. `test_find_orphaned_extractions_returns_null_embedding_id` - Finds correct extractions
2. `test_find_orphaned_excludes_embedded` - Doesn't return extractions with embedding_id set
3. `test_recover_batch_creates_embeddings` - Successfully embeds and updates Qdrant
4. `test_recover_batch_updates_embedding_id` - Sets embedding_id after success
5. `test_recover_batch_handles_partial_failure` - Some succeed, some fail
6. `test_recovery_respects_project_filter` - Only recovers for specified project
7. `test_recovery_respects_limit` - Doesn't exceed batch size

---

## Constraints

- Do NOT modify the main extraction pipeline logic
- Do NOT delete any extractions - only add missing embeddings
- Recovery should be idempotent (safe to run multiple times)
- Batch size should be configurable (default 50)
- Add proper error handling - don't fail entire batch if one extraction fails

---

## Acceptance Criteria

- [ ] Can find orphaned extractions via repository method
- [ ] Can recover orphaned extractions via service
- [ ] API endpoint allows manual recovery trigger
- [ ] Recovery is idempotent (running twice doesn't cause issues)
- [ ] Metrics show count of orphaned extractions
- [ ] All tests pass
- [ ] `ruff check` passes

---

## Verification

```bash
# Run tests
pytest tests/test_embedding_recovery.py -v

# Lint
ruff check src/services/extraction/embedding_recovery.py src/api/v1/extraction.py

# Check for orphaned extractions (manual)
# SELECT COUNT(*) FROM extractions WHERE embedding_id IS NULL;
```

---

## Data Model Reference

```python
# Extraction model (relevant fields)
class Extraction(Base):
    id: UUID
    project_id: UUID
    source_id: UUID
    data: dict  # Contains "fact_text" for embedding
    embedding_id: str | None  # NULL = orphaned, set = has Qdrant embedding
```
