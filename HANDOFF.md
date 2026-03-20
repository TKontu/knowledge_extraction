# Handoff: Field Inference via `factapi_fill_from_lookup`

**Last updated:** 2026-03-20

## Completed This Session (2026-03-20)

### `factapi_fill_from_lookup` validator — field inference from factAPI

New validator type that infers a field value from a factAPI collection lookup. Example: `city="Munich"` → `worldcities` → uniquely maps to `country="Germany"` → fills `country` if null.

**5 files changed, 10 new tests, all 2306 tests passing.**

| File | Change |
|------|--------|
| `src/services/extraction/field_groups.py` | Added `factapi_fill_from_lookup` to types, `fill_if_null`/`fill_always` to actions, `fill_column`/`target_field`/`unique_only` to `ValidatorSpec` |
| `src/services/extraction/field_validation.py` | Added `get_mapping()` + `_fetch_mapping()` (same double-checked locking cache), updated `prefetch_for_groups()` + `apply_to_entity_fields()` |
| `src/services/extraction/schema_adapter.py` | Validates `fill_column`+`target_field` required for fill type; passes new fields through |
| `src/services/projects/templates/drivetrain_company.yaml` | Added fill validator on `city` → infers `country` |
| `tests/services/extraction/test_field_validation.py` | 10 new tests in `TestFillFromLookup` |

**Key behaviour**: Fill validator re-reads `modified.get(fdef.name)` after prior validators run, so if `not_in_column` nullifies `city="Germany"`, the fill validator sees null and skips. `unique_only=true` skips ambiguous cities (e.g. "San Jose" → US + Costa Rica).

### Previously deployed (still uncommitted)
- **Field validation system** (`field_validation.py`, `pipeline.py`, `worker.py`, `config.py`) — `factapi_not_in_column` validator wired end-to-end
- **`field_groups` filter** — extract only named field groups, threaded through API → job payload → worker → pipeline

### Data Quality (as of Mar 19 — post-deduplication cleanup)

**DB cleanup performed this session**: Deleted 51,185 duplicate rows (old Mar 9–10 set) + 7,109 stale single-extraction rows (pre-Mar 17). 53,963 clean rows remain, all from the Mar 17–18 force re-extraction. Consolidation and report regenerated.

**Latest report**: `26fb874a-1c5c-4f35-b3f2-fa23acae18b3` (238 companies)

#### Consolidated coverage (238 companies)

| Type | Companies w/ data | Total entities |
|------|-------------------|----------------|
| company_info | 226/232 (97%) | — |
| company_locations | 209/231 (91%) | 4,400 locations |
| products_accessory | 195/229 (85%) | 12,392 items |
| products_gearbox | 128/224 (57%) | 3,413 items |
| products_motor | 132/229 (58%) | 4,362 items |

#### company_locations field fill

| Metric | Value |
|--------|-------|
| Total location entities | 4,400 |
| City fill | 89.2% |
| Country fill | 67.5% |
| Site_type fill | 42.6% |
| Country/region name in city field | 533 (12.1% of filled city values) |

#### Product field fill rates

| Field | Gearbox | Motor | Accessory |
|-------|---------|-------|-----------|
| product_name | 100% | 100% | 100% |
| series_name | 95.3% | 94.4% | — |
| model_number | 52.6% | 64.1% | 50.9% |
| subcategory | 67.6% | 84.8% | 51.3% |
| torque_rating_nm | 18.7% | — | 10.1% |
| power_rating_kw | 13.0% | 27.8% | — |

#### Notable gaps

- **8 companies** have zero products AND zero locations despite being crawled (e.g. Psjengineering — image-only Joomla site, content not text-extractable)
- **32 companies** have no products of any type
- Technical specs (ratio, power, efficiency) sparse across all product types — rarely published as text on sites
- **Grounding is excellent** (0.991–0.995 avg entity grounding) — pipeline correctly verifies what it extracts

### Known Issues

**Force re-extraction duplication bug**: `extract_project()` calls `extract_source()` without deleting prior extractions first. The `force=True` run on 2026-03-18 created duplicates for all previously-extracted sources. **Mitigated this session** by deleting old rows via SQL. Fix belongs in `extract_project`, not `extract_source` — add delete-before-insert there.

## Next Steps

### Immediate
- [ ] **Commit** all uncommitted changes (`field_validation.py`, `field_groups.py`, `schema_adapter.py`, `pipeline.py`, `worker.py`, `config.py`, template, tests, `FACTAPI_AGENT_INSTRUCTIONS.md`)
- [ ] **Re-extract `company_locations`** on the drivetrain batch to populate `country` via the new fill validator — baseline was 67.5% country fill; target is >85%
- [ ] **Check quality**: run `analyze_quality.py` to confirm country fill rate improvement

### Remaining Location Quality
- [ ] Deploy Fix 4: improve `service_types` field description in `drivetrain_company.yaml` — `docs/TODO-location-quality.md`

### Bug Fix
- [ ] Fix duplication bug in `extract_project()` — add delete-before-insert so future force re-runs don't accumulate stale rows

### Later
- [ ] LLM skip-gate Phase 3 — remove SmartClassifier from `schema_orchestrator.py` (Phase 2 done, SmartClassifier still present as Level 2 fallback). See `docs/TODO_classification_robustness.md`
- [ ] Search fix + reranking (bge-reranker-v2-m3)
- [ ] Country enrichment from `input/worldcities.csv` (48K cities)

## Key Files

- `src/services/extraction/field_validation.py` — `FieldValidationService` (untracked new file)
- `src/services/extraction/field_groups.py` — `ValidatorSpec` with fill fields
- `src/services/extraction/schema_adapter.py` — fill validator parsing + validation
- `src/services/projects/templates/drivetrain_company.yaml` — fill validator on `city`
- `tests/services/extraction/test_field_validation.py` — 25 tests (untracked new file)
- `src/services/extraction/pipeline.py:129-160` — `extract_source()` — duplication fix still needed one level up in `extract_project()`
- `scripts/analyze_quality.py` — quality analysis with location checks (city/country/sentinel)

## Context

- **Smart crawl lesson**: Many drivetrain sites don't map well with smart crawl — use `smart_crawl_enabled=false` by default for this project
- **Duplicate extractions**: Cleaned up this session — 58,294 stale rows deleted, DB now has one clean set (Mar 17–18) per source. Consolidation and report regenerated.
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
- **Latest report**: `26fb874a-1c5c-4f35-b3f2-fa23acae18b3` (Mar 19, post-cleanup)

## TODO Docs Status

| Doc | Status |
|-----|--------|
| `docs/TODO-location-quality.md` | Fix 1 deployed, Fix 2 cancelled, Fix 3 deployed, Fix 4 **ready to deploy** |
| `docs/TODO_grounded_extraction.md` | Layers 1+3 COMPLETE, Layer 2 skip-gate Phase 2 COMPLETE. Phase 3 (SmartClassifier removal) pending |
| `docs/TODO_classification_robustness.md` | Phase 1 COMPLETE · Phase 2 COMPLETE. Phase 3 (remove SmartClassifier) pending |
| `docs/TODO_quote_source_tracing.md` | **COMPLETE** — `locate_in_source()` rewritten, position bug fixed |
| `docs/TODO_phase_c_position_tracing_and_skip_gate.md` | **COMPLETE** — both features done |
