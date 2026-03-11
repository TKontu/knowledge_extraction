# Consolidated Table Reports

## Status: PENDING

**Created:** 2026-03-10

## Problem

The current table report pipeline has five data loss points:

1. **`v2_to_flat()` strips per-field provenance** — confidence averaged, grounding lost, quotes discarded
2. **Entity lists compressed into single cell** — 20 products × 8 fields → one semicolon string
3. **No consolidation integration** — reports query raw extractions, ignoring deterministic merge strategies
4. **List-of-dict fields lose structure** — `locations: [{city, country, site_type}]` flattened to string
5. **No cross-source dedup** — 50 URLs for one company = 50 redundant rows

## Solution

A new `group_by="consolidated"` mode with two layout options:

- **`layout="multi_sheet"`** (default for xlsx) — company overview + entity sub-tables + list expansions as separate sheets
- **`layout="single_sheet"`** — one flat table with configurable row axis via `entity_focus`

Both layouts share the same data gathering layer and row-building primitives. Zero LLM calls — reads pre-computed `consolidated_extractions` table.

---

## Architecture

### Data Flow

```
consolidated_extractions (DB)
            │
            ▼
    ConsolidatedDataGatherer          ← Phase 1: query + classify
    (query, group by source_group,
     separate entity vs scalar groups)
            │
            ▼
    ┌───────────────────┐
    │ Row Builders       │             ← Phase 2-4: pure functions
    │                    │
    │ build_company_row()│  scalar fields → flat dict
    │ build_entity_rows()│  entity items → list[dict]
    │ expand_list_field()│  list-of-dicts → list[dict]
    └───────┬───────────┘
            │
    ┌───────┴────────────────────────────┐
    │                                     │
    ▼                                     ▼
layout="multi_sheet"              layout="single_sheet"
    │                                     │
    ├─ Sheet: Company Overview            ├─ entity_focus=None
    │    one row per company              │    company-only rows
    │    scalar columns + summary         │    entity counts + top-N
    │                                     │
    ├─ Sheet: Gearbox Products            ├─ entity_focus="products_gearbox"
    │    one row per product              │    denormalized: company cols
    │    all 8 gearbox fields             │    on first row of each group
    │                                     │    + full entity fields
    ├─ Sheet: Motor Products              │
    ├─ Sheet: Accessory Products          ├─ entity_focus="all"
    │                                     │    all entity types mixed
    └─ Sheet: Locations                   │    + entity_type discriminator
       one row per location               │    + field superset columns
                                          │
                                          ▼
                                    ExcelFormatter / MarkdownBuilder
```

### Key Design Principles

1. **Shared row builders** — `build_company_row()`, `build_entity_rows()`, `expand_list_field()` are pure functions used by both layouts
2. **Schema-driven columns** — reuse `SchemaTableGenerator` for column order, collision prefixing, labels, and unit inference
3. **Entity field superset** — when `entity_focus="all"`, compute union of entity field names across all entity groups; columns not applicable to a given entity type are `None`
4. **Company sidecar** — in single-sheet entity-focused mode, company scalar fields appear on the first row of each source_group; subsequent entity rows for the same company leave company columns blank (Excel merged cells optional)
5. **Provenance as opt-in columns** — `include_provenance=True` adds `source_count`, `avg_agreement`, `grounded_pct` columns (both layouts)

---

## API Surface

### ReportRequest Changes

**File:** `src/models.py`

```python
class ReportRequest(BaseModel):
    # ... existing fields unchanged ...

    group_by: Literal["source", "domain", "consolidated"] = Field(
        default="source",
        description=(
            "'source': one row per URL. "
            "'domain': LLM smart merge per domain. "
            "'consolidated': deterministic merge from consolidation pipeline."
        ),
    )
    layout: Literal["multi_sheet", "single_sheet"] = Field(
        default="multi_sheet",
        description=(
            "Only for group_by='consolidated'. "
            "'multi_sheet': company overview + entity sub-tables (xlsx only). "
            "'single_sheet': one flat table."
        ),
    )
    entity_focus: str | None = Field(
        default=None,
        description=(
            "Only for layout='single_sheet'. "
            "None: company-only with entity summaries. "
            "'all': denormalized all entity types. "
            "'products_gearbox': denormalized single entity type. "
            "Must match an entity_list field group name from the schema."
        ),
    )
    include_provenance: bool = Field(
        default=False,
        description="Add quality columns (source_count, agreement, grounded_pct) to output.",
    )
```

### Validation Rules

```python
@model_validator(mode="after")
def validate_consolidated_options(self) -> Self:
    if self.group_by != "consolidated":
        # layout and entity_focus are ignored for source/domain modes
        return self
    if self.layout == "multi_sheet" and self.output_format == "md":
        # Markdown can't do multi-sheet; auto-downgrade to concatenated sections
        pass  # handled in service, no error
    if self.entity_focus is not None and self.layout != "single_sheet":
        raise ValueError("entity_focus requires layout='single_sheet'")
    return self
```

---

## Data Model

### ConsolidatedReportData

**File:** `src/services/reports/consolidated_builder.py` (new)

```python
@dataclass
class SheetData:
    """One logical table (sheet or markdown section)."""
    name: str                          # Sheet name / section title
    rows: list[dict[str, Any]]         # Row dicts
    columns: list[str]                 # Column order
    labels: dict[str, str]             # column → display label
    column_types: dict[str, str]       # column → field_type (for formatting)

@dataclass
class ConsolidatedReportData:
    """Complete report data ready for rendering."""
    company_sheet: SheetData
    entity_sheets: list[SheetData]           # one per entity_list group
    list_expansion_sheets: list[SheetData]   # one per structured list field
    summary: ReportSummary

@dataclass
class ReportSummary:
    """Top-level statistics for report header."""
    total_companies: int
    total_entities: dict[str, int]     # group_name → count
    total_locations: int
    coverage: dict[str, float]         # field_name → % non-null across companies
```

---

## Implementation Phases

### Phase 1: ConsolidatedDataGatherer

**New file:** `src/services/reports/consolidated_builder.py`

This is the core building block. A stateless class that takes consolidated DB records + schema and produces `ConsolidatedReportData`.

- [ ] `__init__(schema_generator: SchemaTableGenerator)`
- [ ] `gather(records: list[ConsolidatedExtraction], schema: dict) -> ConsolidatedReportData`
  - [ ] Parse schema to identify: entity_list groups, scalar groups, structured list fields
  - [ ] Group records by `source_group`
  - [ ] For each source_group:
    - [ ] Build company row from scalar groups via `_build_company_row()`
    - [ ] Extract entity rows from entity_list groups via `_build_entity_rows()`
    - [ ] Expand structured list fields via `_expand_list_field()`
  - [ ] Assemble columns, labels, types for each sheet from schema
  - [ ] Compute `ReportSummary` (counts, coverage)

**Classification logic:**
```python
def _classify_fields(self, schema: dict) -> FieldClassification:
    """Classify each field group and field into rendering categories."""
    # For each field_group:
    #   is_entity_list=True  → entity sheet
    #   field.field_type="list" AND items are dicts → list expansion sheet
    #   field.field_type="list" AND items are scalars → inline comma-sep in company sheet
    #   everything else → company overview column
```

### Phase 2: Company Row Builder

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `_build_company_row(source_group, records_by_type, schema, include_provenance) -> dict`
  - [ ] Merge scalar consolidated records for this source_group into one flat dict
  - [ ] Use `SchemaTableGenerator` column ordering + collision prefixing
  - [ ] Format flat list fields (certifications) as comma-separated
  - [ ] Format structured list fields as count summary (e.g., `"3 locations"`)
  - [ ] Add `source_group` as first column
  - [ ] If `include_provenance`:
    - [ ] `source_count` — max source_count across field groups
    - [ ] `avg_agreement` — mean provenance[field].agreement
    - [ ] `grounded_pct` — mean provenance[field].grounded_count / source_count

### Phase 3: Entity Row Builder

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `_build_entity_rows(source_group, record, field_group) -> list[dict]`
  - [ ] Read consolidated `data` — for entity lists this is the union_dedup'd items
  - [ ] Handle two storage formats:
    - Entity list in consolidation `data[group_name]` as list of field dicts
    - Direct field dict if single entity
  - [ ] For each entity item: create row with `source_group` + all entity fields
  - [ ] Column order from schema field definitions
  - [ ] Apply `_infer_unit()` to numeric column labels

### Phase 4: Structured List Expansion

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `_expand_list_field(source_group, field_name, items) -> list[dict]`
  - [ ] Detect dict items vs scalar items (only expand dicts)
  - [ ] For each dict item: prepend `source_group`, flatten dict keys to columns
  - [ ] Derive columns from union of all dict keys across all source_groups
  - [ ] Handle missing keys gracefully (None)

### Phase 5: Entity Field Superset (for single_sheet entity_focus="all")

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `_compute_entity_superset(schema) -> tuple[list[str], dict[str, str]]`
  - [ ] Collect all field names from all entity_list groups
  - [ ] Deduplicate (many share `product_name`, `model_number`, `subcategory`)
  - [ ] Prefix with `{group}.{field}` only when collision AND different semantics
  - [ ] Add `entity_type` discriminator column
  - [ ] Return (columns, labels)

Superset for drivetrain schema:

| Column | From Groups | Sparse? |
|--------|-------------|---------|
| entity_type | discriminator | never |
| product_name | all 3 | never |
| series_name | gearbox, motor | accessory=null |
| model_number | all 3 | never |
| subcategory | all 3 | never |
| power_rating_kw | gearbox, motor | accessory=null |
| torque_rating_nm | gearbox, accessory | motor=null |
| ratio | gearbox only | motor,acc=null |
| efficiency_percent | gearbox only | motor,acc=null |
| speed_rating_rpm | motor only | gearbox,acc=null |
| voltage | motor only | gearbox,acc=null |

= 11 entity columns, max 4 sparse per row. Acceptable.

### Phase 6: Single-Sheet Layout Composer

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `compose_single_sheet(data: ConsolidatedReportData, entity_focus: str | None, schema: dict) -> SheetData`

**Three modes:**

#### Mode A: `entity_focus=None` — Company Overview Only

- [ ] Start with `data.company_sheet`
- [ ] Add entity count summary columns per entity group:
  - `gearbox_count`, `motor_count`, `accessory_count`
- [ ] Add top-3 entity name preview columns per entity group:
  - `top_gearboxes`: `"R Series; K Series; X Series (+9 more)"`
- [ ] Return as single `SheetData`

#### Mode B: `entity_focus="products_gearbox"` — Single Entity Type Denormalized

- [ ] For each source_group:
  - [ ] Get company row (scalar fields)
  - [ ] Get entity rows for the focused entity type
  - [ ] First row: company fields + first entity fields
  - [ ] Subsequent rows: company fields = None + next entity fields
  - [ ] If 0 entities: one row with company fields only, entity fields = None
- [ ] Columns: company scalar columns + entity-specific columns
- [ ] Return as single `SheetData`

#### Mode C: `entity_focus="all"` — All Entity Types Denormalized

- [ ] Same as Mode B but iterates all entity_list groups
- [ ] Uses superset columns from Phase 5
- [ ] Adds `entity_type` discriminator column
- [ ] Company fields on first row of each source_group only
- [ ] Return as single `SheetData`

### Phase 7: Multi-Sheet Layout Composer

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `compose_multi_sheet(data: ConsolidatedReportData) -> list[SheetData]`
  - [ ] Sheet 1: `data.company_sheet` (always present)
  - [ ] Sheets 2-N: `data.entity_sheets` (skip if empty)
  - [ ] Sheets N+1-M: `data.list_expansion_sheets` (skip if empty)
  - [ ] Return ordered list

### Phase 8: Multi-Sheet Excel Formatter

**File:** `src/services/reports/excel_formatter.py`

- [ ] Add `create_multi_sheet_workbook(sheets: list[SheetData]) -> bytes`:
  - [ ] Create workbook
  - [ ] For each `SheetData`:
    - [ ] Add worksheet with sanitized name (max 31 chars)
    - [ ] Write headers + data using existing `_write_sheet()` (extract from `create_workbook`)
    - [ ] Freeze row 1 (header)
    - [ ] Add auto-filter on all columns
  - [ ] First sheet = active sheet
  - [ ] Return bytes

- [ ] Refactor `create_workbook()` to delegate to shared `_write_sheet(ws, rows, columns, labels)`:
  - [ ] Existing `create_workbook` calls `_write_sheet` once → no behavior change
  - [ ] `create_multi_sheet_workbook` calls `_write_sheet` per sheet

### Phase 9: Markdown Multi-Table Renderer

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `render_markdown(sheets: list[SheetData], summary: ReportSummary) -> str`
  - [ ] Header with summary statistics
  - [ ] For each sheet: `## {sheet.name}` + markdown table
  - [ ] Reuse sanitization from `_build_markdown_table()` (pipe/newline escaping)

### Phase 10: Wire Into Report Pipeline

**File:** `src/services/reports/service.py`

- [ ] In `_generate_table_report()`, add `group_by="consolidated"` branch:
  ```python
  if group_by == "consolidated":
      records = self._query_consolidated(project_id, source_groups)
      if not records:
          return "# No consolidated data\n\nRun consolidation first.", None

      builder = ConsolidatedReportBuilder(self._schema_generator)
      report_data = builder.gather(records, extraction_schema)

      if layout == "single_sheet":
          sheet = builder.compose_single_sheet(report_data, entity_focus, extraction_schema)
          sheets = [sheet]
      else:
          sheets = builder.compose_multi_sheet(report_data)

      md_content = builder.render_markdown(sheets, report_data.summary)

      if output_format == "xlsx":
          formatter = ExcelFormatter()
          excel_bytes = formatter.create_multi_sheet_workbook(sheets)
          return md_content, excel_bytes
      return md_content, None
  ```

- [ ] Add `_query_consolidated()`:
  ```python
  def _query_consolidated(self, project_id, source_groups) -> list[ConsolidatedExtraction]:
      stmt = select(ConsolidatedExtraction).where(
          ConsolidatedExtraction.project_id == project_id,
      )
      if source_groups:
          stmt = stmt.where(ConsolidatedExtraction.source_group.in_(source_groups))
      return self._db.execute(stmt).scalars().all()
  ```

**File:** `src/models.py`

- [ ] Extend `group_by` Literal to `"source" | "domain" | "consolidated"`
- [ ] Add `layout: Literal["multi_sheet", "single_sheet"] = "multi_sheet"`
- [ ] Add `entity_focus: str | None = None`
- [ ] Add `include_provenance: bool = False`
- [ ] Add validator: `entity_focus` requires `layout="single_sheet"` requires `group_by="consolidated"`

**File:** `src/api/v1/reports.py`

- [ ] Document new parameters in endpoint docstring
- [ ] No logic changes needed (passthrough to service)

**File:** `src/ke_mcp/tools/reports.py`

- [ ] Add `layout`, `entity_focus`, `include_provenance` parameters to `create_report` tool

### Phase 11: Schema Validation for entity_focus

**File:** `src/services/reports/consolidated_builder.py`

- [ ] `validate_entity_focus(entity_focus: str, schema: dict) -> None`
  - [ ] If `entity_focus == "all"`: always valid
  - [ ] Otherwise: check that `entity_focus` matches an `is_entity_list=True` group name
  - [ ] Raise `ValueError` with available entity group names if invalid

---

## Example Outputs

### Multi-Sheet (default, xlsx)

**Sheet: Company Overview** (290 rows × ~19 cols)

| Company | Company Name | Employee Count | Employee Range | Sites | HQ Location | Mfg Gearboxes | Mfg Motors | Mfg Accessories | Mfg Details | Provides Services | Svc Gearboxes | Svc Motors | Svc Accessories | Field Service | Service Types | Certifications | Locations |
|---------|-------------|----------------|----------------|-------|-------------|---------------|------------|-----------------|-------------|-------------------|---------------|------------|-----------------|---------------|---------------|----------------|-----------|
| ABB | ABB Ltd | 140,000 | 5000+ | 12 | Zurich, CH | Yes | Yes | No | Power transmission... | Yes | Yes | Yes | No | Yes | repair, maintenance | ISO 9001, ISO 14001 | 12 locations |

**Sheet: Gearbox Products** (~2,000 rows × 9 cols)

| Company | Product Name | Series | Model | Subcategory | Power (kW) | Torque (Nm) | Ratio | Efficiency (%) |
|---------|-------------|--------|-------|-------------|-----------|------------|-------|---------------|
| ABB | Dodge Quantis RHB | RHB | RHB-382 | helical | 200 | 20,000 | 5:1 | 96 |

**Sheet: Motor Products**, **Sheet: Accessory Products** — same pattern

**Sheet: Locations** (structured list expansion)

| Company | City | Country | Site Type |
|---------|------|---------|-----------|
| ABB | Zurich | Switzerland | headquarters |

### Single-Sheet, entity_focus=None (company-only)

290 rows × ~22 cols (overview + entity counts + top-3 previews)

| Company | ... all scalar fields ... | Gearbox Count | Top Gearboxes | Motor Count | Top Motors | Accessory Count | Top Accessories |
|---------|--------------------------|---------------|---------------|-------------|------------|-----------------|-----------------|
| ABB | ... | 12 | Dodge Quantis RHB; K Series; ... | 8 | M3BP; M2AA; ... | 5 | ... |

### Single-Sheet, entity_focus="products_gearbox"

~2,000 rows × ~23 cols (company sidecar + gearbox fields)

| Company | Employee Count | HQ Location | Mfg Gearboxes | ... | Product Name | Series | Model | Subcategory | Power (kW) | Torque (Nm) | Ratio | Efficiency (%) |
|---------|----------------|-------------|---------------|-----|-------------|--------|-------|-------------|-----------|------------|-------|---------------|
| ABB | 140,000 | Zurich, CH | Yes | ... | Dodge Quantis RHB | RHB | RHB-382 | helical | 200 | 20,000 | 5:1 | 96 |
| | | | | ... | K Series | K | K167 | bevel | 150 | 15,000 | 5:1 | 94 |
| | | | | ... | R Series | R | R17 | helical | 0.12 | 100 | 3.7:1 | 96 |
| SEW | 21,000 | Bruchsal, DE | Yes | ... | R17 | R | R17 | helical | 0.12 | 100 | 3.7:1 | 96 |

### Single-Sheet, entity_focus="all"

~5,000 rows × ~25 cols (company sidecar + entity superset)

| Company | ... scalar ... | Entity Type | Product Name | Series | Model | Subcategory | Power (kW) | Torque (Nm) | Ratio | Efficiency (%) | Speed (RPM) | Voltage |
|---------|---------------|-------------|-------------|--------|-------|-------------|-----------|------------|-------|---------------|-------------|---------|
| ABB | ... | gearbox | Dodge Quantis | RHB | RHB-382 | helical | 200 | 20,000 | 5:1 | 96 | | |
| | | motor | M3BP | M3 | M3BP-315 | induction | 375 | | | | 3,000 | 400V |

---

## Test Plan

**File:** `tests/test_report_consolidated.py`

### ConsolidatedDataGatherer Tests

| Test | Verifies |
|------|----------|
| `test_gather_separates_entity_and_scalar` | Entity list groups in `entity_sheets`, scalar in `company_sheet` |
| `test_gather_multiple_source_groups` | One company row per source_group, entities linked |
| `test_gather_empty_records` | Returns empty sheets, non-null structure |
| `test_gather_provenance_columns` | `include_provenance=True` adds quality columns |

### Company Row Builder Tests

| Test | Verifies |
|------|----------|
| `test_company_row_merges_scalar_groups` | All non-entity field groups merged into one row |
| `test_company_row_flat_list_inline` | `certifications` → comma-separated string |
| `test_company_row_structured_list_summary` | `locations` list-of-dicts → `"3 locations"` |
| `test_company_row_column_order_from_schema` | Columns match schema field_group order |
| `test_company_row_collision_prefixing` | Duplicate field names get `{group}.{field}` prefix |

### Entity Row Builder Tests

| Test | Verifies |
|------|----------|
| `test_entity_rows_one_per_item` | N entities → N rows |
| `test_entity_rows_source_group_prepended` | Each row has `source_group` column |
| `test_entity_rows_all_fields_present` | All 8 gearbox fields as columns |
| `test_entity_rows_empty_list` | 0 entities → 0 rows (no phantom row) |

### Structured List Expansion Tests

| Test | Verifies |
|------|----------|
| `test_expand_dict_list` | `[{city, country}]` → one row per item |
| `test_expand_union_keys` | Heterogeneous dicts → superset columns |
| `test_skip_scalar_list` | `["ISO 9001", "CE"]` not expanded |

### Single-Sheet Composer Tests

| Test | Verifies |
|------|----------|
| `test_single_sheet_company_only` | `entity_focus=None` → entity count + top-N columns |
| `test_single_sheet_entity_focused` | `entity_focus="products_gearbox"` → denormalized rows |
| `test_single_sheet_company_sidecar` | Company fields on first row only, blank on subsequent |
| `test_single_sheet_company_no_entities` | Company with 0 products → one row, entity cols None |
| `test_single_sheet_all_entities` | `entity_focus="all"` → superset columns, discriminator |
| `test_single_sheet_invalid_entity_focus` | Bad group name → ValueError with valid options |

### Multi-Sheet Composer Tests

| Test | Verifies |
|------|----------|
| `test_multi_sheet_all_present` | Company + entity + expansion sheets |
| `test_multi_sheet_skip_empty_entity` | Entity group with 0 items → no sheet |
| `test_multi_sheet_order` | Company first, then entities, then expansions |

### Excel Formatter Tests

| Test | Verifies |
|------|----------|
| `test_multi_sheet_workbook_sheet_count` | Correct number of worksheets |
| `test_multi_sheet_workbook_sheet_names` | Sheet names match, sanitized to 31 chars |
| `test_multi_sheet_workbook_freeze_pane` | Row 1 frozen on each sheet |
| `test_existing_create_workbook_unchanged` | Refactor doesn't break existing single-sheet |

### Markdown Renderer Tests

| Test | Verifies |
|------|----------|
| `test_markdown_multi_table_sections` | `## Company Overview`, `## Gearbox Products` headers |
| `test_markdown_summary_header` | Stats block at top (companies, entities, coverage) |
| `test_markdown_pipe_escaping` | Values containing `|` escaped |

### Integration Test

- [ ] Generate multi-sheet xlsx for drivetrain project, verify sheet count and row counts
- [ ] Generate single-sheet entity_focus="all" for same project, verify row count = total entities
- [ ] Spot-check: company with known products → verify all products present in entity sheet

---

## Constraints

- **Do NOT modify** existing `group_by="source"` or `group_by="domain"` behavior
- **Do NOT modify** consolidation logic — it's complete and tested
- **Do NOT add LLM calls** — this mode is deterministic
- **Reuse** `SchemaTableGenerator` for column derivation — no duplication
- **Refactor** `ExcelFormatter.create_workbook()` to extract `_write_sheet()` — existing callers must not break
- New code in `consolidated_builder.py` — keep `service.py` as thin orchestrator
- Entity sub-tables must include ALL entity fields (no compression)
- Provenance columns are optional, off by default
- `entity_focus` validated against schema — fail fast with clear error

## Dependencies

- Consolidation must be run first (`POST /projects/{id}/consolidate`)
- If no consolidated data exists, return informative error message, not empty table

## Files Changed

| File | Action |
|------|--------|
| `src/services/reports/consolidated_builder.py` | **New** — gatherer, row builders, composers, markdown renderer |
| `src/services/reports/excel_formatter.py` | Refactor `_write_sheet()` + add `create_multi_sheet_workbook()` |
| `src/services/reports/service.py` | Add `group_by="consolidated"` branch + `_query_consolidated()` |
| `src/models.py` | Extend `group_by`, add `layout`, `entity_focus`, `include_provenance` |
| `src/api/v1/reports.py` | Update docstrings |
| `src/ke_mcp/tools/reports.py` | Add new parameters |
| `tests/test_report_consolidated.py` | **New** — ~25 tests |

**No files removed or deprecated.**

---

## Performance

- **Zero LLM calls** — reads pre-computed consolidated data
- **Single DB query** — `SELECT * FROM consolidated_extractions WHERE project_id = ?`
- **O(source_groups × field_groups)** — linear, fast
- 290 companies × 7 field groups = ~2,030 records → sub-second
- Single-sheet `entity_focus="all"` may produce ~5,000 rows — still instant

---

## Delivery Order

Phases are designed for incremental delivery:

1. **Phases 1-4** — Core builders (testable standalone, no API changes)
2. **Phase 7 + 8** — Multi-sheet layout + Excel (first visible output)
3. **Phase 9 + 10** — Markdown + wiring (API available)
4. **Phases 5-6** — Single-sheet modes (builds on working multi-sheet)
5. **Phase 11** — Validation polish

Each phase has its own test group. No phase depends on a later phase.

---

*Created 2026-03-10*
