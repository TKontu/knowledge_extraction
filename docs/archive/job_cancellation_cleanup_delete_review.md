# Pipeline Review: Job Cancellation, Cleanup, and Delete Endpoints

## Flow

```
POST /jobs/{job_id}/cancel
  → jobs.py:cancel_job
  → JobRepository.request_cancellation
  → db.commit()

POST /jobs/{job_id}/cleanup
  → jobs.py:cleanup_job
  → JobRepository.get
  → JobCleanupService.cleanup_job_artifacts
    → QdrantRepository.delete_batch
    → db.execute(delete Source)
    → DLQService.remove_by_job_id
  → JobRepository.delete (optional)
  → db.commit()

DELETE /jobs/{job_id}
  → jobs.py:delete_job
  → JobRepository.get
  → JobCleanupService.cleanup_job_artifacts (optional)
  → JobRepository.delete
  → db.commit()

Workers (ScraperWorker, CrawlWorker, ExtractionWorker)
  → JobRepository.is_cancellation_requested (at checkpoints)
  → JobRepository.mark_cancelled
```

---

## Critical (must fix)

- [x] **src/services/storage/qdrant/repository.py:238** - `delete_batch` calls `self.client.delete()` synchronously without `run_in_executor`, blocking the event loop. **FIXED: Added run_in_executor**

- [x] **src/ke_mcp/tools/acquisition.py:219,261,298** - MCP tools call `client.post()` and `client.delete()` but `KnowledgeExtractionClient` doesn't have these methods. **FIXED: Added cancel_job(), cleanup_job(), delete_job_record() to client.py and updated MCP tools**

---

## Important (should fix)

- [x] **src/services/extraction/pipeline.py:374-379** - When `chunk_size` is not specified, no cancellation check. **FIXED: Added cancellation check before asyncio.gather**

---

## Minor

- [x] **src/services/scraper/crawl_worker.py:54-58** - Early cancellation leaves result NULL. **FIXED: Added job.result on both cancellation paths**

- [ ] **src/services/job/cleanup_service.py:140** - `extractions_deleted` reports count found before deletion, not actual deletions. *(Not fixed - minor semantic issue, comment explains it)*

- [x] **src/api/v1/jobs.py:299** - Error message for `cancelling` status. **FIXED: More specific message**

---

## Notes

- FK cascade (`ON DELETE CASCADE`) for extractions/entities cleanup via source deletion is correctly leveraged.
- Qdrant delete is idempotent - no errors if points don't exist.
- The `cancelled` status works correctly as a free-form text field.
