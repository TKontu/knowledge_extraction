# Report Table Grouping Replacement

## Status: ✅ COMPLETED

**Created:** 2026-02-03
**Completed:** 2026-02-03

## Overview

Replace the current table report aggregation with a cleaner two-option system that preserves URL-level granularity and provides intelligent domain-level aggregation.

**Key Principles:**
- Replace, don't extend - remove old `group_by` options entirely
- Per-URL as default - all field groups from one URL in a single row
- LLM smart merge for domain aggregation - confidence-weighted, source-aware
- Column-by-column merging for precision

## Current vs New

| Current (Remove) | New (Replace With) |
|------------------|-------------------|
| `group_by="source_group"` - loses URL info | `group_by="source"` - one row per URL (DEFAULT) |
| `group_by="extraction"` - field groups scattered | `group_by="domain"` - LLM smart merge per domain |

## Architecture

```
┌───────────────────────────────────────────────────────────┐
│ 60,000 extractions (10,000 URLs × 6 field groups)         │
└─────────────────────────┬─────────────────────────────────┘
                          │
              ┌───────────┴───────────┐
              ▼                       ▼
       group_by="source"        group_by="domain"
       (DEFAULT)                (LLM smart merge)
       10,000 rows              278 rows
       One row per URL          One row per domain
```

### `group_by="source"` (Default)

Consolidates all field group extractions from a single URL into one row:

```
URL: https://acme.com/products
  Extractions:
    - manufacturing: {manufactures_gearboxes: true, ...}
    - services: {provides_services: true, ...}
    - company_info: {company_name: "Acme", ...}

  → Single Row:
    | source_url | source_title | manufactures_gearboxes | provides_services | company_name | ...
```

### `group_by="domain"` (LLM Smart Merge)

Aggregates per-URL data by domain, using LLM to merge each column intelligently:

```
Domain: acme.com (50 URLs)
  Column: manufactures_gearboxes
    - /products: true (confidence: 0.9)
    - /about: true (confidence: 0.8)
    - /contact: null

  → LLM synthesizes best value per column
  → Result: true (confidence: 0.95, sources: [/products, /about])
```

---

## Implementation Checklist

### Phase 1: Data Models ✅

**File:** `src/models.py`

- [x] Update `ReportRequest.group_by` to only accept `"source"` | `"domain"`
- [x] Set default to `"source"`
- [x] Add `include_merge_metadata: bool = False` for domain merge provenance
- [x] Remove old group_by validation

### Phase 2: Column Flattening ✅

**File:** `src/services/reports/schema_table_generator.py`

- [x] Add `get_flattened_columns_for_source()` method:
  - [x] Iterate all field groups
  - [x] Detect column name collisions across groups
  - [x] Use `{group}.{field}` prefix only when collision exists
  - [x] Return: `(columns, labels, field_metadata)`
- [x] Add column metadata for LLM context (description, type, enum values)

### Phase 3: Per-URL Aggregation ✅

**File:** `src/services/reports/service.py`

- [x] Replace `_aggregate_for_table()` with `_aggregate_by_source()`:
  - [x] Query extractions grouped by `source_id` with eager-load source
  - [x] For each source_id, merge all extraction.data into single dict
  - [x] Flatten using schema column mapping
  - [x] Include: `source_url`, `source_title`, `domain`, `avg_confidence`
- [x] Remove old `_aggregate_for_table()` method
- [x] Remove old entity list special handling (flatten like other fields)

### Phase 4: LLM Smart Merge Service ✅

**New File:** `src/services/reports/smart_merge.py`

```python
@dataclass
class MergeCandidate:
    value: Any
    source_url: str
    source_title: str | None
    confidence: float | None

@dataclass
class MergeResult:
    value: Any
    confidence: float
    sources_used: list[str]
    reasoning: str | None

class SmartMergeService:
    async def merge_column(
        self,
        column_name: str,
        column_meta: ColumnMetadata,
        candidates: list[MergeCandidate],
    ) -> MergeResult
```

- [x] Implement `SmartMergeService.__init__(llm_client, settings)`
- [x] Implement `merge_column()`:
  - [x] Short-circuit: all null → return null
  - [x] Short-circuit: single non-null → return it
  - [x] Short-circuit: all identical → return without LLM
  - [x] Otherwise: call LLM with merge prompt
- [x] Build merge prompt with column context and candidates
- [x] Parse JSON response to MergeResult
- [x] Handle LLM errors gracefully (fallback to highest confidence)

### Phase 5: Domain Aggregation ✅

**File:** `src/services/reports/service.py`

- [x] Implement `_aggregate_by_domain()`:
  - [x] Call `_aggregate_by_source()` first
  - [x] Extract domain from `source_url`
  - [x] Group source rows by domain
  - [x] For each domain:
    - [x] For each column: collect candidates from all URLs
    - [x] Call `SmartMergeService.merge_column()`
    - [x] Build merged row
  - [x] Parallelize: merge columns concurrently within domain
- [x] Add merge metadata to response when requested

### Phase 6: Simplify Report Generation ✅

**File:** `src/services/reports/service.py`

- [x] Update `_generate_table_report()`:
  - [x] Remove `group_by` parameter handling for old values
  - [x] Dispatch to `_aggregate_by_source()` or `_aggregate_by_domain()`
- [x] Remove `_build_entity_table()` (no longer needed)
- [x] Remove entity list special casing in aggregation
- [x] Simplify `_build_markdown_table()` if needed

### Phase 7: Configuration ✅

**File:** `src/config.py`

- [x] `smart_merge_max_candidates: int = 100` (URLs per domain per column)
- [x] `smart_merge_min_confidence: float = 0.3` (exclude low-confidence candidates)

### Phase 8: API Cleanup ✅

**File:** `src/api/v1/reports.py`

- [x] Update endpoint docs for new `group_by` values
- [x] Add `include_merge_metadata` query param
- [x] Remove references to old grouping options

### Phase 9: MCP Tool Update ✅

**File:** `src/ke_mcp/tools/reports.py`

- [x] Update `create_report` tool:
  - [x] `group_by`: `"source"` (default, one row per URL) or `"domain"` (LLM merged)
  - [x] Remove old option descriptions

### Phase 10: Cleanup ✅

- [x] Remove any dead code from old aggregation
- [x] Update docstrings throughout
- [x] Remove unused imports

---

## Test Coverage ✅

### Unit Tests

| Test File | Purpose |
|-----------|---------|
| `tests/test_report_table.py` | All report table tests consolidated |

### Key Test Cases (14 tests passing)

**Column Flattening (TestSchemaTableGenerator):**
- [x] Metadata columns included at start
- [x] Field group fields flattened correctly
- [x] Column name collision → prefixed column names
- [x] Extraction type to fields mapping

**Smart Merge (TestSmartMergeService):**
- [x] All identical values → no LLM call, return value
- [x] Single non-null → no LLM call, return value
- [x] All null → return null
- [x] Mixed values → LLM synthesis
- [x] Low confidence filtering
- [x] LLM error → fallback to highest confidence

**Markdown Table (TestMarkdownTable):**
- [x] Basic table generation
- [x] Newline handling
- [x] Pipe character escaping
- [x] List item sanitization

---

## Files Changed

| File | Action |
|------|--------|
| `src/services/reports/service.py` | Major refactor |
| `src/services/reports/smart_merge.py` | New |
| `src/services/reports/schema_table_generator.py` | Add flattening |
| `src/models.py` | Simplify group_by |
| `src/api/v1/reports.py` | Update docs |
| `src/config.py` | Add merge settings |
| `src/ke_mcp/tools/reports.py` | Update tool |
| `tests/test_report_*.py` | New tests |

**Remove/deprecate:**
- Old `_aggregate_for_table()` logic
- `_build_entity_table()`
- Old `group_by` value handling

---

## LLM Merge Prompt

```
You are merging extracted data from multiple pages of the same company website.

Field: {column_name}
Type: {field_type}
Description: {column_description}

Values from different pages:
{for c in candidates}
- {c.source_url}: {c.value} (confidence: {c.confidence})
{endfor}

Synthesize the most reliable value. Consider:
1. Page relevance (product pages authoritative for specs, about pages for company info)
2. Confidence scores
3. Agreement across sources
4. Specificity over vagueness

Return JSON:
{"value": <merged>, "confidence": <0-1>, "sources_used": [<urls>], "reasoning": "<brief>"}
```

---

## Performance Notes

- **Per-URL**: Single query + O(n) processing, fast
- **Domain merge**: O(domains × columns) potential LLM calls
  - Mitigated by short-circuits (identical, single, null)
  - Parallel column merging per domain
  - Expected: ~10-20% of columns actually need LLM

---

*Created 2026-02-03*
