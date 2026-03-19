# Extraction Pipeline Analysis - Data Not Persisting Issue

## Observed Symptoms

1. **LLM calls succeeding**: vLLM logs show 200 OK responses, entity extractions completing
2. **Sources stay "pending"**: All 224 sources remain in "pending" status, never move to "extracted"
3. **Zero database records**: `list_extractions()` and `list_entities()` return 0 results
4. **PostgreSQL healthy**: No errors in PG logs, checkpoints writing data successfully
5. **Job still running**: Extraction job `5203f4d8-8577-4c7a-9f53-50330df44a30` shows status="running"

## Code Flow Analysis

### 1. Job Scheduler (`scheduler.py:338-394`)

```python
# Line 343: Create new DB session
db = next(get_db())

# Line 364-374: Create pipeline service with repositories sharing same session
pipeline_service = ExtractionPipelineService(
    extraction_repo=ExtractionRepository(db),   # Same session
    source_repo=SourceRepository(db),            # Same session
    entity_extractor=EntityExtractor(..., entity_repo=EntityRepository(db)), # Same session
    ...
)

# Line 378-382: Create worker and process job
worker = ExtractionWorker(db=db, pipeline_service=pipeline_service)
await worker.process_job(job)

# Line 388: Close session in finally block
db.close()
```

**✓ Session management looks correct** - all repositories share the same session.

### 2. Extraction Worker (`worker.py:47-146`)

```python
# Line 61-63: Commit job status to "running"
job.status = "running"
job.started_at = datetime.now(UTC)
self.db.commit()  # ✓ First commit

# Line 91-101: Call pipeline service (NO commit here)
result = await self.pipeline_service.process_batch(...)

# Line 123-131: Commit job result
job.completed_at = datetime.now(UTC)
job.result = {...}
self.db.commit()  # ✓ Second commit - should commit all extractions/entities

# Line 135: Rollback on exception
except Exception as e:
    self.db.rollback()  # Rolls back everything
```

**✓ Commit strategy correct** - second commit (line 131) should persist all extractions/entities created by pipeline.

**⚠️ ISSUE**: If an exception occurs in `process_batch()`, line 135 rolls back ALL changes including extractions.

### 3. Pipeline Service (`pipeline.py:103-253`)

```python
async def process_source(self, source_id, project_id, profile_name):
    # Line 117-126: Fetch source
    source = await self._source_repo.get(source_id)
    if not source or not source.content:
        return PipelineResult(...)  # Early return, no extractions

    # Line 139-144: Extract facts via LLM
    result = await self._orchestrator.extract(...)

    # Line 150-177: Phase 1 - Create extractions
    for fact in result.facts:
        extraction = await self._extraction_repo.create(...)  # Uses flush(), not commit
        extractions_created += 1
        facts_to_embed.append(fact.fact)
        fact_extractions.append((fact, extraction))

    # Line 184-216: Phase 2 & 3 - Batch embed and upsert
    embeddings_succeeded = False
    if facts_to_embed:
        try:
            embeddings = await self._embedding_service.embed_batch(facts_to_embed)
            await self._qdrant_repo.upsert_batch(items)
            embeddings_succeeded = True  # ✓ Set to True on success
        except Exception as e:
            logger.error("batch_embedding_failed", ...)
            # embeddings_succeeded remains False

    # Line 219-227: Phase 4 - Skip entity extraction if embeddings failed
    if not embeddings_succeeded and fact_extractions:
        logger.warning("skipping_entity_extraction", ...)

    for fact, extraction in fact_extractions if embeddings_succeeded else []:
        # Only runs if embeddings_succeeded == True
        entities = await self._entity_extractor.extract(...)
        await self._extraction_repo.update_entities_extracted(extraction.id, True)

    # Line 244: Update source status
    await self._source_repo.update_status(source_id, "extracted")

    return PipelineResult(...)  # Contains extractions_created count
```

**🔴 CRITICAL ISSUES FOUND:**

#### Issue 1: `embeddings_succeeded` defaults to `False` when `facts_to_embed` is empty

```python
embeddings_succeeded = False  # Line 184
if facts_to_embed:  # Line 185
    try:
        embeddings_succeeded = True  # Only set if facts_to_embed not empty
```

**If no facts are extracted** (empty `facts_to_embed`), then:
- `embeddings_succeeded` stays `False`
- Entity extraction is skipped
- Source status IS STILL UPDATED to "extracted" (line 244)
- But no extractions were created!

#### Issue 2: Recent fix broke the entity extraction loop

The conditional expression at line 228 is syntactically valid but the logic is problematic:

```python
for fact, extraction in fact_extractions if embeddings_succeeded else []:
```

This iterates over an **empty list** if `embeddings_succeeded == False`, even when extractions were successfully created.

**Scenario causing the observed behavior:**

1. LLM extraction succeeds, facts are extracted
2. Extractions are created and flushed (line 164-172)
3. Embedding batch **fails or returns empty** → `embeddings_succeeded = False`
4. Entity extraction loop is **skipped entirely** (line 228)
5. Source status updated to "extracted" (line 244)
6. Worker commits (worker.py:131)
7. **Result**: Extractions with `entities_extracted=False`, no entities, source="extracted"

But wait - the symptoms show **0 extractions**, not extractions without entities. Let me check for another issue.

#### Issue 3: Exception in `process_batch()` causes total rollback

`pipeline.py:288-366` shows `process_batch()` calls `process_source()` for each source:

```python
results = await asyncio.gather(
    *[process_with_limit(sid) for sid in source_ids],
    return_exceptions=True,  # ← Exceptions are caught and returned as values
)
```

Exceptions don't propagate to the worker, they're caught and converted to `PipelineResult` with errors.

So rollback in `worker.py:135` wouldn't be triggered unless `process_batch()` itself raises.

### 4. Why Zero Extractions in Database?

**Hypothesis 1: Empty extraction results**

From the logs showing JSON parse failures:
```
warning context=extract_facts_direct error=Unterminated string starting at: line 340
warning model=Qwen3-30B-A3B-Instruct-4bit attempt=2 max_retries=5
```

If the LLM keeps returning malformed JSON and **all retries fail**, then:
- `result.facts` is empty
- No extractions are created (line 150-177 loop doesn't execute)
- `facts_to_embed` is empty
- `embeddings_succeeded` stays `False`
- Source status updated to "extracted" anyway (line 244)
- Worker sees `extractions_created=0` but doesn't fail

**Hypothesis 2: Transaction timeout or connection loss**

If the worker commits after a long processing time, the database connection might have timed out, causing a silent failure.

**Hypothesis 3: Database session auto-rollback**

If an exception occurs during flush() and is caught, SQLAlchemy might mark the session as invalid, causing subsequent operations to fail silently.

## Diagnostic Steps

### Check 1: Are extractions being created successfully?

Look for this log in pipeline container:
```
INF extraction=<extraction_id> event=extraction_created
```

If missing, extractions aren't being created.

### Check 2: Are there JSON parsing failures for ALL sources?

Count warnings like:
```
warning context=extract_facts_direct error=... event=json_parse_failed
```

If 224+ warnings (one per source), then LLM is failing on all sources.

### Check 3: Check job completion

The job should eventually complete with results. Check:
```bash
# Get job status
GET /api/v1/jobs/5203f4d8-8577-4c7a-9f53-50330df44a30
```

If job completes with `sources_processed=224, total_extractions=0`, then LLM extraction is failing.

## Likely Root Cause

**The LLM is producing malformed JSON that the repair utility cannot fix**, causing:
1. `result.facts` to be empty for all sources
2. No extractions created
3. No embeddings needed (empty list)
4. Sources marked "extracted" with zero actual extractions
5. Job continues running because empty results aren't treated as errors

## Recommended Fixes

### Fix 1: Don't mark source as "extracted" if no extractions created

```python
# Only update status if extractions were created
if extractions_created > 0:
    await self._source_repo.update_status(source_id, "extracted")
else:
    await self._source_repo.update_status(source_id, "failed")
    errors.append("No extractions created from source")
```

### Fix 2: Set `embeddings_succeeded = True` when `facts_to_embed` is empty

```python
embeddings_succeeded = True  # Default to True
if facts_to_embed:
    try:
        embeddings = await self._embedding_service.embed_batch(facts_to_embed)
        await self._qdrant_repo.upsert_batch(items)
        # embeddings_succeeded stays True
    except Exception as e:
        embeddings_succeeded = False  # Only set False on actual failure
```

### Fix 3: Improve JSON parsing resilience

The JSON repair utility may need more strategies for handling deeply malformed responses.

### Fix 4: Add extraction validation

```python
if extractions_created == 0 and not errors:
    errors.append("LLM extraction produced no facts")
    logger.warning("zero_extractions", source_id=str(source_id))
```
