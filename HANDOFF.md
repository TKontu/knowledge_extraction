# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-06

## Completed (all previous sessions)

- [x] **Pipeline review & 7-increment fix plan** — 12 verified issues, all 7 increments implemented (boolean merge, grounding alignment, consolidation robustness, confidence gate, grounding weight, pipeline observability, DB indexes + shutdown resilience)
- [x] **Grounding verification & consolidation pipeline** — 6 increments, 164 tests, +4654 lines (string-match grounding, DB schema, LLM verification, 6 consolidation strategies, DB service + API, pipeline inline grounding)
- [x] **Extraction pipeline refactor** — commits `6f6fd1b`, `049d58b`, `e395b8e`
- [x] **DB migrations applied to remote** — `grounding_scores` JSONB, `consolidated_extractions` table, indexes on `jobs.type`/`jobs.status`/`extractions.source_group`

## Deployment Context

- **Remote server**: `192.168.0.136`
- **Deployed via**: `docker-compose.prod.yml` (builds from GitHub `main` branch)
- **Pipeline API**: `http://192.168.0.136:8742` (container port 8000 -> host 8742)
- **API key**: in `.env` (`API_KEY=thisismyapikey3215215632`)
- **LLM**: vLLM on `192.168.0.247:9003` (gemma3-12b-awq default, gemma3-4B for classification)
- **Embeddings**: bge-m3 on `192.168.0.136:9003`
- **MCP tools available**: Portainer (container exec, stack management) + Knowledge Extraction API (projects, extractions, consolidation, reports)
- **Portainer env ID**: 3, pipeline container in `scristill-stack`

## Already Enabled (no action needed)

- Domain dedup, classification, scheduler resilience, schema extraction embeddings
- Extraction reliability (source quoting, conflict detection, schema validation)
- Inline grounding (scores computed during extraction)
- Grounding LLM verification (`grounding_llm_verify_enabled=True`)

## Next Steps (prioritized)

### Deploy & Backfill
- [ ] **Run grounding backfill** — `scripts/backfill_grounding_scores.py` inside pipeline container (string-match pass on ~47K extractions)
- [ ] **Run LLM verification** — `scripts/backfill_grounding_scores.py --llm` on unresolved (score=0.0) fields
- [ ] **Run consolidation** — `POST /projects/{id}/consolidate` via MCP API (main batch: `99a19141-9268-40a8-bc9e-ad1fa12243da`)

### Code Tasks
- [ ] **LLM skip-gate classification** — Replace embedding classifier with binary LLM gate. gemma3-4B, 92.6% recall. See `docs/TODO_classification_robustness.md` v3.
- [ ] **Report integration with consolidation** — Reports read consolidated records instead of raw per-URL extractions.
- [ ] **Multilingual product dedup** — Enhancement to union_dedup strategy during consolidation.
- [ ] **Global sources architecture** — Decouple sources from projects. See `docs/TODO_global_sources.md`.
- [ ] **Search fix + reranking** — Fix 500 errors, add bge-reranker-v2-m3.
- [ ] **Entity extraction wiring** — Connect existing infrastructure to pipeline.

## Key Files

- `scripts/backfill_grounding_scores.py` — Retroactive scoring CLI (string-match + LLM)
- `src/services/extraction/grounding.py` — String-match verification (pure functions)
- `src/services/extraction/llm_grounding.py` — LLM fallback verification
- `src/services/extraction/consolidation.py` — 6 strategies: frequency, weighted_median, any_true, longest_top_k, union_dedup, weighted_frequency
- `src/services/extraction/consolidation_service.py` — DB integration: loads extractions -> consolidates -> upserts
- `src/services/extraction/schema_orchestrator.py` — Chunk merge + inline grounding
- `src/api/v1/projects.py` — POST `/{project_id}/consolidate`
- `src/orm_models.py` — `Extraction.grounding_scores`, `ConsolidatedExtraction` model
- `docker-compose.prod.yml` — Production deployment (builds from Dockerfile)
- `docs/TODO_classification_robustness.md` — LLM skip-gate spec (v3, ready to implement)
- `docs/TODO_global_sources.md` — Global sources architecture spec

## Context

- Test suite: ~1800 tests passing
- GitNexus index behind HEAD — run `npx gitnexus analyze` before graph queries
- Main batch project ID: `99a19141-9268-40a8-bc9e-ad1fa12243da`
- DBS test project ID: `b0cd5830-92b0-4e5e-be07-1e16598e6b78`
- Increment 8 (entity dedup composite key) deferred — entity extraction not wired yet

## Completed TODO Docs

| Doc | Status |
|-----|--------|
| `docs/review_extraction_pipeline_design.md` | Done (32 issues, 6 phases) |
| `docs/TODO_pipeline_fixes.md` | Done (5 phases) |
| `docs/TODO_extraction_reliability.md` | Done (all phases) |
| `docs/TODO_domain_dedup.md` | Done (phases A-F, validated) |
| `docs/TODO_scheduler_startup_resilience.md` | Done (phases 1-2 + ServiceContainer) |
| `docs/TODO_grounding_and_consolidation.md` | Done (6 increments) |
| `docs/PIPELINE_REVIEW.md` | Done (7 increments) |
| `docs/TODO_classification_robustness.md` | Ready to implement (v3 spec) |
| `docs/TODO_global_sources.md` | Ready to implement (full spec) |
