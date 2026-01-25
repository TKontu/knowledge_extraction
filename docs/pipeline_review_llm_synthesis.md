# Pipeline Review: LLM Synthesis Feature

**Date:** 2025-01-25
**Scope:** PR #62 changes - LLM synthesis for report generation
**Status:** 5 of 10 findings fixed, 5 remaining (all minor/optimization)

## Flow

```
reports.py:create_report
  → ReportService.__init__(synthesizer=None)
  → ReportService.generate()
  → ReportService._gather_data(include_source=True)
  → ExtractionRepository.list(include_source=True)
  → ReportSynthesizer.synthesize_facts()
  → LLMClient.complete()
```

---

## Critical (must fix)

### ✅ 1. LLMClient not closed after use in API endpoint — FIXED

**File:** `src/api/v1/reports.py:59-69`

Now uses context manager:
```python
async with LLMClient(settings) as llm_client:
    report_service = ReportService(...)
    report = await report_service.generate(project_id, request)
```

---

### ✅ 2. `sources_referenced` field — RESOLVED (Design Choice)

The `ReportResponse` model intentionally does NOT include a `sources_referenced` field.
Sources are embedded in the markdown `content` as a "Sources Referenced" section.

If structured source data is needed in the API response, that's a feature request.

---

## Important (should fix)

### ✅ 3. LLM called for EVERY category even with single fact — FIXED

**File:** `src/services/reports/service.py:276-302`

Now checks item count and formats single facts directly:
```python
if len(items) == 1:
    # Format directly, no LLM needed
    fact_text = item.get("data", {}).get("fact", ...)
    result = SynthesisResult(synthesized_text=text, ...)
else:
    # Use LLM synthesis for multiple facts
    result = await self._synthesizer.synthesize_facts(items, ...)
```

---

### ✅ 4. `_complete_direct` retry doesn't vary temperature — FIXED

**File:** `src/services/llm/client.py:704-706`

Now varies temperature on retries:
```python
for attempt in range(1, max_retries + 1):
    current_temp = base_temp + (attempt - 1) * temp_increment
```

---

### ✅ 5. Queue worker doesn't handle `request_type="complete"` — FIXED

**File:** `src/services/llm/worker.py:344-347`

Handler now exists:
```python
elif request.request_type == "complete":
    return await self._complete(
        request.payload, temperature, request.retry_count
    )
```

---

### 🟠 6. Chunking doesn't synthesize final result across chunks

**File:** `src/services/reports/synthesis.py:124-125`

```python
# Combine chunk results
all_text = "\n\n".join(r.synthesized_text for r in chunk_results)
```

For 50 facts split into 4 chunks, you get 4 separate paragraphs with no coherence.

**Impact:** Large fact sets produce disjointed reports rather than unified narratives.

**Fix:** Consider a second-pass synthesis to merge chunk results.

---

## Minor

### 🟡 7. `_build_sources_section` method defined but never used

**File:** `src/services/reports/service.py`

Method is defined but never called - dead code.

**Fix:** Either use the method or remove it.

---

### 🟡 8. Hardcoded `max_detail_extractions = 10`

**File:** `src/services/reports/service.py:351`

```python
max_detail_extractions = 10  # Hardcoded, not configurable
```

**Fix:** Consider making configurable via `ReportRequest`.

---

### 🟡 9. No test coverage for queue mode `complete()`

**File:** `tests/test_llm_client.py`

All `complete()` tests use direct mode. No test for `_complete_via_queue()`.

**Fix:** Add test with mocked llm_queue.

---

### 🟡 10. Variable facts embedded in system prompt

**File:** `src/services/reports/synthesis.py:70-73`

```python
system_prompt = f"""You are synthesizing...

Facts to synthesize:
{facts_text}
```

System prompts are typically cached by providers. Variable content reduces cache hits.

**Impact:** Minor efficiency reduction.

**Fix:** Move facts to user prompt.

---

## Summary

| Severity | Count | Fixed | Remaining |
|----------|-------|-------|-----------|
| 🔴 Critical | 2 | 2 | 0 |
| 🟠 Important | 4 | 3 | 1 |
| 🟡 Minor | 4 | 0 | 4 |

## Remaining Work

1. **Chunking synthesis** (#6) - Second-pass to unify chunk results
2. **Dead code cleanup** (#7) - Remove `_build_sources_section`
3. **Configurable limit** (#8) - Make `max_detail_extractions` configurable
4. **Queue tests** (#9) - Add test for `_complete_via_queue`
5. **Prompt optimization** (#10) - Move facts to user prompt
