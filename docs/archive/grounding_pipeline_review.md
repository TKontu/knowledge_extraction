# Pipeline Review: Grounding Pipeline (Full)

## Flow

```
INLINE (during extraction):
  pipeline.extract_source()
    → orchestrator.extract_all_groups()
      → _extract_chunks_batched()
        → extract_chunk_with_semaphore()
          → extractor.extract_field_group()           # LLM call
          → _source_grounding_ratio(result, chunk)     # quote-in-content check
          → IF ratio < min_ratio: retry strict_quoting # re-extract once
        → _merge_chunk_results() / _merge_entity_lists()
          → per-field grounding scores (value-vs-quote) # string-match
      → group_result["grounding_scores"] = merged["_grounding_scores"]
    → Extraction(grounding_scores=scores)              # stored in DB

BACKFILL (post-hoc for existing extractions):
  POST /projects/{id}/backfill-grounding
    → compute_grounding_scores(ext.data, field_types)  # value-vs-quote only
    → update_grounding_scores_batch()                  # DB update

CONSOLIDATION (downstream consumer):
  consolidation_service.consolidate_source_group()
    → effective_weight(confidence, grounding_score, mode)
    → consolidate_extractions()                        # weighted strategies

LLM VERIFICATION (dead code):
  llm_grounding.py: LLMGroundingVerifier               # never called
```

## Re-verification Results

Each finding re-verified against actual code. False positives removed.

## Critical

### 1. Consolidation cannot process entity list extractions at all — REAL (worse than stated)
`consolidation.py:376-406` — The consolidation loop iterates `field_definitions` (individual field names like `product_name`, `type`) and does `data.get(field_name)` at line 390. But entity list extraction data is structured as:

```json
{"products": [{"name": "Motor X", "type": "AC", "_quote": "Motor X"}], "confidence": 0.8}
```

Individual field names don't exist at the top level — they're nested inside list items. So `data.get("product_name")` returns `None` → `continue` at line 391. **Every field is skipped. Consolidation produces empty records for entity list extraction types.**

The grounding scores mismatch (`{"products": 0.85}` vs lookup by `"product_name"`) is also real but moot — the code never reaches the grounding lookup because field values aren't found.

**Impact**: Entity list consolidation is structurally broken. Not just grounding — no entity list data is consolidated at all.

**Note**: This is a broader architectural gap, not specific to grounding. Entity list extractions have a fundamentally different data shape than field group extractions, and the consolidation pipeline assumes field group shape.

### 2. Backfill endpoint produces no scores for entity list extractions — REAL
`api/v1/projects.py:364` + `grounding.py:169-209` — Same root cause as #1. `compute_grounding_scores()` iterates field names and does `data.get(field_name)`. Entity list data nests fields inside list items, so every field returns `None` → skipped → empty scores.

**Impact**: Backfill produces zero grounding scores for all entity list extraction types.

## Important

### 3. LLM grounding verifier is dead code — REAL
`llm_grounding.py` — `LLMGroundingVerifier` is fully implemented (192 lines) but never imported or called anywhere in the codebase. Verified: only found in its own file.

Config settings exist (`grounding_llm_verify_enabled=True`, `grounding_llm_verify_model=""`) but no code reads them.

**Status**: Dead code. Either wire it in or remove it.

### 4. Source-grounding signal discarded after retry — REAL (design choice)
`_source_grounding_ratio` at lines 353-384 computes `sg_ratio` and logs it, but never stores it. The ratio is used only for the retry decision. The persisted `grounding_scores` only contain value-vs-quote scores.

A fabricated quote that happens to contain the correct value would score 1.0 in the DB despite failing source-grounding. The retry may fix this at extraction time, but the signal is lost for post-hoc analysis.

**Verdict**: Real, but this is a design choice not a bug. The retry mechanism acts on the signal; storing it would be for monitoring/analysis only.

### ~~5. Entity list `_quote` leaked into stored data~~ — FALSE POSITIVE
`_quotes` and `_quote` are **intentionally** stored in `Extraction.data` as provenance metadata. Multiple downstream systems read them back:
- `compute_grounding_scores()` (backfill) reads `data.get("_quotes", {})`
- `compute_source_grounding_scores()` reads `data.get("_quotes", {})`
- `LLMGroundingVerifier.verify_extraction()` reads `data.get("_quotes", {})`

This is the designed mechanism for post-hoc grounding analysis — store quotes alongside data so they can be verified later.

## Minor

### 6. `_collect_quotes` rebuilds `_RESERVED` set on every call — REAL, trivial
`schema_orchestrator.py:63` — Set literal constructed per call. Should be module-level constant. No measurable impact.

### 7. Backfill grounding threshold (0.5) differs from inline source-grounding threshold (0.8) — REAL, by design
Different thresholds for different purposes (value-vs-quote vs quote-in-content). Not a bug.

## Previously Fixed (from earlier review)

- [x] `_quotes` non-string crash — Fixed via `_coerce_quote()` in `score_field()`
- [x] `confidence or 0.5` falsy bug — Fixed with `is not None` check
- [x] Entity list bypass source grounding — Fixed: `_collect_quotes()` + `_source_grounding_ratio()`
- [x] Entity list `strict_quoting` ignored — Fixed: passed through to `_build_entity_list_system_prompt()`

## Summary

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | Consolidation can't process entity list data | **REAL** (Critical) | Needs entity-list-aware consolidation |
| 2 | Backfill produces empty scores for entity lists | **REAL** (Critical) | Needs entity-list-aware grounding |
| 3 | LLM grounding verifier is dead code | **REAL** (Important) | Wire in or remove |
| 4 | Source-grounding signal discarded | **REAL** (Design choice) | Optional: persist for analysis |
| 5 | `_quote`/`_quotes` in stored data | **FALSE POSITIVE** | Intentional provenance metadata |
| 6 | `_RESERVED` set rebuilt per call | REAL (Trivial) | Module-level constant |
| 7 | Grounding threshold inconsistency | REAL (By design) | None needed |

**Root cause**: #1 and #2 share the same fundamental issue — entity list extraction data has a different shape (`{"products": [...]}`) than field group data (`{"company_name": "ABB", ...}`), and both consolidation and backfill-grounding assume field group shape. The inline source-grounding retry (just fixed) works because it uses `_collect_quotes()` which handles both shapes.
