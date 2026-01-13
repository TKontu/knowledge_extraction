# TODO: Excel/Tabular Reports

## Overview

Add tabular report generation where each row represents a company (source_group) and columns represent extraction schema fields. Supports Excel (.xlsx) export for easy data analysis and comparison.

## Status: PENDING

## Use Case

For structured extractions like `DRIVETRAIN_COMPANY_TEMPLATE`, produce reports like:

| Company | Manufactures Gearboxes | Manufactures Motors | Employee Count | HQ Location |
|---------|------------------------|---------------------|----------------|-------------|
| WattDrive | Yes | Yes | 500+ | Austria |
| Elecon | Yes | No | 2,000+ | India |
| Bauer Gears | Yes | No | Unknown | Germany |

## Core Tasks

### 1. Add TABLE Report Type

**File:** `src/models.py`

```python
class ReportType(str, Enum):
    SINGLE = "single"
    COMPARISON = "comparison"
    TABLE = "table"  # NEW: Tabular/Excel report
```

**Add request fields:**

```python
class ReportRequest(BaseModel):
    type: ReportType
    source_groups: list[str]
    # ... existing fields ...

    # New fields for TABLE type
    columns: list[str] | None = None  # Field names to include as columns
    include_all_fields: bool = True   # If True, include all schema fields
    format: Literal["md", "xlsx"] = "md"  # Output format
```

### 2. Create Excel Formatter

**File:** `src/services/reports/excel_formatter.py` (NEW)

```python
"""Excel report generation using openpyxl."""

from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


class ExcelFormatter:
    """Formats tabular data into Excel workbooks."""

    def __init__(self):
        self._header_font = Font(bold=True, color="FFFFFF")
        self._header_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
        self._border = Border(
            left=Side(style='thin'),
            right=Side(style='thin'),
            top=Side(style='thin'),
            bottom=Side(style='thin')
        )

    def create_workbook(
        self,
        rows: list[dict[str, Any]],
        columns: list[str],
        column_labels: dict[str, str] | None = None,
        sheet_name: str = "Report",
    ) -> bytes:
        """Create Excel workbook from row data.

        Args:
            rows: List of dicts, each representing a company row
            columns: Column field names in order
            column_labels: Optional mapping of field names to display labels
            sheet_name: Name for the worksheet

        Returns:
            Excel file as bytes
        """
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name

        # Write headers
        labels = column_labels or {}
        for col_idx, col_name in enumerate(columns, 1):
            cell = ws.cell(row=1, column=col_idx)
            cell.value = labels.get(col_name, self._humanize(col_name))
            cell.font = self._header_font
            cell.fill = self._header_fill
            cell.alignment = Alignment(horizontal="center")
            cell.border = self._border

        # Write data rows
        for row_idx, row_data in enumerate(rows, 2):
            for col_idx, col_name in enumerate(columns, 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                value = row_data.get(col_name)
                cell.value = self._format_value(value)
                cell.border = self._border
                cell.alignment = Alignment(wrap_text=True)

        # Auto-adjust column widths
        for col_idx, col_name in enumerate(columns, 1):
            max_length = len(labels.get(col_name, self._humanize(col_name)))
            for row in ws.iter_rows(min_row=2, min_col=col_idx, max_col=col_idx):
                for cell in row:
                    if cell.value:
                        max_length = max(max_length, len(str(cell.value)))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max_length + 2, 50)

        # Save to bytes
        output = BytesIO()
        wb.save(output)
        return output.getvalue()

    def _humanize(self, field_name: str) -> str:
        """Convert field_name to Human Readable Label."""
        return field_name.replace("_", " ").title()

    def _format_value(self, value: Any) -> str:
        """Format extraction values for display."""
        if value is None:
            return "N/A"
        if isinstance(value, bool):
            return "Yes" if value else "No"
        if isinstance(value, list):
            return ", ".join(str(v) for v in value)
        return str(value)
```

### 3. Add Table Aggregation Logic

**File:** `src/services/reports/service.py`

Add method to aggregate extractions into single row per company:

```python
async def _aggregate_for_table(
    self,
    data: ReportData,
    columns: list[str] | None,
) -> tuple[list[dict], list[str]]:
    """Aggregate extractions into table rows.

    For each source_group, consolidate multiple extractions
    into a single row. Uses majority vote for booleans,
    concatenates text values, etc.

    Args:
        data: Report data with extractions by group
        columns: Specific columns to include, or None for all

    Returns:
        Tuple of (rows list, columns list)
    """
    rows = []
    all_columns = set()

    for source_group in data.source_groups:
        extractions = data.extractions_by_group.get(source_group, [])
        row = {"source_group": source_group}

        # Collect all field values from extractions
        field_values: dict[str, list] = {}
        for ext in extractions:
            ext_data = ext.get("data", {})
            for field, value in ext_data.items():
                if field not in field_values:
                    field_values[field] = []
                if value is not None:
                    field_values[field].append(value)
                    all_columns.add(field)

        # Aggregate values per field
        for field, values in field_values.items():
            if not values:
                row[field] = None
            elif isinstance(values[0], bool):
                # Majority vote for booleans
                row[field] = sum(values) > len(values) / 2
            elif isinstance(values[0], (int, float)):
                # Use max for numbers
                row[field] = max(values)
            elif isinstance(values[0], list):
                # Flatten and dedupe lists
                flat = []
                for v in values:
                    flat.extend(v)
                row[field] = list(dict.fromkeys(flat))
            else:
                # For text, take first non-empty or longest
                row[field] = max(values, key=len) if values else None

        rows.append(row)

    # Determine column order
    final_columns = ["source_group"]
    if columns:
        final_columns.extend(c for c in columns if c in all_columns)
    else:
        # Sort alphabetically but group related fields
        final_columns.extend(sorted(all_columns))

    return rows, final_columns


async def _generate_table_report(
    self,
    data: ReportData,
    title: str | None,
    columns: list[str] | None,
    format: str,
) -> str | bytes:
    """Generate table report in markdown or Excel.

    Args:
        data: Aggregated report data
        title: Report title
        columns: Fields to include as columns
        format: Output format ("md" or "xlsx")

    Returns:
        Markdown string or Excel bytes
    """
    rows, final_columns = await self._aggregate_for_table(data, columns)

    if format == "xlsx":
        from services.reports.excel_formatter import ExcelFormatter
        formatter = ExcelFormatter()
        return formatter.create_workbook(
            rows=rows,
            columns=final_columns,
            sheet_name=title or "Company Comparison",
        )

    # Markdown table
    return self._build_markdown_table(rows, final_columns, title)


def _build_markdown_table(
    self,
    rows: list[dict],
    columns: list[str],
    title: str | None,
) -> str:
    """Build markdown table from rows."""
    lines = []
    if title:
        lines.append(f"# {title}")
        lines.append("")

    # Column labels
    labels = [self._humanize(c) for c in columns]
    lines.append("| " + " | ".join(labels) + " |")
    lines.append("|" + "|".join(["---"] * len(columns)) + "|")

    # Data rows
    for row in rows:
        values = []
        for col in columns:
            val = row.get(col)
            if val is None:
                values.append("N/A")
            elif isinstance(val, bool):
                values.append("Yes" if val else "No")
            elif isinstance(val, list):
                values.append(", ".join(str(v) for v in val))
            else:
                values.append(str(val))
        lines.append("| " + " | ".join(values) + " |")

    return "\n".join(lines)


def _humanize(self, field_name: str) -> str:
    """Convert field_name to Human Readable."""
    return field_name.replace("_", " ").title()
```

### 4. Update Report Generation Flow

**File:** `src/services/reports/service.py`

Modify `generate()` method:

```python
async def generate(
    self,
    project_id: UUID,
    request: ReportRequest,
) -> Report:
    """Generate report based on request type."""
    data = await self._gather_data(
        project_id=project_id,
        source_groups=request.source_groups,
        categories=request.categories,
        entity_types=request.entity_types,
        max_extractions=request.max_extractions,
    )

    # Generate content based on type
    if request.type == ReportType.SINGLE:
        content = await self._generate_single_report(data, request.title)
        report_format = "md"
    elif request.type == ReportType.COMPARISON:
        content = await self._generate_comparison_report(data, request.title)
        report_format = "md"
    elif request.type == ReportType.TABLE:
        result = await self._generate_table_report(
            data=data,
            title=request.title,
            columns=request.columns,
            format=request.format or "md",
        )
        report_format = request.format or "md"
        if report_format == "xlsx":
            # Store Excel binary separately
            content = f"Excel report: {len(result)} bytes"
            # TODO: Store binary in blob storage or base64 in content
        else:
            content = result
    else:
        raise ValueError(f"Unknown report type: {request.type}")

    # ... rest of method
```

### 5. Add Binary Report Storage

**File:** `src/orm_models.py`

Add optional binary content column:

```python
class Report(Base):
    # ... existing columns ...
    binary_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
```

Or store in file system with path reference:

```python
class Report(Base):
    # ... existing columns ...
    file_path: Mapped[str | None] = mapped_column(String, nullable=True)
```

### 6. Add Download Endpoint

**File:** `src/api/v1/reports.py`

```python
@router.get("/projects/{project_id}/reports/{report_id}/download")
async def download_report(
    project_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Download report in original format."""
    report = db.query(Report).filter(
        Report.id == report_id,
        Report.project_id == project_id,
    ).first()

    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    if report.format == "xlsx":
        return Response(
            content=report.binary_content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={
                "Content-Disposition": f'attachment; filename="{report.title}.xlsx"'
            },
        )

    return Response(
        content=report.content,
        media_type="text/markdown",
        headers={
            "Content-Disposition": f'attachment; filename="{report.title}.md"'
        },
    )
```

### 7. Add openpyxl Dependency

**File:** `requirements.txt`

```
openpyxl>=3.1.0
```

---

## API Examples

### Create Table Report (Markdown)

```bash
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/reports" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "type": "table",
    "source_groups": ["WattDrive", "Elecon", "BauerGears"],
    "format": "md"
  }'
```

### Create Table Report (Excel)

```bash
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/reports" \
  -H "Content-Type: application/json" \
  -H "X-API-Key: $API_KEY" \
  -d '{
    "type": "table",
    "source_groups": ["WattDrive", "Elecon", "BauerGears"],
    "columns": ["manufactures_gearboxes", "manufactures_motors", "employee_count", "headquarters_location"],
    "format": "xlsx"
  }'
```

### Download Excel Report

```bash
curl "http://localhost:8000/api/v1/projects/{project_id}/reports/{report_id}/download" \
  -H "X-API-Key: $API_KEY" \
  -o report.xlsx
```

---

## File Changes Summary

| File | Action | Description |
|------|--------|-------------|
| `src/models.py` | Modify | Add TABLE to ReportType, add columns/format fields |
| `src/services/reports/excel_formatter.py` | Create | Excel generation with openpyxl |
| `src/services/reports/service.py` | Modify | Add _aggregate_for_table, _generate_table_report |
| `src/orm_models.py` | Modify | Add binary_content or file_path column |
| `src/api/v1/reports.py` | Modify | Add download endpoint |
| `requirements.txt` | Modify | Add openpyxl |
| `alembic/versions/xxx_add_report_binary.py` | Create | Migration for binary column |

---

## Testing Checklist

- [ ] Unit: ExcelFormatter creates valid .xlsx
- [ ] Unit: _aggregate_for_table handles bool/text/list/numeric
- [ ] Unit: Markdown table formatting
- [ ] Integration: TABLE report with md format
- [ ] Integration: TABLE report with xlsx format
- [ ] Integration: Download endpoint returns correct content-type
- [ ] Integration: Excel opens in LibreOffice/Excel/Google Sheets

---

## Future Enhancements

- CSV export option
- Custom column labels in request
- Column grouping (Manufacturing | Services | Company Info)
- Conditional formatting in Excel (green/red for boolean)
- Multiple sheets per report (one per extraction type)
- Pivot table generation
