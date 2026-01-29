# Pipeline Review: Job Cancellation Fixes (Second Pass)

## Flow

```
Qdrant delete_batch → run_in_executor → client.delete
MCP tools → client.cancel_job/cleanup_job/delete_job_record → _request → API
Pipeline cancellation → cancellation_check callback → break/skip
CrawlWorker early cancel → mark_cancelled + set job.result
```

---

## Critical (must fix)

*None identified* - Previous critical issues have been fixed correctly.

---

## Important (should fix)

- [ ] **src/services/extraction/pipeline.py:391-395** - Index mismatch when cancellation breaks early. When chunked processing is cancelled at line 360 (`break`), `results = all_results` contains fewer items than `source_ids`. The loop at line 391 iterates `enumerate(results)` but indexes into `source_ids[i]`, which works. However, this means cancelled sources are silently omitted from results with no indication. Consider logging how many sources were skipped due to cancellation.

---

## Minor

- [ ] **src/ke_mcp/client.py:80-81** - 409 status mapped to generic "Resource already exists" message, but for cancel/cleanup it means "Cannot perform action in current state". The error detail from API response is lost. Consider passing through the actual error detail.

- [ ] **src/services/scraper/crawl_worker.py:57-60** - Early cancellation sets `job.result` but `mark_cancelled()` at line 56 already set `completed_at`. The order is correct but could be clearer if result was set before mark_cancelled for consistency with line 153-158 pattern.

- [ ] **src/services/job/cleanup_service.py:62-64** - Query loads all Source objects into memory just to get IDs. For jobs with many sources, this could be memory-intensive. Consider `select(Source.id)` instead.

---

## Verified Fixed

- [x] `delete_batch` now uses `run_in_executor` correctly
- [x] MCP tools use proper client methods (`cancel_job`, `cleanup_job`, `delete_job_record`)
- [x] Pipeline has cancellation check before non-chunked `asyncio.gather`
- [x] CrawlWorker sets `job.result` on both cancellation paths
- [x] Error message for 'cancelling' status is now specific

---

## Notes

- The implementation is functional and handles the main use cases correctly
- Error handling in MCP tools properly catches `APIError` and returns structured responses
- The `_request` method correctly handles DELETE with params for `delete_job_record`
