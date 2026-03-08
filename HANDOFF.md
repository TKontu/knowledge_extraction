# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-08

## Completed (this session)

- [x] **v2 extraction pipeline deployed to production**: `EXTRACTION_DATA_VERSION=2` enabled, all new extractions produce per-field `{value, confidence, quote, grounding, location}` format
- [x] **Docker compose fix**: Added `EXTRACTION_DATA_VERSION: ${EXTRACTION_DATA_VERSION:-1}` to `docker-compose.prod.yml` (env vars must be explicitly listed for Docker to pass them)
- [x] **Production trial extractions validated**:
  - Wikipedia project extracted with v2 format
  - Salma Kikwete quality assessed: article_info (6 fields, g=0.95-1.0), key_facts (4 fields, g=0.9-1.0), related_entities (16 entities, correctly separated two political roles without mixing succession chains)
- [x] **Quote-to-source tracing plan designed and documented**: `docs/TODO_quote_source_tracing.md`
- [x] **Quote tracing trial on production data** (1218 quotes from drivetrain batch):
  - Original "~60% null offset" claim **debunked** — actual v2 null rate is 16.2% (6/37)
  - Tier 1 (normalized substring) already matches **79.6%** of quotes (not 40%)
  - **Markdown stripping is NOT the main failure mode** — only 1.3% of quotes need it (not 40%)
  - Main failures: fabricated values (25.3% of misses), reworded quotes (22.1%), punct issues (17.7%), hallucinated (14.1%)
  - Projected improvement with full 4-tier matching: 79.6% → 89.7%
  - Remaining ~10% unmatched are LLM fabrications (grounding=0) — no matching algorithm fixes those
  - TODO revised: priority lowered from High to Medium, tiers reordered, markdown demoted to minor tier
  - Trial scripts: `scripts/trial_quote_tracing*.py` (4 analysis scripts + 1 prototype validation)
- [x] **Prototype `ground_and_locate()` validated** (1240 quotes):
  - 4-tier architecture: normalized (72.5%) → punct-stripped (3.9%) → md+punct (1.9%) → block fuzzy (9.0%)
  - **87.3% total matched** (+183 quotes over baseline)
  - Only 2 of 158 unmatched still have grounding >= 0.8 — virtually all findable quotes now found
  - Key pre-processing wins: ellipsis stripping, unicode dash normalization
  - 6 minor position errors (Tier 3 offset map bleeds into URLs) — fix: clamp to \n boundary
  - Reference implementation: `scripts/trial_ground_and_locate.py`
- [x] **Wide extraction quality analysis** (3 schemas, 2329 quotes with real-time grounding):
  - **Cross-schema quality**: Jobs 97.1% well-grounded (excellent), Drivetrain 86.9%, Wikipedia 79.7%
  - **#1 problem: World knowledge leaking** — LLM fabricates values from training data when source doesn't contain them. `headquarters_location` 28.4% poorly grounded, `employee_count_range` 32.3% poorly grounded.
  - **#2 problem: Value-as-quote echo** — 23.1% of quotes are exact copies of the extracted value, providing zero provenance. Concentrated in company_name (241) and headquarters_location (142).
  - **#3 problem: Confidence uncalibrated** — conf=0.9 has 7.6% poorly grounded vs conf=0.3 has 12.8%. Near-zero correlation.
  - **Key insight**: Quality correlates with source completeness. Jobs (self-contained) = perfect. Company pages (missing HQ, employee counts) = LLM fills gaps with training knowledge.
  - **Grounding is the single fix for problems 1, 2, and 5**: legitimate values avg grounding=1.00, fabricated avg=0.14. Clean separation.
  - **Value-as-quote echo solved by grounding**: 377 legitimate (in source, g=1.00) vs 190 bad (not in source, g=0.14). No prompt change needed.
  - **Borderline grounding 0.3-0.8 investigated** (`trial_grounding_middle.py`): 99 cases, only 5% have value in source. 95% are word-window false positives (coincidental word overlap). Hard 0.3 threshold would leak ~94 fabricated fields.
  - **Three-tier grounding decision designed**: >= 0.8 KEEP, 0.3-0.8 LLM RESCUE (ask LLM to find exact quote in source), < 0.3 DROP. LLM rescue volume is tiny (~5.5% of extractions), cost ~20s/batch.
  - **`LLMGroundingVerifier` already exists** (`src/services/extraction/llm_grounding.py`) but never wired into pipeline. Needs new `rescue_quote()` method (different prompt: find exact quote in source, not just verify).
  - Phase A: negation filter + LLM rescue for 0.3-0.8 + grounding gate (drop < 0.3, rescue 0.3-0.8) + confidence recalibration
  - Full analysis: `docs/TODO_extraction_quality.md`
  - Trial scripts: `scripts/trial_wide_analysis.py`, `scripts/trial_grounding_realtime.py`, `scripts/trial_value_as_quote.py`, `scripts/trial_grounding_middle.py`

## Completed (previous sessions)

- [x] **Pipeline review & 7-increment fix plan** — 12 verified issues, all implemented
- [x] **Grounding verification & consolidation pipeline** — 6 increments, 164 tests, +4654 lines
- [x] **Extraction pipeline refactor** — commits `6f6fd1b`, `049d58b`, `e395b8e`
- [x] **DB migrations applied to remote** — `grounding_scores` JSONB, `consolidated_extractions`, `data_version` column
- [x] **v2 extraction pipeline implementation** — per-field structured data, inline grounding, cardinality-based merge, entity pagination, all downstream consumers v2-aware

## Uncommitted Changes

- `docs/TODO_quote_source_tracing.md` — NEW file (implementation plan, revised with trial data)
- `docker-compose.prod.yml` — Added `EXTRACTION_DATA_VERSION` line (already deployed)
- `scripts/trial_quote_tracing.py` — NEW trial script (coverage analysis)
- `scripts/trial_quote_tracing_deep.py` — NEW trial script (failure categorization)
- `scripts/trial_quote_tracing_v2_check.py` — NEW trial script (v2 null offset check)
- `scripts/trial_quote_tracing_examples.py` — NEW trial script (detailed examples)
- `scripts/trial_ground_and_locate.py` — NEW prototype validation (reference implementation)
- `scripts/trial_wide_analysis.py` — NEW wide quality analysis across all projects
- `scripts/trial_grounding_realtime.py` — NEW real-time grounding computation
- `scripts/trial_value_as_quote.py` — NEW value-as-quote echo analysis
- `scripts/trial_grounding_middle.py` — NEW borderline grounding 0.3-0.8 investigation
- `docs/TODO_extraction_quality.md` — NEW quality improvement plan (3-tier grounding gate + LLM rescue + recalibration)

## In Progress

- **Drivetrain batch extraction** (project `99a19141`): Started before v2 redeploy — produced v1 data. Needs re-extraction.
- **Jobs trial extraction** (job `bb608ae5`): Status unknown — check if completed.

## Next Steps (prioritized)

### Phase A: Quality gates (no re-extraction needed)
- [ ] **Commit pending changes** — all new docs + trial scripts
- [ ] **Add negation quote filter** — regex to drop "no mention of X" / "N/A" quotes in `grounding.py`
- [ ] **Add LLM quote rescue** — new `rescue_quote()` method in `LLMGroundingVerifier` for borderline cases
- [ ] **Add three-tier grounding gate** — in `_parse_chunk_to_v2()`: < 0.3 DROP, 0.3-0.8 LLM RESCUE, >= 0.8 KEEP. Make method async.
- [ ] **Add confidence recalibration** — `effective_conf = min(conf, grounding)` in `consolidation.py`
- [ ] **Run grounding backfill** — `scripts/backfill_grounding_scores.py` (v1 data has no scores)
- [ ] **Run consolidation** — `POST /projects/{id}/consolidate`

### Phase B: Re-extraction with improvements
- [ ] **Update extraction prompt** — anti-hallucination ("only extract from source text") + quote≠value ("quote source text, not your answer")
- [ ] **Re-extract drivetrain with v2** — Project `99a19141`. Compare quality metrics before/after.
- [ ] **Check jobs trial status** — `get_job_status(job_id="bb608ae5...")`

### Phase C: Position tracing + classification
- [ ] **Implement quote-to-source tracing** — `docs/TODO_quote_source_tracing.md`. Validated prototype: 87.3% match rate.
- [ ] **LLM skip-gate classification** — See `docs/TODO_classification_robustness.md` v3

### Later
- [ ] **Report integration with consolidation**
- [ ] **Search fix + reranking** — bge-reranker-v2-m3
- [ ] **Field-specific grounding thresholds** — stricter for `headquarters_location`, `employee_count_range`

## Deployment Context

- **Remote server**: `192.168.0.136`
- **Deployed via**: `docker-compose.prod.yml` (builds from GitHub `main` branch)
- **Pipeline API**: `http://192.168.0.136:8742` (container port 8000 -> host 8742)
- **API key**: in `.env` (`API_KEY=thisismyapikey3215215632`)
- **LLM**: vLLM on `192.168.0.247:9003` (gemma3-12b-awq default)
- **Embeddings**: bge-m3 on `192.168.0.136:9003`
- **Portainer env ID**: 3, postgres container `c2b1f4a5df29`
- **DB column**: `uri` not `url` on sources table

## Key Files

- `src/services/extraction/grounding.py` — `verify_quote_in_source()` (3-tier), `ground_field_item()`. Will get `ground_and_locate()`.
- `src/services/extraction/extraction_items.py` — `locate_in_source()` (single-tier), `SourceLocation`. Both need update.
- `src/services/extraction/schema_orchestrator.py` — v2 path: `_parse_chunk_to_v2()`, `_extract_entity_chunk_v2()`. Grounding+location call sites.
- `src/services/llm/chunking.py` — `chunk_document()`, optional `source_char_offset`.
- `src/models.py:246` — `DocumentChunk` dataclass.
- `docker-compose.prod.yml:280` — `EXTRACTION_DATA_VERSION` env var.
- `docs/TODO_quote_source_tracing.md` — Implementation plan (revised with trial data).
- `scripts/trial_quote_tracing*.py` — Trial scripts for validating algorithm against production data.

## Context

- **v2 is live**: All new extractions use v2 format. v1 data coexists (downstream handles both).
- **Quote tracing validated**: `ground_and_locate()` achieves 87.3% match rate. Reference impl: `scripts/trial_ground_and_locate.py`.
- **Extraction quality across schemas**: Jobs 97.1% well-grounded (best), Drivetrain 86.9%, Wikipedia 79.7%. Quality correlates with source completeness. Main problems: world knowledge leaking (8.7%), value-as-quote echo (23.1%), uncalibrated confidence. See `docs/TODO_extraction_quality.md`.
- **Content is NOT sentences**: Tables, lists, key-value specs, fragments. Block-based matching (`\n\n` then `\n`) aligns with actual content structure.
- Test suite: ~1900 tests passing
- Main batch project ID: `99a19141-9268-40a8-bc9e-ad1fa12243da`

## Completed TODO Docs

| Doc | Status |
|-----|--------|
| `docs/TODO_pipeline_fixes.md` | Done |
| `docs/TODO_extraction_reliability.md` | Done |
| `docs/TODO_domain_dedup.md` | Done |
| `docs/TODO_scheduler_startup_resilience.md` | Done |
| `docs/TODO_grounding_and_consolidation.md` | Done |
| `docs/TODO_classification_robustness.md` | Ready to implement (v3 spec) |
| `docs/TODO_global_sources.md` | Ready to implement |
| `docs/TODO_quote_source_tracing.md` | **Ready to implement** (algorithm validated, reference impl exists) |
| `docs/TODO_extraction_quality.md` | **Ready to implement** (5 fixes, Phase A needs no re-extraction) |
