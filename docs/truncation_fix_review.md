# Pipeline Review: Truncation Handling Fix (Verified)

## Flow
```
schema_extractor.py:extract_field_group
  → _extract_direct (direct mode) OR _extract_via_queue → worker.py:_extract_field_group (queue mode)
  → try_repair_json
  → _apply_defaults
```

## Critical Issues

**None found** - All critical findings were false positives upon verification.

## Important Issues

**None found** - The implementation is correct.

## Minor Issues (Code Quality)

### 1. Existing tests don't explicitly mock `finish_reason`
- **File**: tests/test_schema_extractor.py:77-86, 103-112
- **Status**: FALSE POSITIVE (tests work correctly)
- **Explanation**: MagicMock auto-creates attributes. When `finish_reason` is accessed, it returns a MagicMock object. `MagicMock() == "length"` evaluates to `False`, so tests correctly take the non-truncation path.
- **Recommendation**: Consider adding explicit `finish_reason="stop"` to existing tests for clarity, but not required.

### 2. Worker doesn't have explicit else branch for truncated non-entity lists
- **File**: src/services/llm/worker.py:491-516
- **Status**: FALSE POSITIVE (not a bug)
- **Explanation**: For truncated non-entity lists, code falls through to line 516 which calls `try_repair_json()`. This is functionally identical to schema_extractor.py's explicit else branch. Only difference is the logging context string ("extract_field_group" vs "schema_extract_truncated"), which is cosmetic.

### 3. Worker uses `payload.get("field_group", {}).get("name", "unknown")`
- **File**: src/services/llm/worker.py:494
- **Status**: FALSE POSITIVE (defensive coding)
- **Explanation**: The payload is always built by `schema_extractor._extract_via_queue()` which always includes `field_group.name` from the FieldGroup object. The "unknown" fallback is never triggered in practice.

### 4. Broad exception catching
- **File**: src/services/extraction/schema_extractor.py:269, worker.py:508
- **Status**: MINOR ISSUE (code style)
- **Explanation**: `except Exception:` catches all exceptions but `try_repair_json` only raises `json.JSONDecodeError`. Not a bug, but using `except json.JSONDecodeError:` would be more precise and prevent masking unexpected errors in future code changes.

### 5. Test uses wrong output key
- **File**: tests/test_schema_extractor.py:108, 120
- **Status**: PRE-EXISTING ISSUE (unrelated to truncation fix)
- **Explanation**: `test_extract_product_list` mocks LLM returning `{"products": ...}` but the prompt instructs LLM to use `{"products_gearbox": ...}`. The test works because it just parses whatever the mock returns. The truncation tests correctly use `{"products_gearbox": ...}`.

## Summary

The truncation handling implementation is **correct**. All findings are either:
- False positives (code works as intended)
- Minor code style suggestions
- Pre-existing issues unrelated to this change

The fix successfully:
1. Detects truncation via `finish_reason == "length"`
2. Returns empty list for unrecoverable entity list truncation
3. Attempts repair for non-entity truncation
4. Adds prompt guidance to limit extraction size
