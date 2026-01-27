# Handoff: Extraction Pipeline Reliability Fixes

## Completed

- **Issue #1 (CRITICAL)**: Made `link_to_extraction()` idempotent - checks for existing link before INSERT to prevent `UniqueViolation` errors on retry
- **Issue #2 (HIGH)**: Implemented JSON repair utility for malformed LLM responses
  - Created `src/services/llm/json_repair.py` with 8 repair strategies
  - Integrated into `worker.py`, `client.py`, `schema_extractor.py`
  - Created comprehensive test suite
- **Issue #12**: Added verbose Firecrawl logging to diagnose 0-page crawl issues
- **Pipeline review**: Verified all findings - fixed 2 real issues, identified 6 false positives
- **Post-review fixes**:
  - Added `None` input handling to `try_repair_json()` (was raising `TypeError` instead of `JSONDecodeError`)
  - Removed dead code (`close_pos`, `end_text`) from `_fix_unterminated_strings()`

## In Progress

- Server was composed down when session started - user may need to restart services

## Next Steps

- [ ] Run `pytest tests/services/llm/test_json_repair.py -v` to verify JSON repair tests pass
- [ ] Run `pytest tests/test_entity_repository.py -v` to verify idempotency tests pass
- [ ] Consider embedding batching for performance (Issue #4 from TODO)
- [ ] Keep container logs persistent for debugging
- [ ] Observe next crawl to diagnose Issue #12 root cause (now has verbose logging)

## Key Files

- `src/services/llm/json_repair.py` - JSON repair utility (new)
- `src/services/storage/repositories/entity.py:194-218` - Idempotent `link_to_extraction()`
- `src/services/scraper/client.py:368-423` - Firecrawl verbose logging
- `tests/services/llm/test_json_repair.py` - JSON repair test suite (new)
- `docs/pipeline_review_json_repair_firecrawl.md` - Pipeline review (verified & fixed)
- `docs/TODO_extraction_reliability.md` - Master issue tracker (12 issues)

## Context

### Architecture
- `link_to_extraction()` now returns `tuple[ExtractionEntity, bool]` instead of just `ExtractionEntity`
- All callers updated to handle tuple return: `link, created = await repo.link_to_extraction(...)`
- `try_repair_json()` signature changed to accept `str | None` for defensive handling

### Pipeline Review Summary
| Finding | Status |
|---------|--------|
| `None` input crashes `try_repair_json` | FIXED |
| Dead code in `_fix_unterminated_strings` | FIXED |
| 6 other findings | FALSE POSITIVES |

### Remaining TODO Items (from docs/TODO_extraction_reliability.md)
- Issue #4: Embedding batching for performance
- Other lower-priority items documented in TODO file
