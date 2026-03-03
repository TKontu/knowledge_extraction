# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-03

## Recently Completed

- [x] **Group configuration facades** — 10 frozen dataclasses (`DatabaseConfig`, `LLMConfig`, `LLMQueueConfig`, `ExtractionConfig`, `ClassificationConfig`, `ScrapingConfig`, `CrawlConfig`, `ProxyConfig`, `SchedulerConfig`, `ObservabilityConfig`) with `@property` accessors on Settings. Grouped access via `settings.llm.model` while flat fields unchanged. 48 new tests. 1774 total tests pass.
- [x] **Extraction Pipeline Fixes (all 5 phases)** — verified 2026-03-03, all implemented on `main`:
  - Phase 1: Merge strategy defaults (`highest_confidence` for numeric/text, `merge_strategy` field-level override, `VALID_MERGE_STRATEGIES`)
  - Phase 2: Config hardening (runtime content limit, chunk overlap cross-validation, configurable `max_items`)
  - Phase 3: Chunking quality (H2+ multi-level header splitting, CJK-aware token counting, preamble standalone)
  - Phase 4: Schema pipeline searchability (`ExtractionEmbeddingService.embed_and_upsert()`, `schema_extraction_embedding_enabled=True`)
  - Phase 5: Minor cleanup (`SourceStatus.PARTIAL`, `get_batch()` on SourceRepository, entity dedup by content hash)
- [x] **ServiceContainer + Scheduler Startup Resilience** — extracted service lifecycle into `ServiceContainer` (scheduler.py 489→310 lines), added stale job cleanup on startup + worker stagger.
  - `src/services/scraper/service_container.py` — creates, caches, tears down 10 app-lifetime services
  - `src/services/scraper/scheduler.py` — now takes `ServiceContainer`, adds `_cleanup_stale_jobs()` + stagger
  - `src/config.py` — `scheduler_cleanup_stale_on_startup=True`, `scheduler_startup_stagger_seconds=1.0`
  - 13 new tests (`test_service_container.py`, `test_scheduler_startup.py`)
- [x] **Decompose ExtractionPipelineService** — split `pipeline.py` (911→742 lines) into 3 focused services (commit `9f5f471`):
  - `embedding_pipeline.py` — unified embed+upsert service (eliminates duplication between pipelines)
  - `backpressure.py` — LLM queue backpressure with exponential backoff
  - `content_selector.py` — domain-dedup-aware content selection
- [x] **Enable Phase 1A extraction reliability** — chunk overlap, source quoting, conflict detection, schema validation, confidence gating (commit `89b4284`)
- [x] **Exception hierarchy** — `AppError` with `TransientError`/`PermanentError` branches (commit `f2c98ce`)
- [x] **Fix dual import paths** — `from src.X` → `from X` (commit `d567f96`)
- [x] **Domain boilerplate dedup** — Phases A-E complete, section-aware two-pass (commit `91a7f1d`)

## Already Enabled (no action needed)

- **Domain dedup** — `domain_dedup_enabled=True` in config defaults
- **Classification** — all 4 booleans `True`:
  - `classification_enabled=True`
  - `classification_skip_enabled=True`
  - `smart_classification_enabled=True`
  - `classification_use_default_skip_patterns=True`
- **Scheduler startup resilience** — `scheduler_cleanup_stale_on_startup=True`, `scheduler_startup_stagger_seconds=1.0`
- **Schema extraction embeddings** — `schema_extraction_embedding_enabled=True` (search_knowledge works for schema extractions)
- **Extraction reliability** — source quoting, conflict detection, schema validation all enabled
- No `.env` overrides for any of these settings

## In Progress

- **Nothing in progress**

## Next Steps (prioritized)

### Validation on Real Data (operational, no code changes)
- [ ] **Validate domain dedup** (Phase F) — run `analyze_boilerplate` on drivetrain project (`99a19141-...`), inspect stats, spot-check cleaned_content
- [ ] **Validate classification + extraction quality** — re-extract David Brown Santasalo, verify page_type populated, product pages don't get company_meta, HQ = "Jyväskylä, Finland" not "Santasalo"

### Code Tasks (by priority)
- [ ] **Migrate services to typed config facades** — gradually change service constructors from `settings: Settings` to typed subsystem configs (e.g., `LLMClient(config: LLMConfig)` instead of `LLMClient(settings: Settings)`)
- [ ] **Scheduler burst limiting** (Phase 3 from scheduler TODO) — configurable limit on queued jobs per worker in first N seconds after startup (deferred, lower priority)
- [ ] **Schema update safety** — block schema updates when extractions exist, or require `force=true` (see `docs/TODO_production_readiness.md`)

## Key Files

- `src/config.py` — All feature flags, 10 typed subsystem facades (`settings.llm`, `settings.extraction`, etc.)
- `src/services/scraper/service_container.py` — App-lifetime service container
- `src/services/scraper/scheduler.py` — Job scheduler (refactored, uses ServiceContainer)
- `src/services/extraction/pipeline.py` — Main pipeline orchestration (742 lines, decomposed)
- `src/services/extraction/embedding_pipeline.py` — Unified embed+upsert service
- `src/services/extraction/backpressure.py` — Backpressure manager
- `src/services/extraction/content_selector.py` — Content selection logic
- `src/services/extraction/schema_orchestrator.py` — Merge strategies, conflict detection, confidence recalibration
- `src/services/extraction/field_groups.py` — FieldDefinition (with merge_strategy), FieldGroup (with max_items)
- `src/services/llm/chunking.py` — CJK-aware token counting, H2+ splitting

## Completed TODO Docs

| Doc | Status |
|-----|--------|
| `docs/TODO_pipeline_fixes.md` | ✅ All 5 phases complete |
| `docs/TODO_extraction_reliability.md` | ✅ All phases complete (validation pending) |
| `docs/TODO_domain_dedup.md` | ✅ Phases A-E complete (Phase F validation pending) |
| `docs/TODO_scheduler_startup_resilience.md` | ✅ Phases 1-2 + ServiceContainer done |
| `docs/TODO-fix-dual-import-paths.md` | ✅ Complete |
| `docs/TODO_smart_crawl.md` | ✅ Complete |
| `docs/TODO_report_table_grouping.md` | ✅ Complete |

## Context

- All work committed on `main` (not yet pushed to remote)
- Test suite: 1774 tests passing
- GitNexus index behind HEAD — run `npx gitnexus analyze` before using graph queries
- All reliability features enabled in code defaults — not yet validated on real extraction data
