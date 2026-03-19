# Pipeline Review: Extraction Pipeline

**Scope**: `src/services/extraction/` + `src/services/scraper/scheduler.py` + `src/api/v1/extraction.py`
**Focus**: Dead code, obsolete parameters, unused methods
**Date**: 2026-03-04

## Flow

```
API: POST /extract → Job(QUEUED)
  → scheduler.py:_run_extract_worker [poll loop]
    → worker.py:process_job
      ├─ HAS schema → _create_schema_pipeline → SchemaExtractionPipeline.extract_project
      │   └─ orchestrator.extract_all_groups → extractor.extract_field_group [per chunk]
      └─ NO schema  → ExtractionPipelineService.process_project_pending (generic fallback)
```

---

## Critical (must fix)

### 1. Deprecated `extract-schema` endpoint duplicates service instantiation
- [ ] `src/api/v1/extraction.py:257-393` — `POST /extract-schema` endpoint
  - Already marked `deprecated=True` in FastAPI
  - Creates **per-request** `EmbeddingService`, `SmartClassifier`, `LLMRequestQueue` (lines 333-379) — inconsistent with ServiceContainer pattern used everywhere else
  - Missing: job tracking, cancellation, checkpointing, embedding upsert, force re-extract
  - Superseded by `POST /extract` (lines 30-151) which goes through the worker pipeline
  - **~130 lines** to remove
  - **Risk**: Check if any MCP tools or external callers use this endpoint

---

## Important (should fix)

### 2. Dead parameter: `extraction_context` in `extract_source()`
- [ ] `src/services/extraction/pipeline.py:421` — `extraction_context: "ExtractionContext | None" = None`
  - Accepted as parameter, documented in docstring (line 431), but **never read** in the function body
  - The only caller (internal `extract_with_limit` at line 629) does not pass it
  - **Remove**: parameter + docstring line

### 3. Deprecated `company_name` params — no production callers
- [ ] `src/services/extraction/schema_extractor.py:84` — `company_name` on `extract_field_group()`
- [ ] `src/services/extraction/pipeline.py:419` — `company_name` on `extract_source()`
- [ ] `src/services/extraction/schema_orchestrator.py:61` — `company_name` on `extract_all_groups()`
  - All three locations have `source_context` as the replacement
  - **Zero production callers pass `company_name`** — verified in all `src/` call sites:
    - `pipeline.py:629` passes `source_context=source.source_group`
    - `pipeline.py:457` passes `source_context=context_value`
    - `schema_orchestrator.py:247` passes `source_context=source_context`
  - Only **tests** use it: `test_schema_extractor_queue.py` (8 sites), `test_schema_extractor.py` (1), `test_pipeline_context.py` (1)
  - **Remove**: params + backward-compat `context_value = source_context or company_name` lines + update ~10 test call sites to use `source_context=`

### 4. Unused `ProfileRepository` methods
- [ ] `src/services/extraction/profiles.py:38-45` — `list_all()` — never called from `src/`, only tested
- [ ] `src/services/extraction/profiles.py:47-56` — `list_builtin()` — never called from `src/`, only tested
- [ ] `src/services/extraction/profiles.py:58-68` — `exists()` — never called from **anywhere** (not even tests)
  - Only `get_by_name()` is used in production (from `pipeline.py:122`)
  - **Remove**: 3 methods + corresponding test methods in `tests/test_profile_repository.py`

---

## Minor (nice to have)

### 5. `context` parameter never explicitly passed on 2 constructors
- [ ] `src/services/extraction/schema_orchestrator.py:35` — `context: "ExtractionContext | None" = None`
- [ ] `src/services/extraction/schema_extractor.py:45` — `context: "ExtractionContext | None" = None`
  - Neither constructor call in `src/` ever passes `context=`
  - Both always use the default `ExtractionContext()`
  - `self.context` / `self._context` ARE actively used (prompts, entity dedup)
  - **Low priority**: These serve as extensibility points for template-level context. The default works for all current templates. Consider removing only if ExtractionContext customization is confirmed unnecessary.

---

## Verified NOT Dead (false positives from exploration)

| Item | Location | Status |
|------|----------|--------|
| `asyncio` import | `schema_extractor.py:3` | Used at line 332 (`asyncio.sleep`) |
| `json` import | `schema_extractor.py:4` | Used at line 282 (`json.JSONDecodeError`) |
| `self._context` in orchestrator | `schema_orchestrator.py:50` | Used at line 471 (`entity_id_fields`) |
| `self.context` in extractor | `schema_extractor.py:62` | Used at lines 372, 401, 435, 478 (prompts) |
| `BackpressureManager` | `backpressure.py` | Used by scheduler.py:387 and pipeline.py:90 |
| `embed_facts()` | `embedding_pipeline.py:123` | Used by pipeline.py:174 (generic path) |
| `EmbeddingResult` | `embedding_pipeline.py:18` | Return type of `embed_facts()` |
| `ExtractionPipelineService` | `pipeline.py:66` | Active fallback path in worker.py |
| `EmbeddingRecoveryService` | `embedding_recovery.py:38` | Used by API endpoint at extraction.py:424 |
| `ProfileRepository.get_by_name()` | `profiles.py:20` | Used by pipeline.py:122 |

---

## Summary

| Category | Items | Lines saved (est.) |
|----------|------:|-------------------:|
| Deprecated endpoint removal | 1 | ~130 |
| Dead parameters | 4 | ~20 |
| Unused methods | 3 | ~30 |
| Test updates needed | ~12 call sites | — |
| **Total** | **8 items** | **~180 lines** |

## Recommended Order

1. **`extraction_context` param** — zero-risk removal, no test changes needed
2. **`ProfileRepository` unused methods** — isolated, test-only impact
3. **`company_name` deprecated params** — requires coordinated test updates
4. **Deprecated `extract-schema` endpoint** — biggest impact, needs MCP/external caller check first
