# Pipeline Review: Template-Agnostic Table Reports

**Files Reviewed:**
- `src/services/reports/schema_table_generator.py` (new)
- `src/services/reports/service.py` (modified)

**Flow:**
```
reports.py:create_report → ReportService.generate() → _generate_table_report()
    → _get_project_schema() → _aggregate_for_table() → SchemaTableGenerator
    → _build_markdown_table() / ExcelFormatter.create_workbook()
```

---

## Critical (must fix)

### 1. 🔴 `service.py:131-133` - Duplicate title assignment
```python
# Line 117-118 sets title
title = (
    request.title or f"Schema Report: {', '.join(request.source_groups)}"
)
# ... then lines 131-133 set it again (dead code)
title = (
    request.title or f"Schema Report: {', '.join(request.source_groups)}"
)
```
**Impact:** Dead code, no functional impact but confusing.
**Fix:** Remove duplicate lines 131-133.

---

## Important (should fix)

### 2. 🟠 `schema_adapter.py:215-217` - KeyError on malformed schema
```python
fields.append(
    FieldDefinition(
        name=f_def["name"],       # KeyError if missing
        field_type=f_def["field_type"],  # KeyError if missing
        description=f_def["description"],  # KeyError if missing
```
**Impact:** If project has invalid schema (missing required keys), `convert_to_field_groups()` raises `KeyError` instead of graceful handling.
**Context:** `SchemaTableGenerator.get_columns_from_schema()` calls this without validation.
**Fix:** Either:
- Call `validate_extraction_schema()` before `convert_to_field_groups()` in SchemaTableGenerator
- Or add try/except in `_aggregate_for_table()` to handle malformed schemas gracefully

### 3. 🟠 `service.py:61` - Double ProjectRepository instantiation
```python
# In ReportService.__init__:
self._project_repo = project_repo or ProjectRepository(db_session)

# In reports.py:47 (API layer):
project_repo = ProjectRepository(db)
project = await project_repo.get(project_id)
```
**Impact:** Two separate ProjectRepository instances created per request. The one in API isn't passed to ReportService.
**Fix:** Pass `project_repo` to ReportService constructor:
```python
report_service = ReportService(
    extraction_repo=extraction_repo,
    entity_repo=entity_repo,
    llm_client=llm_client,
    db_session=db,
    project_repo=project_repo,  # ADD THIS
)
```

### 4. 🟠 `service.py:594-595` - Empty string causes max() to fail
```python
row[field] = max(values, key=len) if values else None
```
**Impact:** If `values` contains only empty strings `["", ""]`, this works but returns `""`. However, if values contains non-string types that have `len()`, this could raise TypeError.
**Existing behavior:** This is pre-existing code, not introduced in this change.

---

## Minor

### 5. 🟡 `schema_table_generator.py:109` - Potential None in string format
```python
name = item.get(id_field, "Unknown") if id_field else str(item)
```
**Impact:** If `item.get(id_field)` returns `None` explicitly, it won't use "Unknown" fallback.
**Better:** `name = item.get(id_field) or "Unknown" if id_field else str(item)`

### 6. 🟡 `service.py:546-556` - Entity list key detection is fragile
```python
for key in ["products", "items", group_name, "entities", "list"]:
    if key in data_dict and isinstance(data_dict[key], list):
        items.extend(data_dict[key])
        break
else:
    # Fallback: treat whole dict as entity
    if data_dict and not any(...):
        items.append(data_dict)
```
**Impact:** If extraction data uses a different key name (e.g., "results", "entries"), items won't be found.
**Mitigation:** The fallback handles single-entity case, but multi-item lists under unknown keys will be missed.
**Suggestion:** Consider making the list key configurable in the schema, or scanning for any list-valued key.

### 7. 🟡 `schema_table_generator.py:37` - No validation before convert
```python
field_groups = self._adapter.convert_to_field_groups(extraction_schema)
```
**Impact:** Invalid schema will raise KeyError (see #2 above).
**Fix:** Add validation or wrap in try/except.

---

## Verified Working

- ✅ Schema-derived columns correctly ordered
- ✅ Labels derived from field descriptions
- ✅ Entity list groups formatted with `{name}_list` columns
- ✅ Unit inference works for common suffixes
- ✅ Excel and Markdown both use schema-derived labels
- ✅ Deprecation warning for SCHEMA_TABLE works correctly
- ✅ All 40 tests pass

---

## Summary

| Severity | Count | Action |
|----------|-------|--------|
| Critical | 1 | Remove dead code (lines 131-133) |
| Important | 3 | Pass project_repo to service, add schema validation |
| Minor | 3 | Edge case handling improvements |
