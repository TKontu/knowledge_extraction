# Pipeline Review: `link_to_extraction()` Return Type Change

**Date:** 2026-01-27
**Scope:** Issue #1 fix - making `link_to_extraction()` idempotent

## Flow
```
API/Worker
  └─ ExtractionPipelineService.process_source() [pipeline.py:185]
       └─ EntityExtractor.extract() [extractor.py:134]
            └─ EntityRepository.link_to_extraction() [entity.py:194]
```

## Critical (must fix)

### ✅ FIXED: Test mocks not returning tuples

**Files:**
- `tests/test_entity_extractor.py:291, 340, 386, 418, 491`
- `tests/test_entity_extractor_refactor.py:43, 319`

**Problem:** Mocks were set up as `AsyncMock()` without `return_value`. The extractor now unpacks the result:
```python
link, link_created = await self._entity_repo.link_to_extraction(...)
```

**Fixed:** All mocks now return proper tuples:
```python
mock_link = MagicMock()
entity_repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))
```

---

## Important (should fix)

None found.

---

## Minor

### 🟡 Unused `link` variable in extractor

**File:** `src/services/knowledge/extractor.py:172`

The `link` object from unpacking is never used (only `link_created` is checked). This is fine but could use `_` convention:

```python
# Current
link, link_created = await self._entity_repo.link_to_extraction(...)

# Suggested (optional)
_, link_created = await self._entity_repo.link_to_extraction(...)
```

---

## Verified Correct

| Component | Status | Notes |
|-----------|--------|-------|
| `entity.py:link_to_extraction()` | ✅ | Returns `tuple[ExtractionEntity, bool]` on both paths |
| `extractor.py:extract()` | ✅ | Properly unpacks tuple |
| `test_entity_repository.py` | ✅ | All 9 tests properly handle tuple |
| Transaction handling | ✅ | Uses `flush()`, caller manages commit |
| Idempotency logic | ✅ | Prevents UniqueViolation on retry |

---

## Summary

**Production code:** ✅ Correct - no issues
**Test code:** ✅ All 7 mock setups updated to return tuples

The fix is properly implemented and will prevent the 32+ failed jobs caused by duplicate entity link violations.
