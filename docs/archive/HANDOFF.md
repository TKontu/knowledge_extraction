# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-18

## Current State

### Deployed & Verified
- **v2 extraction pipeline** live with grounding gate, LLM quote rescue, negation filtering
- **Grounding architecture rewrite** deployed (`1f718f4`) — value→source + simplified scoring
- **Template-agnostic entity_id_fields** deployed (`1905d62`)
- **Deadlock fix** deployed (`24271a3`) — no more sync-on-async job scheduler issues
- **Location & model_number prompt improvements** deployed (`14c5eba`)

### This Session (2026-03-18)

**Doc audit (round 2)**: Discovered position tracing (Phase C Feature A) is already complete in code — `locate_in_source()` rewritten with 4-tier `ground_and_locate()`. Also: `analyze_quality.py` Fix 3 (city/country/sentinel detection) already deployed. Updated all affected docs.

**Doc audit**: Discovered skip-gate Phase 2 (pipeline integration) is already complete in `schema_orchestrator.py` — docs were wrong. Updated HANDOFF, TODO_classification_robustness, and TODO_grounded_extraction to reflect Phase 2 = COMPLETE, Phase 3 (SmartClassifier removal) = pending.

**Crawl recovery**: Re-submitted 12 failed crawl jobs (stuck from deadlock bug). Findings:
- Smart crawl fails on many drivetrain sites (only maps 1 URL) — switched those to traditional Firecrawl
- ~425 new sources added: Parsons Peebles (68), Klingelnberg (98), Nord (99), Kollmorgen (98), Sswt (21), Ingecogears (24), Kakurrka (11), etc.
- Dirkdrives: only 1 page found even with traditional crawl — site likely blocks crawlers

**Full extraction run**: `force=True` on all 11,969 sources — completed successfully
- Job `1055f4c3`, 53,963 total extractions, 0 errors

**Consolidation**: Re-run — 238 companies, 1,841 records, 0 errors

**Report**: Generated Excel report `4b8ecc6e` (238 companies, ~29K product entities, 5,871 locations)

### company_locations Quality (as of Mar 16)

| Metric | Value |
|--------|-------|
| Total location entities | 5,184 |
| City fill | 90.0% |
| Country fill | 67.8% |
| Site_type fill | 44.0% |
| Country names in city field | 641 |
| Region/continent in city | 82 |
| Sentinel values ("N/A" etc) | 10 |

### Known Issues

**Force re-extraction duplication bug**: `extract_source()` appends new extractions without deleting old ones. The `force=True` run this session created duplicates for all previously-extracted sources. Agreed to handle post-hoc by deleting old extractions by date. Consolidation ran on mixed old+new — data may have duplicates.

## Next Steps

### Immediate — Clean Up Duplicates
- [ ] Delete old extractions (pre-2026-03-18) from DB before re-consolidating — use `created_at` to identify
- [ ] Fix the duplication bug: `pipeline.py:extract_source()` — add delete-then-insert or dedup logic
- [ ] Re-run consolidation after cleanup

### Immediate — Location Quality
- [ ] Fix city/country field placement errors (641 country-in-city, 82 region-in-city)
  - Option A: Post-processing cleanup script
  - Option B: Stronger prompt with WRONG/RIGHT few-shot examples
- [ ] Handle sentinel values ("Not specified", "N/A") — grounding gate or post-processing

### Product Quality
- [ ] Re-extract product field groups with improved prompts once duplication bug is fixed
  - products_gearbox: 54.1% model_number fill
  - products_motor: 63.9% model_number fill
  - products_accessory: 49.8% model_number fill

### Later
- [x] Position tracing (Phase C) — **COMPLETE.** `locate_in_source()` rewritten with 4-tier `ground_and_locate()`. See `docs/TODO_quote_source_tracing.md`.
- [ ] LLM skip-gate Phase 3 — remove SmartClassifier from `schema_orchestrator.py` (Phase 2 is already complete — skip-gate is wired in as Level 1, SmartClassifier remains as Level 2 fallback). See `docs/TODO_classification_robustness.md`
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Country enrichment from `input/worldcities.csv` (48K cities)

## Key Files

- `src/services/extraction/pipeline.py:129-160` — `extract_source()` appends new Extractions with no delete-first. The duplication bug is one level up: `extract_project()` calls this without purging prior extractions for the source. Fix needed in `extract_project`, not here.
- `src/services/extraction/consolidation_service.py:81-99` — deletes old consolidated records then loads ALL raw extractions (no date filter). By design. Problem is upstream: if the raw extraction table has old+new duplicates, consolidated output reflects both.
- `src/services/projects/templates/drivetrain_company.yaml` — template with prompt_hints
- `scripts/analyze_quality.py` — quality analysis with location checks

## Context

- **Smart crawl lesson**: Many drivetrain sites don't map well with smart crawl — use `smart_crawl_enabled=false` by default for this project
- **Duplicate extractions**: DB now has two sets for most sources — old (pre Mar 18) and new (Mar 18). Need cleanup before trusting consolidated data fully.
- **vendor/firecrawl** submodule is dirty from `feat/granular-post-scrape-logging` branch — do not reset
- **Test suite**: 2282+ tests passing

## Deployment Context

- **Pipeline API**: `http://192.168.0.136:8742` (container port 8000 → host 8742)
- **LLM**: vLLM on `192.168.0.247:9003` (gemma3-12b-awq default)
- **Embeddings**: bge-m3 on `192.168.0.136:9003`
- **DB**: `scristill:scristill@192.168.0.136:5432/scristill` (psycopg v3)
- **Portainer env ID**: 3, pipeline container: `scristill-stack-pipeline-1`

## Project IDs

- **Drivetrain**: `99a19141-9268-40a8-bc9e-ad1fa12243da` (11,969 sources)
- **Latest report**: `4b8ecc6e-2382-4a46-b501-ff4ce7be5fec`

## TODO Docs Status

| Doc | Status |
|-----|--------|
| `docs/TODO-location-quality.md` | Fix 1 deployed, Fix 2 cancelled, Fix 3 deployed, Fix 4 ready to deploy |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 skip-gate Phase 2 COMPLETE. Phase 3 (SmartClassifier removal) pending |
| `docs/TODO_classification_robustness.md` | Phase 1 COMPLETE · Phase 2 COMPLETE. Phase 3 (remove SmartClassifier) pending |
| `docs/TODO_quote_source_tracing.md` | **COMPLETE** — `locate_in_source()` rewritten, position bug fixed |
| `docs/TODO_phase_c_position_tracing_and_skip_gate.md` | **COMPLETE** — both features done |
