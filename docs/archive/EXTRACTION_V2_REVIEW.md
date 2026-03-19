# Pipeline Review: Extraction v2

Verified 2026-03-07 against actual code and `docs/IMPLEMENTATION_PLAN_v2_CODE.md`. Each issue traced to specific lines and confirmed.

## Flow

```
pipeline.py:extract_source
  → schema_orchestrator.py:extract_all_groups (dispatches v1/v2 via data_version)
    → schema_extractor.py:extract_field_group (LLM call, v2 prompt format)
    → schema_extractor.py:parse_v2_response / parse_v2_entity_response
    → schema_orchestrator.py:_parse_chunk_to_v2 (inline grounding per item)
    → grounding.py:ground_field_item / ground_entity_item
    → schema_orchestrator.py:_extract_entity_chunk_v2 → _extract_entities_paginated
    → chunk_merge.py:merge_chunk_results (cardinality-based)
    → extraction_items.py:to_v2_data (serialize)
    → schema_validator.py:validate (auto-detect v1/v2 storage/response)
  → pipeline.py: stores Extraction(data_version=2)
  → embedding_pipeline.py:embed_and_upsert (v2_to_flat)
  → consolidation.py:consolidate_extractions (reads v2 per-field data)
  → reports/service.py (v2_to_flat)
  → api/export.py (v2_to_flat for CSV)
  → storage/search.py (v2_to_flat)
```

## Implementation Completeness

All 6 phases from the plan are implemented. Every file listed in the plan has been modified. Specific verification:

| Phase | Status | Notes |
|-------|--------|-------|
| 1: Data Model & Types | COMPLETE | All dataclasses, utilities, ORM, migration, config |
| 2: LLM Response Format | COMPLETE | v2 prompts, parsing, validator |
| 3: Inline Grounding | COMPLETE | ground_field_item, ground_entity_item, retry |
| 4: Storage & Pipeline | COMPLETE | data_version, grounding_scores=None for v2 |
| 5: Downstream Consumers | COMPLETE | All 9 consumers handle v2 |
| 6: Entity Pagination | COMPLETE | Wired in, stall detection, already_found |

---

## Issues Found

### 1. `ListValueItem` has no `confidence` — `v2_to_flat()` silently skips list items in average

**Severity: MINOR — correctness concern for downstream confidence display**

- `ListValueItem` (`extraction_items.py:37-44`) has: `value, quote, grounding, location` — **no confidence field**
- `_list_value_to_dict()` (`extraction_items.py:317-326`) serializes: `value, grounding, quote, location` — **no confidence**
- `v2_to_flat()` (`extraction_items.py:274`) checks `if "confidence" in item` for list items — this will never be True for value-list items because `_list_value_to_dict` doesn't emit a confidence key
- Entity items DO have confidence (`EntityItem.confidence`, `_entity_to_dict` line 332)
- **Impact**: For extractions with value-list fields (e.g., `certifications: ["ISO 9001", "AS9100"]`), the averaged confidence in `v2_to_flat()` only reflects single-value and entity fields, not list fields. This affects reports, search, and export which use `v2_to_flat()`. Consolidation reads v2 data directly and doesn't use list-item confidence, so it's unaffected.
- **Design question**: Should `ListValueItem` carry confidence? Currently the confidence lives on the parent field in the LLM response, not per-item. The simplest fix may be to propagate the parent field's confidence when building list items in `_parse_chunk_to_v2()`.

### 2. v2 path loses `_truncated` flag — truncated extractions stored without warning

**Severity: IMPORTANT — silent data quality loss**

- `schema_extractor.py:331-334` sets `_truncated: True` in the raw response when `finish_reason == "length"` and entity JSON repair fails
- v1 path: `_merge_chunk_results()` (orchestrator line 727-728) propagates `_truncated` to merged result → `pipeline.py:128` pops it and records in `chunk_context`
- v2 path: `_parse_chunk_to_v2()` (orchestrator line 966-1006) calls `parse_v2_response(raw, group)` which reads only `raw["fields"]` — the `_truncated` key in `raw` is ignored
- `merge_chunk_results_v2()` (chunk_merge.py) operates on `ChunkExtractionResult` objects which have no truncation state
- **Impact**: When an LLM response is truncated in v2 mode, the truncation is silently swallowed. The extraction is stored without `chunk_context: {truncated: true}`, making it impossible to identify low-quality extractions caused by token limits. This matters most for entity lists where truncation means missing entities.

### 3. Entity pagination stall detection is unreachable

**Severity: MINOR — defense-in-depth, not functional bug**

- `_extract_entities_paginated()` (orchestrator lines 1104-1130):
  - Line 1104-1109: Filters entities where `ent_id in {x.lower() for x in already_found_ids}` — removes duplicates from `new_entities`
  - Line 1113: If `not new_entities` after filtering → breaks immediately (line 1114)
  - Lines 1117-1121: Stall detection checks if ALL `new_ids` are in `existing_ids` — but this can never be True because the `new_entities` list was already filtered to exclude matches at line 1104-1109
- The stall detection (lines 1117-1130) is dead code — the empty-response break at line 1113 will always fire first
- **Impact**: No functional bug. The empty-response break provides the same protection. Stall detection was meant to catch the case where the LLM returns entities with different casing/formatting that slip past the dedup filter, but the `.lower()` normalization prevents that scenario.

### 4. `_extract_entities_paginated` return type mismatch with plan

**Severity: NOT AN ISSUE — plan was aspirational, implementation is correct**

- Plan says return type should be `list[EntityItem]` (Phase 6A)
- Implementation returns `tuple[list[dict], bool]` — raw dicts, not EntityItem objects
- `_extract_entity_chunk_v2()` (lines 926-964) correctly converts the returned dicts to `EntityItem` objects after calling `_extract_entities_paginated()`
- This is actually better design — pagination operates on raw LLM output, grounding/typing happens once in the caller

---

## Previously Fixed Issues (confirmed resolved)

| # | Issue | Fix |
|---|-------|-----|
| 1 | Validator corrupts v2 data (destroys booleans/numbers, strips _meta) | Three-way format detection: `_is_v2_storage_format()` → `_validate_v2_storage()` |
| 2 | `EmbeddingPipeline` NameError crashes all v2 embedding | Fixed to `ExtractionEmbeddingService` |
| 3 | Entity pagination dead code (never called) | Wired via `_extract_entity_chunk_v2()` + `already_found` param |
| 4 | No source-grounding retry in v2 | Added `_avg_chunk_grounding()` + retry with `strict_quoting=True` |
| 5 | `v2_to_flat()` loses list/entity confidence | Added confidence collection for entity items (list items still lack confidence — see Issue #1 above) |

---

## Architecture Assessment

The implementation follows the plan faithfully. Key design decisions are sound:

- **Cardinality-based merge** (`chunk_merge.py`) cleanly separates merge strategies per field type
- **Inline grounding** during extraction (not as a backfill) ensures every v2 extraction has per-field quality scores
- **Three-way validator format detection** correctly handles v2 storage, v2 response, and v1 flat formats
- **`safe_data_version()`** used consistently across all downstream consumers
- **v1/v2 coexistence** — all consumers handle both formats, allowing gradual migration

No architectural issues. The codebase is ready for production validation with `EXTRACTION_DATA_VERSION=2`.

---

## Summary

| # | Severity | Issue | Impact |
|---|----------|-------|--------|
| 1 | MINOR | `ListValueItem` has no confidence → `v2_to_flat()` skips list items in avg | Wrong confidence in reports/export for list-heavy extractions |
| 2 | IMPORTANT | v2 path loses `_truncated` flag | Truncated extractions stored without quality warning |
| 3 | MINOR | Entity pagination stall detection unreachable | No functional impact (empty-response break covers it) |
| 4 | NOT ISSUE | Return type differs from plan | Implementation is actually cleaner than plan |

**Bottom line**: One important issue (#2, truncation tracking) and one minor issue (#1, list confidence). Neither will cause crashes or data corruption. Both affect data quality observability. Safe to proceed with production validation.
