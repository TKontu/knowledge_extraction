# Pipeline Review: LLM Synthesis Feature

**Date:** 2025-01-25
**Scope:** PR #62 changes - LLM synthesis for report generation
**Status:** All 10 findings verified as REAL issues

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

### 🔴 1. LLMClient not closed after use in API endpoint ✓ VERIFIED

**File:** `src/api/v1/reports.py:58-66`

```python
llm_client = LLMClient(settings)
report_service = ReportService(...)
report = await report_service.generate(project_id, request)
# llm_client.close() never called - HTTP connection leaks
```

**Verification:** LLMClient has `close()` method at `client.py:61-73` and `__aexit__` at `client.py:79-81` but neither is called.

**Impact:** Each report generation creates a new `AsyncOpenAI` client that is never closed. This leaks HTTP connections over time.

**Fix:** Use context manager:
```python
async with LLMClient(settings) as llm_client:
    report_service = ReportService(...)
    report = await report_service.generate(...)
```

---

### 🔴 2. `sources_referenced` field not populated in API response ✓ VERIFIED

**File:** `src/api/v1/reports.py:72-81`

The `ReportResponse` model has `sources_referenced: list[str] | None = None` (models.py:585) but:
1. Report ORM model (`orm_models.py:191-211`) has no `sources_referenced` field
2. API response never populates it - always returns `None`

```python
return ReportResponse(
    id=str(report.id),
    type=report.type,
    # ... sources_referenced is never set
)
```

**Verification:** `sources_referenced` variable exists in service.py (lines 257, 266, 279, 355, 371, 385, 398) but is only used for markdown generation, not stored or returned.

**Impact:** API consumers expecting source URIs in the response will always get `None`.

**Fix:** Either:
1. Add `sources_referenced` to Report ORM and populate in response, or
2. Remove the field from ReportResponse if not implemented

---

## Important (should fix)

### 🟠 3. LLM called for EVERY category even with single fact ✓ VERIFIED

**File:** `src/services/reports/service.py:258-261`

```python
for category, items in sorted(by_category.items()):
    result = await self._synthesizer.synthesize_facts(items, synthesis_type="summarize")
```

No check for number of items - synthesis happens even for 1 fact.

**Impact:** Unnecessary LLM costs and latency for single-fact categories.

**Fix:**
```python
if len(items) <= 1:
    # Format directly, no LLM needed
    text = items[0].get("data", {}).get("fact", "") if items else ""
    result = SynthesisResult(synthesized_text=text, sources_used=[...], ...)
else:
    result = await self._synthesizer.synthesize_facts(items, ...)
```

---

### 🟠 4. `_complete_direct` retry doesn't vary temperature ✓ VERIFIED

**File:** `src/services/llm/client.py:700-730`

```python
temp = temperature or self.settings.llm_base_temperature
for attempt in range(1, max_retries + 1):
    # temp never changes between attempts
```

Compare to `_extract_facts_direct` (line 227):
```python
temperature = base_temp + (attempt - 1) * temp_increment  # Varies!
```

**Impact:** Retries may hit the same failure mode repeatedly.

**Fix:** Add temperature variation like other methods.

---

### 🟠 5. Queue worker doesn't handle `request_type="complete"` ✓ VERIFIED CRITICAL

**File:** `src/services/llm/worker.py:315-345`

```python
async def _execute_llm_call(self, request: LLMRequest) -> dict[str, Any]:
    if request.request_type == "extract_facts":
        return await self._extract_facts(...)
    elif request.request_type == "extract_field_group":
        return await self._extract_field_group(...)
    elif request.request_type == "extract_entities":
        return await self._extract_entities(...)
    else:
        raise ValueError(f"Unknown request type: {request.request_type}")
```

**There is NO handler for `request_type="complete"`!**

**Impact:** Queue mode for `complete()` will fail with `ValueError: Unknown request type: complete`. All synthesis in queue mode is broken.

**Fix:** Add handler in worker.py:
```python
elif request.request_type == "complete":
    return await self._complete(request.payload, temperature, request.retry_count)
```

---

### 🟠 6. Chunking doesn't synthesize final result across chunks ✓ VERIFIED

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

### 🟡 7. `_build_sources_section` method defined but never used ✓ VERIFIED

**File:** `src/services/reports/service.py:284-308`

Method is defined but never called - dead code.

**Fix:** Either use the method or remove it.

---

### 🟡 8. Hardcoded `max_detail_extractions = 10` ✓ VERIFIED

**File:** `src/services/reports/service.py:351`

```python
max_detail_extractions = 10  # Hardcoded, not configurable
```

**Fix:** Consider making configurable via `ReportRequest`.

---

### 🟡 9. No test coverage for queue mode `complete()` ✓ VERIFIED

**File:** `tests/test_llm_client.py`

All `complete()` tests use direct mode. No test for `_complete_via_queue()`.

Note: Given finding #5 (queue worker doesn't handle "complete"), these tests would fail anyway.

**Fix:** Add test with mocked llm_queue once worker is fixed.

---

### 🟡 10. Variable facts embedded in system prompt ✓ VERIFIED

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

| Severity | Count | Status |
|----------|-------|--------|
| 🔴 Critical | 2 | All verified |
| 🟠 Important | 4 | All verified (one is actually critical: #5) |
| 🟡 Minor | 4 | All verified |

## Recommended Priority

1. **Fix queue worker for `complete`** (#5) - Queue mode completely broken
2. **Fix LLMClient leak** (#1) - Production stability issue
3. **Either populate or remove `sources_referenced`** (#2) - API contract violation
4. **Skip synthesis for single facts** (#3) - Cost optimization
5. **Add temperature variation to `_complete_direct`** (#4) - Retry effectiveness
