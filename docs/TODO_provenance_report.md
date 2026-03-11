# TODO: 3-Sheet Provenance Report + Boolean Threshold Fix

**Created**: 2026-03-11
**Status**: Planned
**Priority**: High — blocks report quality

## Context

Consolidation produces one record per (company, field_group) from N raw extractions. Reports render these to Excel/Markdown. Two issues:

1. **`any_true(min_count=3)`** requires 3+ grounded True votes for booleans. With the weight fix (ungrounded=0), only grounded True values count. Many companies have only 1-2 pages that explicitly state something. Result: most booleans show "N/A" instead of True.

2. **No per-cell provenance in reports.** User needs to see quality scores and source URLs alongside data values to assess trustworthiness. Requirement: 3-sheet Excel where cell B12 on each sheet refers to the same data point.

## Design Decisions

| Question | Decision | Rationale |
|----------|----------|-----------|
| Where to compute quality weight? | Consolidation time, stored in `ConsolidatedField` | Raw `WeightedValue` list only exists during `consolidate_field()` |
| Where to resolve source URLs? | Report-build time in service.py | Keeps consolidation.py pure (no DB). Avoids stale URLs in provenance |
| How to ensure identical sheet dimensions? | Quality/Sources sheets are transforms of the Data sheet | Same columns, same rows, same order, different cell content |
| Entity-level quality? | All entities share the list-level winning_weight | Entity consolidation is via `union_dedup` at the list level |
| New option or breaking change? | New `provenance_sheets: bool` field on ReportRequest | Existing reports unaffected |

## Tasks

### Part A: Fix boolean threshold

- [ ] **A1.** `src/services/extraction/consolidation.py:197` — Change `any_true()` default `min_count=3` → `min_count=1`
- [ ] **A2.** `tests/test_consolidation.py` — Update `test_default_min_count_is_3` → `test_default_min_count_is_1`, flip assertion. Add `test_single_grounded_true_sufficient` (1 True weight>0 + several False → True)

### Part B: Add winning_weight to consolidation

- [ ] **B1.** `src/services/extraction/consolidation.py:53` — Add `winning_weight: float = 0.0` to `ConsolidatedField` dataclass
- [ ] **B2.** `src/services/extraction/consolidation.py:297` — Compute winning_weight in `consolidate_field()` after strategy returns result:
  - frequency/weighted_frequency/any_true: `max(v.weight for v in values if v.value matches result_value)`
  - weighted_median: `mean(v.weight for v in values if v.weight > 0)` (computed value, no direct match)
  - longest_top_k: weight of the string that won
  - union_dedup: `mean(v.weight for v in values)` across all items
- [ ] **B3.** `src/services/extraction/consolidation_service.py:195` — Add `"winning_weight": field.winning_weight` to provenance dict in `_upsert_record()`
- [ ] **B4.** `tests/test_consolidation.py` — Add tests for winning_weight per strategy

### Part C: 3-sheet report option

- [ ] **C1.** `src/models.py` (after line 682) — Add to `ReportRequest`:
  ```python
  provenance_sheets: bool = Field(
      default=False,
      description="3-sheet report: Data + Quality + Sources per group. Requires output_format='xlsx' and group_by='consolidated'.",
  )
  ```
- [ ] **C2.** `src/services/reports/consolidated_builder.py` — Add `build_provenance_sheets()`:
  ```python
  def build_provenance_sheets(
      data_sheet: SheetData,
      records_by_sg: dict[str, dict[str, Any]],
      source_url_map: dict[str, str],
      scalar_columns: list[str],
  ) -> tuple[SheetData, SheetData]:
  ```
  For each cell (row=source_group, col=field_name):
  - Quality sheet: `provenance[field_name]["winning_weight"]` → float 0.00-1.00
  - Sources sheet: `provenance[field_name]["top_sources"]` → resolved URLs via source_url_map

  Returns (quality_sheet, sources_sheet) with identical columns/rows as data_sheet.

- [ ] **C3.** `src/services/reports/service.py:_generate_consolidated_table()` — When `provenance_sheets=True`:
  1. Collect all source_ids from all records' provenance top_sources
  2. Query: `SELECT id, uri FROM sources WHERE id = ANY(:ids)` → build `source_url_map: dict[str, str]`
  3. For each data sheet, call `build_provenance_sheets()` → (quality, sources)
  4. Interleave sheets: `[Companies, Companies Quality, Companies Sources, Products, Products Quality, ...]`
  5. Pass full list to `create_multi_sheet_workbook()`

- [ ] **C4.** Thread `provenance_sheets` parameter from `generate()` → `_generate_consolidated_table()`

- [ ] **C5.** `tests/test_provenance_sheets.py` (new) — Test:
  - Identical dimensions between data/quality/sources sheets
  - Correct winning_weight values in quality cells
  - Correct URL resolution in sources cells
  - Backward compat: old records missing winning_weight → N/A in quality cells
  - Entity sheets get provenance companions too

### Part D: Deploy + reconsolidate

- [ ] **D1.** Deploy code changes
- [ ] **D2.** Reconsolidate all 3 projects (repopulates with min_count=1 + winning_weight)
- [ ] **D3.** Generate test report with `provenance_sheets=true` and verify

## Implementation Order

```
A1, A2 (boolean fix, independent)
  → B1, B2, B3, B4 (winning_weight in consolidation)
    → C1 (model field)
      → C2 (builder), C3+C4 (service wiring) — can be parallel
        → C5 (tests)
          → D1, D2, D3 (deploy + verify)
```

## Files Modified

| File | Changes |
|------|---------|
| `src/services/extraction/consolidation.py` | any_true min_count, ConsolidatedField.winning_weight, consolidate_field() weight computation |
| `src/services/extraction/consolidation_service.py` | Persist winning_weight in provenance JSONB |
| `src/services/reports/consolidated_builder.py` | New build_provenance_sheets() function |
| `src/services/reports/service.py` | Source URL resolution, provenance_sheets wiring, sheet interleaving |
| `src/models.py` | provenance_sheets field on ReportRequest |
| `tests/test_consolidation.py` | Updated + new tests |
| `tests/test_provenance_sheets.py` | New test file |

## Constraints

- `consolidation.py` must remain pure functions (no DB access)
- Import convention: `from config import ...` not `from src.config import ...`
- Repository pattern: takes Session, calls flush() not commit()
- Excel sheet names max 31 chars — entity names like "Products Gearbox Sources" (24) are fine
- Existing report behavior must not change when provenance_sheets=False
