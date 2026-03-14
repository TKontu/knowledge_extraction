# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-14

## Current State

### Deployed & Verified
- **v2 extraction pipeline** live with three-tier grounding gate, LLM quote rescue, negation filtering, confidence recalibration
- **Phase B prompt improvements** deployed (92.0% well-grounded baseline)
- **Grounding simplification** deployed — `GROUNDING_DEFAULTS["text"]` = `"required"`, descriptive fields retyped to `summary`
- **Consolidation quality fixes (0, A-I)** all deployed — including v2 list field fix (Fix I)
- **All 3 project schemas updated** — descriptive text fields retyped to `summary` in DB
- **Grounding backfill** completed on all 3 projects
- **Reconsolidation** completed on all 3 projects with `use_llm=true`

### Quality Results (2026-03-14)
See `docs/quality_analysis_2026-03-14.md` for full analysis.

Key metrics (drivetrain, 238 companies consolidated):
- company_name: 98.3% fill, 0.790 avg grounding
- headquarters_location: 95.7% fill, 0.242 avg grounding
- certifications: **61.0%** fill (was 0.9% before Fix I)
- service_types: **51.8%** fill (was 0% before Fix I)
- manufacturing_details: 87.7% fill
- locations: 0% fill (extraction gap — LLM doesn't extract, not a consolidation issue)

### Uncommitted Changes
- Template change: `company_meta` split — locations removed, new `company_locations` entity list group
- This template change needs to be applied to the live drivetrain project schema via `PUT` and then re-extracted

## Next Steps

### Immediate
- [ ] Apply `company_locations` entity list schema to live drivetrain project
- [ ] Re-extract `company_locations` group (or full re-extraction with `force=True`)
- [ ] Generate final quality report for stakeholder review

### Later
- [ ] Position tracing (Phase C) — `docs/TODO_quote_source_tracing.md`, algorithm validated: 87.3% match rate
- [ ] LLM skip-gate classification — gemma3-4B, 92.6% recall. See `docs/TODO_classification_robustness.md`
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Multilingual product dedup during consolidation

## Key Files

- `src/services/extraction/grounding.py` — `GROUNDING_DEFAULTS` (text=required, summary=none), `ground_entity_fields()`, `score_entity_confidence()`
- `src/services/extraction/schema_orchestrator.py` — `apply_grounding_gate()`, `grounding_mode_overrides`
- `src/services/extraction/consolidation.py` — v2 list field handling, `llm_summarize` strategy, `union_dedup`
- `src/services/extraction/consolidation_service.py` — async methods, `_llm_post_process()`
- `src/services/extraction/consolidation_worker.py` — background worker for consolidation jobs
- `src/services/scraper/scheduler.py` — `_run_consolidate_worker()` loop
- `src/api/v1/projects.py` — consolidation endpoint (202 pattern), backfill endpoints
- `src/constants.py` — `JobType.CONSOLIDATE`
- `docs/quality_analysis_2026-03-14.md` — post-grounding-simplification quality analysis

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
| `docs/TODO_consolidation_quality.md` | COMPLETE — all fixes (0, A-I) + grounding simplification + post-deploy verification |
| `docs/TODO_grounding_and_consolidation.md` | COMPLETE — all 6 increments |
| `docs/TODO_extraction_quality.md` | Phase A & B COMPLETE & deployed |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 (skip-gate) pending |
| `docs/TODO_classification_robustness.md` | Ready to implement (v3 spec) |
| `docs/TODO_quote_source_tracing.md` | Ready to implement (algorithm validated) |

## Context

- **v2 is live**: All new extractions use v2 format with inline grounding. v1 data coexists.
- **Grounding model**: `text` = required (value-in-quote + quote-in-source), `summary` = none (always 1.0), `boolean` = semantic (quote-in-source only). No per-field overrides needed.
- **Test suite**: 2286+ tests passing
- **Consolidation stale threshold**: 30 minutes (vs 10 min default) for LLM synthesis time
- **Postgres container ID**: changes on deploy — look up via Portainer `GET /containers/json?filters={"name":["postgres"]}`
