# Handoff: Review Documents Updated

## Completed This Session

### Backlog Review (2026-01-25)

Reviewed all critical and important items - **most are resolved**:

**Critical (all fixed):**
| Issue | Status | Notes |
|-------|--------|-------|
| Queue worker `complete` handler | ✅ Fixed | `worker.py:344-347` now handles `request_type="complete"` |
| LLMClient connection leak | ✅ Fixed | `reports.py:59-69` uses context manager |
| `sources_referenced` in API | ✅ Design choice | Field intentionally not in API; sources in markdown content |
| LLM client unused | ✅ Fixed | Used via `ReportSynthesizer` at `service.py:56-59` |

**Important (verified):**
| Issue | Status | Notes |
|-------|--------|-------|
| LLM called for single fact | ✅ Fixed | `service.py:276-302` skips LLM for single facts |
| `_complete_direct` temperature | ✅ Fixed | `client.py:706` varies temp on retries |
| No source attribution | ✅ Fixed | `service.py:195-204` includes source_uri/title |
| Deprecated FIELD_GROUPS_BY_NAME | ✅ Resolved | Only in deprecated path; main uses SchemaTableGenerator |
| Chunking no second-pass | 🟠 Still real | `synthesis.py:127` just joins chunks |
| Lossy text aggregation | 🟠 Still real | `service.py:605` takes longest only |

Updated review documents:
- `docs/pipeline_review_llm_synthesis.md` - 5 of 10 fixed, 5 remaining (all minor)
- `docs/endpoint_reports_review.md` - All critical fixed, 1 important remaining

### Previous: Pipeline Review Fixes (Commit `0f4a3c6`)

Fixed 4 issues identified during pipeline review of SchemaTableGenerator:

1. **Duplicate title assignment** - Removed dead code (lines 131-133 in service.py)
2. **KeyError on malformed schema** - Added try/except with logging fallback
3. **Double ProjectRepository** - Pass `project_repo` to ReportService in API
4. **None shows as "None"** - Fixed explicit None handling in entity name formatting

### Previous: Template-Agnostic Table/Excel Generation (Commit `287af76`)

Refactored table report generation to derive columns and labels dynamically from project's `extraction_schema`, eliminating hardcoded drivetrain-specific code.

## Current State

**Main branch clean** - all changes committed and pushed.

```
37173a6 docs: Update handoff with pipeline review fixes
d7f5f2d chore: Update cache bust for fresh build
0f4a3c6 fix(reports): Address pipeline review findings
287af76 feat(reports): Template-agnostic table/Excel generation
```

**Docker images built and pushed:**
- ghcr.io/tkontu/camoufox:latest
- ghcr.io/tkontu/firecrawl-api:latest
- ghcr.io/tkontu/proxy-adapter:latest

## Test Status

- 40 report-related tests passing
- 25 new SchemaTableGenerator tests
- All linting clean

## Remaining Backlog

### Reports (Minor/Low Priority)

| Issue | Priority | Notes |
|-------|----------|-------|
| Chunking doesn't synthesize across chunks | Low | Second-pass to unify chunk results |
| Lossy text aggregation | Low | `max(values, key=len)` takes longest only |
| `_build_sources_section` dead code | Low | Remove unused method |
| Hardcoded `max_detail_extractions = 10` | Low | Make configurable |
| No test for `_complete_via_queue` | Low | Add queue mode test |
| Variable facts in system prompt | Low | Move to user prompt for cache efficiency |

### Crawl Pipeline (Not Started)

See `docs/PLAN-crawl-improvements.md` for full plan:

| Phase | Issues | Status |
|-------|--------|--------|
| Phase 1: Error Handling | I1 (error messages), M2 (UUID types) | Not started |
| Phase 2: Data Safety | I2 (batch commits) | Not started |
| Phase 3: Observability | I3 (HTTP filtering), M3 (metrics), M1 (created/updated) | Not started |

### Technical Debt (Low Priority)

| Issue | Notes |
|-------|-------|
| `SchemaTableReport` still exists | Can be removed (deprecated) |
| `FIELD_GROUPS_BY_NAME` still exists | Can be removed if SchemaTableReport removed |
| No LLM cost tracking | Add metrics later |

## Next Steps

- [ ] Test with real projects using different templates
- [ ] Consider removing SchemaTableReport entirely (deprecated)
- [ ] Clean up FIELD_GROUPS_BY_NAME if no longer needed
- [ ] Start Crawl Pipeline Phase 1 (error handling improvements)
