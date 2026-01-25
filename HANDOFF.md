# Handoff: Template-Agnostic Table Reports

## Completed This Session

### Template-Agnostic Table/Excel Generation (Commit `287af76`)

Refactored table report generation to derive columns and labels dynamically from project's `extraction_schema`, eliminating hardcoded drivetrain-specific code.

**New File:** `src/services/reports/schema_table_generator.py`
- `get_columns_from_schema()` - derives columns/labels from schema
- `get_entity_list_groups()` - identifies entity list field groups
- `format_entity_list()` - generic formatting for any entity list
- `_find_id_field()` - finds identifying field (name, product_name, etc.)
- `_infer_unit()` - infers units from field names (_kw → "kW", _nm → "Nm")

**Modified:** `src/services/reports/service.py`
- Added `ProjectRepository` dependency
- TABLE report now loads project schema and uses SchemaTableGenerator
- SCHEMA_TABLE deprecated (forwards to TABLE with warning)
- `_aggregate_for_table()` handles entity lists and schema-derived columns

**Tests:** `tests/test_schema_table_generator.py` (25 tests)

### Previous: TODO Cleanup (Commit `a5bed98`)

Removed 6 completed agent TODO files (all features implemented).

## Current State

**Main branch clean** - all changes committed.

```
287af76 feat(reports): Template-agnostic table/Excel generation
a5bed98 chore: Remove completed TODO files
a82b2ed chore: Update cache bust for fresh build
bf2f864 fix(reports): Address pipeline review issues for LLM synthesis
```

## Test Status

- 40 report-related tests passing
- 25 new SchemaTableGenerator tests
- All linting clean

## Key Architecture

TABLE report type now works uniformly across all templates:

```
Request: POST /projects/{id}/reports
         {"type": "table", "source_groups": [...], "output_format": "xlsx"}

Flow:
1. ReportService.generate() receives request
2. _get_project_schema() loads extraction_schema from DB
3. SchemaTableGenerator.get_columns_from_schema() derives columns/labels
4. _aggregate_for_table() uses schema for entity lists + field aggregation
5. ExcelFormatter.create_workbook() uses schema-derived labels
```

## Remaining Technical Debt

| Issue | Priority | Notes |
|-------|----------|-------|
| `SchemaTableReport` still exists | Low | Can be removed after deprecation period |
| `FIELD_GROUPS_BY_NAME` still exists | Low | Still used by SchemaTableReport |
| No LLM cost tracking | Low | Add metrics later |

## Next Steps

- [ ] Test with real projects using different templates
- [ ] Consider removing SchemaTableReport entirely (it's deprecated)
- [ ] Clean up FIELD_GROUPS_BY_NAME if no longer needed
