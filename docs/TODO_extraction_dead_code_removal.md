# Plan: Extraction Pipeline Dead Code Removal

## Context

A thorough review of `src/services/extraction/` (18 files) confirmed the codebase has a complete parallel extraction system — the "generic pipeline" — that is never reached in production. Every project gets an `extraction_schema` via the Pydantic validator on `ProjectCreate` (`src/models.py:378-385`), and the schema pipeline already has its own `DEFAULT_EXTRACTION_TEMPLATE` fallback (`pipeline.py:566-571`). The ORM column is `nullable=False`. The generic pipeline is dead weight.

Additionally, two confirmed dead code items in `PageClassifier` need cleanup.

## Phase 1: PageClassifier dead code (deletion)

**`src/services/extraction/page_classifier.py`:**
1. Delete `LLM_ASSISTED = "llm"  # Future` (line 16) — zero production usage, only a test asserts it exists
2. Remove the `if/else` in `classify()` (lines 98-102), replace with unconditional `result = self._classify_rule_based(url, title)` — both branches were identical, no caller passes non-RULE_BASED method

**`tests/test_page_classifier.py`:**
3. Delete the `LLM_ASSISTED` assertion (line 383)

## Phase 2: Harden schema fallback + eliminate generic pipeline branch

**`src/services/extraction/pipeline.py` (SchemaExtractionPipeline.extract_project):**
1. Strengthen the fallback check at line 566 to also catch schemas without valid `field_groups`:
   ```python
   # Before:
   if not schema:
   # After:
   if not schema or not isinstance(schema.get("field_groups"), list):
   ```
   This ensures legacy projects (e.g. "Test Project" with `{"type": "test"}`) get `DEFAULT_EXTRACTION_TEMPLATE` instead of a validation error. Verified in production DB: 1 of 4 projects has no `field_groups`.

**`src/services/extraction/worker.py`:**
2. Delete `_has_extraction_schema()` method (lines 105-119)
3. Remove `pipeline_service` constructor parameter, its type import, and `self.pipeline_service` assignment
4. In `process_job()`, remove the `if has_schema and self._llm:` / `else:` branch (lines 350, 367-424). Always call `_process_with_schema_pipeline()`. Add early guard: raise `ValueError("LLM config required")` if `self._llm` is None.
5. Remove `profile_name` extraction from payload (line 338) — only used by generic path

**`src/services/scraper/scheduler.py`:**
6. Remove imports: `ExtractionOrchestrator`, `ExtractionPipelineService`, `ProfileRepository`, `EntityExtractor` (+ `EntityRepository` if only used here)
7. Remove the construction block (lines 368-388): `LLMClient(...)`, `ExtractionOrchestrator(...)`, `EntityExtractor(...)`, `ExtractionPipelineService(...)`, `ProfileRepository(...)`. Note: `LLMClient` is also used by `src/api/v1/reports.py:60` — keep the import in `client.py` but remove from scheduler if no longer needed there.
8. Remove `pipeline_service=pipeline_service` from the `ExtractionWorker()` call

## Phase 3: Delete dead source files and methods

**Delete entire files (5):**

| File | Contents | Why dead |
|------|----------|----------|
| `src/services/extraction/extractor.py` | `ExtractionOrchestrator` | Only used by `ExtractionPipelineService` |
| `src/services/extraction/profiles.py` | `ProfileRepository` | Only used by generic pipeline |
| `src/services/extraction/backpressure.py` | `BackpressureManager` | Only used by `ExtractionPipelineService`; schema pipeline uses its own semaphore |
| `src/services/knowledge/extractor.py` | `EntityExtractor` | Only used by generic pipeline |
| `src/services/storage/deduplication.py` | `ExtractionDeduplicator` | Only used by `ExtractionPipelineService` via `ServiceContainer` |

**Edit source files (7):**

| File | Remove | Keep |
|------|--------|------|
| `src/services/extraction/pipeline.py` | `ExtractionPipelineService` class (lines 86-419), `PipelineResult`, `BatchPipelineResult`, `DEFAULT_PROFILE`, dead imports: `ExtractionOrchestrator`, `ExtractionDeduplicator`, `EntityExtractor`, `ProfileRepository`, `ExtractionProfile`, `BackpressureManager`, `get_alert_service` | `SchemaExtractionPipeline`, `SchemaPipelineResult`, `CheckpointCallback` |
| `src/services/extraction/embedding_pipeline.py` | `embed_facts()` method (lines 126-183) | `ExtractionEmbeddingService`, `embed_and_upsert()`, `extraction_to_text()`, `EmbeddingResult` |
| `src/services/extraction/schema_extractor.py` | `EXTRACTION_CONTENT_LIMIT` constant (line 27), `settings as _settings_singleton` from import (line 12) | `SchemaExtractor` class, `_singularize()` |
| `src/services/llm/client.py` | `extract_facts()`, `_extract_facts_via_queue()`, `_extract_facts_direct()`, `_parse_facts_from_result()`, `ExtractedFact` import | `LLMClient` class, `complete()` and helpers (used by reports) |
| `src/services/llm/worker.py` | `_extract_facts()` method, `"extract_facts"` branch in `_process_request()`, `EXTRACTION_CONTENT_LIMIT` import | Queue processing, `_process_extract_field_group`, adaptive concurrency |
| `src/services/scraper/service_container.py` | `ExtractionDeduplicator` import (line 22), `self._deduplicator` field (line 39), instantiation (lines 81-84), `deduplicator` property (lines 163-165) | All other services and properties |
| `src/models.py` | `ExtractedFact` dataclass (~line 258), `ExtractionResult` dataclass (~line 269), `ExtractionProfile` dataclass (~line 279) | All other models |

**NOT deleting (confirmed live):**
- `Profile` ORM model in `orm_models.py` — DB table exists, needs migration to drop
- `LLMClient` class — `complete()` used by report generation (`api/v1/reports.py:60`)
- `LLMWorker` class — processes `extract_field_group` requests from schema pipeline
- `QueueFullError` exception — raised by `llm/queue.py`, caught by `LLMClient.complete()`
- `DocumentChunk` model — used by `chunking.py` on the schema path

## Phase 4: Delete dead test files and clean up affected tests

**Delete entire test files (8):**
- `tests/test_extraction_pipeline.py` — tests `ExtractionPipelineService` exclusively
- `tests/test_pipeline_batch_errors.py` — tests `ExtractionPipelineService` batch errors
- `tests/test_parallel_extraction.py` — tests parallel path through `ExtractionPipelineService`
- `tests/test_extractor.py` — tests `ExtractionOrchestrator` exclusively
- `tests/test_profile_repository.py` — tests `ProfileRepository` exclusively
- `tests/test_extraction_deduplicator.py` — tests `ExtractionDeduplicator` exclusively
- `tests/test_entity_extractor.py` — tests `EntityExtractor` exclusively
- `tests/test_entity_extractor_refactor.py` — tests `EntityExtractor` exclusively
- `tests/test_backpressure.py` — tests `BackpressureManager` exclusively

**Edit test files (remove dead references):**

| Test file | What to remove |
|-----------|---------------|
| `tests/test_page_classifier.py` | `LLM_ASSISTED` assertion (line 383) |
| `tests/test_service_container.py` | `ExtractionDeduplicator` patch (line 27), `deduplicator` assertion (line 90) |
| `tests/test_scheduler_llm_worker.py` | `ExtractionDeduplicator` patch in 6 test functions (lines 68, 101, 132, 162, 196, 267) |
| `tests/test_scheduler_startup.py` | `container.deduplicator = MagicMock()` (lines 29, 102) |
| `tests/test_scheduler_recovery.py` | `container.deduplicator = MagicMock()` (line 23) |
| `tests/test_extraction_checkpointing.py` | Mock of `ExtractionPipelineService` (line 24), any test for the generic path |
| `tests/test_extraction_worker.py` | Mock of `ExtractionPipelineService` (line 23), tests for else-branch/generic path |
| `tests/test_llm_client.py` | `extract_facts` test methods (lines 59-221), `ExtractedFact` import (line 9) |
| `tests/test_llm_client_queue.py` | `extract_facts` test cases (lines 63, 96, 107, 132, 167, 194, 224, 502) |
| `tests/test_llm_worker_prompts.py` | `extract_facts` test cases |
| `tests/test_llm_worker_dlq.py` | `request_type="extract_facts"` test cases (lines 66, 100, 194, 229) |
| `tests/test_llm_worker_concurrency.py` | `request_type="extract_facts"` test case (line 79) |
| `tests/test_llm_queue.py` | `request_type="extract_facts"` test case (line 65) |
| `tests/test_llm_queue_pubsub.py` | `request_type="extract_facts"` test case (line 233) |
| `tests/test_extraction_embedding_service.py` | `embed_facts` test class (lines 163-270) |
| `tests/test_schema_extractor.py` | `EXTRACTION_CONTENT_LIMIT` import (line 9), tests at lines 216-221 |

## Verification

1. `ruff check .` — zero import errors or undefined names
2. `pytest` — all remaining tests pass, no collection errors
3. `git diff --stat` — confirm net deletion (expect -1500+ lines)
4. `grep -r "ExtractionPipelineService\|ExtractionOrchestrator\|ProfileRepository\|EntityExtractor\|ExtractionDeduplicator\|extract_facts\|embed_facts\|DEFAULT_PROFILE\|EXTRACTION_CONTENT_LIMIT\|LLM_ASSISTED\|BackpressureManager" src/` — returns zero hits
