# Pipeline Review: Significant Issues (Verified)

**Date:** 2026-03-06
**Scope:** Extraction pipeline end-to-end — orchestration, LLM extraction, grounding, consolidation, embedding, API layer
**Criteria:** Real production issues only. Each issue verified against actual code.

---

## CRITICAL

### 1. Consolidation endpoint has no error handling

**Location:** `src/api/v1/projects.py:303-339`
**Verified:** YES — real issue.

The `POST /{project_id}/consolidate` endpoint wraps no try/except around the service call. If `ConsolidationService.consolidate_project()` or `consolidate_source_group()` throws (DB constraint violation, schema mismatch), the exception propagates to FastAPI's default handler, session is left dirty. SQLAlchemy's `Session.close()` does rollback implicitly, so no data corruption — but the user gets a raw 500 with stack trace instead of a meaningful error.

Note: the `{"error": "project_not_found"}` path in `consolidation_service.py:113` is **unreachable** — the endpoint checks `repo.get(project_id)` first at line 319 and raises HTTPException 404. The service's own check is dead code.

**Impact:** Raw 500 errors on consolidation failures. No structured error response.

---

### 2. Consolidation loop continues on dirty session state after exception

**Location:** `src/services/extraction/consolidation_service.py:129-139`
**Verified:** YES — real issue, but narrower than originally stated.

The loop catches exceptions per source group and continues. Each `consolidate_source_group()` calls `_upsert_record()` which flushes (line 212). If one group's flush fails, SQLAlchemy automatically rolls back the failed flush statement. But the session state after a failed flush is **indeterminate** — SQLAlchemy docs say "the session should be discarded" after a flush failure. Continuing to use it for subsequent source groups is undefined behavior.

The partial-commit concern is a **false positive** — nothing commits until the API endpoint calls `db.commit()`, and if any exception escapes the loop, commit is never reached. Implicit rollback-on-close handles cleanup correctly.

**Impact:** Undefined session state after flush failure. Subsequent source groups may silently produce wrong results or throw confusing secondary errors.

---

### ~~3. chunk_extractions shared list + flush failure → null IDs to embedder~~

**REMOVED — FALSE POSITIVE.**

This is asyncio (single-threaded event loop), not multi-threaded. `asyncio.gather()` interleaves at await points but `list.extend()` is synchronous and atomic. More importantly: if `self._db.flush()` at line 364 throws, the exception propagates immediately — the embedding code at lines 367-377 **never executes**. There is no path where null-ID objects reach the embedder.

---

### ~~4. Domain dedup loads all source content into memory simultaneously~~

**REMOVED — OVERSTATED.**

Verified the code loads all pages into memory, and `del pages` at line 379 frees one copy before section pass. Peak memory: ~50MB for 500 pages at 50KB each. Production's largest domain has ~500 pages. This is well within worker memory limits and has been running successfully in production (12K sources processed). Not a real OOM risk at current scale.

---

## HIGH

### 3. Extraction failures are invisible — job shows COMPLETED

**Location:** `src/services/extraction/pipeline.py:85-97, 308-316`
**Verified:** YES — real issue.

When `extract_source()` returns `[]` (no content at line 86, no field groups at line 91), `extract_with_limit()` returns `(0, True)` — success=True. The job result includes total extraction count but no failed/skipped count. No way to distinguish "source had no relevant data" from "source had no content."

Same in `schema_orchestrator.py:274-281`: chunk exceptions caught, return None, filtered out at line 298. If 7/8 chunks fail, merge uses 1 chunk's data. The log at line 300-305 does show `successful=1, total=8`, but this is only in logs, not in the job result.

**Impact:** Job results don't reflect partial failures. Must dig through logs to find issues.

---

### 4. Embedding failures not recorded in job result

**Location:** `src/services/extraction/pipeline.py:366-377`, `embedding_pipeline.py:118-124`
**Verified:** YES — real issue.

`embed_and_upsert` catches all exceptions and returns `EmbeddingResult(0, [str(e)])`. Pipeline logs the error but continues. Job result doesn't include embedding success/failure counts. When all texts are empty (line 88-94), returns `EmbeddingResult(0, [])` — zero errors, indistinguishable from "nothing to embed."

**Impact:** No visibility into embedding coverage without log analysis. Job appears successful with 0% embedding rate.

---

### 5. Majority vote in chunk merge biases booleans toward False

**Location:** `src/services/extraction/schema_orchestrator.py:390-392`
**Verified:** YES — real issue, with nuance.

```python
true_count = sum(1 for v in values if v is True)
merged[field.name] = true_count > len(values) / 2
```

`values` IS pre-filtered to non-None (line 380-382), so the None concern is a false positive. However, the real issue remains: LLMs tend to return explicit `false` for boolean fields when no evidence is found in a chunk, rather than returning null. With 3 chunks where only chunk 1 has evidence (True) and chunks 2-3 default to False, the merge produces False.

The downstream trials confirmed this pattern: "Majority vote FAILS for boolean facts (48%)." The consolidation layer uses `any_true`, but chunk merge still uses `majority_vote` — the damage is done before consolidation sees it.

**Impact:** Boolean fields biased toward False during chunk merge. Consolidation receives already-corrupted data.

---

### 6. Grounding scores can use quote from different chunk than value

**Location:** `src/services/extraction/schema_orchestrator.py:211-215, 424-449`
**Verified:** PARTIALLY — narrower than stated.

Quote merge (lines 433-449) picks quote from highest-confidence chunk per field. The default merge strategy `highest_confidence` (line 424) ALSO picks value from the highest-confidence chunk. So for the default strategy, **value and quote come from the same chunk** — no mismatch.

The mismatch only occurs with `merge_dedupe`, `majority_vote`, or `concat` strategies, where the merged value is synthesized across chunks but the quote is from one specific chunk. For `merge_dedupe` (lists), the quote for the entire list comes from one chunk while the list contains items from multiple chunks.

**Impact:** Grounding scores may be inaccurate for list fields and boolean fields (non-default merge strategies). Does not affect string/numeric fields using default `highest_confidence`.

---

### 7. Confidence gate nullifies data before consolidation can use it

**Location:** `src/services/extraction/schema_validator.py:42-63`
**Verified:** YES — real issue. Threshold is 0.3 (config default, line 699-700).

When merged confidence < 0.3, all field values are set to None. The extraction is stored in DB with null data. Consolidation cannot recover the original values.

**Impact:** Extractions below 0.3 confidence are stored as empty shells. For entities with few sources, this can eliminate all data. The threshold IS low (0.3), so this only fires on genuinely poor extractions — but those might still be the best available data for rare entities.

---

## MEDIUM

### 8. Confidence averaging diluted by 0.5 fallback

**Location:** `src/services/extraction/schema_orchestrator.py:429-431`
**Verified:** YES — real issue, but narrow impact.

The 0.5 fallback only fires when the LLM omits the `confidence` key entirely. The extraction prompt explicitly requests confidence, so well-functioning LLMs return it. This mainly affects malformed responses or edge cases. When it does fire, the dilution is real — e.g., `(0.9 + 0.5) / 2 = 0.7`.

**Impact:** Occasional confidence dilution on malformed LLM responses. Low frequency but real when it happens.

---

### 9. Consolidation effective_weight excludes all ungrounded fields

**Location:** `src/services/extraction/consolidation.py:263-279`
**Verified:** YES — real issue by design.

`GROUNDING_DEFAULTS` (grounding.py:19-27) sets most field types to "required" — string, integer, float, enum, list. Only boolean ("semantic") and text ("none") bypass grounding checks. For "required" fields with `grounding_score < 0.5` or None, weight = 0.0, completely excluded.

This is intentional design (exclude ungrounded data), but the cliff at 0.5 means a field with score 0.49 is fully excluded while 0.50 is included. No gradual degradation.

**Impact:** Fields without good quotes produce zero consolidated output regardless of confidence. Affects new schema fields until quote quality improves.

---

### 10. No database indexes on scheduler query paths

**Location:** `src/orm_models.py:74-75, 282`
**Verified:** YES — real issue.

Confirmed: `Job.type` (line 74) — NO index. `Job.status` (line 75) — NO index. `Extraction.source_group` (line 282) — NO index. (Note: the `index=True` at line 136 is `Report.type`, not `Job.type`.)

The scheduler queries `WHERE Job.type = ? AND Job.status = 'queued'` every 5 seconds. Consolidation queries `WHERE Extraction.project_id = ? AND Extraction.source_group = ?`. `Extraction.project_id` has implicit FK index but compound queries with `source_group` can't use it efficiently.

**Impact:** Scheduler poll queries do full table scans. Consolidation queries are O(n) on extractions table. Current scale (few hundred jobs, 47K extractions) is manageable but will degrade.

---

### 11. ServiceContainer shutdown has no timeout or cleanup guarantee

**Location:** `src/services/scraper/service_container.py:113-124`
**Verified:** YES — real issue.

No try/finally around the sequential cleanup. If `self._llm_worker.stop()` raises, `_llm_worker_task` is never awaited and `_firecrawl_client` / `_async_redis` are never closed. No timeout on `await self._llm_worker_task`, so a stuck worker blocks shutdown indefinitely.

**Impact:** Resource leaks on abnormal shutdown. In container orchestration, can cause SIGTERM → hang → SIGKILL.

---

### 12. Entity dedup uses only first matching ID field

**Location:** `src/services/extraction/schema_orchestrator.py:500-518`
**Verified:** YES — real issue, but low probability.

The `break` at line 506 stops at first non-empty ID field. Default `entity_id_fields = ["entity_id", "name", "id"]`. If two distinct entities share the same `entity_id` value, the second is silently dropped even though their `name` differs.

In practice, `entity_id` is typically unique per entity within a page. The risk is mainly with LLM-generated sequential IDs ("1", "2") that might collide across chunks.

**Impact:** Potential entity loss when LLMs generate non-unique IDs. Low frequency but silent when it happens.

---

## Removed (False Positives)

| Original # | Issue | Why removed |
|---|---|---|
| 3 | chunk_extractions race condition | Asyncio is single-threaded; flush exception skips embedding code |
| 4 | Domain dedup OOM | 25-50MB peak is within normal worker limits; proven at production scale |
| 13 | get_db() implicit rollback | `Session.close()` rollback is documented SQLAlchemy behavior, not fragile |

---

## Summary

| # | Severity | Issue | Location |
|---|----------|-------|----------|
| 1 | CRITICAL | Consolidation endpoint: no error handling, raw 500 on failure | `projects.py:303-339` |
| 2 | CRITICAL | Consolidation loop: continues on dirty session after flush failure | `consolidation_service.py:129-139` |
| 3 | HIGH | Extraction failures invisible — job COMPLETED with 0 extractions | `pipeline.py:85-97` |
| 4 | HIGH | Embedding failures not recorded in job result | `pipeline.py:366-377` |
| 5 | HIGH | Majority vote in chunk merge biases booleans toward False | `schema_orchestrator.py:390-392` |
| 6 | HIGH | Grounding scores misaligned for list/boolean merge strategies | `schema_orchestrator.py:211-215` |
| 7 | HIGH | Confidence gate nullifies data before consolidation | `schema_validator.py:42-63` |
| 8 | MEDIUM | Confidence averaging diluted by 0.5 fallback | `schema_orchestrator.py:429-431` |
| 9 | MEDIUM | Consolidation excludes all ungrounded fields (weight=0.0 cliff) | `consolidation.py:263-279` |
| 10 | MEDIUM | No indexes on Job.type, Job.status, Extraction.source_group | `orm_models.py` |
| 11 | MEDIUM | ServiceContainer shutdown: no timeout, no try/finally | `service_container.py:113-124` |
| 12 | MEDIUM | Entity dedup first-match only, ignores subsequent ID fields | `schema_orchestrator.py:500-518` |

---

## Fix Plan

### Design principles

- **Fix the data path first** — issues that corrupt or lose extraction data (#5, #6, #7, #8, #9) are the highest-value fixes because they affect every extraction run going forward AND inform consolidation quality (see `TODO_downstream_trials.md` Trial 2A: majority_vote=48% vs any_true=86%).
- **Separate "fix broken behavior" from "add observability"** — issues #3 and #4 don't corrupt data, they just hide failures. Fix them, but in a separate increment.
- **Minimal blast radius per change** — each increment touches 1-3 files and has clear test cases.

### Increment 1: Chunk merge boolean strategy + confidence averaging

**Fixes:** #5 (majority_vote bias), #8 (confidence fallback dilution)
**Files:** `src/services/extraction/schema_orchestrator.py`
**~20 lines changed**

**#5 fix — Replace `majority_vote` with `any_true` for boolean chunk merging:**

The downstream trials (Trial 2A) proved `any_true` with min_count is strictly superior to majority_vote for boolean company-level facts (86% vs 48%). The chunk-merge `majority_vote` strategy has the same problem as cross-source majority_vote — most chunks don't mention the topic, so the LLM returns explicit `false`, drowning out the few chunks that correctly say `true`.

Change `_merge_chunk_results` (line 390-392):

```python
# BEFORE
if strategy == "majority_vote":
    true_count = sum(1 for v in values if v is True)
    merged[field.name] = true_count > len(values) / 2

# AFTER
if strategy == "majority_vote":
    # For boolean fields across chunks, any credible True should win.
    # LLMs return explicit False when a chunk lacks evidence (not when
    # evidence contradicts), so majority vote is biased toward False.
    # See TODO_downstream_trials.md Trial 2A: any_true=86% vs majority=48%.
    if any(v is True for v in values):
        merged[field.name] = True
    elif any(v is False for v in values):
        merged[field.name] = False
    # else: all non-boolean → skip (values already filtered non-None)
```

This is the chunk-merge equivalent of the consolidation layer's `any_true` strategy. At chunk level, even `min_count=1` is appropriate because chunks are from the same page — if any chunk on the page says True, the page says True.

**#8 fix — Skip missing confidence in average instead of defaulting to 0.5:**

```python
# BEFORE (line 429-431)
confidences = [r.get("confidence", 0.5) for r in chunk_results]
merged["confidence"] = sum(confidences) / len(confidences)

# AFTER
confidences = [r["confidence"] for r in chunk_results if "confidence" in r and r["confidence"] is not None]
merged["confidence"] = sum(confidences) / len(confidences) if confidences else 0.5
```

Same fix in `_merge_entity_lists` (line 520-522) which has the same pattern.

**Tests:**
- Boolean merge: 3 chunks `[True, False, False]` → True (was False)
- Boolean merge: 3 chunks `[False, False, False]` → False (unchanged)
- Boolean merge: 1 chunk `[True]` → True (unchanged)
- Confidence: 2 chunks, one has confidence 0.9, other omits → 0.9 (was 0.7)
- Confidence: all chunks omit → 0.5 fallback
- Confidence: normal case with all present → average (unchanged)

---

### Increment 2: Grounding score alignment for non-default merge strategies

**Fixes:** #6 (grounding computed against wrong quote for list/boolean merges)
**Files:** `src/services/extraction/schema_orchestrator.py`
**~30 lines changed**

The root cause: grounding scores are computed on the merged result (line 211-215), but the merged `_quotes` dict was assembled by picking quotes from the highest-confidence chunk per field (lines 433-449). For `highest_confidence` strategy this is correct (value and quote from same chunk). For `merge_dedupe`, `majority_vote`/`any_true`, and `concat`, the merged value is synthesized across chunks while the quote is from one chunk.

**Fix — Compute grounding scores per-chunk BEFORE merge, then aggregate:**

Move grounding computation from after-merge to during-merge. For each field in each chunk result, compute the string-match score for that chunk's value against that chunk's quote (guaranteed aligned). Then store the best-aligned score.

```python
# In _merge_chunk_results, after assembling merged values and merged _quotes:

# Compute per-chunk grounding scores and pick the best-aligned one
grounding_scores = {}
for field in group.fields:
    if field.name not in merged or merged[field.name] is None:
        continue
    best_score = 0.0
    for r in chunk_results:
        chunk_val = r.get(field.name)
        chunk_quotes = r.get("_quotes", {})
        chunk_quote = chunk_quotes.get(field.name, "")
        if chunk_val is not None and chunk_quote:
            from services.extraction.grounding import compute_field_grounding_score
            score = compute_field_grounding_score(
                chunk_val, chunk_quote, field.field_type
            )
            best_score = max(best_score, score)
    grounding_scores[field.name] = best_score
```

Then in `extract_group()` (line 211-215), use the pre-computed scores instead of recomputing on the merged result:

```python
# BEFORE
field_types = {f.name: f.field_type for f in group.fields}
group_result["grounding_scores"] = compute_grounding_scores(merged, field_types)

# AFTER — scores already computed during merge with aligned value/quote pairs
group_result["grounding_scores"] = merged.pop("_grounding_scores", {})
```

This requires a small refactor: `_merge_chunk_results` returns grounding scores embedded in the merged dict (under a `_grounding_scores` key), and `extract_group` pulls them out. This keeps the interface clean — `_merge_chunk_results` still returns a single dict.

Need a new helper `compute_field_grounding_score(value, quote, field_type) -> float` extracted from the existing `compute_grounding_scores` loop in `grounding.py`. This is a pure function, easy to extract.

**Tests:**
- List field with items from 3 chunks, quote from chunk 1 → score reflects chunk 1's items only (not full merged list)
- Boolean field True from chunk 2, quote from chunk 2 → semantic grounding uses chunk 2's quote
- `highest_confidence` strategy → same result as before (regression test)
- No quote for any chunk → score 0.0 (unchanged)

---

### Increment 3: Consolidation service robustness

**Fixes:** #1 (endpoint error handling), #2 (dirty session after flush failure)
**Files:** `src/api/v1/projects.py`, `src/services/extraction/consolidation_service.py`
**~40 lines changed**

**#1 fix — Wrap consolidation endpoint in try/except with rollback:**

```python
@router.post("/{project_id}/consolidate")
async def consolidate_project(
    project_id: UUID,
    source_group: str | None = Query(default=None, ...),
    db: Session = Depends(get_db),
) -> dict:
    repo = ProjectRepository(db)
    project = repo.get(project_id)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, ...)

    from services.extraction.consolidation_service import ConsolidationService
    service = ConsolidationService(db, repo)

    try:
        if source_group:
            records = service.consolidate_source_group(project_id, source_group)
            db.commit()
            return {"source_groups": 1, "records_created": len(records), "errors": 0}

        result = service.consolidate_project(project_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
```

Also remove the dead code at `consolidation_service.py:111-113` — the `if not project: return {"error": "project_not_found"}` path in `consolidate_project()`. The endpoint already handles this case. The service should assume it receives a valid project_id and raise if not, rather than returning an error dict that looks like a success response.

**#2 fix — Stop continuing after flush failure; use fresh session scope per source group:**

The core problem is that after a failed `flush()`, the session is in an undefined state per SQLAlchemy docs, but the loop continues using it. Two options:

- **Option A**: Catch the flush failure, call `db.rollback()`, then continue. This restores the session to a clean state. The failed source group's records are lost but subsequent groups can proceed safely.
- **Option B**: Stop the loop on first failure. Report partial progress.

Option A is better — it matches the existing intent (the loop already catches exceptions and increments `errors`). The missing piece is the rollback after the exception:

```python
for sg in source_groups:
    try:
        records = self.consolidate_source_group(project_id, sg)
        total_records += len(records)
    except Exception:
        logger.exception("consolidation_error", ...)
        self._session.rollback()  # <-- restore session to clean state
        errors += 1
```

Remove the redundant `self._session.flush()` at line 141 — each `_upsert_record` already flushes, and the API endpoint commits after the loop returns.

**Tests:**
- Endpoint: service raises → 500 with rollback (no dirty session)
- Service: flush failure in group 3 of 5 → groups 1-2 and 4-5 succeed, group 3 counted as error
- Service: project_id not found → raises (not returns error dict)
- Endpoint: project_id not found → 404 (unchanged)

---

### Increment 4: Confidence gate — preserve data, just flag it

**Fixes:** #7 (data nullified before consolidation)
**Files:** `src/services/extraction/schema_validator.py`
**~15 lines changed**

The `TODO_grounded_extraction.md` design doc states: "Grounding as a weight signal, not a filter. Don't delete ungrounded values — deprioritize them in consolidation." The confidence gate at `schema_validator.py:42-63` violates this principle — it destroys data that consolidation could weight appropriately.

**Fix — Keep the data, record the violation, let downstream decide:**

```python
# BEFORE: confidence < threshold → all fields = None
if self.min_confidence > 0 and confidence < self.min_confidence:
    cleaned = {k: v for k, v in data.items() if k in _METADATA_KEYS}
    ...
    for field in group.fields:
        cleaned[field.name] = None
    return cleaned, violations

# AFTER: confidence < threshold → preserve data as-is, record violation
if self.min_confidence > 0 and confidence < self.min_confidence:
    violations.append({
        "field": "*",
        "issue": "confidence_below_threshold",
        "detail": f"confidence {confidence} < threshold {self.min_confidence}",
    })
    # Don't nullify — data preserved for consolidation weighting.
    # Confidence value already in data; downstream uses it as weight.
```

Then continue to the normal validation path (type coercion, enum validation, etc.) instead of early-returning. The low confidence is already recorded in the extraction's `confidence` field and available to consolidation's `effective_weight()` calculation.

This aligns with the design principle: the confidence threshold becomes informational (recorded as a violation), not destructive (doesn't delete data). Consolidation already handles low-confidence data via weighting.

**Tests:**
- Confidence 0.2 with threshold 0.3 → data preserved, violation recorded (was: data nullified)
- Confidence 0.5 with threshold 0.3 → data preserved, no violation (unchanged)
- Entity list with confidence 0.1 → list preserved, violation recorded (was: empty list)
- Validation still runs (type coercion, enum check) regardless of confidence

---

### Increment 5: Consolidation grounding weight — gradual degradation

**Fixes:** #9 (cliff at 0.5 excludes all ungrounded fields)
**Files:** `src/services/extraction/consolidation.py`
**~10 lines changed**

The `TODO_grounded_extraction.md` doc says: "If all values for a field are ungrounded, the best ungrounded value is still better than nothing." The current `effective_weight` returns 0.0 for grounding < 0.5, which means if ALL extractions for a field are ungrounded, consolidation produces None — worse than using the best available data.

**Fix — Replace cliff with continuous weighting + floor:**

```python
def effective_weight(
    confidence: float,
    grounding_score: float | None,
    grounding_mode: str,
) -> float:
    if grounding_mode == "required":
        gs = grounding_score if grounding_score is not None else 0.0
        # Continuous weighting: well-grounded data dominates, but
        # ungrounded data still contributes when nothing better exists.
        # Floor of 0.1 prevents total exclusion.
        return confidence * max(gs, 0.1)
    return confidence
```

With this change:
- Grounded (score=0.9, conf=0.8): weight = 0.72 (was 0.72)
- Partially grounded (score=0.4, conf=0.8): weight = 0.32 (was 0.0)
- Ungrounded (score=0.0, conf=0.8): weight = 0.08 (was 0.0)
- High-confidence ungrounded vs low-confidence grounded: 0.08 vs 0.36 — grounded still wins

Grounded data always dominates. But when ALL data is ungrounded, the highest-confidence value still wins rather than producing None.

**Tests:**
- All extractions grounded → same result as before (regression)
- Mix of grounded and ungrounded → grounded wins (same as before)
- All extractions ungrounded → highest-confidence wins (was: None)
- Score=0.49 → weight = conf × 0.49 (was: 0.0)

---

### Increment 6: Pipeline observability

**Fixes:** #3 (invisible extraction failures), #4 (invisible embedding failures)
**Files:** `src/services/extraction/pipeline.py`, `src/services/extraction/embedding_pipeline.py`
**~30 lines changed**

**#3 fix — Add `sources_skipped` and `sources_empty` counters to `SchemaPipelineResult`:**

```python
@dataclass
class SchemaPipelineResult:
    project_id: str
    sources_processed: int
    sources_failed: int
    sources_skipped: int      # classified as skip
    sources_no_content: int   # had no content
    total_extractions: int
    total_embedded: int       # new
    embedding_errors: int     # new
    ...
```

In `extract_source()`, return a richer signal: instead of bare `[]`, return extractions but also have the caller track why it was empty. In `extract_with_limit()`, distinguish between:
- `source.page_type == "skip"` → increment `sources_skipped`
- `len(extractions) == 0 and source.content` → source processed, no results (legitimate)
- `not source.content` → increment `sources_no_content`

**#4 fix — Surface embedding counts in `SchemaPipelineResult`:**

`total_embedded` and `embedding_errors` are already tracked in local variables (`total_embedded` at line 321, error count logged at line 372-377). Just thread them into the result dataclass.

In `embedding_pipeline.py`, return a diagnostic message when all texts are empty:

```python
if not valid:
    return EmbeddingResult(
        embedded_count=0,
        errors=[f"All {len(extractions)} extractions produced empty text"],
    )
```

**Tests:**
- SchemaPipelineResult includes skipped/no_content/embedded/embedding_error counts
- Source with no content → sources_no_content incremented
- Source classified as skip → sources_skipped incremented
- Embedding failure → embedding_errors > 0 in result
- All empty texts → EmbeddingResult.errors has diagnostic message

---

### Increment 7: Database indexes + ServiceContainer shutdown

**Fixes:** #10 (missing indexes), #11 (shutdown cleanup)
**Files:** `src/orm_models.py`, `src/services/scraper/service_container.py`, new alembic migration
**~30 lines changed**

**#10 fix — Add indexes:**

```python
# orm_models.py - Job
type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
status: Mapped[str] = mapped_column(Text, default=JobStatus.QUEUED, index=True)

# orm_models.py - Extraction
source_group: Mapped[str] = mapped_column(Text, nullable=False, index=True)
```

Plus alembic migration to add the indexes to existing tables.

**#11 fix — try/finally + timeout in ServiceContainer.stop():**

```python
async def stop(self) -> None:
    errors = []
    # Reverse-order teardown with individual error isolation
    for name, coro in [
        ("llm_worker", self._stop_llm_worker()),
        ("firecrawl", self._close_firecrawl()),
        ("redis", self._close_redis()),
    ]:
        try:
            await asyncio.wait_for(coro, timeout=30)
        except asyncio.TimeoutError:
            logger.error("shutdown_timeout", service=name)
        except Exception as e:
            logger.error("shutdown_error", service=name, error=str(e))
            errors.append(e)
    self._started = False
    logger.info("service_container_stopped", errors=len(errors))

async def _stop_llm_worker(self) -> None:
    if self._llm_worker:
        await self._llm_worker.stop()
    if self._llm_worker_task:
        self._llm_worker_task.cancel()
        try:
            await self._llm_worker_task
        except asyncio.CancelledError:
            pass

async def _close_firecrawl(self) -> None:
    if self._firecrawl_client:
        await self._firecrawl_client.close()

async def _close_redis(self) -> None:
    if self._async_redis:
        await self._async_redis.close()
```

**Tests:**
- ServiceContainer.stop() completes even if llm_worker.stop() raises
- ServiceContainer.stop() completes even if llm_worker_task hangs (30s timeout)
- All services cleaned up regardless of individual failures
- Migration applies cleanly, indexes created

---

### Increment 8: Entity dedup composite key (deferred)

**Fixes:** #12 (first-match ID field only)

**Deferred** — low probability issue, and the fix needs design work (composite keys across chunks, hash collision handling). Entity extraction itself is disconnected from the pipeline (per `TODO_downstream_trials.md` Trial 3: "0 entities in DB despite 47K extractions"). Fix this when entity extraction is wired in.

---

### Execution Order

```
Increment 1 (chunk merge boolean + confidence avg)  ← highest data quality impact
    ↓
Increment 2 (grounding score alignment)             ← enables accurate consolidation weights
    ↓
Increment 3 (consolidation service robustness)      ← needed before deploying consolidation
    ↓
Increment 4 (confidence gate → preserve data)       ← more data for consolidation
    ↓
Increment 5 (grounding weight gradual degradation)  ← better consolidation for new fields
    ↓
Increment 6 (pipeline observability)                 ← independent, can be parallel
    ↓
Increment 7 (indexes + shutdown)                     ← independent, can be parallel
```

Increments 1-5 form a logical chain (each improves data quality for the next).
Increments 6-7 are independent and can be done in parallel with any other.

**Total estimated scope:** ~175 lines changed across 7 files, plus 1 alembic migration.
**Test scope:** ~40-50 new test cases across the increments.
