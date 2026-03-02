# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-03

## Recently Completed

- [x] **Decompose ExtractionPipelineService** — split `pipeline.py` (911→742 lines) into 3 focused services (commit `9f5f471`):
  - `embedding_pipeline.py` — unified embed+upsert service (eliminates duplication between pipelines)
  - `backpressure.py` — LLM queue backpressure with exponential backoff
  - `content_selector.py` — domain-dedup-aware content selection
  - 23 new tests, all 1715 tests pass
- [x] **Enable Phase 1A extraction reliability** — chunk overlap, source quoting, conflict detection, schema validation, confidence gating (commit `89b4284`)
- [x] **Exception hierarchy** — `AppError` with `TransientError`/`PermanentError` branches (commit `f2c98ce`)
- [x] **Fix dual import paths** — `from src.X` → `from X` (commit `d567f96`)
- [x] **Domain boilerplate dedup** — Phases A-E complete, section-aware two-pass (commit `91a7f1d`)

## Already Enabled (no action needed)

- **Domain dedup** — `domain_dedup_enabled=True` already in `config.py:418`
- **Classification** — all 4 booleans already `True` in `config.py:442-483`:
  - `classification_enabled=True`
  - `classification_skip_enabled=True`
  - `smart_classification_enabled=True`
  - `classification_use_default_skip_patterns=True`
- No `.env` overrides for any of these settings

## In Progress

- **Nothing in progress**

## Next Steps

- [ ] **Validate domain dedup on real data** (Phase F) — run `analyze_boilerplate` on drivetrain project (`99a19141-...`), inspect stats, spot-check cleaned_content. See `docs/TODO_domain_dedup.md` Phase F
- [ ] **Validate classification on real data** — re-extract David Brown Santasalo, verify page_type populated, product pages don't get company_meta. See `docs/TODO_extraction_reliability.md` verification items 3 & 7
- [ ] **Scheduler startup resilience** (MED effort, HIGH impact) — cleanup stale jobs on startup, stagger workers. See `docs/TODO_scheduler_startup_resilience.md`
- [ ] **Extraction pipeline fixes** — merge strategy defaults, config hardening, chunking quality. See `docs/TODO_pipeline_fixes.md`
- [ ] **Separate ServiceContainer from Scheduler** (MED effort, MED impact) — extract service creation from `JobScheduler`
- [ ] **Group configuration** (LOW effort, MED impact) — nest 100+ flat settings into subsystem classes

## Key Files

- `src/services/extraction/pipeline.py` — Main pipeline orchestration (742 lines, decomposed)
- `src/services/extraction/embedding_pipeline.py` — Unified embed+upsert service (NEW)
- `src/services/extraction/backpressure.py` — Backpressure manager (NEW)
- `src/services/extraction/content_selector.py` — Content selection logic (NEW)
- `src/config.py` — All feature flags (domain dedup + classification already enabled)
- `docs/TODO_pipeline_fixes.md` — 5-phase extraction pipeline improvements
- `docs/TODO_scheduler_startup_resilience.md` — Startup cleanup + throttle plan

## Context

- All work committed on `main` (not yet pushed to remote)
- Test suite: 1715 tests passing
- GitNexus index behind HEAD — run `npx gitnexus analyze` before using graph queries
- Domain dedup and classification are enabled in code defaults but not yet validated on real extraction data
