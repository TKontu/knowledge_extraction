# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-19

## Current State

### Deployed & Verified
- **v2 extraction pipeline** live with grounding gate, LLM quote rescue, negation filtering
- **Grounding architecture rewrite** deployed (`1f718f4`) — value→source + simplified scoring
- **Template-agnostic entity_id_fields** deployed (`1905d62`)
- **Deadlock fix** deployed (`24271a3`) — no more sync-on-async job scheduler issues
- **Location & model_number prompt improvements** deployed (`14c5eba`)
- **Position tracing (Phase C)** — `locate_in_source()` rewritten with 4-tier `ground_and_locate()`
- **LLM skip-gate Phase 2** — wired into `schema_orchestrator.py` as Level 1 classifier
- **analyze_quality.py location checks** — city/country confusion + sentinel detection deployed

### This Session (2026-03-19)

**Documentation audit** (3 rounds, all active docs checked against real source code):
- Corrected skip-gate Phase 2: COMPLETE in code, docs said "pending"
- Corrected position tracing (Phase C): COMPLETE in code, docs said "planned"
- Corrected analyze_quality.py Fix 3: COMPLETE in code, docs said "pending"
- Fixed docker-compose default embedding `bge-large-en` → `bge-m3`
- Fixed SECURITY.md API key minimum 16 → 32 chars
- Moved ~85 obsolete docs to `docs/archive/`
- Added new review docs (deadlock fix, entity_id_fields, template agnosticism, crawl bug)
- Committed as `3b2d932`

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

**Force re-extraction duplication bug**: `extract_project()` calls `extract_source()` without deleting prior extractions first. The `force=True` run on 2026-03-18 created duplicates for all previously-extracted sources. Consolidation ran on mixed old+new — data may have duplicates. Fix belongs in `extract_project`, not `extract_source`.

## Next Steps

### Immediate — Clean Up Duplicates
- [ ] Delete old extractions (pre-2026-03-18) from DB before re-consolidating — use `created_at` to identify
- [ ] Fix duplication bug: add delete-before-insert in `extract_project()` (caller of `extract_source`)
- [ ] Re-run consolidation after cleanup

### Immediate — Location Quality
- [ ] Deploy Fix 4: improve `service_types` field description in `drivetrain_company.yaml` — `docs/TODO-location-quality.md`
- [ ] Address remaining city/country errors (641 country-in-city, 82 region-in-city) with post-processing or stronger prompt

### Later
- [ ] LLM skip-gate Phase 3 — remove SmartClassifier from `schema_orchestrator.py` (Phase 2 done, SmartClassifier still present as Level 2 fallback). See `docs/TODO_classification_robustness.md`
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Country enrichment from `input/worldcities.csv` (48K cities)

## Key Files

- `src/services/extraction/pipeline.py:129-160` — `extract_source()` — appends Extractions, no delete. Duplication fix needed one level up in `extract_project()`
- `src/services/extraction/consolidation_service.py:81-99` — loads ALL raw extractions (no date filter, by design). Upstream duplicate raw extractions propagate here.
- `src/services/extraction/schema_orchestrator.py:458-483` — skip-gate Level 1 flow
- `src/services/projects/templates/drivetrain_company.yaml` — template with prompt_hints
- `scripts/analyze_quality.py` — quality analysis with location checks (city/country/sentinel)

## Context

- **Smart crawl lesson**: Many drivetrain sites don't map well with smart crawl — use `smart_crawl_enabled=false` by default for this project
- **Duplicate extractions**: DB has two sets for most sources — old (pre Mar 18) and new (Mar 18). Need cleanup before trusting consolidated data.
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
| `docs/TODO-location-quality.md` | Fix 1 deployed, Fix 2 cancelled, Fix 3 deployed, Fix 4 **ready to deploy** |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 skip-gate Phase 2 COMPLETE. Phase 3 (SmartClassifier removal) pending |
| `docs/TODO_classification_robustness.md` | Phase 1 COMPLETE · Phase 2 COMPLETE. Phase 3 (remove SmartClassifier) pending |
| `docs/TODO_quote_source_tracing.md` | **COMPLETE** — `locate_in_source()` rewritten, position bug fixed |
| `docs/TODO_phase_c_position_tracing_and_skip_gate.md` | **COMPLETE** — both features done |
