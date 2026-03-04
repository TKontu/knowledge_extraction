# Pipeline Review: Crawl/Scrape Pipeline

**Scope**: `src/api/v1/crawl.py`, `src/api/v1/scrape.py`, `src/services/scraper/`, `src/services/filtering/`, plus legacy fact-based extraction chain (`extractor.py`, `validator.py`, `profiles.py`)
**Focus**: Dead code, obsolete parameters, unused methods, legacy code chains
**Date**: 2026-03-04

## Flow

```
API: POST /crawl → Job(QUEUED) | POST /scrape → Job(QUEUED)
  → scheduler.py:_run_single_crawl_worker / _run_scrape_worker [poll loops]
    → CrawlWorker.process_job
    │   ├─ Standard: client.start_crawl → get_crawl_status → _store_pages → _create_extraction_job
    │   └─ Smart:    _smart_crawl_map_phase → _smart_crawl_filter_phase → _smart_crawl_scrape_phase
    → ScraperWorker.process_job
        └─ _scrape_url_with_retry → client.scrape → store sources → _create_extraction_job

Downstream: _create_extraction_job → Job(QUEUED, type=extract)
  → scheduler.py:_run_extract_worker
    → worker.py:ExtractionWorker.process_job
      ├─ HAS schema → SchemaExtractionPipeline (new, active)
      └─ NO schema  → ExtractionPipelineService (legacy fact-based fallback)
```

---

## Critical (must fix)

_None — the crawl/scrape pipeline itself is solid._

---

## Important (should fix)

### 1. Bug: `scrape.py:101` — Missing job type filter in `get_job_status`
- [ ] `src/api/v1/scrape.py:101` — Query uses `Job.id == job_uuid` without filtering `Job.type == "scrape"`
  - Compare to `crawl.py:139` which correctly filters `Job.type == "crawl"`
  - This means the scrape status endpoint returns ANY job type (crawl, extract, etc.) given its UUID
  - **Fix**: Add `.filter(Job.type == JobType.SCRAPE)` to the query

### 2. Dead ORM models: `Page`, `Fact`, `RateLimit`
- [ ] `src/orm_models.py:103-134` — `Page` model: never imported in any `src/` production code
- [ ] `src/orm_models.py:137-163` — `Fact` model: never imported in any `src/` production code
- [ ] `src/orm_models.py:217-231` — `RateLimit` model: never imported in any `src/` production code
  - All three only referenced in `tests/test_orm_models.py`
  - `Page`/`Fact` represent the old schema (before `Source`/`Extraction`). They appear to be leftover DB table definitions.
  - `RateLimit` was planned for DB-based rate limiting but `DomainRateLimiter` uses Redis instead
  - **Remove**: 3 ORM models + their test references. Also cleans up `Date` and `ARRAY` imports from orm_models.py (only used by `RateLimit` and `Profile` respectively)
  - **Caution**: Verify no Alembic migration references these tables before removing the models

### 3. Dead static method: `ScrapeResponse.create()`
- [ ] `src/models.py:98-107` — `ScrapeResponse.create()` static method
  - Never called anywhere — `scrape.py` constructs `ScrapeResponse(...)` directly
  - Also **broken**: doesn't pass required `project_id` field, so calling it would raise `ValidationError`
  - **Remove**: 10 lines

### 4. Completely dead service: `FactValidator`
- [ ] `src/services/extraction/validator.py` (entire file, 66 lines) — `FactValidator` class
  - Never imported by any production code in `src/`
  - Only tested in `tests/test_validator.py`
  - Part of the legacy fact-based chain
  - **Remove**: entire file + test file

### 5. Dead `ServiceContainer` properties (test-only)
- [ ] `src/services/scraper/service_container.py:154-157` — `qdrant_repo` property
  - Internal `_qdrant_repo` is used to construct `ExtractionEmbeddingService` and `ExtractionDeduplicator`, but the **public property** is only accessed in `tests/test_service_container.py:89`
  - **Remove**: property only (keep `_qdrant_repo` field). Update test to not access it.
- [ ] `src/services/scraper/service_container.py:174-177` — `llm_worker` property
  - Internal `_llm_worker` is used in `start()`/`stop()`, but the **public property** is only accessed in tests
  - **Remove**: property only. Update tests.

### 6. Dead `JobScheduler` context manager
- [ ] `src/services/scraper/scheduler.py:412-419` — `__aenter__`/`__aexit__`
  - Never used anywhere (production or tests)
  - Actual usage is `start_scheduler()`/`stop_scheduler()` from `main.py`
  - **Remove**: 8 lines

### 7. Dead filtering utilities
- [ ] `src/services/filtering/patterns.py:82-103` — `should_exclude_url()` function
  - Never called in production. Firecrawl handles exclusion server-side using the generated patterns; this client-side validation is unused.
  - Only tested in `tests/test_language_patterns.py`
  - **Remove**: function + test references
- [ ] `src/services/filtering/patterns.py:14-27` — `DEFAULT_EXCLUDED_LANGUAGES` constant
  - Never referenced in production. `crawl.py:44-45` builds its own list from `settings.excluded_language_codes`
  - Only tested in `tests/test_language_patterns.py`
  - **Remove**: constant + test references
- [ ] `src/services/filtering/language.py:8` — unused import `Any`
- [ ] `src/services/filtering/language.py:9` — unused import `parse_qs`

---

## Minor (nice to have)

### 8. `_extract_domain` duplicated in 3 locations
- [ ] `src/services/scraper/client.py:863` — `FirecrawlClient._extract_domain`
- [ ] `src/services/scraper/worker.py:260` — `ScraperWorker._extract_domain`
- [ ] `src/services/scraper/crawl_worker.py:353` — inline `urlparse(url).netloc`
  - All do the same thing: `urlparse(url).netloc`
  - **Consider**: Extract to a shared `utils.py` helper or pick one canonical location

### 9. `crawl.py:22-37` — No-op `DEFAULT_COMPANY_INCLUDE_PATHS`
- [ ] `src/api/v1/crawl.py:22` — `DEFAULT_COMPANY_INCLUDE_PATHS = None`
- [ ] `src/api/v1/crawl.py:35-37` — conditional assigns `None` to `include_paths` when it's already `None`
  - Functionally dead but serves as a documentation placeholder (comment on line 19 explains intent)
  - **Remove**: constant + conditional, or replace with a comment

### 10. Redundant singleton pattern in `language.py`
- [ ] `src/services/filtering/language.py:250-266` — `_service_instance` global + manual None-check
  - Redundant with `@lru_cache(maxsize=1)` on `get_language_service()`
  - **Remove**: the global `_service_instance` and the manual check; `lru_cache` handles it

### 11. Deprecated `asyncio.get_event_loop()` in `language.py`
- [ ] `src/services/filtering/language.py:187` — should be `asyncio.get_running_loop()`
  - `get_event_loop()` is deprecated since Python 3.10+

### 12. `filtering/__init__.py` re-exports are never used
- [ ] `src/services/filtering/__init__.py` — all 7 re-exported symbols
  - Every consumer imports directly from submodules (`from services.filtering.language import ...`)
  - The `__init__.py` re-exports are dead code. Could be emptied.

### 13. Misleading comment in `scheduler.py:422`
- [ ] `# Global instances -- backward compatible with start_scheduler()/stop_scheduler()`
  - These ARE the primary API (called from `main.py`), not a backward-compat shim
  - **Fix**: Remove or rewrite the comment

### 14. `ServiceContainer.__aenter__`/`__aexit__` — test-only
- [ ] `src/services/scraper/service_container.py:179-184` — context manager protocol
  - Only used in 1 test. Production uses `start()`/`stop()` directly.
  - **Low priority**: Could keep as convenience or remove + update test

### 15. Inline `import asyncio` in `service_container.py`
- [ ] `src/services/scraper/service_container.py:110` — `import asyncio` inside `start()` method
  - Should be a top-level import per project conventions

---

## Legacy Chain: Fact-Based Extraction (design decision)

The old fact-based extraction pipeline is still wired up as the **fallback path** when a project has no `extraction_schema` (see `worker.py:393`). This chain includes:

| Component | Location | Status |
|-----------|----------|--------|
| `ExtractionOrchestrator` | `src/services/extraction/extractor.py` | Used in `pipeline.py:71`, `scheduler.py:373` |
| `ExtractionPipelineService` | `src/services/extraction/pipeline.py:66-399` | Used in `worker.py:82,415,423` |
| `FactValidator` | `src/services/extraction/validator.py` | **DEAD** — never imported in production |
| `ProfileRepository` | `src/services/extraction/profiles.py` | Used by `pipeline.py:78,121-122` |
| `ExtractionProfile` dataclass | `src/models.py:289-298` | Used by `extractor.py`, `profiles.py`, `pipeline.py` |
| `ExtractedFact` dataclass | `src/models.py:268-276` | Used by `LLMClient.extract_facts`, `extractor.py` |
| `ExtractionResult` dataclass | `src/models.py:279-286` | Used only by `extractor.py` |
| `LLMClient.extract_facts()` | `src/services/llm/client.py:84-105` | Used only by `ExtractionOrchestrator` |
| `LLMClient._extract_facts_*()` | `src/services/llm/client.py:107-270` | Internal to above |
| `LLMWorker._extract_facts()` | `src/services/llm/worker.py:365-433` | Queue handler for above |
| `Profile` ORM model | `src/orm_models.py:166-188` | Used by `ProfileRepository` |
| `Page` ORM model | `src/orm_models.py:103-134` | **DEAD** |
| `Fact` ORM model | `src/orm_models.py:137-163` | **DEAD** |

**Question**: Do we still need the fact-based fallback? Every project created via templates has an `extraction_schema`. If we decide all projects MUST have a schema, the entire legacy chain (~400+ lines across 8 files) can be removed in a future cleanup pass.

**Recommendation**: For now, remove only the **confirmed dead** items (`FactValidator`, `Page`, `Fact`, `RateLimit` ORM models). Defer the full legacy chain removal until we confirm no projects use the fallback path.

---

## Verified NOT Dead (false positives)

| Item | Location | Why it's live |
|------|----------|---------------|
| `check_llms_txt` | `client.py:241` | Called inside `start_crawl` (line 351) — llms.txt checking feature |
| `LlmsTxtResult` | `client.py:29` | Return type for above, used internally |
| `AI_BOT_PATTERNS` | `client.py:18` | Used by `check_llms_txt` |
| `DEFAULT_USER_AGENT` | `client.py:15` | Used in `start_crawl` (line 369) |
| `reset_daily_count` | `rate_limiter.py:205` | Test-only but intentional (docstring says "mainly for testing") |
| `_get_stale_warning` | `crawl_worker.py:29` | Internal helper, called at line 142 |
| Smart crawl methods | `crawl_worker.py` | All wired through phase routing |
| `get_language_service` | `language.py:254` | Called from `crawl_worker.py:274` |
| `generate_language_exclusion_patterns` | `patterns.py:30` | Called from `crawl.py:49` |
