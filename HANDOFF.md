# Handoff: Deprecated Code Cleanup Complete

## Completed This Session

### Deprecated Code Removal (2026-01-25)

Removed deprecated `SchemaTableReport` class and hardcoded field group constants:

| Removed | Notes |
|---------|-------|
| `src/services/reports/schema_table.py` | Entire file deleted - replaced by `SchemaTableGenerator` |
| `tests/test_schema_table_report.py` | Tests for deprecated class |
| `FIELD_GROUPS_BY_NAME` constant | Hardcoded drivetrain-specific groups |
| `ALL_FIELD_GROUPS` constant | List of hardcoded groups |
| `MANUFACTURING_GROUP`, etc. | 7 hardcoded `*_GROUP` constants |

**Kept:** `FieldDefinition` and `FieldGroup` dataclasses in `field_groups.py` - used by `SchemaAdapter`.

Also fixed:
- Test in `test_schema_adapter.py` expecting error for entity_list without ID field (implementation returns warning)
- Updated `test_schema_extractor*.py` to use inline test fixtures instead of importing deprecated constants

### Previous: Backlog Review (2026-01-25)

**Critical (all fixed):**
| Issue | Status | Notes |
|-------|--------|-------|
| Queue worker `complete` handler | Fixed | `worker.py:344-347` handles `request_type="complete"` |
| LLMClient connection leak | Fixed | `reports.py:59-69` uses context manager |
| `sources_referenced` in API | Design choice | Field intentionally not in API |
| LLM client unused | Fixed | Used via `ReportSynthesizer` |

**Important (verified):**
| Issue | Status | Notes |
|-------|--------|-------|
| LLM called for single fact | Fixed | `service.py:276-302` skips LLM |
| `_complete_direct` temperature | Fixed | `client.py:706` varies temp |
| No source attribution | Fixed | `service.py:195-204` includes source |
| Deprecated FIELD_GROUPS_BY_NAME | **Removed** | Deleted this session |

## Current State

**Main branch clean** - all changes committed.

```
ccd689d refactor: Remove deprecated SchemaTableReport and hardcoded field groups
fad2ad4 docs: Correct important issue status after verification
4de5ebd docs: Update review documents - mark critical items as fixed
37173a6 docs: Update handoff with pipeline review fixes
0f4a3c6 fix(reports): Address pipeline review findings
```

## Test Status

- 58 schema-related tests passing
- 25 SchemaTableGenerator tests
- All linting clean
- Removed 605 lines of deprecated code

## Remaining Backlog

### Reports (Minor/Low Priority)

| Issue | Priority | Notes |
|-------|----------|-------|
| Chunking doesn't synthesize across chunks | Low | Second-pass to unify chunk results |
| Lossy text aggregation | Low | `max(values, key=len)` takes longest only |
| `_build_sources_section` dead code | Low | Remove unused method |
| Hardcoded `max_detail_extractions = 10` | Low | Make configurable |
| No test for `_complete_via_queue` | Low | Add queue mode test |

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
| No LLM cost tracking | Add metrics later |

## Next Steps

- [ ] Test with real projects using different templates
- [ ] Start Crawl Pipeline Phase 1 (error handling improvements)
- [ ] Consider adding LLM cost tracking metrics
