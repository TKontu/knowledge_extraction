# Pipeline Review #3 — Full Extraction Pipeline

**Date:** 2026-03-06
**Status:** ALL 7 VERIFIED ISSUES FIXED (commit TBD)
**Scope:** Complete extraction pipeline: ingestion, chunking, LLM extraction, merge, grounding, consolidation, embedding, API
**Method:** 5 parallel code review agents + manual verification against source code. 15 raw findings filtered to 7 real issues (8 false positives rejected).
**Prior review:** `PIPELINE_REVIEW_2.md` — 4 issues found. This review corrects one false-negative from that review (consolidation rollback) and adds new findings.

---

## Summary

| Severity | Count |
|----------|-------|
| CRITICAL | 3 |
| HIGH | 7 |
| MEDIUM | 5 |
| **Total** | **15** |

---

## CRITICAL — Will cause data loss or corruption

### C1. Consolidation rollback destroys successful source groups

**File:** `src/services/extraction/consolidation_service.py:143-160`
**Note:** Previously rejected in PIPELINE_REVIEW_2 as "deliberate best-effort" — that analysis was wrong.

```python
for sg in source_groups:
    try:
        records = self.consolidate_source_group(project_id, sg)  # calls flush()
        total_records += len(records)
    except Exception:
        self._session.rollback()  # Rolls back ALL unflushed work
        errors += 1
```

Each `consolidate_source_group()` calls `_upsert_record()` which calls `self._session.flush()` (line 206). Flush writes to the DB within the current transaction but does NOT commit. When group N fails and `rollback()` is called, it rolls back the **entire transaction** including all previously flushed groups.

**Example:** Groups A, B, C. A succeeds (flushed in T1). B fails → `rollback()` rolls back T1 (A's data is gone). SQLAlchemy auto-begins T2. C succeeds (flushed in T2). Caller commits T2 — only C's data survives. A is silently lost. The return dict reports `records_created` including A's count, which is now a lie.

**Fix:** Use SAVEPOINTs (nested transactions) per source group, or commit after each successful group.

---

### C2. Entity list truncation returns indistinguishable empty result

**File:** `src/services/extraction/schema_extractor.py:289-305`

```python
if field_group.is_entity_list:
    try:
        result_data = try_repair_json(result_text, context="schema_extract_truncated")
    except json.JSONDecodeError:
        return {field_group.name: [], "confidence": 0.0}
```

When an entity list is truncated by max_tokens AND JSON repair fails, the code returns `{name: [], confidence: 0.0}`. This is merged into the final result as a valid extraction. The 0.0 confidence distinguishes it from a genuine empty result only if downstream consumers check — but neither the orchestrator merge logic nor consolidation treats 0.0 as "data loss." The entities are permanently lost with no error signal or retry.

**Fix:** Return an error signal (e.g. `{"_truncation_error": True}`) that the orchestrator can detect and handle (retry with smaller content, skip the chunk, or flag for review).

---

### C3. Embedding failures produce unsearchable but persisted extractions

**File:** `src/services/extraction/pipeline.py:376-396`

```python
if embed_enabled and chunk_extractions:
    embed_result = await self._extraction_embedding.embed_and_upsert(chunk_extractions)
    if embed_result.errors:
        total_embedding_errors += embed_result.failed_count or 1
        logger.error("chunk_embedding_failed", ...)

# ALWAYS commits, regardless of embedding errors
self._db.commit()
```

When Qdrant or the embedding service is unavailable, extractions are committed to PostgreSQL but never added to the vector store. There is no mechanism to re-embed failed extractions later. The `total_embedding_errors` counter is logged but not acted upon.

**Impact:** Extractions exist in the DB and appear in reports, but are invisible to semantic search. No automated recovery path.

**Fix:** Either (a) track embedding status on the Extraction model and provide a re-embed endpoint, or (b) fail the chunk commit when embedding fails (making it retryable).

---

## HIGH — Significant production impact

### H1. union_dedup keeps first entity occurrence, loses attributes from others

**File:** `src/services/extraction/consolidation.py:235-259`

```python
def _dedup_dicts(items: list[dict]) -> list[dict]:
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        name = item.get("name") or item.get("product_name") or item.get("id", "")
        key = str(name).strip().lower()
        if key not in seen:
            seen.add(key)
            result.append(item)  # First occurrence wins — others discarded entirely
    return result
```

When the same entity (by name) appears across 5 sources with different attributes, only the first occurrence's attributes are kept. Example:
- Source 1: `{name: "Product X", power: "100 kW"}`
- Source 2: `{name: "Product X", power: "120 kW", efficiency: "92%"}`
- Result: `{name: "Product X", power: "100 kW"}` — `efficiency` lost

This is the primary consolidation strategy for entity lists and will lose data in every consolidation run.

**Fix:** Merge attribute dicts across occurrences (e.g. keep non-null values from the highest-weight source per attribute).

---

### H2. Stale consolidated records never cleaned up

**File:** `src/services/extraction/consolidation_service.py:185-206`

The upsert creates/updates consolidated records but never deletes them. If a source group's extractions are deleted or re-extracted with different types, old consolidated records remain. Reports will include stale data from deleted source groups.

**Fix:** Delete existing consolidated records for the project (or source group) before upserting new ones, within the same transaction.

---

### H3. update_grounding_scores_batch is N individual UPDATE queries

**File:** `src/services/storage/repositories/extraction.py:424-449`

```python
def update_grounding_scores_batch(self, updates: list[tuple[UUID, dict[str, float]]]) -> int:
    count = 0
    for extraction_id, scores in updates:  # N queries
        result = self._session.execute(
            update(Extraction).where(Extraction.id == extraction_id).values(grounding_scores=scores)
        )
        count += result.rowcount
```

Despite the name "batch", this executes one UPDATE per extraction. For 47K extractions during backfill, this is 47K round-trips. Compare to `update_embedding_ids_batch()` which correctly uses a single query.

**Fix:** Use `bulk_update_mappings()` or a single UPDATE with VALUES clause.

---

### H4. Consolidation loads all extractions for a source group into memory

**File:** `src/services/extraction/consolidation_service.py:57-66`

```python
extractions = (
    self._session.execute(
        select(Extraction).where(
            Extraction.project_id == project_id,
            Extraction.source_group == source_group,
        )
    ).scalars().all()  # Loads ALL ORM objects into memory
)
```

For large source groups (thousands of extractions with JSONB `data` and `grounding_scores`), this loads everything into memory at once. Currently manageable (~hundreds per group) but will OOM as data grows.

**Fix:** Use server-side streaming or pagination for large groups.

---

### H5. CJK token counting systematically undercounts

**File:** `src/services/llm/chunking.py:34`

```python
return (non_cjk_count // 4) + int(cjk_count / 1.5)
```

`int()` truncates toward zero: for 7 CJK chars, `int(7/1.5) = int(4.666) = 4` instead of 5. This systematically undercounts CJK tokens by up to 33%, meaning CJK chunks can exceed the specified `max_tokens` limit.

For extraction (5000 token chunks, 32K LLM context), this is safe. For embedding (bge-m3, 8192 token limit), pure CJK content could exceed the limit and produce degraded vectors.

**Fix:** Use `math.ceil()` instead of `int()` for the CJK term.

---

### H6. Quote merging picks highest-confidence chunk even if it has no quote

**File:** `src/services/extraction/schema_orchestrator.py:450-466`

```python
for result in chunk_results:
    chunk_quotes = result.get("_quotes", {})
    chunk_conf = result.get("confidence", 0.5)
    if isinstance(chunk_quotes, dict):
        for field_name, quote in chunk_quotes.items():
            if field_name not in best_conf or chunk_conf > best_conf[field_name]:
                merged_quotes[field_name] = quote
                best_conf[field_name] = chunk_conf
```

This iterates quotes that exist and picks the highest-confidence one. But if the winning chunk (for the merged value via `highest_confidence`) didn't return a quote for that field, the field gets NO quote, even though a lower-confidence chunk had one.

**Consequence:** Grounding score at line 495 finds `chunk_quote=""` → score = 0.0 → field appears ungrounded even though evidence existed in another chunk.

**Fix:** When the winning chunk has no quote for a field, fall back to any available quote from other chunks.

---

### H7. Entity dedup treats dict/list IDs as unique strings

**File:** `src/services/extraction/schema_orchestrator.py:546-550`

```python
for id_field in self._context.entity_id_fields:
    raw_id = entity.get(id_field)
    if raw_id is not None and raw_id != "":
        entity_id = str(raw_id).strip().lower()
        break
```

If an LLM returns a nested structure as an entity ID (e.g. `{"name": {"en": "Product X"}}`), `str()` produces a unique string per occurrence, defeating deduplication. Every entity appears unique.

**Fix:** Add type checking: skip non-scalar ID values, or flatten dicts to their first string value.

---

## MEDIUM — Correctness concern, manageable impact

### M1. Domain dedup reprocessing clears cleaned_content from previously cleaned sources

**File:** `src/services/extraction/domain_dedup.py:426-431`

```python
else:
    avg_removed = 0
    for source in sources:
        if source.cleaned_content is not None:
            source.cleaned_content = None
```

When `analyze_domain()` is re-run and finds no boilerplate (e.g. domain has fewer pages now), it clears `cleaned_content` for ALL sources. If extraction already used `cleaned_content`, re-running dedup analysis destroys the cleaning and subsequent extractions use uncleaned content.

**Mitigation:** Currently dedup is only run once per domain. Risk only on re-analysis.

---

### M2. Confidence averaging skips chunks with missing confidence

**File:** `src/services/extraction/schema_orchestrator.py:441-448`

```python
confidences = [
    r["confidence"] for r in chunk_results if r.get("confidence") is not None
]
merged["confidence"] = sum(confidences) / len(confidences) if confidences else 0.5
```

If a chunk omits confidence (returns None), it's excluded from the average. With 3 chunks where one returns 0.9 and two return None, the average is 0.9 — not 0.63. The code comment acknowledges this as intentional ("avoid diluting"), but it creates systematic upward bias that downstream consumers may not expect.

---

### M3. Smart classifier fallback extracts all groups on any error

**File:** `src/services/extraction/smart_classifier.py` (fallback path)

When embedding or reranking fails, the classifier returns `relevant_groups=[]` (meaning "extract all groups") instead of falling back to rule-based classification. This wastes LLM budget extracting irrelevant field groups during embedding service outages.

---

### M4. Content cleaner link density regex overcounts with nested parentheses

**File:** `src/services/extraction/content_cleaner.py:37-42`

Markdown links with parentheses in URLs (e.g. `[Text](https://example.com/path(v1))`) cause the first regex to match partially, then the bare URL regex matches the remainder — double-counting characters. Inflates link density by 10-40% for such URLs.

---

### M5. frequency() tie-breaking is non-deterministic for equal counts and weights

**File:** `src/services/extraction/consolidation.py:99-104`

```python
form_counts: dict[str, int] = {}
for wv in best_group:
    form_counts[wv.value] = form_counts.get(wv.value, 0) + 1
return max(form_counts, key=form_counts.get)
```

When all case variants have equal count (e.g. "ABB", "Abb", "abb" each once), `max()` picks arbitrarily. Non-reproducible consolidation output. Low practical impact since most real data has a dominant form.

---

## False Positives Rejected

| Claim | Why rejected |
|-------|-------------|
| `chunk_extractions` race condition (pipeline.py:292) | asyncio is single-threaded; `extend()` has no `await` so it's atomic within the event loop. `clear()` runs before `gather()` starts. |
| Checkpoint saved before commit (pipeline.py:391) | Checkpoint modifies the ORM object (`job.payload`), committed in the same `self._db.commit()` on line 396. Atomic. |
| `stmt.excluded.metadata` wrong column name (source.py:296) | SQLAlchemy `excluded` uses the **DB column name** ("metadata"), not the ORM attribute ("meta_data"). Correct. |
| Grounding scores misaligned with merged values (orchestrator:474-499) | Code intentionally scores each chunk's own value against its own quote (comment lines 474-478). For `highest_confidence` merge, correct. For booleans, grounding mode is "semantic" (skipped). For lists, "best of any chunk" is reasonable. |
| LLM grounding skips score >= 0.5 | By design — string-match above 0.5 is considered sufficient evidence. |
| Redis async client memory leak | Global singleton; `ServiceContainer.stop()` cleans it up. |
| `asyncio.gather` ordering | Preserves result order by specification. |
| Job state inconsistency on worker exception (worker.py:421-434) | Rollback + re-commit is the standard recovery pattern. If the re-commit also fails, the scheduler's stale job cleanup handles it. |
| Flush doesn't guarantee ID assignment | PostgreSQL UUID generation is reliable; constraint violations would raise before flush returns. |
| Embedding semaphore race condition | asyncio is single-threaded; class-level init cannot race within a single event loop. |

---

## Corrections to PIPELINE_REVIEW_2

| PIPELINE_REVIEW_2 verdict | This review | Reason |
|---------------------------|-------------|--------|
| Consolidation rollback rejected as "deliberate best-effort" | **CRITICAL (C1)** | The analysis was wrong. `rollback()` rolls back the entire transaction including previously flushed groups. SQLAlchemy auto-begin starts a new transaction, but Group A's data was in the rolled-back transaction — it's lost. The `records_created` count is inflated. |
| `reconsolidate()` no error handling (Issue 2) | Subsumed by C1 | `reconsolidate()` now delegates to `_process_source_groups()` which has the rollback bug. Fix C1 and both are fixed. |

PIPELINE_REVIEW_2 issues 1 (zero grounding), 3 (embedding error count), 4 (_dedup_dicts) remain valid and are not duplicated here.

---

## Priority Order for Fixes

| # | Issue | Effort | Risk |
|---|-------|--------|------|
| 1 | C1 — Consolidation rollback | Small | High — active data loss on partial failures |
| 2 | C2 — Entity list truncation signal | Small | High — silent data loss |
| 3 | H2 — Stale consolidated records | Small | Medium — data correctness |
| 4 | H1 — union_dedup attribute merging | Medium | High — every consolidation loses data |
| 5 | C3 — Embedding failure tracking | Medium | Medium — needs model change |
| 6 | H6 — Quote fallback for unquoted winners | Small | Medium — grounding accuracy |
| 7 | H3 — Batch update performance | Small | Low — performance only |
| 8 | H7 — Entity ID type check | Small | Low — rare LLM behavior |
| 9 | H5 — CJK token ceiling | Trivial | Low — edge case |
| 10 | M1-M5 | Various | Low |
