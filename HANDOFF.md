# Handoff: Knowledge Extraction Orchestrator

Updated: 2026-02-01

## Current State

The system is **production-ready**. All major feature work is complete.

### Recently Completed

| Feature | Status | Date |
|---------|--------|------|
| Camoufox Browser Recovery | Complete | 2026-02-01 |
| Page Classification System | Complete | 2026-01-30 |
| Smart Classifier (LLM-assisted) | Complete | 2026-01-30 |
| Crawl Pipeline Fixes | Complete | 2026-01-29 |
| Embedding Recovery Service | Complete | 2026-01-29 |
| Job Duration Metrics | Complete | 2026-01-28 |

### Camoufox Browser Recovery (Latest)

**Problem solved**: When `page.goto()` timed out (180s), the browser died and all queued requests failed instantly with "Browser.new_context: Target page, context or browser has been closed".

**Solution** (commit `45dc906`):
- `is_connected()` health check skips dead browsers in round-robin selection
- Background restarts scheduled for dead browsers found during selection
- `_restart_browser()` with 30s timeout on cleanup prevents hanging
- All-disconnected case synchronously restarts browser 0

**Key files**:
- `src/services/camoufox/scraper.py` - Browser pool with recovery logic
- `docs/reviews/camoufox_browser_recovery_review.md` - Detailed review & verification

**Tests**: 17/17 camoufox tests passing

### Feature Flags

Classification is controlled by three feature flags (all default `False` for safe rollout):

| Flag | Purpose |
|------|---------|
| `classification_enabled` | Enable page classification |
| `classification_skip_enabled` | Enable skipping irrelevant pages |
| `smart_classification_enabled` | Use LLM-assisted classification |

## Remaining TODO Files

| File | Priority | Summary |
|------|----------|---------|
| `docs/TODO_production_readiness.md` | HIGH | Schema update safety, production checklist |
| `docs/TODO_architecture_database_consistency.md` | MEDIUM | Async/sync mismatch, transaction boundaries |
| `docs/TODO_high_concurrency_tuning.md` | LOW | Configuration for high-throughput scenarios |

## Key Files

| Component | Path |
|-----------|------|
| Camoufox Scraper | `src/services/camoufox/scraper.py` |
| Page Classifier | `src/services/extraction/page_classifier.py` |
| Smart Classifier | `src/services/extraction/smart_classifier.py` |
| Schema Orchestrator | `src/services/extraction/schema_orchestrator.py` |
| Extraction Pipeline | `src/services/extraction/pipeline.py` |
| Config | `src/config.py` |

## Test Status

Core tests passing:
- `test_camoufox_browser_pool.py`: 12 passed
- `test_camoufox_headers.py`: 5 passed
- `test_page_classifier.py`: 35 passed
- `test_smart_classifier.py`: 32 passed
- `test_schema_orchestrator.py`: 3 passed
- `test_pipeline_context.py`: 3 passed

Note: Some test files require optional dependencies (aiohttp, playwright) that may not be installed.

## Next Steps

1. **Deploy updated camoufox service** - Browser recovery is ready for production
2. **Monitor camoufox logs** - Look for `browser_disconnected_skipping` and `browser_restarted` events
3. **Enable classification in production** - Set `classification_enabled=True` to start filtering field groups
4. **Address remaining TODOs** - Schema update safety is the highest priority remaining item
