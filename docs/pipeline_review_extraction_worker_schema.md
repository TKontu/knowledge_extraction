# Pipeline Review: Extraction Worker Schema Selection

## Flow
```
POST /projects/{id}/extract → ExtractRequest validation → Job created
    ↓
JobScheduler picks up job → ExtractionWorker.process_job()
    ↓
_has_extraction_schema() check
    ├── YES + settings → _process_with_schema_pipeline() → SchemaExtractionPipeline.extract_project()
    └── NO → ExtractionPipelineService.process_batch/process_project_pending()
```

## Critical (must fix)

- [x] **worker.py:114,131-137 - `source_ids` parameter is completely ignored for schema extraction**

  ✅ **FIXED**: Added `source_ids` parameter to `SchemaExtractionPipeline.extract_project()` (pipeline.py:574).
  Worker now passes `source_ids` to the pipeline (worker.py:135).

- [x] **pipeline.py:488-552 - Schema extraction doesn't update source status to "extracted"**

  ✅ **FIXED**: Added `source.status = "extracted"` in `extract_with_limit()` after successful extraction (pipeline.py:662).

## Important (should fix)

- [x] **extraction.py:94-99 - API `source_count` is wrong when `force=True`**

  ✅ **FIXED**: API now counts sources with correct status filters based on `force` flag (extraction.py:95-111).
  When `force=True`, counts `["ready", "pending", "extracted"]` statuses.

- [x] **worker.py:147-152 - Schema extraction always reports `sources_failed=0`**

  ✅ **FIXED**:
  - Pipeline tracks failures in `extract_with_limit()` (pipeline.py:679)
  - Returns `sources_failed` count in result dict (pipeline.py:686)
  - Worker reads `sources_failed` from result (worker.py:150)

## Minor

- [ ] **worker.py:219-224 - Warning log for fallback could be more descriptive**

  When schema exists but settings not provided, the warning could include what capabilities are lost.

- [ ] **pipeline.py:649 - Hardcoded semaphore value**

  Schema extraction uses `asyncio.Semaphore(10)` - should be configurable like the generic pipeline's `EXTRACTION_MAX_CONCURRENT_CHUNKS` setting.

## Summary

All critical and important issues have been fixed. Minor issues remain as low priority.

### Changes Made

1. **src/services/extraction/pipeline.py**
   - Added `source_ids: list[UUID] | None` parameter to `extract_project()`
   - Added filtering logic to process only specified sources when `source_ids` provided
   - Added `source.status = "extracted"` update on successful extraction
   - Added failure tracking and `sources_failed` in return dict

2. **src/services/extraction/worker.py**
   - Pass `source_ids` to `extract_project()`
   - Read `sources_failed` from pipeline result instead of hardcoding 0

3. **src/api/v1/extraction.py**
   - Fixed `source_count` calculation to account for `force=True` flag
   - Counts sources with `["ready", "pending"]` or `["ready", "pending", "extracted"]` based on force
