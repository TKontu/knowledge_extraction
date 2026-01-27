# Pipeline Review: `link_to_extraction()` Implementation Issues (VERIFIED)

**Date:** 2026-01-27
**Scope:** Issue #1 fix - making `link_to_extraction()` idempotent

## Flow
```
ExtractionPipelineService.process_source() [pipeline.py:147]
  └─ for fact in result.facts:  ← SEQUENTIAL loop
       └─ extraction = create() [pipeline.py:161]  ← NEW UUID each fact
            └─ EntityExtractor.extract() [pipeline.py:185]
                 └─ for entity in stored_entities:  ← SEQUENTIAL loop
                      └─ link_to_extraction() [extractor.py:172]
```

---

## Findings Verification

### ❌ FALSE POSITIVE: Race condition (check-then-insert)

**Original claim:** Two concurrent requests could both pass the "exists" check and both try to insert.

**Verification:** This cannot happen in the current architecture:

1. **Unique extraction_id per fact:** Each fact creates a NEW extraction with unique UUID (pipeline.py:161-169)
2. **Sequential processing within source:** Facts are processed in a `for` loop (pipeline.py:147), not concurrently
3. **Sequential entity linking:** Entities are linked in a `for` loop (extractor.py:171), not concurrently
4. **Parallel sources have different extraction_ids:** `process_batch` runs sources in parallel via `asyncio.gather`, but each source creates its OWN extractions

**The unique constraint is on (extraction_id, entity_id, role).** For a collision to occur, two operations must use the **same extraction_id**. This only happens on **retry**, which the check handles correctly.

**Duplicate entity from LLM scenario:** If LLM returns `[{type: "company", value: "Acme"}, {type: "company", value: "Acme"}]`:
- `get_or_create` returns same entity object for both
- First `link_to_extraction` creates link
- Second `link_to_extraction` finds existing link, returns `(link, False)`
- ✅ Handled

---

### ⚠️ PRE-EXISTING (not introduced by fix): Async/sync mismatch

**Status:** REAL but PRE-EXISTING architectural issue

**Evidence:**
```python
# database.py:14
engine = create_engine(...)  # Synchronous engine

# database.py:22
SessionLocal = sessionmaker(...)  # Synchronous sessionmaker

# entity.py:7
from sqlalchemy.orm import Session  # Synchronous Session
```

All repository methods are `async def` but call synchronous SQLAlchemy:
```python
async def link_to_extraction(...):  # async declaration
    existing = self._session.execute(...)  # SYNC - blocks event loop
```

**Impact:** Blocks event loop during DB operations. This affects the ENTIRE repository class (all methods), not just `link_to_extraction`.

**Verdict:** Real architectural issue but NOT introduced by this fix. Every repository method has this pattern.

---

### ❌ FALSE POSITIVE: No FK violation handling

**Original claim:** If extraction_id or entity_id doesn't exist, gets generic IntegrityError.

**Verification:** Looking at the call path:
- `extraction_id` comes from extraction just created at pipeline.py:161-169
- `entity_id` comes from `get_or_create` which returns an entity that exists

Both IDs are **guaranteed to exist** by the time `link_to_extraction` is called. FK violations cannot occur in normal operation.

---

### ⚠️ MINOR: ORM model missing UniqueConstraint declaration

**Status:** TRUE but cosmetic

The constraint exists in the migration (alembic:130):
```python
sa.UniqueConstraint("extraction_id", "entity_id", "role")
```

But not declared in orm_models.py. This is a documentation/sync issue that doesn't affect runtime behavior.

---

### ✅ TRUE (minor): Unused `link` variable

**File:** `src/services/knowledge/extractor.py:172`
```python
link, link_created = await self._entity_repo.link_to_extraction(...)
# 'link' is never used
```

Should use `_` convention: `_, link_created = ...`

---

### ✅ TRUE (minor): No logging when existing link returned

**File:** `src/services/knowledge/extractor.py:176-181`

Only logs on `link_created=True`. Could add trace-level logging for existing links for debugging retries.

---

## Summary

| Finding | Status | Severity |
|---------|--------|----------|
| Race condition | ❌ FALSE POSITIVE | N/A |
| Async/sync mismatch | ⚠️ PRE-EXISTING | Architectural |
| FK violation handling | ❌ FALSE POSITIVE | N/A |
| ORM UniqueConstraint | ⚠️ MINOR | Cosmetic |
| Unused `link` variable | ✅ TRUE | Minor |
| No existing-link logging | ✅ TRUE | Minor |

---

## Conclusion

**The `link_to_extraction()` fix is CORRECT and handles the Issue #1 scenario properly.**

The fix targets **retry scenarios** where a partially-completed extraction job restarts and tries to re-link entities. The check-then-insert pattern is sufficient because:
1. Each extraction has a unique UUID
2. Entity linking within an extraction is sequential
3. The only duplicate scenario is retry, which the check handles

No critical issues were found in the implementation.
