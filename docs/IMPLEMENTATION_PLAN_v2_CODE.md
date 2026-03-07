# Extraction v2 — Code-Level Implementation Plan

**2026-03-07. Maps IMPLEMENTATION_PLAN_v2.md architecture to concrete code changes.**

## Overview

Three categories for every affected file:
- **NEW** — file/class/function doesn't exist yet
- **MODIFY** — existing code changes signature or behavior
- **OBSOLETE** — removed after v2 is validated (keep for v1 compat during migration)

---

## Phase 1: Data Model & Types

### 1A. New file: `src/services/extraction/extraction_items.py`

Core dataclasses for the v2 per-field structured format.

```python
# NEW dataclasses
@dataclass(frozen=True)
class SourceLocation:
    """Where in the source a value was found."""
    heading_path: list[str]     # ["Products", "Gearboxes"] from chunk.header_path
    char_offset: int | None     # Position of quote in full source content
    char_end: int | None
    chunk_index: int            # Which chunk (processing provenance, not storage key)

@dataclass
class FieldItem:
    """Single extracted value with full provenance."""
    value: Any
    confidence: float
    quote: str | None
    grounding: float            # min(quote_in_source, value_in_quote)
    location: SourceLocation | None

@dataclass
class ListValueItem:
    """One item from a multi-value list field."""
    value: Any
    quote: str | None
    grounding: float
    location: SourceLocation | None

@dataclass
class EntityItem:
    """One entity from an entity list."""
    fields: dict[str, Any]      # {field_name: value, ...}
    confidence: float
    quote: str | None
    grounding: float
    location: SourceLocation | None

@dataclass
class ChunkExtractionResult:
    """Structured result from one chunk, before merge."""
    chunk_index: int
    field_items: dict[str, FieldItem]       # single-answer + summary fields
    list_items: dict[str, list[ListValueItem]]  # multi-value fields
    entity_items: dict[str, list[EntityItem]]   # entity list fields
```

Utility functions in same file:

```python
def locate_in_source(quote: str, full_content: str, chunk) -> SourceLocation | None:
    """Compute SourceLocation by finding quote in full source content."""
    # Uses chunk.header_path for heading_path
    # Finds char_offset via normalized substring search in full_content
    # Falls back to chunk-relative offset if full_content search fails

def read_field_value(data: dict, field_name: str, data_version: int = 1) -> Any:
    """Universal reader: extracts value from v1 (flat) or v2 (structured) data format."""
    # v1: data[field_name]
    # v2: data[field_name]["value"] or data[field_name]["items"][*]["value"]

def to_v2_data(field_items, list_items, entity_items, group) -> dict:
    """Serialize ChunkExtractionResult fields into v2 JSON for Extraction.data."""

def v2_to_flat(data: dict) -> dict:
    """Convert v2 structured data to v1-compatible flat dict for backward compat."""
```

### 1B. Modify: `src/services/extraction/field_groups.py`

```python
# MODIFY FieldDefinition — add "summary" as valid field_type
# Line 17: field_type docstring: add "summary"

# MODIFY VALID_MERGE_STRATEGIES — add new strategies
# Line 7-9: Add "longest_confident", "llm_synthesize"
VALID_MERGE_STRATEGIES = frozenset({
    "highest_confidence", "max", "min", "concat", "majority_vote",
    "merge_dedupe", "longest_confident", "llm_synthesize"
})

# MODIFY FieldGroup — add cardinality helper
@property
def cardinality(self) -> str:
    """Resolve cardinality: 'single' | 'multi_value' | 'entity_list' | 'summary'."""
    if self.is_entity_list:
        return "entity_list"
    # Per-field cardinality computed from field_type
    # (used when iterating fields within a group)
```

### 1C. Modify: `src/orm_models.py`

```python
# MODIFY Extraction class (line 264-319)
# ADD column:
data_version: Mapped[int] = mapped_column(
    Integer, default=1, server_default="1", nullable=False
)
# data_version=1: legacy flat format {field: value, _quotes: {...}, confidence: 0.8}
# data_version=2: per-field structured format (see extraction_items.py)

# OBSOLETE (after v2 validated): grounding_scores column
# Keep column but stop writing to it for v2 extractions (grounding is inside data)
```

### 1D. DB Migration

```
alembic revision --autogenerate -m "add_data_version_to_extractions"
```

- Add `data_version` column (integer, default 1, not null)
- No data migration needed — all existing rows are v1

---

## Phase 2: LLM Response Format

### 2A. Modify: `src/services/extraction/schema_extractor.py`

#### `_build_system_prompt()` (line 379-434) — MODIFY

Change response format instruction from flat to per-field structured:

```
# CURRENT (v1):
# Output JSON with exactly these fields and a "confidence" field (0.0-1.0)

# NEW (v2):
# Output JSON where each field has {value, confidence, quote}:
# {
#   "fields": {
#     "company_name": {"value": "Acme", "confidence": 0.95, "quote": "Acme Corp is..."},
#     "employee_count": {"value": 500, "confidence": 0.6, "quote": "approximately 500"}
#   }
# }
```

Keep existing prompt as `_build_system_prompt_v1()` for backward compat during migration.

#### `_build_entity_list_system_prompt()` (line 436-524) — MODIFY

Add `has_more` signal and per-entity confidence:

```
# NEW response format:
# {
#   "products": [
#     {"name": "X", "type": "Y", ..., "_confidence": 0.9, "_quote": "..."},
#   ],
#   "has_more": true
# }
```

Add `already_found` parameter for pagination:

```python
def _build_entity_list_system_prompt(
    self, field_group, strict_quoting=False,
    already_found: list[str] | None = None,   # NEW — entity IDs already extracted
) -> str:
```

When `already_found` is provided, add exclusion instruction:
```
Already extracted entities (DO NOT repeat these): [A, B, C, ...]
Extract ONLY entities NOT in this list.
```

#### `_build_user_prompt()` (line 526-550) — NO CHANGE

#### `_apply_defaults()` (line 552-565) — OBSOLETE for v2

Defaults are applied during merge, not after LLM response. Keep for v1 compat.

#### `extract_field_group()` — MODIFY

Add response parsing for v2 format:

```python
# NEW method
def _parse_v2_response(self, raw: dict, field_group: FieldGroup) -> dict:
    """Parse v2 per-field structured response into normalized format."""
    # Handles both:
    #   {"fields": {"name": {"value": ..., "confidence": ..., "quote": ...}}}
    #   and fallback to v1 flat format (graceful degradation if LLM ignores format)

# NEW method
def _detect_response_format(self, raw: dict) -> int:
    """Detect if LLM returned v1 (flat) or v2 (structured) format."""
    # Returns 1 or 2
```

#### Entity pagination loop — NEW in `schema_extractor.py` or `schema_orchestrator.py`

```python
async def extract_entity_list_paginated(
    self, content: str, field_group: FieldGroup, source_context: str | None,
) -> tuple[list[dict], float]:
    """Extract entities with iterative pagination.

    Loop:
    1. Call LLM → get entities + has_more
    2. If has_more=True and not stalled: add to already_found, re-call
    3. Stall detection: if consecutive_duplicates >= 2, stop
    4. Empty response: stop
    5. Schema max_items cap: stop
    """
```

### 2B. Modify: `src/services/extraction/schema_validator.py`

```python
# MODIFY validate() (line 28-50)
# Must handle v2 format: data["fields"][name]["value"] instead of data[name]
# Add data_version parameter or auto-detect

def validate(self, data: dict, group: FieldGroup) -> tuple[dict, list[dict]]:
    """Validate v1 or v2 format data against schema."""
    if "fields" in data:
        return self._validate_v2(data, group)
    return self._validate_v1(data, group)  # existing logic

# NEW method
def _validate_v2(self, data: dict, group: FieldGroup) -> tuple[dict, list[dict]]:
    """Validate per-field structured format."""
    # For each field in data["fields"]:
    #   coerce field["value"] using existing _coerce_value()
    #   validate confidence is 0.0-1.0

# MODIFY _coerce_value() (line 139-158)
# Add "summary" field type — treated same as "text" (string, no coercion)
```

---

## Phase 3: Inline Grounding

### 3A. Modify: `src/services/extraction/grounding.py`

```python
# MODIFY GROUNDING_DEFAULTS (line 19-27) — add summary
GROUNDING_DEFAULTS["summary"] = "none"

# NEW function — replaces compute_chunk_grounding for v2
def ground_field_item(
    field_name: str,
    item: FieldItem,
    chunk_content: str,
    field_type: str,
) -> float:
    """Complete inline grounding for one field item.

    Combines Layer A (quote-in-source) and Layer B (value-in-quote):
      grounding = min(quote_in_source, value_in_quote)

    For grounding_mode="none" (summary, text): returns 1.0
    For grounding_mode="semantic" (boolean): returns quote_in_source only
    For grounding_mode="required": returns min(A, B)
    """

# NEW function
def ground_entity_item(
    entity: EntityItem,
    chunk_content: str,
) -> float:
    """Inline grounding for one entity. Quote-in-source only (entity-level)."""

# OBSOLETE for v2 inline use (keep for v1 backfill compat):
# - compute_chunk_grounding()        → replaced by ground_field_item per field
# - compute_chunk_grounding_entities() → replaced by ground_entity_item per entity
# - compute_grounding_scores()       → was backfill-only, now inline via ground_field_item

# NO CHANGE (used by both v1 and v2):
# - verify_quote_in_source()         → Layer A, called by ground_field_item
# - verify_numeric_in_quote()        → Layer B helper
# - verify_string_in_quote()         → Layer B helper
# - verify_list_items_in_quote()     → Layer B helper
```

### 3B. Modify: `src/services/extraction/schema_orchestrator.py`

#### `extract_chunk_with_semaphore()` (line 329-405) — REWRITE

```python
async def extract_chunk_with_semaphore(chunk, chunk_idx: int) -> ChunkExtractionResult | None:
    """Extract + ground one chunk, return structured result."""
    # 1. Call extractor (v2 prompt format)
    # 2. Parse response into FieldItem/ListValueItem/EntityItem
    # 3. For each item: call ground_field_item() / ground_entity_item() INLINE
    # 4. Compute SourceLocation via locate_in_source(quote, full_content, chunk)
    # 5. If grounding ratio too low: retry with strict quoting (existing logic)
    # 6. Return ChunkExtractionResult
```

Key change: grounding happens **per item** immediately after extraction, not as a batch post-step.

#### `_merge_chunk_results()` (line 482-614) — REPLACE

Replace with cardinality-based merge. New function (can be in same file or new `chunk_merge.py`):

```python
def merge_chunk_results(
    chunk_results: list[ChunkExtractionResult],
    group: FieldGroup,
) -> dict:
    """Merge chunk results using cardinality-appropriate strategies.

    Returns v2 structured data dict ready for Extraction.data.
    """
    merged = {}
    for field in group.fields:
        cardinality = _field_cardinality(field)
        if cardinality == "single":
            merged[field.name] = merge_single_answer(field, chunk_results)
        elif cardinality == "boolean":
            merged[field.name] = merge_boolean(field, chunk_results)
        elif cardinality == "multi_value":
            merged[field.name] = merge_list_values(field, chunk_results)
        elif cardinality == "summary":
            merged[field.name] = merge_summary(field, chunk_results)
    return merged
```

New merge functions:

```python
def merge_single_answer(field, chunks) -> dict:
    """Best item by grounding*confidence. Losers stored as alternatives."""
    # Returns: {"value": X, "confidence": 0.9, "quote": "...", "grounding": 0.95,
    #           "location": {...}, "alternatives": [{...}, ...]}

def merge_boolean(field, chunks) -> dict:
    """Credible True wins (any True with confidence >= 0.5)."""

def merge_list_values(field, chunks) -> dict:
    """Union across chunks, per-item dedup, each item keeps provenance."""
    # Returns: {"items": [{"value": "ISO 9001", "quote": "...", "grounding": 0.9}, ...]}

def merge_summary(field, chunks) -> dict:
    """Longest confident text wins (default). Optional LLM synthesis."""
    # Returns: {"value": "...", "confidence": 0.8, "evidence": [...]}

def merge_entities(entity_results, group) -> dict:
    """Union + dedup by ID fields. Per-entity grounding preserved."""
    # Returns: {"items": [{"fields": {...}, "confidence": 0.9, "quote": "...",
    #           "grounding": 0.85, "location": {...}}, ...], "has_more": false}
```

#### Functions that become OBSOLETE (remove after v2 validated):

| Function | Lines | Reason |
|----------|-------|--------|
| `_pick_highest_confidence()` | 433-454 | Replaced by `merge_single_answer` |
| `_get_merge_strategy()` | 456-480 | Replaced by cardinality-based dispatch |
| `_merge_chunk_results()` | 482-614 | Replaced by `merge_chunk_results()` |
| `_merge_entity_lists()` | 616-716 | Replaced by `merge_entities()` |
| `_detect_conflicts()` | 718-779 | Conflicts captured as `alternatives` in merge |
| `_is_empty_result()` | 781-823 | Replaced by checking if all field items are null |

That's ~390 lines removed, ~250 lines added (net reduction).

#### `extract_all_groups()` — MODIFY (minor)

Pass `full_content` to chunk extraction so `locate_in_source()` can compute positions against full source.

---

## Phase 4: Storage & Pipeline

### 4A. Modify: `src/services/extraction/pipeline.py`

#### `extract_source()` (line 68-151) — MODIFY

```python
# Line 136-148: Create Extraction with v2 format
extraction = Extraction(
    project_id=source.project_id,
    source_id=source.id,
    data=result["data"],           # v2 structured format
    data_version=2,                # NEW
    extraction_type=result["extraction_type"],
    source_group=context_value,
    confidence=result.get("confidence"),
    grounding_scores=None,         # CHANGE: grounding is inside data for v2
    profile_used=schema_name,
    chunk_context=chunk_context,
)
```

### 4B. Modify: `src/services/extraction/schema_adapter.py`

```python
# Line ~444: Add "summary" to valid field types
# Currently validates: "boolean", "integer", "text", "list", "float", "enum"
# Add: "summary"
```

---

## Phase 5: Downstream Consumers (v2 Format Awareness)

All consumers that read `Extraction.data` must handle both v1 and v2.
Use `read_field_value(data, field, data_version)` from `extraction_items.py`.

### 5A. `src/services/extraction/embedding_pipeline.py`

#### `extraction_to_text()` (line 46-76) — MODIFY

```python
@staticmethod
def extraction_to_text(extraction) -> str:
    version = getattr(extraction, 'data_version', 1)
    if version == 2:
        return _extraction_to_text_v2(extraction.data, extraction.extraction_type)
    # existing v1 logic unchanged
```

New helper `_extraction_to_text_v2()`:
- Iterates `data[field]["value"]` for single fields
- Iterates `data[field]["items"]` for list/entity fields
- Skips `_` prefixed keys and metadata

### 5B. `src/services/extraction/consolidation_service.py`

#### `consolidate_source_group()` (line 33-112) — MODIFY

```python
# Line 92-100: Convert ORM to dicts — add data_version
ext_dicts = [
    {
        "data": ext.data,
        "data_version": getattr(ext, 'data_version', 1),  # NEW
        "confidence": ext.confidence if ext.confidence is not None else 0.5,
        "grounding_scores": ext.grounding_scores or {},
        "source_id": str(ext.source_id),
    }
    for ext in type_extractions
]
```

### 5C. `src/services/extraction/consolidation.py`

#### `consolidate_extractions()` — MODIFY

Must read per-field confidence and grounding from v2 data instead of extraction-level:

```python
# CURRENT (v1): weight = ext["confidence"] * grounding_scores.get(field, 1.0)
# NEW (v2):     weight = data[field]["confidence"] * data[field]["grounding"]

# Add: "summary" to STRATEGY_DEFAULTS
STRATEGY_DEFAULTS["summary"] = "longest_top_k"

# MODIFY WeightedValue — add grounding_score
@dataclass(frozen=True)
class WeightedValue:
    value: Any
    weight: float
    source_id: str = ""
    grounding_score: float = 1.0  # NEW — enables grounding-aware consolidation
```

### 5D. `src/services/reports/service.py`

#### Line 215: reads `ext.data` — MODIFY

```python
# Must use read_field_value() or v2_to_flat() to normalize
# Report generation needs flat values, not structured items
data = v2_to_flat(ext.data) if getattr(ext, 'data_version', 1) == 2 else ext.data
```

### 5E. `src/api/v1/extraction.py`

#### Line 249: returns `extraction.data` — MODIFY (minor)

```python
# Option A: Return v2 data as-is (richer API response)
# Option B: Add ?format=flat query param for backward compat
"data": extraction.data,
"data_version": getattr(extraction, 'data_version', 1),  # ADD
```

### 5F. `src/api/v1/export.py`

Exports `extraction.data` directly. Add `data_version` to export and optionally flatten:

```python
# For CSV/tabular export: always flatten v2 to v1 format
# For JSON export: include data_version field
```

### 5G. `src/api/v1/projects.py`

#### Backfill grounding endpoint (line 362-382) — MODIFY

```python
# v2 extractions don't need backfill (grounding is inline)
# Skip v2 extractions in backfill loop:
if getattr(ext, 'data_version', 1) >= 2:
    continue  # grounding already inline
```

### 5H. `scripts/backfill_grounding_scores.py`

OBSOLETE for v2 extractions. Add early skip:

```python
if ext.data_version >= 2:
    continue  # v2 has inline grounding
```

### 5I. `src/services/storage/search.py`

#### Line 121: passes `extraction.data` — MODIFY (minor)

Needs to extract flat values for search indexing. Use `v2_to_flat()`.

---

## Phase 6: Entity Pagination

### 6A. New logic in `schema_orchestrator.py` or `schema_extractor.py`

```python
async def _extract_entities_paginated(
    self,
    chunk_content: str,
    field_group: FieldGroup,
    source_context: str | None,
) -> list[EntityItem]:
    """Iterative entity extraction with stall/convergence detection.

    Safety controls:
    1. has_more=False from LLM → stop
    2. Empty response (0 new entities) → stop
    3. Consecutive stall (same entities returned 2x) → stop
    4. Total entities >= field_group.max_items → stop
    5. Max iterations = ceil(max_items / batch_size) + 2 safety margin
    """
    all_entities = []
    already_found_ids = []
    consecutive_stalls = 0
    MAX_CONSECUTIVE_STALLS = 2

    for iteration in range(max_iterations):
        raw = await self._extractor.extract_field_group(
            content=chunk_content,
            field_group=field_group,
            source_context=source_context,
            already_found=already_found_ids if already_found_ids else None,
        )

        new_entities = _extract_new_entities(raw, already_found_ids)
        has_more = raw.get("has_more", False)

        if not new_entities:
            break

        # Stall detection
        if _all_duplicates(new_entities, all_entities):
            consecutive_stalls += 1
            if consecutive_stalls >= MAX_CONSECUTIVE_STALLS:
                break
        else:
            consecutive_stalls = 0

        all_entities.extend(new_entities)
        already_found_ids.extend(_entity_ids(new_entities, field_group))

        if not has_more:
            break
        if field_group.max_items and len(all_entities) >= field_group.max_items:
            break

    return all_entities
```

---

## Files Summary

### New Files

| File | Purpose |
|------|---------|
| `src/services/extraction/extraction_items.py` | FieldItem, ListValueItem, EntityItem, SourceLocation, ChunkExtractionResult + utility functions |
| `alembic/versions/xxx_add_data_version.py` | DB migration for `data_version` column |

### Modified Files (by change size)

| File | Change Size | What Changes |
|------|------------|--------------|
| `schema_orchestrator.py` | **LARGE** | Rewrite merge logic (~390 lines replaced), inline grounding per item, entity pagination |
| `schema_extractor.py` | **LARGE** | v2 prompt format, response parsing, pagination support, `already_found` param |
| `grounding.py` | **MEDIUM** | Add `ground_field_item()`, `ground_entity_item()`, summary grounding mode |
| `consolidation.py` | **MEDIUM** | Read v2 per-field weights, add summary strategy, WeightedValue.grounding_score |
| `consolidation_service.py` | **SMALL** | Pass data_version in ext_dicts |
| `schema_validator.py` | **SMALL** | v2 format validation, summary field type |
| `field_groups.py` | **SMALL** | Add summary type, new merge strategies, cardinality helper |
| `pipeline.py` | **SMALL** | Set data_version=2, stop writing grounding_scores |
| `schema_adapter.py` | **SMALL** | Accept "summary" field type |
| `embedding_pipeline.py` | **SMALL** | v2 format text extraction |
| `orm_models.py` | **SMALL** | Add data_version column |
| `reports/service.py` | **SMALL** | v2-aware data reading |
| `api/v1/extraction.py` | **TINY** | Add data_version to response |
| `api/v1/export.py` | **TINY** | Flatten v2 for tabular export |
| `api/v1/projects.py` | **TINY** | Skip v2 in grounding backfill |
| `storage/search.py` | **TINY** | Flatten v2 for search indexing |

### Obsolete After v2 Validation

| Item | Location | Replaced By |
|------|----------|-------------|
| `_pick_highest_confidence()` | `schema_orchestrator.py:433-454` | `merge_single_answer()` |
| `_get_merge_strategy()` | `schema_orchestrator.py:456-480` | Cardinality-based dispatch |
| `_merge_chunk_results()` | `schema_orchestrator.py:482-614` | `merge_chunk_results()` |
| `_merge_entity_lists()` | `schema_orchestrator.py:616-716` | `merge_entities()` |
| `_detect_conflicts()` | `schema_orchestrator.py:718-779` | Alternatives in merge |
| `_is_empty_result()` | `schema_orchestrator.py:781-823` | Null-check in new merge |
| `_apply_defaults()` | `schema_extractor.py:552-565` | Defaults in merge |
| `compute_chunk_grounding()` | `grounding.py` | `ground_field_item()` |
| `compute_chunk_grounding_entities()` | `grounding.py` | `ground_entity_item()` |
| `compute_grounding_scores()` | `grounding.py` | Inline in `ground_field_item()` |
| `grounding_scores` column | `orm_models.py:297` | Grounding inside `data` (v2) |
| `backfill_grounding_scores.py` | `scripts/` | Inline grounding (v2) |

---

## Implementation Order

Build incrementally. Each phase is independently deployable and testable.

### Step 1: Foundation (no behavior change)
1. Create `extraction_items.py` with dataclasses + `read_field_value()` + `v2_to_flat()`
2. Add `data_version` column to ORM + migration
3. Add `"summary"` to field types, grounding defaults, valid strategies
4. Tests: unit tests for all new dataclasses and utility functions

### Step 2: v2 Prompt Format + Response Parsing
1. Add `_build_system_prompt_v2()` and `_parse_v2_response()` to `schema_extractor.py`
2. Add `_validate_v2()` to `schema_validator.py`
3. Wire v2 prompt behind a config flag (`extraction.data_version = 2`)
4. Tests: prompt output assertions, response parsing for various LLM outputs

### Step 3: Inline Grounding
1. Add `ground_field_item()` and `ground_entity_item()` to `grounding.py`
2. Rewrite `extract_chunk_with_semaphore()` to ground per-item inline
3. Tests: grounding scores match expected for various field types

### Step 4: Cardinality-Based Merge
1. Implement `merge_single_answer`, `merge_boolean`, `merge_list_values`, `merge_summary`, `merge_entities`
2. Replace `_merge_chunk_results()` call with new `merge_chunk_results()`
3. Tests: merge behavior for each cardinality, edge cases (empty chunks, single chunk)

### Step 5: Entity Pagination
1. Implement `_extract_entities_paginated()` with stall detection
2. Add `already_found` parameter to entity prompt builder
3. Tests: pagination loop, stall detection, max_items cap, convergence

### Step 6: Downstream v2 Support
1. Update all consumers (embedding, consolidation, reports, API, export, search)
2. Use `read_field_value()` / `v2_to_flat()` for backward compat
3. Tests: each consumer works with both v1 and v2 data

### Step 7: Cleanup
1. Remove v1 code paths after production validation
2. Drop `grounding_scores` column (or leave nullable, unused)
3. Remove `_build_system_prompt_v1()`, old merge functions

---

## Config Flag

Add to `ExtractionConfig` facade:

```python
data_version: int = 1  # Set to 2 to enable v2 extraction format
```

This allows gradual rollout:
- `data_version=1`: current behavior, no changes
- `data_version=2`: v2 prompts, inline grounding, cardinality merge

Both versions coexist in the same database. Downstream consumers handle both via `read_field_value()`.

---

## Risk Mitigation

1. **LLM format compliance**: v2 prompt asks for `{"fields": {...}}` structure. If LLM returns flat format, `_detect_response_format()` falls back to v1 parsing. No data loss.

2. **Migration safety**: `data_version` column with default=1 means all existing data is unaffected. v2 is opt-in per extraction run.

3. **Rollback**: If v2 produces worse results, set `data_version=1` in config. All v2 extractions can be re-extracted. No schema migration needed to roll back.

4. **Entity pagination cost**: Stall detection + max_items cap prevent runaway LLM calls. Worst case: `ceil(max_items / batch_size) + 2` calls per chunk per entity group.

5. **Test coverage**: Each phase has independent tests. v1 test suite remains unchanged and must pass throughout.
