# TODO: Extraction Pipeline Fixes

**Source**: `docs/pipeline_review_extraction_deep.md` (verified review, 13 real findings)
**Date**: 2026-03-02
**Status**: ✅ ALL PHASES COMPLETE (verified 2026-03-03)

---

## Overview

Five phases, each independently deployable. Ordered by: data correctness first, then safety, quality, features, cleanup.

```
Phase 1: Merge Strategy Defaults          ✅ DONE
Phase 2: Config Hardening                  ✅ DONE
Phase 3: Chunking Quality                  ✅ DONE
Phase 4: Schema Pipeline Searchability     ✅ DONE
Phase 5: Minor Cleanup                     ✅ DONE
```

---

## Phase 1: Merge Strategy Defaults ✅ DONE

**Problem**: Numeric fields use `max()` (wrong for year_founded, price) and text fields concatenate with `"; "` (garbage for description/headquarters). Both should use highest-confidence chunk — the same strategy enum fields already use.

**Approach**: Add optional `merge_strategy` field to `FieldDefinition` so templates can override. Change defaults: numeric → `highest_confidence`, text → `highest_confidence`. Keep concat available as explicit opt-in (`concat`).

### Files

| File | Change |
|------|--------|
| `src/services/extraction/field_groups.py` | Add `merge_strategy: str \| None = None` to `FieldDefinition` |
| `src/services/extraction/schema_orchestrator.py:285-388` | Refactor `_merge_chunk_results` to check `field.merge_strategy` first, then fall back to new type-based defaults |
| `src/services/extraction/schema_adapter.py` | Add validation for `merge_strategy` values in `SchemaAdapter.validate_extraction_schema()` |

### Implementation Details

**`field_groups.py`** — Add to `FieldDefinition`:
```python
merge_strategy: str | None = None
# Valid values: "highest_confidence", "max", "min", "concat", "majority_vote"
# None = use type-based default
```

**`schema_orchestrator.py`** — Refactor merge logic in `_merge_chunk_results`:
```python
# Resolution order:
# 1. field.merge_strategy (explicit template override)
# 2. Type-based defaults:
#    - boolean → majority_vote (unchanged)
#    - integer/float → highest_confidence (was: max)
#    - enum → highest_confidence (unchanged)
#    - text → highest_confidence (was: concat)
#    - list → merge_dedupe (unchanged)
```

Extract the "pick value from highest-confidence chunk" logic (currently only in enum branch, lines 340-351) into a reusable `_pick_highest_confidence(field_name, chunk_results)` helper.

**`schema_adapter.py`** — In `validate_extraction_schema`, validate `merge_strategy`:
```python
VALID_MERGE_STRATEGIES = {"highest_confidence", "max", "min", "concat", "majority_vote", None}
```

### Backward Compatibility

- `merge_strategy=None` preserves old defaults for existing templates
- Wait — no. The whole point is to fix the defaults. So:
  - New default for numeric: `highest_confidence` (breaking change, but the old behavior was wrong)
  - New default for text: `highest_confidence` (breaking change, but the old behavior produced garbage)
  - Templates that WANT `max()` or `concat` can explicitly set `merge_strategy: "max"` / `merge_strategy: "concat"`

### Tests

- `test_merge_numeric_uses_highest_confidence` — two chunks, chunk 1 has year_founded=1985 at confidence 0.9, chunk 2 has year_founded=2005 at confidence 0.6 → picks 1985
- `test_merge_text_uses_highest_confidence` — two chunks with different descriptions → picks higher confidence, no concat
- `test_merge_strategy_override_max` — field with `merge_strategy="max"` → uses max() for numeric
- `test_merge_strategy_override_concat` — field with `merge_strategy="concat"` → concatenates text
- `test_merge_backward_compat_list` — list merge unchanged (dedupe + flatten)
- `test_merge_backward_compat_boolean` — majority vote unchanged
- `test_merge_backward_compat_enum` — highest confidence unchanged
- `test_invalid_merge_strategy_rejected` — schema validation rejects `merge_strategy: "bogus"`

---

## Phase 2: Config Hardening ✅ DONE

**Problem**: Three independent config issues:
1. `EXTRACTION_CONTENT_LIMIT` captured at import time, immune to runtime/test overrides
2. No cross-validation between `extraction_chunk_overlap_tokens` and `extraction_chunk_max_tokens` (negative effective_max possible)
3. "max 20 items" hardcoded in entity list prompt, not configurable

### Files

| File | Change |
|------|--------|
| `src/services/extraction/schema_extractor.py:27,475` | Use `self.settings.extraction_content_limit` in `_build_user_prompt` instead of module constant |
| `src/config.py` | Add `@model_validator` for overlap < chunk_max_tokens |
| `src/services/extraction/field_groups.py` | Add `max_items: int \| None = None` to `FieldGroup` |
| `src/services/extraction/schema_extractor.py:431` | Use `field_group.max_items or 20` in entity list prompt |
| `src/services/extraction/schema_adapter.py` | Parse and validate `max_items` from schema |

### Implementation Details

**2a. Content limit at runtime**

`schema_extractor.py` — Change `_build_user_prompt` (line 475):
```python
# Before:
{cleaned[:EXTRACTION_CONTENT_LIMIT]}

# After:
limit = self.settings.extraction_content_limit
...
{cleaned[:limit]}
```

Keep `EXTRACTION_CONTENT_LIMIT` as deprecated module-level alias with a comment. Do NOT remove it — external code or tests may reference it.

**2b. Overlap cross-validation**

`config.py` — Add to existing `validate_classification_thresholds` or as new validator:
```python
@model_validator(mode="after")
def validate_chunk_config(self) -> "Settings":
    if self.extraction_chunk_overlap_tokens >= self.extraction_chunk_max_tokens:
        raise ValueError(
            f"extraction_chunk_overlap_tokens ({self.extraction_chunk_overlap_tokens}) "
            f"must be less than extraction_chunk_max_tokens ({self.extraction_chunk_max_tokens})"
        )
    return self
```

**2c. Configurable entity list max items**

`field_groups.py` — Add to `FieldGroup`:
```python
max_items: int | None = None  # Max entities to extract per chunk. None = default (20).
```

`schema_extractor.py` — In `_build_entity_list_system_prompt` (line 431):
```python
max_items = field_group.max_items or 20
...
f"- Extract ONLY the most relevant/significant items (max {max_items} items)"
```

`schema_adapter.py` — Parse `max_items` from field group dict, validate `1 <= max_items <= 200`.

### Tests

- `test_content_limit_respects_settings` — override `settings.extraction_content_limit=100`, verify prompt is truncated at 100
- `test_overlap_exceeds_max_tokens_rejected` — `overlap=1000, max=500` → ValueError on Settings init
- `test_overlap_equals_max_tokens_rejected` — `overlap=500, max=500` → ValueError
- `test_valid_overlap_accepted` — `overlap=200, max=5000` → OK
- `test_entity_list_custom_max_items` — `FieldGroup(max_items=50)` → prompt says "max 50 items"
- `test_entity_list_default_max_items` — `FieldGroup(max_items=None)` → prompt says "max 20 items"

---

## Phase 3: Chunking Quality ✅ DONE

**Problem**: Three issues degrade chunk quality:
1. Only splits on `## ` (H2) — H3/H4 structured docs produce oversized sections
2. `count_tokens = len(text) // 4` underestimates 4-8x for CJK text
3. Preamble merged into first section can create oversized chunks

### Files

| File | Change |
|------|--------|
| `src/services/llm/chunking.py:74` | Split on H2+ headers (any `##` or deeper) |
| `src/services/llm/chunking.py:8-17` | CJK-aware token counting heuristic |
| `src/services/llm/chunking.py:81-83` | Treat preamble as standalone section |

### Implementation Details

**3a. Multi-level header splitting**

Change `split_by_headers` (line 74):
```python
# Before:
pattern = r"(?=^## )"

# After — split on any header level >= 2:
pattern = r"(?=^#{2,} )"
```

This splits on `## `, `### `, `#### `, etc. H1 (`# `) is excluded because it's typically the page title and should stay with the content below it.

The `extract_header_path` function already handles H1/H2/H3, so header breadcrumbs remain correct.

**3b. CJK-aware token counting**

Replace the simple approximation with a CJK-aware heuristic. No external dependency (no tiktoken):

```python
def count_tokens(text: str) -> int:
    """Approximate token count, CJK-aware.

    English/Latin: ~4 chars per token (unchanged).
    CJK characters: ~1.5 chars per token (conservative).
    """
    cjk_count = 0
    for ch in text:
        cp = ord(ch)
        # CJK Unified Ideographs + common CJK ranges
        if (0x4E00 <= cp <= 0x9FFF    # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF  # CJK Extension A
            or 0xF900 <= cp <= 0xFAFF  # CJK Compatibility
            or 0x3040 <= cp <= 0x309F  # Hiragana
            or 0x30A0 <= cp <= 0x30FF  # Katakana
            or 0xAC00 <= cp <= 0xD7AF):  # Hangul
            cjk_count += 1

    non_cjk_count = len(text) - cjk_count

    # CJK: ~1.5 chars per token (some subword splitting)
    # Non-CJK: ~4 chars per token (English average)
    return (non_cjk_count // 4) + int(cjk_count / 1.5)
```

This is still a heuristic but corrects the 4-8x underestimation for CJK. The ratio `1.5` is conservative — most CJK tokenizers produce 1-2 tokens per character.

**Performance note**: Iterating chars is O(n) but `count_tokens` is already called per-paragraph during chunking. The text sizes are small (paragraphs). No measurable impact.

**3c. Preamble as standalone section**

Change `split_by_headers` (lines 81-83):
```python
# Before — merge preamble into first section:
if len(sections) > 1 and not sections[0].startswith("## "):
    sections[0] = sections[0] + "\n\n" + sections[1]
    sections.pop(1)

# After — keep preamble separate:
# (Just delete these 3 lines. Preamble stays as its own section.)
# chunk_document already handles sections that fit together.
```

The `chunk_document` function already merges small adjacent sections when they fit within `max_tokens` (lines 243-244). Removing the forced merge lets chunk_document make the right decision based on actual size.

### Tests

- `test_split_headers_h3_h4` — doc with H3/H4 sections → each becomes separate section
- `test_split_headers_h2_unchanged` — doc with only H2 → same as before
- `test_count_tokens_english_unchanged` — English text → same result as `len//4`
- `test_count_tokens_cjk_higher` — Chinese text → significantly higher than `len//4`
- `test_count_tokens_mixed` — Mixed English + CJK → weighted sum
- `test_preamble_not_merged` — doc with preamble + H2 section → preamble is separate chunk
- `test_preamble_small_merged_by_chunk_document` — small preamble + small section → merged by `chunk_document` naturally

---

## Phase 4: Schema Pipeline Searchability ✅ DONE

**Problem**: `SchemaExtractionPipeline` stores extractions in Postgres but never generates embeddings. `search_knowledge()` queries only Qdrant. All template-based extractions are invisible to search.

**Approach**: After storing each batch of extractions, generate embeddings and upsert to Qdrant. Reuse the existing `EmbeddingService.embed_batch()` and Qdrant repository patterns from the generic pipeline.

### Files

| File | Change |
|------|--------|
| `src/services/extraction/pipeline.py` (SchemaExtractionPipeline) | Add embedding generation after extraction storage |
| `src/services/storage/qdrant_repository.py` | Verify `upsert_batch` interface works for schema extractions |
| `src/config.py` | Add `schema_extraction_embedding_enabled: bool = True` feature flag |

### Implementation Details

**Architecture decision**: Embed at the 20-source commit checkpoint (lines 554-567 in pipeline.py), not per-extraction. This batches embedding calls for efficiency and keeps the commit boundary clean.

**What to embed**: Create a text representation of each extraction's `data` field:
```python
def _extraction_to_text(extraction: Extraction) -> str:
    """Convert extraction data to embeddable text."""
    parts = []
    if extraction.extraction_type:
        parts.append(f"Type: {extraction.extraction_type}")
    if extraction.data:
        for key, value in extraction.data.items():
            if key.startswith("_") or key == "confidence":
                continue
            if value is not None:
                parts.append(f"{key}: {value}")
    return "\n".join(parts)
```

**Integration point** — In `SchemaExtractionPipeline._process_source_batch` (around the commit checkpoint):
```python
# After: self._db.flush() for extractions
if settings.schema_extraction_embedding_enabled:
    await self._embed_extractions(new_extractions)
# Then: self._db.commit()
```

**`_embed_extractions` method**:
1. Create text representations for each extraction
2. Call `self._embedding_service.embed_batch(texts)`
3. Upsert to Qdrant with extraction IDs as point IDs
4. Update extraction records with Qdrant point references

**Dependencies**: `SchemaExtractionPipeline` needs `EmbeddingService` and Qdrant repo injected. Currently it only takes `(orchestrator, db)`. Add optional `embedding_service` and `qdrant_repo` parameters.

**Feature flag**: `schema_extraction_embedding_enabled` defaults to `True` but can be disabled for extraction-only runs (e.g., when embeddings aren't needed or Qdrant is unavailable).

### Qdrant Collection

Schema extractions should use the same Qdrant collection as generic extractions so `search_knowledge()` works without modification. The extraction `id` is already a UUID — use it as the Qdrant point ID.

Payload fields to store in Qdrant (for filtering):
```python
{
    "project_id": str(extraction.project_id),
    "source_id": str(extraction.source_id),
    "source_group": extraction.source_group,  # company name
    "extraction_type": extraction.extraction_type,  # field group name
}
```

### Tests

- `test_schema_pipeline_generates_embeddings` — run schema extraction → verify Qdrant upsert called
- `test_schema_extraction_searchable` — extract + embed → `search_knowledge()` finds it
- `test_embedding_disabled_flag` — `schema_extraction_embedding_enabled=False` → no embedding calls
- `test_embedding_failure_does_not_block_extraction` — embedding service error → extraction still stored in Postgres, warning logged
- `test_extraction_text_representation` — verify `_extraction_to_text` produces sensible output for various field types

---

## Phase 5: Minor Cleanup ✅ DONE

Low-priority fixes. Each is independent.

### 5a. Generic pipeline source status with errors

**File**: `src/services/extraction/pipeline.py:288`

After extraction in `process_source()`, check if `errors` list is non-empty:
```python
if errors:
    self._source_repo.update_status(source_id, SourceStatus.PARTIAL)
else:
    self._source_repo.update_status(source_id, SourceStatus.EXTRACTED)
```

Requires adding `PARTIAL` to `SourceStatus` enum in `src/constants.py`. Check downstream queries that filter on `EXTRACTED` — they should also include `PARTIAL` where appropriate.

### 5b. N+1 source validation

**File**: `src/api/v1/extraction.py:82-93`

Replace per-source query loop with batch query:
```python
sources = source_repo.get_batch(source_uuids)
found_ids = {s.id for s in sources}
missing = set(source_uuids) - found_ids
if missing:
    raise HTTPException(404, f"Sources not found: {missing}")
# Check project membership
wrong_project = [s for s in sources if s.project_id != project_uuid]
if wrong_project:
    raise HTTPException(400, f"Source {wrong_project[0].id} does not belong to project")
```

Add `get_batch(ids: list[UUID])` to `SourceRepository` using `WHERE id IN (...)`.

### 5c. Entity dedup without ID field

**File**: `src/services/extraction/schema_orchestrator.py:435-437`

When no ID field found, use content-based dedup (JSON hash):
```python
elif not entity_id:
    entity_hash = hashlib.md5(json.dumps(entity, sort_keys=True).encode()).hexdigest()
    if entity_hash not in seen_ids:
        seen_ids.add(entity_hash)
        all_entities.append(entity)
    else:
        logger.debug("entity_dedup_by_hash", entity_preview=str(entity)[:100])
```

### 5d. `_infer_page_type` broader matching

**File**: `src/services/extraction/smart_classifier.py:544-568`

Low value. The page_type is informational only and not used in downstream logic. Skip or add a few more patterns if convenient:
```python
patterns = {
    "product": ["product", "catalog", "equipment", "motor", "model"],
    "service": ["service", "solution", "offering"],
    "about": ["company", "about", "overview", "history"],
    "contact": ["contact", "location", "office"],
    "technical": ["specification", "technical", "engineering", "fleet"],
    "pricing": ["pricing", "price", "cost", "plan"],
}
```

### Tests for Phase 5

- `test_source_partial_status_on_error` — extraction with errors → PARTIAL status
- `test_batch_source_validation` — 50 source IDs validated in single query
- `test_entity_dedup_by_hash` — duplicate entities without ID field → deduplicated
- `test_infer_page_type_technical` — group "motor_specifications" → "technical"

---

## Delivery Order & Risk Assessment

| Phase | Risk | LOC est. | Dependencies |
|-------|------|----------|-------------|
| 1. Merge Strategy | Low (isolated method) | ~80 | None |
| 2. Config Hardening | Low (additive) | ~40 | None |
| 3. Chunking Quality | Medium (affects all chunks) | ~60 | None |
| 4. Schema Searchability | Medium (new integration) | ~150 | Qdrant access |
| 5. Minor Cleanup | Low | ~60 | Phase 5a needs new enum value |

Phases 1-3 have zero dependencies and can be done in parallel.
Phase 4 is independent but larger — recommended after 1-3 are stable.
Phase 5 items are each independent and can be picked off opportunistically.

---

## Key Files Summary

| File | Phases |
|------|--------|
| `src/services/extraction/schema_orchestrator.py` | 1, 5c |
| `src/services/extraction/field_groups.py` | 1, 2c |
| `src/services/extraction/schema_extractor.py` | 2a, 2c |
| `src/services/extraction/schema_adapter.py` | 1, 2c |
| `src/config.py` | 2b, 4 |
| `src/services/llm/chunking.py` | 3 |
| `src/services/extraction/pipeline.py` | 4, 5a |
| `src/api/v1/extraction.py` | 5b |
| `src/services/extraction/smart_classifier.py` | 5d |
| `src/constants.py` | 5a |
