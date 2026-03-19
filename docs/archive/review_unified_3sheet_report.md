# Pipeline Review: Unified 3-Sheet Consolidated Report

## Flow
```
API: api/v1/reports.py:create_report
  → service.py:generate() → _generate_consolidated_table()
    → consolidated_builder.py:gather() → _build_unified_row()
    → consolidated_builder.py:build_provenance_sheets()
    → excel_formatter.py:create_multi_sheet_workbook() → _write_sheet() → _apply_quality_formatting()
```

## Review #1 (2026-03-11) — Fixed

- [x] **excel_formatter.py:136+164 — Quality sheet conditional formatting never fired.** `_format_value()` coerced ALL values to `str`, so `isinstance(cell.value, (int, float))` in `_apply_quality_formatting` was always False. **Fixed**: `_format_value` now preserves `int`/`float` values. Verified with live openpyxl workbook — cells now get proper green/yellow/red fills.

## Review #2 (2026-03-11) — No new issues found

Full end-to-end trace verified:

1. **Data flow**: `gather()` → `get_unified_columns()` → `_build_unified_row()` — correct. Scalars looked up across extraction types, entity lists formatted via `format_entity_list()`. Column types correctly tracked.

2. **Provenance flow**: `build_provenance_sheets()` — correct. `_list` suffix stripping works for entity list columns. `winning_weight` preserved as `float` (not stringified). Source URL resolution via `_resolve_source_urls()` handles the full provenance → source_id → URL chain.

3. **Excel formatting**: `_format_value()` now preserves numeric types. Quality conditional formatting fires correctly (`float` values → green/yellow/red fills). Header colors differentiated by sheet type (data=blue, quality=green, sources=grey). Freeze panes and auto-filter applied.

4. **API surface**: Removed 4 params (`layout`, `entity_focus`, `include_provenance`, `provenance_sheets`) from models, MCP tools, and client. Pydantic v2 `extra="ignore"` means old callers sending these fields won't break.

5. **Markdown path**: `render_markdown()` uses `total_count` (not old `total_companies`). Single data sheet rendered; provenance sheets are Excel-only.

42 tests pass. No real issues identified.

## Non-issues (verified as theoretical only)

- **Collision-prefixed columns** (`group_a.name`): No existing template has field name collisions across groups. All 9 templates produce zero prefixed columns.

- **Long source_label sheet name truncation**: No existing template sets `source_label` — all fall back to `"Source"` (22 chars for Quality sheet, well under 31 limit).

- **Scalar column lookup scanning entity records**: Entity records store data under the group name key (e.g., `"products_gearbox": [...]`), which structurally cannot collide with scalar field names.

- **Entity counting double-iteration**: Correct but negligible cost — a few dozen dict lookups.

- **Import inside method body**: Pre-existing pattern, stylistic only.

## Review #3 (2026-03-12) — Data loss fixes + horizontal entity pagination

Addressed 4 data loss issues identified through production data analysis:

### 1. Entity list formatting — ALL fields now shown

**Before**: `format_entity_list()` showed ID field + max 3 numeric/enum fields, hardcoded `max_items=10`. With production data averaging 26-60 entities per company (max 506), this caused 50-98% data loss.

**After**: Shows ID field + ALL non-null fields per entity. Items newline-separated, fields within each item semicolon-separated. `max_items` raised to 50 (per page). Template-agnostic: `_find_id_field()` uses first field in schema order.

### 2. Horizontal entity pagination

**Problem**: Companies with >50 entities can't fit in one cell without becoming unusable.

**Solution**: Two-phase row building in `gather()`:
- Phase 1: `_build_unified_row()` collects raw entity data (items + per-entity provenance) without formatting
- Phase 2: `_paginate_entities()` determines max entity count across all rows, creates page columns (e.g., "Products Gearbox (1-50)", "Products Gearbox (51-100)"), distributes entities, formats cells

Key data structures:
- `SheetData.provenance_key_map`: maps paginated column names back to provenance keys (e.g., `products_gearbox_list_p2` → `products_gearbox`)
- `SheetData.row_entity_provenance`: per-row, per-column list of entity provenance dicts for quality computation
- `_EntityRaw` dataclass: holds `items` (filtered entity list) and `provenance` (per-entity provenance list)

### 3. Per-page quality scoring

**Before**: Quality sheet showed single `winning_weight` for entire entity list — misleading when list spans multiple pages.

**After**: `build_provenance_sheets()` uses `row_entity_provenance` to compute average `winning_weight` of entities in each specific page column. Empty pages show "N/A".

### 4. Entity quality filtering

Entities with `winning_weight < 0.3` excluded from report display (`ENTITY_MIN_QUALITY = 0.3` constant in `consolidated_builder.py`). Filtering happens in `_filter_entities()` before pagination.

### 5. List-of-dicts formatting

**Before**: `_format_dict_list()` showed "N items" placeholder.

**After**: Shows actual items as semicolon-delimited lines (same format as entity lists). `max_items` raised to 50.

### Production data scale (drivetrain project)

| Entity Type | Avg Count | Max Count | % Exceeding Old max_items=10 |
|-------------|-----------|-----------|------------------------------|
| products_accessory | 59.7 | 506 | 78% |
| products_motor | 27.2 | 189 | 62% |
| products_gearbox | 26.0 | 157 | 58% |

78 report tests pass. Lint clean. Full suite confirmation pending.
