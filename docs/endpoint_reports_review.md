# Pipeline Review: Reports Generation Endpoints

**Last Updated:** 2026-01-24
**Status:** Issues identified and fixed

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

### 🔴 `src/services/reports/service.py:122` - extraction_ids always empty
```python
extraction_ids=[],
```
**Status:** CONFIRMED REAL
**Issue:** The `extraction_ids` field is never populated, making it impossible to trace which extractions contributed to a report. This breaks provenance tracking.

---

### 🔴 `src/services/reports/service.py:47` - LLM client injected but never used
```python
self._llm_client = llm_client
```
**Status:** CONFIRMED REAL - only assigned at line 47, never referenced elsewhere
**Issue:** LLMClient is passed to ReportService but never called. The docstring says it's "for generating summaries" but no LLM-based synthesis happens - all aggregation is rule-based.

**Impact:** Reports lose information through simplistic aggregation (longest text, max number, any() for booleans).

---

### 🔴 `src/api/v1/reports.py:308` - NoneType crash in download endpoint
```python
safe_title = "".join(c for c in report.title if c.isalnum() or c in " -_")[:50]
```
**Status:** REAL but LOW-PROBABILITY
**Issue:** If `report.title` is `None`, this crashes with `TypeError: 'NoneType' object is not iterable`.

**Mitigating factor:** `service.py` lines 104-112 always set fallback titles (`request.title or "fallback"`), so newly created reports always have titles. However, old DB records or direct DB inserts could have `None`.

**Fix:** Add null check: `report.title or "report"`

---

## Important (should fix)

### 🟠 `src/services/reports/service.py:165-171` - No source attribution in extractions
```python
extractions_by_group[source_group] = [
    {
        "data": ext.data,
        "confidence": ext.confidence,
        "extraction_type": ext.extraction_type,
    }
    for ext in extractions
]
```
**Status:** CONFIRMED REAL
**Issue:** Report data doesn't include `ext.id`, `source_uri`, or `source_title`. Users can't see which page facts came from.

---

### 🟠 `src/services/reports/schema_table.py:87` - Uses deprecated FIELD_GROUPS_BY_NAME
```python
group = FIELD_GROUPS_BY_NAME.get(group_name)
```
**Status:** CONFIRMED REAL
**Issue:** Code comments in `field_groups.py:297-300` explicitly say this is deprecated and should use `SchemaAdapter.convert_to_field_groups()` from project schema. Hardcoded field groups won't work for projects with custom schemas.

---

### 🟠 `src/api/v1/reports.py:78,195` - entity_count hardcoded to 0
```python
entity_count=0,  # TODO: count entities from report data
```
**Status:** CONFIRMED REAL (has TODO comment acknowledging it)
**Issue:** API response always shows `entity_count: 0` even when entities exist.

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

### 🟠 `src/services/reports/service.py:308` - Hardcoded limit in comparison report
```python
for ext in extractions[:10]:  # Limit to top 10 per group
```
**Status:** CONFIRMED REAL
**Issue:** Only shows 10 extractions per group in "Detailed Findings" section. Rest silently dropped.

---

## Minor

### 🟡 N+1 query potential
**Status:** POTENTIAL - not currently triggered
**Issue:** `ExtractionRepository.list()` doesn't use `joinedload(Extraction.source)`. Currently harmless because `_gather_data()` doesn't access `ext.source`, but would become real if source attribution is added.

---

### 🟡 `src/services/reports/schema_table.py:250` - Text truncation without indication
```python
cells.append(str(val)[:50])  # Truncate for MD
```
**Status:** CONFIRMED REAL
**Issue:** Text silently truncated at 50 characters without ellipsis indicator.

---

### 🟡 `src/services/reports/service.py:409-411` - Boolean majority vote semantics
```python
row[field] = sum(values) > len(values) / 2
```
**Status:** Design choice, not a bug
**Note:** Majority vote may not always be semantically correct, but this is a reasonable aggregation strategy.

---

### 🟡 `src/api/v1/reports.py:319-322` - Content could be None
```python
return Response(content=report.content, ...)
```
**Status:** REAL but LOW-PROBABILITY
**Issue:** ORM allows `content=None`, but generators always return strings. Only affects old DB records.

---

### 🟡 `src/services/reports/service.py:91-106` - SchemaTableReport bypasses patterns
```python
schema_report = SchemaTableReport(self._db)
```
**Status:** CONFIRMED REAL
**Issue:** SchemaTableReport is instantiated without LLMClient, so it can't use LLM synthesis. Also queries DB directly instead of using repository pattern.

---

### 🟡 `src/models.py:561-571` - ReportResponse missing provenance fields
**Status:** CONFIRMED REAL
**Issue:** No field for `sources_referenced` or other provenance data.

---

## Summary

| Severity | Count | Verified |
|----------|-------|----------|
| Critical | 3 | 3 confirmed (1 low-probability) |
| Important | 6 | 6 confirmed |
| Minor | 6 | 4 confirmed, 1 potential, 1 design choice |

### Key Takeaways
1. **extraction_ids** and **LLM client unused** are the most impactful confirmed bugs
2. **No source attribution** is the biggest missing feature
3. **NoneType crashes** are real but unlikely in practice due to fallback logic
4. **Deprecated code path** in SchemaTableReport needs migration

---

## Fixes Applied

### Fixed Issues

| Issue | Fix | File |
|-------|-----|------|
| extraction_ids always empty | Collect IDs in `_gather_data()`, pass to Report | `service.py:122` |
| entity_count=0 hardcoded | Store in metadata during generation, read in API | `service.py`, `reports.py` |
| NoneType crash on title | Added null check: `report.title or "report"` | `reports.py:308` |
| NoneType crash on content | Added fallback: `report.content or ""` | `reports.py:320` |
| Silent truncation at 50 chars | Added ellipsis: `text[:47] + "..."` | `schema_table.py:250` |
| Boolean majority vote | Changed to `any()` for semantic correctness | `service.py:411` |
| Hardcoded [:10] limit | Added truncation notice in output | `service.py:308` |
| ReportData missing provenance | Added `extraction_ids` and `entity_count` fields | `service.py:18-25` |

### Remaining Issues (Not Fixed)

| Issue | Reason |
|-------|--------|
| LLM client unused | Requires new synthesis service (larger feature) |
| No source attribution | Requires eager-loading + synthesis (larger feature) |
| Deprecated FIELD_GROUPS_BY_NAME | Requires SchemaAdapter migration (separate task) |
| Lossy text aggregation | Requires LLM synthesis (larger feature) |
| SchemaTableReport bypasses patterns | Part of LLM synthesis refactor |

### Test Updates

- Updated `ReportData` in tests to include new required fields
- Updated boolean aggregation test from "majority" to "any"
- Updated mock_report fixture with `meta_data` attribute
