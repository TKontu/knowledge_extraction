# Handoff: v1.2.2 - Critical Bug Fix & Error Handling

**Session Date**: 2026-01-21
**Branch**: `feat/improve-error-handling` (merged to main)
**Previous**: v1.2.0 - LLM Queue Integration (deployed)

## Completed

### 1. Critical Bug Fix (C1) - Crawl Failures
- ✅ **Root Cause**: `meta_data` AttributeError in `source.py:270`
  - ON CONFLICT upsert referenced `stmt.excluded.meta_data` (doesn't exist)
  - Database column is named `metadata`, not `meta_data`
  - Caused 100% crawl job failures with cryptic error message
- ✅ **Fix**: Use SQLAlchemy Column objects in upsert statement
  - Changed to: `Source.meta_data: stmt.excluded.metadata`
  - Properly maps Python attribute to database column name
- ✅ **Impact**: Resolved crawl job failures (e.g., Job 1e0335de: 48 pages crawled, 0 sources stored)

### 2. Error Handling Improvements (I1)
- ✅ **Problem**: Error messages lacked type information
  - Job errors stored as `str(e)` - lost exception class name
  - Made debugging difficult (e.g., "meta_data" vs "AttributeError: meta_data")
- ✅ **Fix**: Enhanced error formatting in all workers
  - Format: `f"{type(e).__name__}: {str(e)}"` in `job.error`
  - Added `error_type` field to structured logs
  - Added `exc_info=True` for full stack traces
- ✅ **Files Modified**:
  - `src/services/scraper/crawl_worker.py`
  - `src/services/scraper/worker.py`
  - `src/services/extraction/worker.py`
- ✅ **TDD**: 11 tests in `tests/test_worker_error_handling.py` (all passing)

### 3. Build & Deployment
- ✅ **Docker Images Built & Pushed**:
  ```
  ghcr.io/tkontu/pipeline:v1.2.2
  ghcr.io/tkontu/camoufox:v1.2.2
  ghcr.io/tkontu/firecrawl-api:v1.2.2
  ghcr.io/tkontu/proxy-adapter:v1.2.2
  ```
- ✅ **Build Script**: Created `build-and-push.sh` for automated releases
- ✅ **Authentication**: Configured GHCR with `write:packages` token

## Previous Release: v1.2.0 (2026-01-20)
- ✅ Redis streams-based LLM request queue
- ✅ Adaptive concurrency worker with DLQ support
- ✅ Backpressure monitoring and signaling
- ✅ Fixed 2 critical bugs (C0: queue return type, C1: async Redis)
- ✅ 80+ comprehensive tests
- ✅ Feature flag: `llm_queue_enabled` (default: False)

## Next Steps

### Immediate (Deploy v1.2.2)
- [ ] Deploy to production:
  ```bash
  export PIPELINE_TAG=v1.2.2
  export CAMOUFOX_TAG=v1.2.2
  export FIRECRAWL_TAG=v1.2.2
  docker compose -f docker-compose.prod.yml pull
  docker compose -f docker-compose.prod.yml up -d
  ```

### Enable LLM Queue (Optional)
- [ ] Set `llm_queue_enabled=True` in `.env` or `config.py`
- [ ] Monitor Redis stream: `redis-cli XLEN llm:requests`
- [ ] Check DLQ if issues: `redis-cli LLEN llm:dlq`

### Cleanup
- [ ] Delete debug files: `rm *_cmd.txt test_qwen_llm.py`
- [ ] Add to `.gitignore`: `*_cmd.txt`
- [ ] Review and archive: `PIPELINE_REVIEW_2026-01-20.md`, `docs/PLAN-redis-llm-queue.md`

## Key Files

### LLM Queue System
- `src/services/llm/queue.py` - LLMRequestQueue with backpressure (returns dict now!)
- `src/services/llm/worker.py` - LLMWorker with adaptive concurrency and DLQ
- `src/services/llm/models.py` - LLMRequest/LLMResponse data models
- `src/services/llm/client.py` - LLMClient with queue mode support

### Integration Points
- `src/services/extraction/pipeline.py` - Backpressure-aware batch processing
- `src/services/extraction/schema_extractor.py` - Queue-based extraction
- `src/services/scraper/scheduler.py` - LLMWorker lifecycle management
- `src/api/v1/extraction.py` - `/extract-schema` endpoint (uses async Redis)

### Tests
- `tests/test_llm_queue.py` - Queue operations (29 tests)
- `tests/test_llm_worker_*.py` - Worker concurrency, DLQ, prompts
- `tests/test_extract_schema_async_redis.py` - Async Redis verification
- `tests/test_extraction_pipeline.py` - Pipeline integration

### Build & Deploy
- `build-and-push.sh` - Build script for all 4 images
- `Dockerfile` - Updated cache buster for v1.2.0
- `docker-compose.prod.yml` - Production compose file

## Context

### Architecture Decisions
1. **Redis Streams** chosen for queue (vs RabbitMQ) - simpler, already in stack
2. **Consumer Groups** for distributed processing with multiple workers
3. **Adaptive Concurrency** scales based on timeout rate (backs off on errors)
4. **DLQ Implementation** stores failed requests after max retries (3)
5. **Backpressure as Dict** - changed from string to `{"should_wait": bool, ...}` format

### Important Notes
- ✅ All 80+ tests passing
- ✅ Feature flag prevents breaking changes when disabled
- ✅ LLM model switched from Gemma to Qwen3-30B-A3B-Instruct-4bit (previous commit)
- ✅ Build script handles multi-platform (linux/amd64) correctly
- 🔄 `llm_queue_enabled` defaults to `False` - safe to deploy

### Blockers Resolved
- ❌ ~~Backpressure type mismatch~~ → ✅ Fixed
- ❌ ~~Sync Redis in async endpoint~~ → ✅ Fixed
- ❌ ~~Missing error handling~~ → ✅ Added QueueFullError/RequestTimeoutError handling

### Performance Notes
- Queue depth threshold: 500 (configurable)
- Backpressure triggers at 80% (400 requests)
- Worker concurrency: 10 initial, 5-50 range, adaptive
- Request timeout: 300s (5 minutes)
- DLQ retention: indefinite (manual cleanup needed)

---

**Ready for deployment!** Run `/clear` to start fresh for next session.
