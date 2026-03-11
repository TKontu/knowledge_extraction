# Pipeline Review: Consolidated Table Reports (Post-Fix)

## Flow
```
API POST /projects/{id}/reports (request.group_by="consolidated")
  → reports.py:create_report  → ReportService.generate()
  → short-circuit: _generate_consolidated_table()
    → DB: select ConsolidatedExtraction WHERE project_id + source_group IN (...)
    → ConsolidatedReportBuilder.gather()
      → get_scalar_columns() → _build_company_row() per source_group
      → get_entity_group_columns() → _build_entity_rows() per entity group
    → compose_multi_sheet() | compose_single_sheet()
    → render_markdown() + ExcelFormatter.create_multi_sheet_workbook()
  → Report ORM → commit → ReportResponse
```

## Previous issues (all fixed)

1. ~~entity_focus name mismatch~~ — Fixed via SheetData.key + _find_entity_sheet dual-lookup
2. ~~double H1 header~~ — Fixed: render_markdown no longer emits its own H1
3. ~~entity_count/extraction_count always 0~~ — Fixed: summary wired into meta_data

## Current issues

### Minor (UX)

- [ ] **service.py:604 — invalid entity_focus returns generic 500 instead of 422 with helpful message**
  `validate_entity_focus()` raises `ValueError("Unknown entity group 'foo'. Available: [...]")` but the route handler has no try/except and the global handler only catches `AppError`. FastAPI swallows the message and returns `{"detail": "Internal Server Error"}`. A user who typos entity_focus (e.g., "products" instead of "products_gearbox") gets a cryptic 500. The helpful error message is lost. Not a crash or data issue — purely a UX gap.

## No critical or important issues found
