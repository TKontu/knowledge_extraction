# TODO: Production Readiness

Review date: 2026-01-31 (updated)

## Summary

Codebase is **production-ready**. This document tracks remaining polish items.

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

### ~~2. Specific Exception Handling~~ (DONE)
- **Status:** Completed 2026-01-31
- **Files:** `src/redis_client.py`, `src/qdrant_connection.py`
- Broad `except Exception:` patterns replaced with specific exception types

---

## Low Priority (Backlog)

### 3. Crawl Pipeline Improvements
See `docs/PLAN-crawl-improvements.md` for full details.

| Phase | Items | Status |
|-------|-------|--------|
| Phase 1 | Error messages (I1), UUID types (M2) | Not started |
| Phase 2 | Batch commits (I2) | Not started |
| Phase 3 | HTTP filtering (I3), metrics (M3), timestamps (M1) | Not started |

### 4. Database Pool Sizing
- **File:** `src/database.py:17`
- **Current:** `pool_size=5, max_overflow=10`
- **Action:** Verify sufficient for expected concurrency under load

### 5. JSONB Validation
- **Files:** ORM models with JSONB columns
- **Issue:** No JSONSchema validation for `data`, `payload`, `meta_data` fields
- **Action:** Add validation at repository layer or via Pydantic

### 6. LLM Timeout Review
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

- [x] Extraction pipeline optimization (page classification) - completed 2026-01-30
- [x] Crawl/Camoufox pipeline fixes (pagination, cleanup, AJAX limits) - completed 2026-01-29
- [x] Exception handling cleanup - completed 2026-01-31
- [x] Commit pending changes (reports improvements, sources endpoint) - merged via PR #74
- [x] Reports module improvements (synthesis, aggregation, configurable limits)
- [x] Deprecated code removal (SchemaTableReport, hardcoded field groups)
- [x] Security headers middleware
- [x] Rate limiting middleware
- [x] Structured logging setup
- [x] 50+ tests for report functionality
- [x] Queue mode tests for LLM client (497 lines)
- [x] Exception hierarchy (AppError → TransientError/PermanentError, FastAPI handler, 40+ tests) - 2026-03-02
- [x] Fix dual import paths (standardize `from src.X` → `from X`) - 2026-03-02
- [x] Enable Phase 1A extraction reliability features (chunk overlap, source quoting, conflict detection, schema validation, confidence gating) - 2026-03-02
- [x] Domain boilerplate dedup (Phases A-E, section-aware two-pass) - 2026-02-27
- [x] ServiceContainer + Scheduler startup resilience (stale cleanup, worker stagger, service lifecycle extraction) - 2026-03-03
- [x] Extraction pipeline fixes — all 5 phases (merge strategy defaults, config hardening, chunking quality, schema searchability, minor cleanup) - 2026-03-03
- [x] Pipeline decomposition (embedding_pipeline, backpressure, content_selector extracted from pipeline.py) - 2026-03-03
- [x] Group configuration facades (10 frozen dataclasses + @property accessors on Settings for grouped access) - 2026-03-03
- [x] Typed config facade migration — all 7 phases (services accept typed facades, global settings use facade access) - 2026-03-03
- [x] Fix 32 verified extraction pipeline design issues — 6 phases (data correctness, config DI completion, reliability, LLM/chunking, polish). Full review: `docs/review_extraction_pipeline_design.md` - 2026-03-04
