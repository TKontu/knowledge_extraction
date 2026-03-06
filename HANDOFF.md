# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-06

## Completed This Session

- [x] **Pipeline review & 7-increment fix plan** — Systematic review found 12 verified issues (3 false positives removed). Documented in `docs/PIPELINE_REVIEW.md`. All 7 increments implemented:
  - **Increment 1**: Boolean chunk merge (any-true-wins) + confidence averaging without dilution — `schema_orchestrator.py`
  - **Increment 2**: Per-chunk grounding alignment (scores computed with each chunk's own value+quote) — `schema_orchestrator.py`, `grounding.py`
  - **Increment 3**: Consolidation service robustness (session rollback after flush failure, endpoint error handling, dead code removal) — `consolidation_service.py`, `projects.py`
  - **Increment 4**: Confidence gate preserves data (records violation instead of nullifying) — `schema_validator.py`
  - **Increment 5**: Grounding weight continuous degradation with 0.1 floor (no 0.5 cliff) — `consolidation.py`
  - **Increment 6**: Pipeline observability (sources_skipped, sources_no_content, total_embedded, embedding_errors in SchemaPipelineResult) — `pipeline.py`, `embedding_pipeline.py`
  - **Increment 7**: DB indexes (Job.type, Job.status, Extraction.source_group) + ServiceContainer shutdown resilience (per-service error isolation + timeout) — `orm_models.py`, `service_container.py`

- [x] **Grounding verification & consolidation pipeline** (prior session, commit `1d5435b`):
  - Increment 1: String-match grounding verification (57 tests) — `src/services/extraction/grounding.py`
  - Increment 2: DB schema + backfill script (11 tests) — `alembic/versions/20260305_add_grounding_scores.py`, `scripts/backfill_grounding_scores.py`
  - Increment 3: LLM quote verification via Qwen3-30B (18 tests) — `src/services/extraction/llm_grounding.py`
  - Increment 4: Consolidation pure functions with 6 strategies (60 tests) — `src/services/extraction/consolidation.py`
  - Increment 5: Consolidation DB service + API + migration (10 tests) — `src/services/extraction/consolidation_service.py`, `alembic/versions/20260306_add_consolidated_extractions.py`
  - Increment 6: Pipeline inline grounding (8 tests) — grounding scores computed during extraction in `schema_orchestrator.py`, stored on `Extraction` via `pipeline.py`

## Already Enabled (no action needed)

- **Domain dedup** — `domain_dedup_enabled=True`
- **Classification** — all 4 booleans `True` (enabled, skip, smart, default_skip_patterns)
- **Scheduler startup resilience** — `scheduler_cleanup_stale_on_startup=True`
- **Schema extraction embeddings** — `schema_extraction_embedding_enabled=True`
- **Extraction reliability** — source quoting, conflict detection, schema validation
- **Inline grounding** — scores computed automatically during extraction (no flag needed)
- **Grounding LLM verification** — `grounding_llm_verify_enabled=True` (config default)

## Not Yet Deployed

- **DB migrations** need applying to remote (`192.168.0.136`):
  - `grounding_scores` JSONB column on `extractions` table (alembic `20260305`)
  - `consolidated_extractions` table (alembic `20260306`)
  - Indexes on `jobs.type`, `jobs.status`, `extractions.source_group` (`CREATE INDEX CONCURRENTLY`)
  - Apply via psycopg directly (remote deployment, not alembic CLI)
- **Backfill** existing 47K extractions with grounding scores: `scripts/backfill_grounding_scores.py`
- **LLM verification pass** on backfilled data: `scripts/backfill_grounding_scores.py --llm`

## Next Steps (prioritized)

### Deploy & Backfill
- [ ] Apply DB migrations to remote (2 schema changes + 3 indexes)
- [ ] Run backfill script for string-match grounding scores on existing extractions
- [ ] Run LLM verification pass on unresolved (score=0.0) fields
- [ ] Run consolidation: `POST /projects/{id}/consolidate`

### Code Tasks
- [ ] **LLM skip-gate classification** — Replace embedding classifier with binary LLM gate. gemma3-4B, 92.6% recall. See `docs/TODO_classification_robustness.md`.
- [ ] **Report integration with consolidation** — Reports read consolidated records instead of raw per-URL extractions.
- [ ] **Multilingual product dedup** — Enhancement to union_dedup strategy during consolidation.
- [ ] **Global sources architecture** — Decouple sources from projects. See `docs/TODO_global_sources.md`.
- [ ] **Search fix + reranking** — Fix 500 errors, add bge-reranker-v2-m3.
- [ ] **Entity extraction wiring** — Connect existing infrastructure to pipeline.

## Key Files

- `docs/PIPELINE_REVIEW.md` — 12 verified issues, 7-increment fix plan (all implemented)
- `src/services/extraction/grounding.py` — String-match verification (pure functions, stdlib only)
- `src/services/extraction/llm_grounding.py` — LLM fallback verification via `LLMClient.complete()`
- `src/services/extraction/consolidation.py` — 6 strategies: frequency, weighted_median, any_true, longest_top_k, union_dedup, weighted_frequency
- `src/services/extraction/consolidation_service.py` — DB integration: loads extractions → consolidates → upserts
- `src/services/extraction/schema_orchestrator.py` — Chunk merge (any-true booleans, per-chunk grounding), inline grounding score computation
- `src/services/extraction/schema_validator.py` — Confidence gate records violation but preserves data
- `src/services/extraction/pipeline.py` — SchemaPipelineResult with observability counters
- `src/services/extraction/embedding_pipeline.py` — Diagnostic error when all texts empty
- `src/api/v1/projects.py` — POST `/{project_id}/consolidate` with try/except rollback
- `src/orm_models.py` — `Extraction.grounding_scores` (JSONB), `ConsolidatedExtraction` model, new indexes
- `src/services/scraper/service_container.py` — Shutdown with per-service error isolation + timeout
- `scripts/backfill_grounding_scores.py` — Retroactive scoring CLI (string-match + LLM)

## Context

- Test suite: **~1800 tests** (added ~30 new tests across 7 increments)
- GitNexus index behind HEAD — run `npx gitnexus analyze` before using graph queries
- Untracked docs: `TODO_classification_robustness.md`, `TODO_downstream_trials.md`, `TODO_global_sources.md`, `TODO_grounded_extraction.md` (trial results & specs)
- Increment 8 (entity dedup composite key) deferred — entity extraction not wired into pipeline yet

## Completed TODO Docs

| Doc | Status |
|-----|--------|
| `docs/review_extraction_pipeline_design.md` | ✅ All 32 issues (6 phases) |
| `docs/TODO_pipeline_fixes.md` | ✅ All 5 phases |
| `docs/TODO_extraction_reliability.md` | ✅ All phases |
| `docs/TODO_domain_dedup.md` | ✅ All phases (A-F), validated |
| `docs/TODO_scheduler_startup_resilience.md` | ✅ Phases 1-2 + ServiceContainer |
| `docs/TODO_grounding_and_consolidation.md` | ✅ All 6 increments implemented |
| `docs/PIPELINE_REVIEW.md` | ✅ All 7 increments implemented |
| `docs/TODO_classification_robustness.md` | ⬜ v3 spec ready to implement |
| `docs/TODO_global_sources.md` | ⬜ Full spec with migration plan |
