# Pipeline Review: JSON Repair & Firecrawl Logging (VERIFIED & FIXED)

**Date:** 2026-01-27
**Scope:** Issue #2 (JSON repair) and Issue #12 (Firecrawl logging)

---

## Verified Findings (FIXED)

### ✅ FIXED: `try_repair_json` crashes on `None` input

**File:** `src/services/llm/json_repair.py:133-135`

**Problem:** `json.loads(None)` raised `TypeError`, not `JSONDecodeError`.

**Fix Applied:** Added `None` check at start of function:
```python
def try_repair_json(text: str | None, context: str = "") -> dict[str, Any]:
    # Handle None input - json.loads(None) raises TypeError, not JSONDecodeError
    if text is None:
        raise json.JSONDecodeError("Cannot parse None", "", 0)
```

**Test Added:** `test_none_input_raises_json_decode_error` in `tests/services/llm/test_json_repair.py`

---

### ✅ FIXED: Dead code in `_fix_unterminated_strings`

**File:** `src/services/llm/json_repair.py:208-211`

**Problem:** Unused `close_pos` and `end_text` variables.

**Fix Applied:** Removed dead code, simplified to:
```python
if in_string and last_string_start >= 0:
    # If string seems truncated (no closing quote found), add one
    result.append('"')
    return "".join(result)
```

---

## ❌ FALSE POSITIVES

### Return type `dict` but can be `list` - FALSE POSITIVE

**Reason:** All callers use `response_format={"type": "json_object"}` which guarantees OpenAI returns an object, not an array. The prompts also explicitly request object format. While the type annotation is technically imprecise, callers are safe.

---

### Duplicate logging - FALSE POSITIVE

**Reason:** The two logs serve different purposes:
1. `json_repair_failed` - repair module context (content_length, original_error)
2. `entity_extraction_json_parse_failed` - caller context (attempt, max_retries, model)

This is intentional layered logging, not duplication.

---

### Test import path may fail - FALSE POSITIVE

**Reason:** `pyproject.toml` line 20 specifies:
```toml
pythonpath = ["src"]
```
Pytest adds `src` to Python path, so `from services.llm.json_repair import ...` works correctly.

---

### Negative bracket depth not handled - FALSE POSITIVE (by design)

**Reason:** The function's purpose is to ADD missing closers, not REMOVE extra closers. If `brace_depth` goes negative (extra `}`), the function correctly does NOT add more closers. This is correct behavior.

---

### Truncated next_url in logs - FALSE POSITIVE

**Reason:** 100 characters is sufficient to see the base URL and pagination parameters. Full URLs can be extremely long. Truncation is reasonable for log readability.

---

### Apostrophe corruption in `_fix_quotes` - FALSE POSITIVE

**Reason:** The function has a guard condition at line 286:
```python
if "'" in text and '"' not in text:
    return text.replace("'", '"')
```
This only applies full replacement when text uses ONLY single quotes (no double quotes). Normal JSON with double quotes is never modified.

---

## Summary

| Finding | Verified Status | Resolution |
|---------|-----------------|------------|
| `None` input crashes | ✅ REAL | ✅ FIXED |
| Dead code (`close_pos`, `end_text`) | ✅ REAL | ✅ FIXED |
| Return type mismatch | ❌ FALSE POSITIVE | N/A |
| Duplicate logging | ❌ FALSE POSITIVE | N/A |
| Test import path | ❌ FALSE POSITIVE | N/A |
| Negative bracket depth | ❌ FALSE POSITIVE | N/A |
| Truncated next_url | ❌ FALSE POSITIVE | N/A |
| Apostrophe corruption | ❌ FALSE POSITIVE | N/A |

---

## Conclusion

**All verified issues have been fixed.**

1. **`None` handling** - Added explicit check that raises `JSONDecodeError` with descriptive message.

2. **Dead code** - Removed unused `close_pos` and `end_text` variables.

**Status:** Ready to deploy.
