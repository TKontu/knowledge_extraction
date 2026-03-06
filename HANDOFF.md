# Handoff: Knowledge Extraction Orchestrator

**Last updated:** 2026-03-05

## Recently Completed

- [x] **Fix 32 verified extraction pipeline design issues** — 6 phases: critical data correctness (source_groups plumbing, temperature=0.0 falsy, confidence pop/default), config facade completion (SchemaExtractionPipeline DI, content_selector migration, LLMWorker content_limit injection), reliability (EmbeddingResult typed return, domain dedup single-pass, entity dedup case-insensitive, flush-not-commit), LLM client fixes (backoff cap+jitter, retry hints, CJK-aware chunking, H4+ headers, _singularize helper), polish (cache key includes model, facade caching, time-throttled cancel, word-boundary page matching, MCP offset param). 30+ files changed. 1738 tests pass. Full review: `docs/review_extraction_pipeline_design.md`.
- [x] **Fix 10 verified extraction pipeline issues** — 2 critical bugs, 4 important design problems, 4 minor fixes. Typed `SchemaPipelineResult` return, orchestrator config injection, `merge_dedupe` strategy, SHA-256 hashing, configurable batch size/classifier limit/embedding dimension, fail on invalid schema, remove drivetrain defaults, recovery endpoint uses shared services. 55 files changed, net -928 lines.
- [x] **Typed config facade migration (all 7 phases)** — Services now accept typed frozen dataclass facades instead of monolithic `Settings`. Global module-level settings also migrated to facade-style access (`settings.llm.model` instead of `settings.llm_model`).
  - Phase 0: Cleanup schema_extractor.py global_settings
  - Phase 1a: UrlRelevanceFilter → scalar kwargs
  - Phase 1b: DomainDedupService → ExtractionConfig
  - Phase 2: SmartClassifier → ClassificationConfig
  - Phase 3: EmbeddingService → LLMConfig
  - Phase 4a: LLMClient → LLMConfig
  - Phase 4b: SchemaExtractor → LLMConfig
  - Phase 5: ExtractionWorker → typed facades (llm, extraction, classification)
  - Phase 6: Global module-level settings → facade access in 7 source files + 7 test files
- [x] **Group configuration facades** — 10 frozen dataclasses (`DatabaseConfig`, `LLMConfig`, `LLMQueueConfig`, `ExtractionConfig`, `ClassificationConfig`, `ScrapingConfig`, `CrawlConfig`, `ProxyConfig`, `SchedulerConfig`, `ObservabilityConfig`) with `@property` accessors on Settings. 48 new tests.
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
- [x] **Domain boilerplate dedup** — All phases (A-F) complete, section-aware two-pass (commit `91a7f1d`). Validated on 249 production domains (2026-03-04).

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

- **Grounding & Consolidation** — Implementation plan ready. See `docs/TODO_grounding_and_consolidation.md`. 6 increments, ~1100 new lines, ~100 tests.

## Next Steps (prioritized)

### Completed Validation
- [x] **Validate domain dedup** (Phase F) — ✅ Validated 2026-03-04.
- [x] **Full re-extraction** — 12,068 sources, 46,949 extractions, zero failures.
- [x] **Classification quality assessment** — 57.7% zero-confidence waste confirmed. LLM skip-gate trials done.
- [x] **Downstream pipeline trials** (Trials 1-2A, 4A) — See `docs/TODO_downstream_trials.md`.
- [x] **Grounding & verification trials** — 10+ trials, 5 models tested. Quote-based verification with Qwen3-30B: 80-100% detection, 100% recall. Full-content verification rejected (dead end). Prompt-based grounding rejected (47-80% recall loss). See `docs/TODO_grounded_extraction.md`.
- [x] **Multilingual product dedup** — Batched LLM dedup validated (Rossi: 3.6x dedup, 6.2s). Prompt tuning needed for edge cases.

### Code Tasks (by priority)
- [ ] **Grounding verification + consolidation** — #1 PRIORITY. 6 increments. See `docs/TODO_grounding_and_consolidation.md`.
  - [ ] Increment 1: Grounding pure functions (string-match)
  - [ ] Increment 2: DB schema + retroactive scoring (47K extractions)
  - [ ] Increment 3: LLM quote verification (Qwen3-30B, unresolved fields)
  - [ ] Increment 4: Consolidation pure functions (6 strategies)
  - [ ] Increment 5: Consolidation service + DB + API
  - [ ] Increment 6: Pipeline integration (inline grounding)
- [ ] **LLM skip-gate classification** — Replace embedding classifier with binary LLM gate. See `docs/TODO_classification_robustness.md`.
- [ ] **Report integration with consolidation** — Reports read consolidated records instead of raw per-URL extractions.
- [ ] **Multilingual product dedup** — Enhancement to union_dedup strategy. Batched LLM grouping during consolidation.
- [ ] **Global sources architecture** — Decouple sources from projects. See `docs/TODO_global_sources.md`.
- [ ] **Search fix + reranking** — Fix 500 errors, add bge-reranker-v2-m3.
- [ ] **Entity extraction wiring** — Connect existing infrastructure to pipeline (run on consolidated records).

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
| `docs/review_extraction_pipeline_design.md` | ✅ All 32 issues implemented (6 phases) |
| `docs/TODO_pipeline_fixes.md` | ✅ All 5 phases complete |
| `docs/TODO_extraction_reliability.md` | ✅ All phases complete (extraction quality validation pending) |
| `docs/TODO_domain_dedup.md` | ✅ All phases (A-F) complete, validated on production data |
| `docs/TODO_scheduler_startup_resilience.md` | ✅ Phases 1-2 + ServiceContainer done |
| `docs/TODO-fix-dual-import-paths.md` | ✅ Complete |
| `docs/TODO_smart_crawl.md` | ✅ Complete |
| `docs/TODO_report_table_grouping.md` | ✅ Complete |
| `docs/TODO_classification_robustness.md` | ⬜ v3 spec with trial results, ready to implement |
| `docs/TODO_global_sources.md` | ⬜ Full spec with migration plan |
| `docs/TODO_downstream_trials.md` | ✅ Trials 1, 2A, 4A complete. Findings feed into grounding plan. |
| `docs/TODO_grounded_extraction.md` | ✅ All trials complete. Design doc with model comparison results. |
| `docs/TODO_grounding_and_consolidation.md` | ⬜ Implementation plan (6 increments). Ready to start. |

## Context

- Code changes on `main` (HANDOFF.md, TODO docs modified/new — not yet committed)
- Test suite: 1738 tests passing
- GitNexus index behind HEAD — run `npx gitnexus analyze` before using graph queries
- **Full re-extraction completed** (2026-03-05): 12,068 sources, 46,949 extractions, zero failures. Confirmed 57.7% zero-confidence waste from embedding classifier.
- **LLM classification trials completed** (2026-03-05): 4 trials across 6 models (gemma3-4B/12b/27B, Qwen3-4B/8B/30B), 30-80 pages each. Key findings:
  - Binary "extract or skip?" >> group selection (92% vs 34-53% recall)
  - gemma3-4B best for classification: 92.6% recall, 0.18s/page, ~1100 tokens
  - Schema-agnostic design works: pass schema as context, no hardcoded domain knowledge
  - GT from extraction confidence is noisy: ~38% of "should skip" pages have relevant data the extraction model failed on
  - Architecture: permissive skip-gate + downstream confidence gating in reports
- **vLLM model names updated** (2026-03-05): gemma3-12b-awq → gemma3-12b-it-qat-awq, Qwen3-30B-A3B-Instruct-4bit → Qwen3-30B-A3B-it-4bit, etc. qwen3-8B broken (100% parse errors), Qwen3.5-27B-4bit broken (won't load).
- Trial scripts at `/tmp/llm_classify_compare.py`, `/tmp/llm_classify_compare2.py`, `/tmp/llm_skip_gate_trial.py`, `/tmp/llm_skip_gate_v3.py`, `/tmp/llm_skip_gate_v4.py`
- Two new TODO specs ready for implementation: `TODO_classification_robustness.md` (v3, LLM skip-gate) and `TODO_global_sources.md` (decouple sources from projects)
- **Grounding trial results** (2026-03-05): 10+ trials across 5 models. Key findings:
  - Quote-based verification with Qwen3-30B: 80% detection / 100% recall (employee counts), 100% / 67% (product specs)
  - Full-content verification: dead end regardless of model/context (20% recall with larger models)
  - Prompt-based grounding: rejected (47-80% recall loss)
  - gemma3-4B for classification, Qwen3-30B for verification (faster + better on this task)
  - Multilingual dedup: batched at 10 names/call works (Rossi 3.6x dedup in 6.2s)
  - Trial scripts: `/tmp/trial_quote_verification.py`, `/tmp/trial_model_comparison_v2.py`, `/tmp/trial_spec_verification_models.py`, `/tmp/trial_multilingual_dedup_v2.py`, `/tmp/trial_fullcontent_verify_v2.py`
