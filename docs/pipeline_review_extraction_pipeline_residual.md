# Pipeline Review: Extraction Pipeline - Residual Issues (Verified)

## Flow
```
MCP extract_knowledge() → client.create_extraction() → POST /projects/{id}/extract
    ↓
API creates Job with payload → JobScheduler picks up job
    ↓
ExtractionWorker.process_job()
    ↓
_has_extraction_schema() check
    ├── YES + settings → _process_with_schema_pipeline() → SchemaExtractionPipeline.extract_project()
    └── NO → ExtractionPipelineService.process_batch/process_project_pending()
```

---

## Verified Findings

### Critical (must fix)

- [x] **worker.py:213-217 - Schema extraction lacks cancellation support** ✅ FIXED

  **Fix applied:**
  - Added `cancellation_check` parameter to `SchemaExtractionPipeline.extract_project()` (pipeline.py:560)
  - Added chunked processing with cancellation checks between batches (pipeline.py:700-714)
  - Added `cancellation_check` parameter to `_process_with_schema_pipeline()` (worker.py:117)
  - Worker now creates single `check_cancellation` callback used by both extraction paths (worker.py:206-207)
  - Schema path passes callback to pipeline (worker.py:224)

- [x] **extraction.py:259-334 - Duplicate `/extract-schema` endpoint with inconsistent behavior** ✅ FIXED

  **Fix applied:**
  - Marked endpoint as deprecated in FastAPI decorator (`deprecated=True`) (extraction.py:259-262)
  - Added deprecation docstring with migration guidance (extraction.py:265-280)
  - Added deprecation warning log when endpoint is called (extraction.py:295-300)
  - Added `_deprecated` field to response (extraction.py:346-348)

---

### False Positives (not issues)

- ~~**Schema detection checks only `field_groups`**~~ ❌ FALSE POSITIVE

  **Verification:** The `SchemaAdapter.validate_extraction_schema()` at schema_adapter.py:63-65 ALSO requires `field_groups`:
  ```python
  if "field_groups" not in schema:
      errors.append("Schema must have 'field_groups' field")
      return ValidationResult(is_valid=False, ...)
  ```

  Both the worker detection (line 95) and the validator are CONSISTENT - both require `field_groups`.

- ~~**ORM status update in async gather risks race condition**~~ ❌ FALSE POSITIVE

  **Verification:** The code pattern is safe because:
  1. Source objects are loaded BEFORE `asyncio.gather` (line 636: `sources = query.all()`)
  2. Each coroutine only modifies its OWN source object's `status` attribute
  3. Attribute assignment is just Python memory operation (no DB round-trip)
  4. Single `db.commit()` at line 681 flushes all changes atomically

  SQLAlchemy ORM objects can be safely modified from multiple coroutines in the same event loop when no DB queries are interleaved.

- ~~**worker.py:203 - Fragile null check on project**~~ ❌ FALSE POSITIVE

  **Verification:** Tracing `_has_extraction_schema()` (lines 89-97):
  ```python
  if not project:
      return False, None  # has_schema=False when project is None
  if schema and ... and schema.get("field_groups"):
      return True, project  # has_schema=True ONLY when project exists
  return False, project
  ```

  If `has_schema` is True, `project` is GUARANTEED to be not None. The guard at line 201 (`if has_schema and self.settings`) ensures safe access.

---

### Minor (low priority)

- [ ] **pipeline.py:673-674 - asyncio.gather without return_exceptions** ⚠️ EDGE CASE

  **Analysis:** The `extract_with_limit()` function has `except Exception` which catches most errors. However, `asyncio.CancelledError` (in Python 3.8+) inherits from `BaseException`, not `Exception`, so it would NOT be caught and would propagate to the gather.

  **Practical impact:** Low. Since schema extraction now supports cancellation, this could matter if a task is cancelled mid-execution. Consider adding `return_exceptions=True` for robustness.

- [ ] **pipeline.py:688 - Hardcoded semaphore value (10) and chunk_size (20)**

  Generic pipeline uses configurable `EXTRACTION_MAX_CONCURRENT_CHUNKS`. Schema pipeline hardcodes values. Minor inconsistency.

- [ ] **extraction.py:97 - Import inside function**

  ```python
  from orm_models import Source  # Inside else block
  ```

  Works but inconsistent with module-level imports elsewhere. Minor style issue.

- [ ] **worker.py:213 - Inconsistent log field type**

  ```python
  source_count=len(source_ids) if source_ids else "all_pending"  # int or string
  ```

  Log field `source_count` can be int or string "all_pending". May cause issues with structured logging systems expecting consistent types.

---

## Summary

| Finding | Status | Severity |
|---------|--------|----------|
| Schema extraction lacks cancellation | ✅ FIXED | Critical |
| Duplicate `/extract-schema` endpoint | ✅ FIXED | Critical |
| Schema detection inconsistency | ❌ False positive | - |
| ORM race condition | ❌ False positive | - |
| Fragile null check | ❌ False positive | - |
| asyncio.gather exceptions | ⚠️ Open | Minor |
| Hardcoded semaphore/chunk | ⚠️ Open | Minor |
| Import inside function | ⚠️ Open | Minor |
| Inconsistent log type | ⚠️ Open | Minor |

**All critical issues fixed. 4 minor issues remain (low priority).**

---

## Changes Made

### pipeline.py
- Added `cancellation_check: Callable[[], Awaitable[bool]] | None = None` parameter to `extract_project()` (line 560)
- Added early cancellation check before processing starts (lines 651-665)
- Changed from single `asyncio.gather` to chunked processing with cancellation checks between batches (lines 700-714)
- Updated return to include `cancelled` flag when applicable (lines 726-728)

### worker.py
- Added `cancellation_check` parameter to `_process_with_schema_pipeline()` (line 117)
- Moved `check_cancellation` callback creation before the if/else branch so both paths can use it (lines 206-207)
- Pass `cancellation_check` to schema pipeline (line 224)
- Removed duplicate callback definition from generic path

### extraction.py
- Added `deprecated=True` to `/extract-schema` route decorator (line 259)
- Added deprecation docstring with migration guidance (lines 265-280)
- Added deprecation warning log (lines 295-300)
- Added `_deprecated` field to response (lines 346-348)
