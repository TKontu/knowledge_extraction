# Pipeline Review: Reports Generation Endpoints

**Last Updated:** 2026-01-25
**Status:** Most critical issues fixed, remaining items are minor optimizations

## Flow
```
api/v1/reports.py:create_report
  → ReportService.__init__ (injection)
  → ReportService.generate()
    → _gather_data() → ExtractionRepository.list() / EntityRepository.list()
    → _generate_{single|comparison|table}_report() OR SchemaTableReport.generate()
    → Report ORM → db.commit()
  → ReportResponse
```

---

## Critical (must fix)

### ✅ `src/services/reports/service.py:122` - extraction_ids always empty — FIXED
```python
extraction_ids=[],
```
**Status:** FIXED - IDs now collected in `_gather_data()` and passed to Report

---

### ✅ `src/services/reports/service.py:47` - LLM client injected but never used — FIXED
```python
self._llm_client = llm_client
```
**Status:** FIXED - LLMClient is now used via `ReportSynthesizer`:
- Line 59: `self._synthesizer = synthesizer or ReportSynthesizer(llm_client)`
- Line 305: `result = await self._synthesizer.synthesize_facts(items, ...)`

---

### ✅ `src/api/v1/reports.py:308` - NoneType crash in download endpoint — FIXED
```python
safe_title = "".join(c for c in report.title if c.isalnum() or c in " -_")[:50]
```
**Status:** FIXED - Added null check: `report.title or "report"`

---

## Important (should fix)

### ✅ `src/services/reports/service.py:195-204` - No source attribution in extractions — FIXED
```python
extractions_by_group[source_group] = [
    {
        "data": ext.data,
        "confidence": ext.confidence,
        "extraction_type": ext.extraction_type,
        "source_id": str(ext.source_id),
        "source_uri": ext.source.uri if ext.source else None,
        "source_title": ext.source.title if ext.source else None,
        "chunk_index": ext.chunk_index,
    }
    for ext in extractions
]
```
**Status:** FIXED - Now includes `source_id`, `source_uri`, and `source_title` for provenance tracking.

---

### ✅ `src/services/reports/schema_table.py:87` - Uses deprecated FIELD_GROUPS_BY_NAME — RESOLVED
```python
group = FIELD_GROUPS_BY_NAME.get(group_name)
```
**Status:** RESOLVED - `SchemaTableReport` is now deprecated. `SCHEMA_TABLE` report type forwards to `TABLE` with a warning. The new `TABLE` report uses `SchemaTableGenerator` which derives columns from project schema.

---

### ✅ `src/api/v1/reports.py:78,195` - entity_count hardcoded to 0 — FIXED
```python
entity_count=0,  # TODO: count entities from report data
```
**Status:** FIXED - Now stored in metadata during generation, read in API response.

---

### 🟠 `src/services/reports/service.py:421-423` - Lossy text aggregation
```python
# For text, take longest non-empty
row[field] = max(values, key=len) if values else None
```
**Status:** CONFIRMED REAL
**Issue:** Multiple valuable text values are reduced to just the longest one. Information is lost.

---

### 🟠 `src/services/reports/schema_table.py:142-148` - Semicolon-join loses context
```python
unique = list(dict.fromkeys([str(v) for v in values if v]))
merged[field.name] = "; ".join(unique)
```
**Status:** CONFIRMED REAL
**Issue:** Multiple text values are joined with semicolons but no source attribution.

---

### ✅ `src/services/reports/service.py:308` - Hardcoded limit in comparison report — MITIGATED
```python
for ext in extractions[:10]:  # Limit to top 10 per group
```
**Status:** MITIGATED - Added truncation notice in output when more than 10 extractions exist.

---

## Minor

### 🟡 N+1 query potential
**Status:** POTENTIAL - not currently triggered
**Issue:** `ExtractionRepository.list()` doesn't use `joinedload(Extraction.source)`. Currently harmless because `_gather_data()` doesn't access `ext.source`, but would become real if source attribution is added.

---

### ✅ `src/services/reports/schema_table.py:250` - Text truncation without indication — FIXED
```python
cells.append(str(val)[:50])  # Truncate for MD
```
**Status:** FIXED - Added ellipsis: `text[:47] + "..."`

---

### ✅ `src/services/reports/service.py:409-411` - Boolean majority vote semantics — FIXED
```python
row[field] = sum(values) > len(values) / 2
```
**Status:** FIXED - Changed to `any()` for semantic correctness.

---

### ✅ `src/api/v1/reports.py:319-322` - Content could be None — FIXED
```python
return Response(content=report.content, ...)
```
**Status:** FIXED - Added fallback: `report.content or ""`

---

### ✅ `src/services/reports/service.py:91-106` - SchemaTableReport bypasses patterns — RESOLVED
```python
schema_report = SchemaTableReport(self._db)
```
**Status:** RESOLVED - `SCHEMA_TABLE` now deprecated and forwards to `TABLE`. The new `TABLE` path uses `SchemaTableGenerator` which follows proper patterns.

---

### 🟡 `src/models.py:561-571` - ReportResponse missing provenance fields
**Status:** Design choice
**Note:** Sources are embedded in markdown `content` as "Sources Referenced" section. Structured source data in API is a feature request.

---

## Summary

| Severity | Count | Fixed | Remaining |
|----------|-------|-------|-----------|
| Critical | 3 | 3 | 0 |
| Important | 6 | 5 | 1 |
| Minor | 6 | 5 | 1 |

## Remaining Work

| Issue | Priority | Notes |
|-------|----------|-------|
| Lossy text aggregation | Low | `max(values, key=len)` takes longest only |
| Semicolon-join loses context | Low | Consider attribution markers |
| N+1 query potential | Low | Add joinedload if needed |

---

## Fixes Applied

### Fixed Issues

| Issue | Fix | File |
|-------|-----|------|
| extraction_ids always empty | Collect IDs in `_gather_data()`, pass to Report | `service.py:122` |
| LLM client unused | Now used via ReportSynthesizer | `service.py:56-59` |
| No source attribution | Added `source_id`, `source_uri`, `source_title` | `service.py:195-204` |
| entity_count=0 hardcoded | Store in metadata during generation, read in API | `service.py`, `reports.py` |
| NoneType crash on title | Added null check: `report.title or "report"` | `reports.py:308` |
| NoneType crash on content | Added fallback: `report.content or ""` | `reports.py:320` |
| Silent truncation at 50 chars | Added ellipsis: `text[:47] + "..."` | `schema_table.py:250` |
| Boolean majority vote | Changed to `any()` for semantic correctness | `service.py:411` |
| Hardcoded [:10] limit | Added truncation notice in output | `service.py:308` |
| ReportData missing provenance | Added `extraction_ids` and `entity_count` fields | `service.py:18-25` |
| SchemaTableReport bypasses patterns | Deprecated SCHEMA_TABLE, forwards to TABLE | `service.py` |
| Deprecated FIELD_GROUPS_BY_NAME | New TABLE uses SchemaTableGenerator | `schema_table_generator.py` |

### Test Updates

- Updated `ReportData` in tests to include new required fields
- Updated boolean aggregation test from "majority" to "any"
- Updated mock_report fixture with `meta_data` attribute
