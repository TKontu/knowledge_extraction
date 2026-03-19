# Pipeline Review: Grounding → Merge → Consolidation → Report

**Date**: 2026-03-11
**Scope**: Recently edited grounding gate, consolidation weight logic, and downstream data flow
**Method**: Code trace + DB verification against production data

## Flow

```
schema_orchestrator.py:_parse_chunk_to_v2 (inline grounding)
  → schema_orchestrator.py:apply_grounding_gate (filter/rescue)
  → chunk_merge.py:merge_chunk_results (per-source merge)
  → [stored in DB as Extraction]
  → consolidation_service.py:consolidate_source_group (loads all extractions)
  → consolidation.py:consolidate_extractions (weight + strategy)
  → [stored as ConsolidatedExtraction]
  → reports/consolidated_builder.py (renders to sheets)
  → reports/service.py (generates report)
```

---

## Critical (must fix before consolidation)

### 1. `force=True` re-extraction creates duplicates that poison consolidation
**Files**: `pipeline.py:137-152`, `consolidation_service.py:67-76`

**Verified in production DB:**
- **179,888 total extractions**, only **46,954 unique** (source_id, extraction_type) combos → **132,934 duplicate rows** (74%)
- **85,990 are v1** (no grounding scores), **93,898 are v2** (with inline grounding)
- v1 data has zero grounding info: all `grounding_scores` are NULL or `{}`
- Per `effective_weight()` line 289: `grounding_score is None → return confidence` — v1 data votes at **full confidence weight**

`pipeline.py:137-152` does a plain `self._db.add(extraction)` — no deletion of old rows, no upsert. No unique constraint on `(source_id, extraction_type)` exists on the Extraction table.

**Impact**: If consolidation runs now, 85K old v1 extractions (no grounding, potentially hallucinated) mix with 93K new v2 extractions (grounding-gated). v1 data votes at full confidence, overwhelming the quality improvements. `frequency()` counts by occurrence — v1 values add ~2x more votes.

---

## Important (should fix)

### 2. `any_true()` returns `None` for all-ungrounded booleans → "N/A" in reports
**Files**: `consolidation.py:197-218`

**Verified by running the function:**
```
All True, all weight=0:  any_true() = None
Mixed True/False, all weight=0: any_true() = None
All False, all weight=0: any_true() = False
```

After the `effective_weight` fix, boolean fields where all extractions have `grounding=0.0` get `weight=0`. `any_true()` requires `weight > 0` to count True votes. With zero weighted True values but `has_any_true=True`, it returns `None` (insufficient evidence).

Reports render `None` as "N/A". This is semantically correct (no grounded evidence = can't confirm), but users may not understand why a boolean field shows "N/A" instead of True/False.

**This will actually occur**: ~41% of services/manufacturing boolean extractions have `grounding=0.0`. After dedup cleanup (Issue #1), if only v2 data remains, many booleans will consolidate to None/N/A.

### 3. `frequency()` doesn't filter by weight — but only matters because of Issue #1
**File**: `consolidation.py:76-111`

`frequency()` counts occurrences regardless of weight. Zero-weight values count equally.

**Verified**: No v2 string/list fields with `grounding=0.0` exist in DB (grounding gate drops them before storage). So this is NOT a problem for v2-only data. However, when v1 data mixes in (Issue #1), v1 string values have `grounding_score=None → weight=confidence` — they're not zero-weight, they're full-weight without grounding verification.

**Conclusion**: This is a dependency of Issue #1, not an independent problem. Fix Issue #1 (remove v1 duplicates) and this becomes moot.

---

## Removed from original review (not real issues)

### ~~`union_dedup()` ignores weight~~ — NOT A REAL ISSUE
**Verified**: Zero list fields with `grounding=0.0` exist in v2 data. The grounding gate drops ungrounded list items before they're stored. `union_dedup` never sees zero-weight data in practice.

### ~~`longest_top_k()` picks zero-weight text~~ — NOT A REAL ISSUE
Text/summary fields use `grounding_mode="none"`, so `effective_weight` returns confidence directly. No text field can have `weight=0` unless `confidence=0`. Theoretical only.

### ~~Stale docstring~~ — MINOR HOUSEKEEPING
Real but not a functional issue. Update when touching the function.

### ~~Default `grounding=1.0` / `confidence=0.5` for missing keys~~ — NOT A REAL ISSUE
All v2 data has explicit grounding and confidence values. These defaults never trigger.

### ~~Boolean null placeholders flow through consolidation~~ — NOT A REAL ISSUE
Verified: `any_true([WeightedValue(False, 0.0, ...)...]) = False` — null placeholders produce correct `False` result with zero weight. No incorrect behavior.

---

## Summary (verified issues only)

| # | Severity | Issue | Verified? | Impact |
|---|----------|-------|-----------|--------|
| 1 | **Critical** | 132,934 duplicate extractions (v1+v2 mixed) | DB query confirmed | Consolidation will mix ungrounded v1 data with grounded v2 data |
| 2 | Important | `any_true()` → None for all-ungrounded booleans | Function test confirmed | ~41% of booleans will show "N/A" instead of True/False |
| 3 | Dependent on #1 | `frequency()` ignores weight | Only matters if v1+v2 mixed | Moot if Issue #1 is fixed first |

**Action required**: Delete the 85,990 v1 duplicate extractions (or filter to latest per source) before running consolidation.
