# Handoff: Codebase Review & Architecture Documentation

## Completed

- **Full codebase review** — read every service module, API endpoint, pipeline, storage layer, MCP tools
- **Created `docs/review_20260302/`** with 8 comprehensive documentation files (2,738 lines total):
  - `README.md` — Project overview, tech stack, architecture diagram, quick start
  - `api_review.md` — All 47 endpoints, 13 routers, middleware stack, MCP mapping
  - `pipeline_crawl_scrape.md` — Traditional + smart crawl, scrape worker, rate limiting, Firecrawl
  - `pipeline_extraction.md` — Schema + generic extraction, classification, chunking, merging, validation
  - `pipeline_llm.md` — Dual-mode LLM client, Redis Streams queue, adaptive concurrency, JSON repair
  - `pipeline_reports.md` — Report types (single/comparison/table), LLM synthesis, formats
  - `storage_layer.md` — ORM models, repository pattern, Qdrant, Redis usage
  - `architecture_analysis.md` — Critical assessment (scored 3.5/5) with viable alternatives

## In Progress

- **Nothing in progress** — review and documentation is complete
- Existing uncommitted changes on `main` are from **prior sessions** (Phase 1A extraction reliability, 47 files changed)

## Next Steps

- [ ] **Fix dual import paths** (LOW effort, HIGH impact) — standardize `from src.X` → `from X` in 103 test file occurrences. See `docs/TODO-fix-dual-import-paths.md`
- [ ] **Establish exception hierarchy** (LOW effort, HIGH impact) — add `AppError` base class in `src/exceptions.py`, subclass existing 11 scattered exception types
- [ ] **Enable Phase 1A features** (LOW effort, MED impact) — flip config flags for source quoting, conflict detection, validation (already built + tested)
- [ ] **Decompose ExtractionPipelineService** (MED effort, HIGH impact) — split into ExtractionCoordinator + focused services (extraction, embedding, entity, status)
- [ ] **Separate ServiceContainer from Scheduler** (MED effort, MED impact) — extract service creation from `JobScheduler`
- [ ] **Group configuration** (LOW effort, MED impact) — nest 100+ flat settings into subsystem classes
- [ ] **Commit the uncommitted Phase 1A work** — 47 modified files with extraction reliability features, 54 new tests

## Key Files

- `docs/review_20260302/architecture_analysis.md` — Full critical assessment with prioritized recommendations and alternative architectures
- `docs/review_20260302/README.md` — Accurate project overview (replaces outdated root README)
- `src/services/extraction/pipeline.py` — Main target for decomposition (900 lines, 5+ responsibilities)
- `src/exceptions.py` — Currently only has `LLMExtractionError` + `QueueFullError`, needs hierarchy
- `docs/TODO-fix-dual-import-paths.md` — Detailed plan for fixing the import issue

## Context

- **Prior uncommitted work**: 47 files changed from Phase 1A extraction reliability (chunk overlap, source quoting, conflict detection, schema validation). This was done in prior sessions and is NOT from this session.
- **Architecture alternatives discussed**: Pipeline decomposition, exception hierarchy, Instructor for LLM, pgvector vs Qdrant, ServiceContainer pattern. User reviewed these and may want to implement some.
- **No tests were run this session** (per user instruction).
- **GitNexus index** is 1 commit behind HEAD — run `npx gitnexus analyze` before using graph queries.
