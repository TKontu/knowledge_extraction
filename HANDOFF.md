# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-13

## Current State

### Uncommitted: Consolidation Quality Fixes D, F, G→I, H + Grounding Simplification
20+ modified files + 2 new files. All tests pass.

**What was implemented:**
- **Fix D**: LLM summary consolidation — `llm_summarize` strategy, async `_llm_post_process()` in `ConsolidationService`, background job via `ConsolidationWorker` + scheduler loop. `use_llm=True` returns 202 with `job_id`.
- **Fix F**: Entity per-field grounding — `ground_entity_fields()`, `field_grounding` on `EntityItem`, `_filter_entity_fields()` in gate
- **Fix G→I (superseded)**: Grounding mode overrides removed — replaced by grounding simplification (see below)
- **Fix H**: Entity confidence scoring — `score_entity_confidence()` replaces default 0.5 using completeness, ID presence, field grounding, quote quality
- **Grounding Simplification**: `GROUNDING_DEFAULTS["text"]` changed from `"semantic"` to `"required"`. Descriptive text fields retyped to `summary` (grounding_mode=none). All 22 `grounding_mode: required` overrides removed from templates (now redundant). Net effect: field_type alone determines grounding behavior, no per-field overrides needed.
- **Pipeline review bug fixes**: `grounding_mode` overrides were silently dropped (now fixed), LLM consolidation moved from inline HTTP to background job

### Deployed & Running
- **v2 extraction pipeline** live with three-tier grounding gate, LLM quote rescue, negation filtering, confidence recalibration
- **Phase B prompt improvements** deployed (92.0% well-grounded baseline)

## Next Steps

### Immediate
- [ ] Commit all uncommitted changes
- [ ] Deploy to production
- [ ] Update existing project schemas via `PUT /projects/{id}` to retype descriptive text fields to `summary` (templates only affect new projects)
- [ ] Run backfill: `POST /projects/{id}/backfill-grounding-v2?dry_run=false` on all 3 projects
- [ ] Reconsolidate drivetrain project with `use_llm=true` (now a background job)
- [ ] Generate reports and verify quality improvements

### Later
- [ ] Position tracing (Phase C) — `docs/TODO_quote_source_tracing.md`, algorithm validated: 87.3% match rate
- [ ] LLM skip-gate classification — gemma3-4B, 92.6% recall. See `docs/TODO_classification_robustness.md`
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Multilingual product dedup during consolidation

## Key Files

- `src/services/extraction/grounding.py` — `GROUNDING_DEFAULTS` (text=required, summary=none), `ground_entity_fields()`, `score_entity_confidence()`
- `src/services/extraction/schema_orchestrator.py` — `apply_grounding_gate()`, `grounding_mode_overrides` (for rare per-field overrides)
- `src/services/extraction/consolidation_service.py` — async methods, `_llm_post_process()`
- `src/services/extraction/consolidation_worker.py` — NEW: background worker for consolidation jobs
- `src/services/scraper/scheduler.py` — `_run_consolidate_worker()` loop
- `src/api/v1/projects.py` — consolidation endpoint: 202 pattern for `use_llm=True`
- `src/services/extraction/consolidation.py` — `llm_summarize` strategy, `get_llm_summarize_candidates()`
- `src/constants.py` — `JobType.CONSOLIDATE`

## Deployment Context

- **Remote server**: `192.168.0.136`
- **Deployed via**: `docker-compose.prod.yml` (builds from GitHub `main` branch)
- **Pipeline API**: `http://192.168.0.136:8742` (container port 8000 -> host 8742)
- **LLM**: vLLM on `192.168.0.247:9003` (gemma3-12b-awq default, Qwen3-30B for verification)
- **Embeddings**: bge-m3 on `192.168.0.136:9003`
- **DB**: `scristill:scristill@192.168.0.136:5432/scristill` (psycopg v3)
- **Portainer env ID**: 3

## Project IDs

- **Drivetrain**: `99a19141-9268-40a8-bc9e-ad1fa12243da` (11,340 sources + 729 skipped)
- **Jobs trial**: `b972e016-3baa-403f-ae79-22310e4e895a` (35 sources)
- **Wikipedia trial**: `6ce9755e-9d77-4926-90dd-86d4cd2b9cda` (20 sources)

## TODO Docs Status

| Doc | Status |
|-----|--------|
| `docs/TODO_consolidation_quality.md` | COMPLETE — all fixes (0, A-H) + grounding simplification |
| `docs/TODO_grounding_and_consolidation.md` | COMPLETE — all 6 increments |
| `docs/TODO_extraction_quality.md` | Phase A & B COMPLETE & deployed |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 (skip-gate) pending |
| `docs/TODO_classification_robustness.md` | Ready to implement (v3 spec) |
| `docs/TODO_quote_source_tracing.md` | Ready to implement (algorithm validated) |

## Context

- **v2 is live**: All new extractions use v2 format with inline grounding. v1 data coexists.
- **Grounding model**: `text` = required (value-in-quote + quote-in-source), `summary` = none (always 1.0), `boolean` = semantic (quote-in-source only). No per-field `grounding_mode` overrides needed — field_type determines behavior.
- **Test suite**: 2281+ tests passing
- **Consolidation stale threshold**: 30 minutes (vs 10 min default) for LLM synthesis time
- **Pre-existing lint warnings**: B008 (Depends) in projects.py, E402 (imports) in scheduler.py — not introduced by this work
