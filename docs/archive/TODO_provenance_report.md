# TODO: 3-Sheet Provenance Report + Boolean Threshold Fix

**Created**: 2026-03-11
**Status**: COMPLETE
**Completed**: 2026-03-12

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

### Part A: Fix boolean threshold — DONE

- [x] **A1.** `any_true()` default changed to `min_count=1`
- [x] **A2.** Tests updated and passing

### Part B: Add winning_weight to consolidation — DONE

- [x] **B1.** `winning_weight: float = 0.0` added to `ConsolidatedField`
- [x] **B2.** `consolidate_field()` computes `winning_weight` per strategy
- [x] **B3.** `consolidation_service.py` persists `winning_weight` in provenance JSONB
- [x] **B4.** Tests added for winning_weight per strategy

### Part C: 3-sheet report — DONE (design changed)

The `provenance_sheets: bool` field was **not added** as planned. Instead, provenance sheets are **always generated** for xlsx output. The 4 API params (`layout`, `entity_focus`, `include_provenance`, `provenance_sheets`) were all removed — the report is always a unified 3-sheet (Data + Quality + Sources).

- [x] **C1.** `ReportRequest` simplified — removed fields instead of adding
- [x] **C2.** `build_provenance_sheets()` implemented with per-entity quality averaging for paginated columns
- [x] **C3.** `service.py` always builds 3 sheets: `_resolve_source_urls()` + `_group_records_by_sg()` extracted as helpers
- [x] **C4.** No threading needed — always on
- [x] **C5.** Tests in `tests/test_provenance_sheets.py` — entity provenance averaging, quality filtering, source resolution

### Part D: Deploy + reconsolidate — PENDING

- [ ] **D1.** Deploy code changes
- [ ] **D2.** Reconsolidate all 3 projects
- [ ] **D3.** Generate test report and verify

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
