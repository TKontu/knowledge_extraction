# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-16

## Current State

### Deployed & Verified
- **v2 extraction pipeline** live with grounding gate, LLM quote rescue, negation filtering
- **Phase B prompt improvements** deployed (92.0% well-grounded baseline)
- **Grounding simplification** deployed — `GROUNDING_DEFAULTS["text"]` = `"required"`, descriptive fields retyped to `summary`
- **Consolidation quality fixes (0, A-I)** all deployed
- **Location & model_number prompt improvements** deployed (`14c5eba`)
- **`company_locations` re-extracted** with improved template (11,340 sources, 0 failures, job `bd55082f`)
- **Consolidation** re-run — 238 companies, 1793 records

### company_locations Quality Results (2026-03-16, post-improvement)

| Metric | Before (Mar 15) | After (Mar 16) | Change |
|--------|-----------------|----------------|--------|
| Total location entities | 3,662 | 5,184 | +41.5% |
| city fill | 85.1% | 90.0% | +4.9pp |
| country fill | 37.0% | 67.8% | **+30.8pp** |
| site_type fill | 12.9% | 44.0% | **+31.1pp** |
| Grounding % | 27.8% | 34.3% | +6.5pp |
| City-without-country | 1,970 | 1,412 | -28.4% |

**Remaining quality issues (absolute count up due to +41% more entities):**
- Country names in city field: 641 (was 445)
- Region/continent in city field: 82 (was 68)
- Region/continent in country field: 87 (was 72)
- 10 sentinel values ("Not specified", "N/A")

**Product model_number fill rates (not yet re-extracted with new hints):**
- products_accessory: 49.8%
- products_gearbox: 54.1%
- products_motor: 63.9%

### Uncommitted Changes (NOT YET DEPLOYED)
**Entity grounding architecture rewrite** — 3 changes in `grounding.py`:

1. **`score_entity_confidence`**: Simplified to `raw_confidence * avg_field_gnd * entity_grounding`. Removed: completeness penalty, id_boost (+0.1), quote_factor, (0.5+0.5*gnd) floor. When entity_grounding ≈ 0 but field values verified in source, falls back to `min(avg_field_gnd, 0.9)`.

2. **`ground_entity_fields`**: Changed from value→quote to **value→source**. Two independent signals now: value→source (this function) + quote→source (entity_grounding). Dropped the fragile value→quote middle layer that broke on plurals/reformulations.

3. **Empty field handling**: Null/empty fields excluded from grounding averages. Sparse entities (e.g., city-only locations) no longer penalized.

**Tests**: 2282 passed (1 pre-existing Qdrant search failure excluded).

## Next Steps

### Immediate — Location Quality
- [ ] Fix remaining city/country field placement errors — choose approach:
  - **Post-processing cleanup**: move country values from city→country programmatically (quick win)
  - **Stronger prompt**: add few-shot WRONG/RIGHT examples to prompt_hint
  - **Both**: prompt fix + cleanup as safety net
- [ ] Handle sentinel values ("Not specified", "N/A") — add to grounding gate or post-processing

### Immediate — Product Quality
- [ ] Re-extract product field groups (`products_gearbox`, `products_motor`, `products_accessory`) with `force=true` + `field_groups` filter
- [ ] **Delete old product extractions before re-consolidating** (same duplication bug as locations)

### Bug: Force Re-extraction Duplication
- `pipeline.py:extract_source()` appends new extractions without deleting old ones for the same source+field_group
- `consolidation_service.py` loads ALL extractions — old + new get mixed together
- Workaround: manually delete old extractions by date before consolidating
- Fix: add delete-then-insert logic in extract_source or dedup in consolidation

### Later
- [ ] **Commit & deploy** the grounding architecture changes
- [ ] Position tracing (Phase C) — `docs/TODO_quote_source_tracing.md`, algorithm validated: 87.3% match rate
- [ ] LLM skip-gate classification — gemma3-4B, 92.6% recall. See `docs/TODO_classification_robustness.md`
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Multilingual product dedup during consolidation
- [ ] Country enrichment: `input/worldcities.csv` (48K cities) could fill remaining country gaps

## Key Files

- `src/services/projects/templates/drivetrain_company.yaml` — template with prompt_hints (the production config)
- `scripts/analyze_quality.py` — quality analysis with location checks (country-in-city, region, sentinel detection)
- `src/services/extraction/grounding.py` — `ground_entity_fields()` (value→source), `score_entity_confidence()` (simplified)
- `src/services/extraction/pipeline.py:129-158` — extract_source() where extractions are stored (no dedup logic — duplication bug)
- `src/services/extraction/consolidation_service.py:81-99` — consolidation loads ALL extractions per source_group

## Entity Grounding Architecture (current)

| Signal | What it proves | Function |
|--------|---------------|----------|
| **value→source** | Extracted value exists on the page | `ground_entity_fields()` |
| **quote→source** | LLM's quote is real text from page | `ground_entity_item()` |
| **confidence** | `raw_confidence * avg_field_gnd * entity_grounding` | `score_entity_confidence()` |

Note: flat (non-entity) fields still use value→quote + quote→source. Only entity list fields changed.

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
| `docs/TODO_consolidation_quality.md` | COMPLETE — needs Fix H formula update |
| `docs/TODO-location-quality.md` | IN PROGRESS — template changes deployed, field placement issues remain |
| `docs/TODO_grounding_and_consolidation.md` | COMPLETE — all 6 increments |
| `docs/TODO_extraction_quality.md` | Phase A & B COMPLETE & deployed |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 (skip-gate) pending |
| `docs/TODO_classification_robustness.md` | Ready to implement (v3 spec) |
| `docs/TODO_quote_source_tracing.md` | Ready to implement (algorithm validated) |

## Context

- **v2 is live**: All new extractions use v2 format with inline grounding. v1 data coexists.
- **Extraction with field_groups** requires curl — MCP tool doesn't expose `field_groups` param
- **Force re-extraction creates duplicates** — always delete old extractions by date before consolidating
- **vendor/firecrawl** submodule is dirty from user's `feat/granular-post-scrape-logging` branch — do not reset
- **Test suite**: 2282+ tests passing (1 pre-existing search test failure)
- **Postgres container ID**: changes on deploy — look up via Portainer
