# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-15

## Current State

### Deployed & Verified
- **v2 extraction pipeline** live with grounding gate, LLM quote rescue, negation filtering
- **Phase B prompt improvements** deployed (92.0% well-grounded baseline)
- **Grounding simplification** deployed — `GROUNDING_DEFAULTS["text"]` = `"required"`, descriptive fields retyped to `summary`
- **Consolidation quality fixes (0, A-I)** all deployed
- **Grounding backfill + reconsolidation** completed on all 3 projects
- **`company_locations` extraction** completed on drivetrain (11,340 sources, 0 failures)
- **Consolidation** re-run with `company_locations` — 238 companies, 1793 records

### company_locations Quality Results (2026-03-15)
- 220/238 companies (92.4%) have location data (was 0% before)
- 3,662 location entities extracted, avg 16.6 per company
- **city**: 85.1% fill — good
- **country**: 37.0% fill — LLM correctly leaves null when page doesn't mention country
- **site_type**: 12.9% fill — data availability issue (pages rarely label facility types)

### Uncommitted Changes (NOT YET DEPLOYED)
**Entity grounding architecture rewrite** — 3 changes in `grounding.py`:

1. **`score_entity_confidence`**: Simplified to `raw_confidence * avg_field_gnd * entity_grounding`. Removed: completeness penalty, id_boost (+0.1), quote_factor, (0.5+0.5*gnd) floor. When entity_grounding ≈ 0 but field values verified in source, falls back to `min(avg_field_gnd, 0.9)`.

2. **`ground_entity_fields`**: Changed from value→quote to **value→source**. Two independent signals now: value→source (this function) + quote→source (entity_grounding). Dropped the fragile value→quote middle layer that broke on plurals/reformulations.

3. **Empty field handling**: Null/empty fields excluded from grounding averages. Sparse entities (e.g., city-only locations) no longer penalized.

**Tests**: 2282 passed (1 pre-existing Qdrant search failure excluded).

**Impact**: After deploy + re-extraction, location entity confidence will jump from ~0.20 to ~0.50 for well-grounded city-only entities.

## Next Steps

### Immediate
- [ ] **Commit & deploy** the grounding architecture changes
- [ ] **Re-extract `company_locations`** to get new confidence scores (or just reconsolidate — existing field_grounding in DB will be re-evaluated during consolidation)
- [ ] **Update `docs/TODO_consolidation_quality.md` line 88** — Fix H formula description is stale (still shows old formula)
- [ ] **Country enrichment decision**: `input/worldcities.csv` (48K cities) could fill the 37% country gap post-extraction. But needs design thought for template-agnostic system — not all entity types have city/country semantics.

### Later
- [ ] Position tracing (Phase C) — `docs/TODO_quote_source_tracing.md`, algorithm validated: 87.3% match rate
- [ ] LLM skip-gate classification — gemma3-4B, 92.6% recall. See `docs/TODO_classification_robustness.md`
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Multilingual product dedup during consolidation

## Key Files

- `src/services/extraction/grounding.py` — `ground_entity_fields()` (value→source), `score_entity_confidence()` (simplified), `ground_entity_item()` (quote→source)
- `src/services/extraction/schema_orchestrator.py` — `_extract_entity_chunk_v2()` line 1298: passes `entity_grounding=grounding` to confidence scorer
- `src/services/extraction/consolidation.py` — Uses `field_grounding` dict correctly (line 574), no changes needed
- `scripts/analyze_quality.py` — NEW: comprehensive quality analysis script (fill rates, provenance, worst fields)
- `scripts/update_schema_add_locations.py` — NEW: idempotent script to add `company_locations` group to live project schema

## Deprecated Code (identified, not removed)
- `grounding.py` `ground_entity_fields(entity_quote=...)` — param kept for API compat, unused
- `grounding.py` `score_entity_confidence(quote=...)` — param kept for API compat, unused
- `docs/TODO_consolidation_quality.md` line 88 — Fix H formula still shows old `(0.4 + 0.6 * completeness) * (0.5 + 0.5 * avg_gnd) * quote_factor + id_boost`

## Entity Grounding Architecture (current)

| Signal | What it proves | Function |
|--------|---------------|----------|
| **value→source** | Extracted value exists on the page | `ground_entity_fields()` |
| **quote→source** | LLM's quote is real text from page | `ground_entity_item()` |
| **confidence** | `raw_confidence * avg_field_gnd * entity_grounding` | `score_entity_confidence()` |

Note: flat (non-entity) fields still use value→quote + quote→source (two separate functions). Only entity list fields changed.

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
| `docs/TODO_grounding_and_consolidation.md` | COMPLETE — all 6 increments |
| `docs/TODO_extraction_quality.md` | Phase A & B COMPLETE & deployed |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 (skip-gate) pending |
| `docs/TODO_classification_robustness.md` | Ready to implement (v3 spec) |
| `docs/TODO_quote_source_tracing.md` | Ready to implement (algorithm validated) |

## Context

- **v2 is live**: All new extractions use v2 format with inline grounding. v1 data coexists.
- **Entity grounding model** (NEW): value→source + quote→source (independent signals). Flat fields still use value→quote + quote→source.
- **Flat field grounding model**: `text` = required (value-in-quote + quote-in-source), `summary` = none (always 1.0), `boolean` = semantic (quote-in-source only).
- **Test suite**: 2282+ tests passing (1 pre-existing search test failure)
- **Consolidation stale threshold**: 30 minutes (vs 10 min default) for LLM synthesis time
- **Postgres container ID**: changes on deploy — look up via Portainer `GET /containers/json?filters={"name":["postgres"]}`
