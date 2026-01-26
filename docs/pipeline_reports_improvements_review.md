# Pipeline Review: Reports Module Improvements

## Flow

```
POST /api/v1/projects/{project_id}/reports
  → reports.py:create_report
  → ReportService.generate
  → _generate_*_report methods
  → ReportSynthesizer.synthesize_facts (for SINGLE/COMPARISON)
  → _aggregate_for_table (for TABLE)
  → SchemaExtractionOrchestrator._merge_chunk_results (extraction phase)
```

## Critical (must fix)

None found.

## Important (should fix)

- [x] `schema_orchestrator.py:205` - **Docstring out of sync with implementation** - FIXED
  Docstring said "text: Take longest non-empty" but implementation concatenates unique values. Updated to: "text/enum: Dedupe and concatenate unique values with '; '"

## Minor

- [ ] `service.py:609` vs `schema_orchestrator.py:254` - **Slightly inconsistent empty checks**
  Uses `if v` (truthy) vs `if v is not None`. Both work correctly for text values but inconsistent style.

## Verified False Positives

- ✅ `synthesis.py:234` - List mutation is fine. The `all_conflicts` list is created fresh in `_unify_chunk_results` (line 161) via list comprehension, not passed from outside. Mutation only affects locally-created list.

- ✅ `synthesis.py:160-161` - Source order via `set()` is fine. Source URIs are for attribution only; order is not semantically meaningful.

## Verified Working

- ✅ `max_detail_extractions` properly flows from `ReportRequest` → `generate()` → `_generate_comparison_report()`
- ✅ Text aggregation concatenates unique values with "; " separator in both locations
- ✅ Two-pass synthesis properly chains chunk synthesis → unification
- ✅ Fallback paths exist for LLM failures in both synthesis and unification
- ✅ `_fallback_unify` properly reduces confidence (0.9 multiplier)
- ✅ Division in `_unify_chunk_results` is safe - only called when `len(chunk_results) > 1`

## Test Coverage

- ✅ `test_chunked_synthesis_uses_two_pass_unification` - verifies unification pass
- ✅ `test_unification_fallback_on_llm_failure` - verifies fallback behavior
- ✅ `test_fallback_unify_preserves_chunk_content` - verifies section preservation
- ✅ `test_aggregate_for_table_*` - verifies table aggregation logic
