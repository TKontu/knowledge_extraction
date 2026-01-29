# Handoff: TODO Verification Complete

## Session Summary

Performed comprehensive verification of all TODO items. **Major discovery**: Many items marked "pending" were actually completed. Updated both TODO files with accurate status.

## Newly Discovered Completed Items

| Item | Location | Evidence |
|------|----------|----------|
| Embedding Recovery Service | `src/services/extraction/embedding_recovery.py` | Full service + API endpoint + tests |
| Embedding Recovery API | `POST /projects/{project_id}/extractions/recover` | Manual trigger endpoint |
| Embedding Recovery Tests | `tests/test_embedding_recovery.py` | 374 lines |
| LLM pub/sub | `src/services/llm/queue.py:176-256` | Redis pub/sub with fallback |
| Queue Mode Tests | `tests/test_llm_client_queue.py` | 497 lines |
| `update_embedding_ids_batch()` tests | `tests/test_extraction_repository_batch.py` | 108 lines |
| `_job_duration_by_type()` tests | `tests/test_metrics_job_duration.py` | 201 lines |
| SQLite fallback | `src/services/metrics/collector.py:145-148` | Uses `julianday()` |
| ExtractionContext "lint error" | `schema_orchestrator.py:22-27` | Valid pattern, not a bug |

## Updated TODO Summary

### TODO_architecture_database_consistency.md

| # | Item | Status |
|---|------|--------|
| 1 | No distributed transactions | **Partial** - recovery service exists (manual) |
| 2 | Async/sync mismatch | Pending |
| 3 | Inconsistent transaction boundaries | Pending |
| 4 | LLM polling → pub/sub | ✅ **DONE** |
| 5 | Database pool sizing | Pending (LOW) |
| 6 | Qdrant sync operations | Pending (LOW) |
| 7 | SQLite fallback | ✅ **DONE** |
| 8 | Unit tests for new methods | ✅ **DONE** |

### TODO_production_readiness.md

| # | Item | Status |
|---|------|--------|
| 1 | Schema update safety | Pending (HIGH) |
| 2 | Specific exception handling | Pending (MEDIUM) |
| 3 | ExtractionContext lint error | ✅ **NOT A BUG** |
| 4 | Queue mode test | ✅ **DONE** |
| 5-8 | Low priority items | Pending |

## Actual Remaining Work

### HIGH Priority
- [ ] **Schema update safety** - Block or require `force=true` when updating schema with existing extractions
- [ ] **Alerting for partial-failure states** - Notify when PostgreSQL succeeds but Qdrant fails

### MEDIUM Priority
- [ ] **Async/sync mismatch** - Choose: AsyncSession or remove async keywords
- [ ] **Specific exception handling** - Replace `except Exception:` in redis_client.py and qdrant_connection.py
- [ ] **Transaction boundary documentation** - Document and add savepoints

### LOW Priority
- [ ] Database pool sizing load test
- [ ] Qdrant async client evaluation
- [ ] Crawl pipeline improvements (see PLAN-crawl-improvements.md)
- [ ] JSONB validation
- [ ] LLM timeout monitoring

## Key File Reference

| File | Purpose |
|------|---------|
| `src/services/extraction/embedding_recovery.py` | Recovery service for orphaned extractions |
| `src/services/llm/queue.py:176-256` | Redis pub/sub implementation |
| `tests/test_llm_client_queue.py` | Queue mode tests (497 lines) |
| `tests/test_embedding_recovery.py` | Recovery service tests |
| `tests/test_extraction_repository_batch.py` | Batch update tests |
| `tests/test_metrics_job_duration.py` | Job duration metric tests |

## Note on Embedding Recovery

The embedding recovery service exists as a **manual trigger** via API, not an automatic background task. To recover orphaned extractions:

```bash
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/extractions/recover?max_batches=10" \
  -H "X-API-Key: your-api-key"
```

If automatic background recovery is needed, a scheduled task calling `EmbeddingRecoveryService.run_recovery()` would need to be added to the scheduler.
