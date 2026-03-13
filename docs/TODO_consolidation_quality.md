# TODO: Consolidation Quality — Field Type Misclassification, Grounding Bypass & Weight Signal Gaps

**Created:** 2026-03-12
**Updated:** 2026-03-13
**Status:** Complete
**Priority:** RESOLVED

## Root Cause: `field_type="text"` Misclassification

**49 of 101 fields (49%) across all projects were typed as `text`.** Only 5 of these are genuinely free text. The remaining 44 are identifiers, locations, names, and short structured values that should be `string` or `enum`.

The `text` type triggered a **double vulnerability** (now fixed):

1. ~~**Grounding bypassed**~~: ✅ FIXED — `GROUNDING_DEFAULTS["text"]` changed from `"none"` → `"semantic"` (Fix 0) → **`"required"` (grounding simplification)**. Text fields now verify value-in-quote + quote-in-source (Layers A+B). Descriptive fields retyped to `summary` (grounding_mode=none).

2. ~~**Wrong consolidation strategy**~~: ✅ FIXED — `STRATEGY_DEFAULTS["text"]` changed from `"longest_top_k"` to `"weighted_frequency"`. Descriptive fields retyped to `summary` (default strategy: `longest_top_k`) or use explicit `consolidation_strategy: llm_summarize`.

3. ~~**Extraction-level confidence ignored**~~: ✅ FIXED — v2 consolidation now caps per-field weight by `max(ext_confidence, 0.3)`.

### Proof Case (pre-fix): Flender HQ = "Bielefeld, Germany" (hallucinated, quality 0.9)

```
1. LLM extracts "Bielefeld, Germany" from a product page (drill/cutter page)
2. "Bielefeld" does NOT appear anywhere in the source text — pure hallucination
3. field_type="text" → grounding_mode="none" → grounding = 1.0 (NEVER CHECKED)
4. Per-field confidence = 0.9 (LLM self-assessed, unverified)
5. Weight = min(confidence=0.9, grounding=1.0) = 0.9
6. Strategy = longest_top_k → "Bielefeld, Germany" (19 chars) beats "Bocholt, Germany" (16 chars)
7. Report shows "Bielefeld, Germany" with quality 0.9
```

## Implemented Fixes

### ✅ Fix 0 → Grounding Simplification: Field type determines grounding behavior
- `src/services/extraction/grounding.py:25` — `"text": "none"` → `"semantic"` (Fix 0) → **`"required"` (simplification)**
- `ground_field_item()` accepts `grounding_mode: str | None` kwarg for rare per-field overrides
- Text fields verify value-in-quote + quote-in-source (Layers A+B). Summary fields = `"none"` (always 1.0).
- All 22 `grounding_mode: required` overrides removed from templates (now redundant)
- 11 descriptive text fields retyped to `summary` across 6 templates (manufacturing_details, notable_for, dimensions_or_measurements, fact_text, source_quote, description, term_text, finding_text)

### ✅ Fix A: Strategy defaults + per-field overrides
- `src/services/extraction/consolidation.py` — `STRATEGY_DEFAULTS["text"]` = `"weighted_frequency"`, removed dead `"string"` key
- `src/services/extraction/schema_orchestrator.py` — passes `field_def.grounding_mode` to `ground_field_item()`
- Template overrides for genuinely free-text fields:
  - `drivetrain_company.yaml`: `manufacturing_details` → `consolidation_strategy: longest_top_k`
  - `wikipedia_articles.yaml`: `notable_for`, `dimensions_or_measurements` → `consolidation_strategy: longest_top_k`

### ✅ Fix B: Extraction-level confidence floor for v2
- `consolidation.py` v2 branch: `weight = min(weight, max(ext_confidence, 0.3))`
- Same cap applied in `_consolidate_entity_list()` for v2 entities
- Floor of 0.3 prevents complete zeroing — low-quality pages still contribute, just can't dominate

### ✅ Fix C: V2 grounding backfill endpoint
- `POST /api/v1/projects/{project_id}/backfill-grounding-v2` — re-computes grounding on stored v2 extractions
- Uses `ground_field_item()` with current defaults (text→semantic)
- Dry-run mode (default=True), batched processing, per-field stats
- `ExtractionRepository.update_v2_data_batch()` — batch update data column

### ✅ Fix D: LLM-based summary consolidation
- New consolidation strategy `"llm_summarize"` added to `VALID_CONSOLIDATION_STRATEGIES`
- Pure function routes `llm_summarize` → `longest_top_k` as sync fallback
- `ConsolidationService._llm_post_process()` — async post-processing step: collects candidates via `get_llm_summarize_candidates()`, calls LLM to synthesize, replaces `ConsolidatedField` value. Falls back to `longest_top_k` on LLM failure.
- Consolidation endpoint: `use_llm=True` creates a background job (`JobType.CONSOLIDATE`) and returns 202 with `job_id`. Non-LLM consolidation remains inline.
- `ConsolidationWorker` — background worker following same pattern as `ExtractionWorker`
- Scheduler: `_run_consolidate_worker()` loop added with 30-minute stale threshold
- Template updates: `manufacturing_details`, `notable_for`, `dimensions_or_measurements` → `consolidation_strategy: llm_summarize`

### ✅ Fix E: Dict-in-list grounding for location fields
- `verify_list_items_in_quote()`: dicts without `name` key now extract ALL string values as grounding targets
- `score_field()`: dict values in list fields check each string value independently (proportional: 2/3 matched = 0.67)
- Each dict value grounded 1:1 — hallucinated locations with no matching values still score 0.0 and get dropped

### ✅ Fix F: Entity per-field grounding
- Added `field_grounding: dict[str, float] | None` to `EntityItem` dataclass
- `ground_entity_fields()` — scores each entity field value against entity quote, with grounding mode from `GROUNDING_DEFAULTS` or per-field override
- `score_entity_confidence()` — adjusts raw 0.5 confidence using field completeness, ID presence, field grounding average, and quote quality
- `_filter_entity_fields()` — nullifies non-ID fields with `field_grounding < threshold` in grounding gate
- Entity consolidation uses `field_grounding` average to refine entity weight

### ✅ Fix G → Superseded by Grounding Simplification
- ~~Added `grounding_mode: required` to 21 text fields~~ — **removed**. No longer needed: `text` defaults to `required`.
- Override infrastructure remains available (`grounding_mode_overrides` parameter on `apply_grounding_gate()`, `grounding_mode` key in field_defs) for rare edge cases
- **Post-deploy note:** Existing DB projects need schema update via `PUT /projects/{id}` to retype descriptive text fields to `summary`

### ✅ Fix H: Entity confidence signal improvement
- `score_entity_confidence()` replaces raw 0.5 default with quality-signal-based score
- Heuristics: field completeness (fill ratio), ID field presence (+0.05 boost), average field grounding, quote quality (length vs field count)
- Formula: `adjusted = raw * (0.4 + 0.6 * completeness) * (0.5 + 0.5 * avg_gnd) * quote_factor + id_boost`
- Called in `_extract_entity_chunk_v2()` after per-field grounding

### Tests
- All 2281 tests pass
- Test files updated/added: `test_grounding.py`, `test_grounding_v2.py`, `test_grounding_gate.py`, `test_inline_grounding.py`, `test_grounding_backfill.py`, `test_consolidation.py`, `test_consolidation_service.py`, `test_scheduler_startup.py`

## Remaining Work

### 🔲 Post-deploy: Update existing project schemas
Existing projects (drivetrain, jobs, wikipedia) need descriptive text fields retyped to `summary` in their DB schemas via `PUT /projects/{id}` — template changes only affect new projects.

### 🔲 Post-deploy: Backfill → Reconsolidate → Verify
1. Run `POST /projects/{id}/backfill-grounding-v2?dry_run=true` on all 3 projects — check stats
2. Run with `dry_run=false` — verify Flender "Bielefeld" grounding drops to ~0.0
3. Reconsolidate drivetrain project (with `use_llm=true` for synthesis)
4. Generate report → verify Flender HQ = "Bocholt, Germany"
5. Verify product entity quality still reasonable, confidence distribution no longer uniform 0.5

## Files Modified

| File | Fix | Change |
|------|-----|--------|
| `src/services/extraction/grounding.py` | 0→Simplification, A, E, F, H | `"text": "required"`, `grounding_mode` kwarg, dict-in-list grounding, `ground_entity_fields()`, `score_entity_confidence()` |
| `src/services/extraction/consolidation.py` | A, B, D, F | Strategy defaults, ext-level confidence cap, `llm_summarize` strategy, `get_llm_summarize_candidates()`, entity field_grounding in weight |
| `src/services/extraction/consolidation_service.py` | D | Async methods, `_llm_post_process()`, LLM client parameter |
| `src/services/extraction/consolidation_worker.py` | D | New: background worker for consolidation jobs |
| `src/services/extraction/extraction_items.py` | F | `field_grounding` on `EntityItem`, `_entity_to_dict()` |
| `src/services/extraction/schema_orchestrator.py` | A, F, G, H | `grounding_mode` override, `ground_entity_fields()` call, `score_entity_confidence()` call, `_filter_entity_fields()`, `grounding_mode_overrides` param on gate |
| `src/services/scraper/scheduler.py` | D | `_run_consolidate_worker()` loop, stale threshold |
| `src/services/projects/templates/drivetrain_company.yaml` | A, D, Simplification | Strategy overrides, `llm_summarize`, `manufacturing_details` → summary, removed 15 `grounding_mode` overrides |
| `src/services/projects/templates/wikipedia_articles.yaml` | A, D, Simplification | Strategy overrides, `llm_summarize`, `notable_for`+`dimensions_or_measurements` → summary, removed 7 `grounding_mode` overrides |
| `src/services/projects/templates/company_analysis.yaml` | Simplification | `fact_text`+`source_quote` → summary |
| `src/services/projects/templates/book_catalog.yaml` | Simplification | `description` → summary |
| `src/services/projects/templates/contract_review.yaml` | Simplification | `term_text` → summary |
| `src/services/projects/templates/research_survey.yaml` | Simplification | `finding_text`+`source_quote` → summary |
| `src/services/projects/templates/default.yaml` | Simplification | `description`+`fact_text` → summary |
| `src/api/v1/projects.py` | C, D | Backfill endpoint, consolidation job creation (202 pattern) |
| `src/services/storage/repositories/extraction.py` | C | `update_v2_data_batch()` method |
| `src/constants.py` | D | `JobType.CONSOLIDATE` |
