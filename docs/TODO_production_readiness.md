# TODO: Production Readiness

Review date: 2026-01-29 (verified)

## Summary

Codebase is **production-ready** with minor polish needed. This document tracks remaining items.

---

## High Priority (Before Production)

### 1. Schema Update Safety
- **File:** `src/api/v1/projects.py:168-175`
- **Issue:** Updating project schema with existing extractions proceeds with only a warning header
- **Risk:** Schema/data mismatches
- **Options:**
  - [ ] Block updates when extractions exist (require delete first)
  - [ ] Add `force=true` query param to confirm intent
  - [ ] Keep warning-only (document behavior)

---

## Medium Priority (Post-Launch)

### 2. Specific Exception Handling
Replace broad `except Exception:` with specific types.

| File | Line | Current | Recommended |
|------|------|---------|-------------|
| `src/redis_client.py` | 50-54 | `except Exception:` | `except (redis.ConnectionError, redis.TimeoutError):` |
| `src/qdrant_connection.py` | 33-38 | `except Exception:` | `except (QdrantException, ConnectionError):` |

### ~~3. Fix Lint Error~~ (NOT AN ISSUE)
- **File:** `src/services/extraction/schema_orchestrator.py`
- **Status:** ✅ NOT A BUG - Deliberate design pattern
- **Analysis:** Uses string annotation `"ExtractionContext | None"` with deferred import inside `__init__`. This is a valid pattern to avoid circular imports.

### ~~4. Add Queue Mode Test~~ (DONE)
- **File:** `tests/test_llm_client_queue.py`
- **Status:** ✅ COMPLETED (verified 2026-01-29)
- **Tests:** 497 lines of comprehensive tests including:
  - `TestLLMClientQueueMode` - 6 tests for fact extraction via queue
  - `TestLLMClientExtractEntities` - 7 tests for entity extraction via queue
  - `TestLLMClientConcurrency` - concurrent operations test

---

## Low Priority (Backlog)

### 5. Crawl Pipeline Improvements
See `docs/PLAN-crawl-improvements.md` for full details.

| Phase | Items | Status |
|-------|-------|--------|
| Phase 1 | Error messages (I1), UUID types (M2) | Not started |
| Phase 2 | Batch commits (I2) | Not started |
| Phase 3 | HTTP filtering (I3), metrics (M3), timestamps (M1) | Not started |

### 6. Database Pool Sizing
- **File:** `src/database.py:17`
- **Current:** `pool_size=5, max_overflow=10`
- **Action:** Verify sufficient for expected concurrency under load

### 7. JSONB Validation
- **Files:** ORM models with JSONB columns
- **Issue:** No JSONSchema validation for `data`, `payload`, `meta_data` fields
- **Action:** Add validation at repository layer or via Pydantic

### 8. LLM Timeout Review
- **File:** `src/config.py:81-84`
- **Current:** 120s default timeout
- **Action:** Monitor in production; may mask hanging requests

---

## Production Checklist

Before deploying to production:

- [ ] Set strong `API_KEY` (32+ chars, non-default)
- [ ] Enable `ENFORCE_HTTPS=true`
- [ ] Configure database with SSL (`sslmode=require`)
- [ ] Set up monitoring/alerting
- [ ] Enable Prometheus metrics collection
- [ ] Review rate limit settings for expected traffic
- [ ] Test graceful shutdown behavior
- [ ] Verify Redis and Qdrant connectivity

---

## Completed Items

- [x] Commit pending changes (reports improvements, sources endpoint) - merged via PR #74
- [x] Reports module improvements (synthesis, aggregation, configurable limits)
- [x] Deprecated code removal (SchemaTableReport, hardcoded field groups)
- [x] Security headers middleware
- [x] Rate limiting middleware
- [x] Structured logging setup
- [x] 50+ tests for report functionality
