# TODO: JSON Repair for LLM Responses

**Status:** ✅ IMPLEMENTED (2026-01-27)
**Priority:** High
**Estimated Scope:** ~200 lines of code + tests

## Context

The knowledge extraction system uses LLMs (local Qwen3, OpenAI) to extract structured data from web content. LLM responses must be valid JSON, but models sometimes produce malformed output due to:

- **Truncation:** Output hits `max_tokens` limit mid-string
- **Syntax errors:** Missing closing quotes, braces, or brackets
- **Escape issues:** Unescaped newlines or special characters in strings

Current behavior: `json.loads()` fails → retry with same parameters → same failure → exhausts retries → extraction fails permanently.

**Example failure log:**
```
warning model=Qwen3-30B-A3B-Instruct-4bit error=Unterminated string starting at: line 22 column 15 (char 977) attempt=5 max_retries=5 event=llm_extraction_attempt_failed
ERR model=Qwen3-30B-A3B-Instruct-4bit error=Unterminated string starting at: line 22 column 15 (char 977) attempts=5 event=llm_extraction_failed_all_retries
```

## Objective

Add JSON repair capability to recover from malformed LLM responses before triggering retries, reducing extraction failures and improving system resilience.

## Tasks

### 1. Create JSON Repair Utility Module

**File:** `src/services/llm/json_repair.py`

```python
"""JSON repair utilities for handling malformed LLM responses."""

import json
import re
from typing import Any

def repair_json(malformed: str) -> dict[str, Any]:
    """Attempt to repair and parse malformed JSON.

    Repair strategies (in order):
    1. Direct parse (fast path)
    2. Fix unterminated strings
    3. Balance braces/brackets
    4. Remove trailing commas
    5. Fix single quotes to double quotes

    Args:
        malformed: Potentially malformed JSON string

    Returns:
        Parsed dictionary

    Raises:
        json.JSONDecodeError: If all repair strategies fail
    """
```

**Repair strategies to implement:**

| Strategy | Description | Example |
|----------|-------------|---------|
| `_fix_unterminated_strings` | Add missing closing quotes | `"value` → `"value"` |
| `_balance_brackets` | Add missing `]` and `}` | `{"a": [1, 2` → `{"a": [1, 2]}` |
| `_remove_trailing_commas` | Remove commas before `]` or `}` | `[1, 2,]` → `[1, 2]` |
| `_fix_quotes` | Convert single to double quotes | `{'a': 1}` → `{"a": 1}` |
| `_escape_newlines` | Escape unescaped newlines in strings | Literal newline → `\n` |

### 2. Integrate into Worker (High Priority)

**File:** `src/services/llm/worker.py`

Modify these methods to use repair before raising errors:

| Line | Method | Change |
|------|--------|--------|
| 399 | `_extract_facts` | Wrap `json.loads()` with repair fallback |
| 461 | `_extract_field_group` | Wrap `json.loads()` with repair fallback |
| 511 | `_extract_entities` | Wrap `json.loads()` with repair fallback |
| 561 | `_complete` | Wrap `json.loads()` with repair fallback |

**Pattern:**
```python
from src.services.llm.json_repair import repair_json

def _extract_entities(self, result_text: str) -> dict:
    try:
        return json.loads(result_text)
    except json.JSONDecodeError as e:
        logger.warning("json_parse_failed_attempting_repair", error=str(e))
        try:
            repaired = repair_json(result_text)
            logger.info("json_repair_succeeded")
            return repaired
        except json.JSONDecodeError:
            logger.warning("json_repair_failed")
            raise  # Original error, let retry logic handle
```

### 3. Integrate into Client (Medium Priority)

**File:** `src/services/llm/client.py`

Apply same pattern to:

| Line | Method |
|------|--------|
| 259 | `_extract_facts_direct` |
| 551 | `_extract_entities_direct` |
| 726 | `_complete_direct` |

### 4. Integrate into Schema Extractor

**File:** `src/services/extraction/schema_extractor.py`

| Line | Method |
|------|--------|
| 250 | `_extract_direct` |

### 5. Add Logging for Observability

Log these events with structlog:

| Event | Level | When |
|-------|-------|------|
| `json_parse_failed_attempting_repair` | WARNING | Initial parse fails |
| `json_repair_succeeded` | INFO | Repair worked |
| `json_repair_failed` | WARNING | Repair also failed |

Include fields: `original_error`, `repair_strategy_used`, `content_length`

## Test Cases

**File:** `tests/services/llm/test_json_repair.py`

### Unit Tests for Repair Module

```python
class TestRepairJson:
    def test_valid_json_passes_through(self):
        """Valid JSON should parse directly without repair."""

    def test_unterminated_string_repaired(self):
        """'{"name": "test' should become '{"name": "test"}'"""

    def test_missing_closing_brace_repaired(self):
        """'{"a": 1' should become '{"a": 1}'"""

    def test_missing_closing_bracket_repaired(self):
        """'{"items": [1, 2' should become '{"items": [1, 2]}'"""

    def test_nested_incomplete_structure(self):
        """'{"a": {"b": [1, 2' should become '{"a": {"b": [1, 2]}}'"""

    def test_trailing_comma_removed(self):
        """'{"a": 1,}' should become '{"a": 1}'"""

    def test_single_quotes_converted(self):
        """"{'a': 1}" should become '{"a": 1}'"""

    def test_unescaped_newline_fixed(self):
        """Literal newlines in strings should be escaped."""

    def test_irreparable_json_raises(self):
        """Completely invalid content should raise JSONDecodeError."""

    def test_empty_string_raises(self):
        """Empty string should raise JSONDecodeError."""

    def test_truncated_mid_key_repaired(self):
        """'{"na' should attempt repair or raise cleanly."""
```

### Integration Tests

```python
class TestWorkerJsonRepair:
    async def test_worker_recovers_from_malformed_response(self):
        """Worker should use repair and succeed on fixable JSON."""

    async def test_worker_logs_repair_event(self):
        """Repair usage should be logged for observability."""
```

## Constraints

- **DO NOT** modify the retry logic itself - repair is an additional step before retry
- **DO NOT** add external dependencies - implement repair with stdlib only
- **DO NOT** attempt repair on non-JSON content types
- **DO NOT** silently swallow errors - always log repair attempts
- **PRESERVE** original error if repair fails (for debugging)
- **KEEP** repair strategies simple and focused on common LLM failure modes

## Verification

1. **Unit tests pass:**
   ```bash
   pytest tests/services/llm/test_json_repair.py -v
   ```

2. **Integration tests pass:**
   ```bash
   pytest tests/services/llm/test_worker.py -v -k "json"
   ```

3. **Linting clean:**
   ```bash
   ruff check src/services/llm/json_repair.py
   ruff format src/services/llm/json_repair.py
   ```

4. **Manual verification:**
   - Run extraction against source that previously failed
   - Check logs for `json_repair_succeeded` events

## Files to Create/Modify

| Action | File |
|--------|------|
| CREATE | `src/services/llm/json_repair.py` |
| CREATE | `tests/services/llm/test_json_repair.py` |
| MODIFY | `src/services/llm/worker.py` |
| MODIFY | `src/services/llm/client.py` |
| MODIFY | `src/services/extraction/schema_extractor.py` |

## References

- Error log showing failure: `Unterminated string starting at: line 22 column 15`
- Current JSON parsing locations identified in code review
- No external JSON repair library in requirements.txt (keep it that way)
