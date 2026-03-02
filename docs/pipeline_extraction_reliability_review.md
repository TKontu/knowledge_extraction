# Pipeline Review: Extraction Reliability Features (fe8184d)

Scope: chunk overlap, source quoting, conflict detection, schema validation — and their interaction with the existing merge/pipeline/report flow.

All findings verified with code execution against the actual codebase.

## Flow

```
worker.py:ExtractionWorker.process_job
  → pipeline.py:SchemaExtractionPipeline.extract_source
    → schema_orchestrator.py:extract_all_groups
      → chunking.py:chunk_document (overlap applied here)
      → schema_orchestrator.py:_extract_chunks_batched (per-chunk LLM calls)
        → schema_extractor.py:extract_field_group → _extract_direct (has own retry loop)
      → schema_orchestrator.py:_merge_chunk_results (quotes merge, conflict detect)
      → schema_validator.py:validate (coercion, enum, confidence gating)
      → schema_orchestrator.py:_is_empty_result (confidence cap)
    → pipeline.py: store Extraction(data=merged) in DB
  → reports/service.py: read extraction.data for table reports
```

---

## Critical (must fix before enabling features)

### 1. Overlap truncates real content — CONFIRMED

**Files**: `chunking.py:262-270` + `schema_extractor.py:478`
**Triggers when**: `extraction_chunk_overlap_tokens > 0` (default: 0, planned: 200)
**Current impact**: None (overlap disabled by default)
**Impact when enabled**: Silent data loss on every multi-chunk source

`chunk_document()` creates chunks up to `max_tokens=5000` (~20K chars). Overlap prepends the previous chunk's tail, making the content larger. But `_build_user_prompt()` truncates at `EXTRACTION_CONTENT_LIMIT = 20000` chars. The overlap content survives (it's prepended), while the chunk's own new content at the end gets cut.

**Verified**:
```
With overlap=200: 4 chunks
  Chunk 0: 20001 chars — EXCEEDS by 1 chars
  Chunk 1: 22026 chars — EXCEEDS by 2026 chars
  Chunk 2: 22023 chars — EXCEEDS by 2023 chars
  Chunk 3: 24026 chars — EXCEEDS by 4026 chars
```

Chunk 3 exceeds the limit by 4026 chars — 20% of the content window is wasted on overlap while the end of the chunk is lost.

**Fix**: Reduce `max_tokens` by `overlap_tokens` in `chunk_document` call (orchestrator line 143), so that `chunk_content + overlap <= EXTRACTION_CONTENT_LIMIT`.

### 2. Double retry: 3 × 3 = 9 LLM calls per failing chunk — CONFIRMED

**Files**: `schema_orchestrator.py:231-266` (outer retry) + `schema_extractor.py:223-339` (inner retry)
**Triggers when**: LLM server returns errors or bad JSON consistently (direct mode, default)
**Current impact**: Active now — happens on every persistent LLM failure

The outer retry in `_extract_chunks_batched` (3 attempts) wraps `extract_field_group`, which in direct mode calls `_extract_direct` (also 3 attempts). Each `LLMExtractionError` from the inner loop triggers a fresh outer retry, resetting the inner counter.

- Total calls per chunk: 3 × 3 = 9
- With `extraction_max_concurrent_chunks=80`: up to 720 concurrent retries
- Each inner attempt has exponential backoff (2s, 4s, up to 30s)

**Note**: In queue mode (`llm_queue` is not None), `_extract_via_queue` has no retry loop, so the outer retry is the *only* retry. The outer retry is useful for queue mode but wasteful in direct mode.

**Fix**: Remove outer retry OR condition it on queue mode only.

---

## Important (fix before enabling validation)

### 3. Confidence gating produces malformed data for entity lists + fools empty check — CONFIRMED

**Files**: `schema_validator.py:44-55` + `schema_orchestrator.py:525-531`
**Triggers when**: `extraction_validation_enabled=True` AND `extraction_validation_min_confidence > 0` AND entity list extraction has low confidence
**Current impact**: None (both flags off)
**Impact when enabled**: Wrong confidence, malformed data stored in DB

Two bugs in sequence:

**Bug A — Validator produces malformed entity list output**: Confidence gating (validator line 44-55) runs before the `is_entity_list` check (line 57). When gating triggers, it copies only `_METADATA_KEYS`, drops the entity list key entirely, and sets individual field names to None at the top level — which is structurally wrong for entity lists.

**Bug B — `_is_empty_result` fooled by `_validation`**: After gating, the merged data contains `_validation: [{...}]` (a non-empty list). The entity list branch of `_is_empty_result` iterates all `data.items()` and treats any non-empty list as entity data, bypassing the confidence cap.

**Verified**:
```python
# Input: entity list with confidence 0.2, threshold 0.5
# After gating:
cleaned keys: ['confidence', 'product_name', 'power_kw', '_validation']
has entity key "products": False    # <-- entity key DROPPED
_validation is list with len 1      # <-- triggers false "not empty"
```

**Fix**: (A) Handle `is_entity_list` before confidence gating, setting the entity key to `[]`. (B) Skip `_METADATA_KEYS` in the entity list branch of `_is_empty_result`.

### 4. Enum merge + validation destroys data — CONFIRMED

**Files**: `schema_orchestrator.py:349-357` + `schema_validator.py:244-268`
**Triggers when**: Multi-chunk source (>5000 tokens), chunks disagree on enum field, validation enabled
**Current impact**: None (validation off), and multi-chunk + enum disagreement is uncommon
**Impact when enabled**: Enum fields silently nullified on multi-chunk pages

The merge handler treats `text` and `enum` identically — disagreeing values are joined with `"; "`. For enum fields, this creates `"IE3; IE4"`, which is not a valid enum value. When validation runs, `_coerce_enum` can't match the concatenated string → field nullified.

**Verified**:
```python
# Merge produces: efficiency_class = "IE3; IE4"
# After validation: efficiency_class = None
# Violation: "'IE3; IE4' not in ['IE1', 'IE2', 'IE3', 'IE4']"
```

**Fix**: In `_merge_chunk_results`, handle `enum` separately from `text` — pick the value from the highest-confidence chunk.

---

## Downgraded / Removed

### ~~Quoting flag uses `global_settings`~~ — REAL but not practical

**Files**: `schema_extractor.py:359,419`

`SchemaExtractor` uses `global_settings` for the quoting flag while using `self.settings` for everything else. Architecturally inconsistent, but since there is only one `Settings` singleton in the application, this never produces a functional difference. Tests use monkeypatching which changes the singleton. No real scenario where `self.settings != global_settings`.

**Verdict**: Not worth fixing — zero practical impact.

### ~~Dead `confidence` guard in `_is_empty_result`~~ — TRUE but harmless

The `if key == "confidence": continue` in the entity list branch is dead code since `confidence` is popped before the call. Harmless — acts as a safety net if the call order ever changes.

**Verdict**: Not worth touching.

---

## Summary

| # | Finding | Default safe? | When it fires | Severity |
|---|---------|--------------|---------------|----------|
| 1 | Overlap truncation | Yes (overlap=0) | When overlap enabled | Data loss |
| 2 | Double retry 9x | **No** | Every LLM failure | Performance/DoS |
| 3 | Entity confidence gating | Yes (validation off) | When validation + min_confidence enabled | Wrong data + wrong confidence |
| 4 | Enum merge + validation | Yes (validation off) | Multi-chunk + enum disagreement + validation | Data loss |

**Finding 2 is the only issue active today.** Findings 1, 3, 4 are latent — they will surface when the corresponding flags are enabled as planned in the HANDOFF.
