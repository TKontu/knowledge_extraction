# Pipeline Review 2: Post-Increment Issues

**Date:** 2026-03-06
**Scope:** Uncommitted changes from 7-increment pipeline review + grounding/consolidation pipeline
**Method:** Three parallel code review agents + two rounds of manual verification

## Summary

4 real issues confirmed after filtering 7 false positives / design-not-bugs from the initial 11.

---

## CONFIRMED ISSUES

### 1. `verify_numeric_in_quote` rejects zero values

**File:** `src/services/extraction/grounding.py:46`
**Severity:** LOW (real bug, rare trigger)

```python
num = _to_number(value)
if num is None or num == 0:   # <-- zero is rejected
    return 0.0
```

If a field extracts `0` (e.g., "0 employees remaining") with quote "they now have 0 employees", grounding returns 0.0 instead of 1.0. Biases consolidation against legitimate zero values.

**Frequency:** Very low — zero is rare in extraction data. When it happens, the value still participates in consolidation with `weight = confidence * max(0.0, 0.1)` = confidence * 0.1 (the floor prevents total exclusion).

---

### 2. `reconsolidate()` has no error handling (asymmetric with `consolidate_project`)

**File:** `src/services/extraction/consolidation_service.py:150-154`
**Severity:** MEDIUM

```python
if source_groups:
    total = 0
    for sg in source_groups:
        records = self.consolidate_source_group(project_id, sg)  # no try/except
        total += len(records)
    self._session.flush()
```

`consolidate_project()` catches exceptions per-group and continues. `reconsolidate()` with explicit source groups has zero error handling — one bad group crashes the entire call, no partial results, session left dirty.

---

### 3. Embedding error count is always 0 or 1

**File:** `src/services/extraction/embedding_pipeline.py:121-127`
**Severity:** LOW (observability gap, no data loss)

```python
except Exception as e:
    return EmbeddingResult(embedded_count=0, errors=[str(e)])  # always len=1
```

The pipeline sums `len(embed_result.errors)` per chunk, but this is always 0 or 1. A batch of 50 embeddings failing reports `total_embedding_errors=1`.

---

### 4. `_dedup_dicts()` doesn't deduplicate items without name/id

**File:** `src/services/extraction/consolidation.py:442-443`
**Severity:** LOW

```python
elif not key:
    result.append(item)  # added without dedup check
```

Dicts without `name`, `product_name`, or `id` are appended unconditionally. Two identical dicts without identifier fields will both appear in results. Note: the chunk-merge path in `_merge_entity_lists` (line 556-562) already handles this with SHA-256 content hashing — this function doesn't.

---

## REJECTED (False Positives / Not Bugs)

### Entity lists have no grounding scores (originally #2)
**Why rejected:** Entity consolidation isn't wired up yet (handoff: "Entity extraction wiring — Connect existing infrastructure to pipeline"). Entity grounding is fundamentally different from scalar grounding — you can't score a list of dicts against a scalar quote. This is an unimplemented future feature, not a regression.

### Consolidation loop continues after session rollback (originally #3)
**Why rejected:** This is a **deliberate best-effort design**. The return dict reports `errors=N` so the caller knows some groups failed. SQLAlchemy autobegin starts a fresh transaction for subsequent groups. The API endpoint wraps the whole call in its own try/except. Consolidation is idempotent (upsert), so partial results can be completed by re-running. Not silent corruption.

### `any_true()` vacuous truth (originally #5)
**Why rejected:** `weight=0` requires `confidence=0.0` exactly (since weight = confidence * max(gs, 0.1), and 0.1 is the floor). Default confidence is 0.5, LLMs always return >0. This scenario is practically unreachable. AND even if it happened, line 209 independently catches the same case: `if not has_any_true and any(v.value is False ...): return False`. The behavior matches the docstring: "Returns False if all values are False."

### `total_grounded` uses `max()` (originally #6)
**Why rejected:** Design choice, not a bug. `max()` represents "best-case grounding" across fields. Per-field grounding data IS preserved in the `provenance` dict, which stores per-field `grounded_count`. The DB-level aggregate is a summary metric — max is a valid summary choice.

### List agreement always 1.0 (originally #7)
**Why rejected:** Undefined semantics, not data corruption. What should "agreement" mean for a set union? Every source contributed items — there's no concept of "matching the result" for a union. Jaccard similarity could work but isn't obviously better. The agreement field is meaningful for scalars; for lists, 1.0 means "we produced a result."

### Boolean merge strategy name mismatch (originally #8)
**Why rejected:** Intentional, documented design choice. The comments at lines 392-397 explicitly explain: "Any credible True wins at chunk level. LLMs return explicit False when a chunk lacks evidence... See TODO_downstream_trials.md Trial 2A: any_true=86% vs majority_vote=48%." No schema currently uses explicit `merge_strategy` overrides. The naming is misleading but the behavior is deliberately chosen with data backing.

### Entity validator silent data loss (originally #9)
**Why rejected:** Unreachable code path. `_merge_entity_lists` ALWAYS adds `entity_key` to its return (line 574: `{entity_key: all_entities, ...}`), and both functions use `group.name` as first choice. The validator's `entity_key=None` path cannot be triggered from the current pipeline.

### Async race condition on `chunk_extractions`
**Why rejected:** `asyncio.gather` is awaited; Python asyncio is single-threaded. `clear()` runs before gather starts, gather completes before code after it runs. No race.

### `_grounding_scores` popped before validation
**Why rejected:** Intentional. Scores are extracted into `group_result["grounding_scores"]` before pop. Validator doesn't need them.

### Grounding skips empty quotes
**Why rejected:** By design. No quote = no grounding evidence = score 0.0.

---

## Recommended Fixes

1. **Issue 2** (reconsolidate error handling) - Add try/except matching consolidate_project's pattern
2. **Issue 1** (zero grounding) - Remove `num == 0` check, handle zero as valid number
3. **Issue 4** (_dedup_dicts) - Add SHA-256 content hash fallback (matching _merge_entity_lists)
4. **Issue 3** (embedding error count) - Track batch size in error result
