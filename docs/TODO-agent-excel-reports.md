# Agent Task: Excel/Tabular Reports

**Agent ID:** `agent-excel-reports`
**Branch:** `feat/excel-tabular-reports`
**Priority:** High

## Objective

Add TABLE report type that generates tabular reports with one row per company and columns per extraction field. Support both Markdown and Excel (.xlsx) output.

## Context

The current report system (`src/services/reports/service.py`) supports SINGLE and COMPARISON report types that generate prose-style markdown. Users need tabular output for structured data like the drivetrain company template, where each company is a row and extraction fields are columns.

**Example Output:**
```
| Source Group | Manufactures Gearboxes | Manufactures Motors | Employee Count |
|--------------|------------------------|---------------------|----------------|
| WattDrive    | Yes                    | Yes                 | 500+           |
| Elecon       | Yes                    | No                  | 2,000+         |
```

## Tasks

### 1. Add openpyxl dependency

**File:** `requirements.txt`

Add:
```
openpyxl>=3.1.0
```

### 2. Add TABLE report type and request fields

**File:** `src/models.py`

2a. Add TABLE to ReportType enum (around line 402):
```python
class ReportType(str, Enum):
    SINGLE = "single"
    COMPARISON = "comparison"
    TABLE = "table"  # Add this
```

2b. Add new fields to ReportRequest (around line 409):
```python
class ReportRequest(BaseModel):
    # ... existing fields ...

    # Add these new fields:
    columns: list[str] | None = Field(
        default=None,
        description="Specific field names to include as columns (None = all fields)"
    )
    output_format: Literal["md", "xlsx"] = Field(
        default="md",
        description="Output format for TABLE reports"
    )
```

### 3. Create Excel formatter

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

    def __init__(self) -> None:
        self._header_font = Font(bold=True, color="FFFFFF")
        self._header_fill = PatternFill(
            start_color="4472C4", end_color="4472C4", fill_type="solid"
        )
        self._border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin"),
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
            rows: List of dicts, each representing a row
            columns: Column field names in order
            column_labels: Optional mapping of field names to display labels
            sheet_name: Name for the worksheet

        Returns:
            Excel file as bytes
        """
        wb = Workbook()
        ws = wb.active
        ws.title = sheet_name

        labels = column_labels or {}

        # Write headers
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
            ws.column_dimensions[get_column_letter(col_idx)].width = min(
                max_length + 2, 50
            )

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

### 4. Add table generation methods to ReportService

**File:** `src/services/reports/service.py`

4a. Add import at top:
```python
from services.reports.excel_formatter import ExcelFormatter
```

4b. Add these methods to the ReportService class:

```python
def _humanize(self, field_name: str) -> str:
    """Convert field_name to Human Readable Label."""
    return field_name.replace("_", " ").title()

async def _aggregate_for_table(
    self,
    data: ReportData,
    columns: list[str] | None,
) -> tuple[list[dict], list[str]]:
    """Aggregate extractions into table rows.

    For each source_group, consolidate multiple extractions
    into a single row.

    Args:
        data: Report data with extractions by group
        columns: Specific columns to include, or None for all

    Returns:
        Tuple of (rows list, columns list)
    """
    rows = []
    all_columns: set[str] = set()

    for source_group in data.source_groups:
        extractions = data.extractions_by_group.get(source_group, [])
        row: dict = {"source_group": source_group}

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
                flat: list = []
                for v in values:
                    flat.extend(v)
                row[field] = list(dict.fromkeys(flat))
            else:
                # For text, take longest non-empty
                row[field] = max(values, key=len) if values else None

        rows.append(row)

    # Determine column order
    final_columns = ["source_group"]
    if columns:
        final_columns.extend(c for c in columns if c in all_columns)
    else:
        final_columns.extend(sorted(all_columns))

    return rows, final_columns

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
        lines.append(
            f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
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

async def _generate_table_report(
    self,
    data: ReportData,
    title: str | None,
    columns: list[str] | None,
    output_format: str,
) -> tuple[str, bytes | None]:
    """Generate table report in markdown or Excel.

    Args:
        data: Aggregated report data
        title: Report title
        columns: Fields to include as columns
        output_format: Output format ("md" or "xlsx")

    Returns:
        Tuple of (markdown_content, excel_bytes or None)
    """
    rows, final_columns = await self._aggregate_for_table(data, columns)

    md_content = self._build_markdown_table(rows, final_columns, title)

    if output_format == "xlsx":
        formatter = ExcelFormatter()
        excel_bytes = formatter.create_workbook(
            rows=rows,
            columns=final_columns,
            sheet_name=title or "Company Comparison",
        )
        return md_content, excel_bytes

    return md_content, None
```

4c. Modify the `generate()` method to handle TABLE type. Find the section that checks `request.type` and add:

```python
elif request.type == ReportType.TABLE:
    md_content, excel_bytes = await self._generate_table_report(
        data=data,
        title=request.title,
        columns=request.columns,
        output_format=request.output_format,
    )
    content = md_content
    # Store excel_bytes in binary_content if xlsx format requested
```

### 5. Add binary content column to Report model

**File:** `src/orm_models.py`

5a. Add import at top:
```python
from sqlalchemy import LargeBinary
```

5b. Add column to Report class (around line 184):
```python
class Report(Base):
    # ... existing columns ...
    binary_content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
```

### 6. Create Alembic migration for binary_content

**File:** `alembic/versions/YYYYMMDD_add_report_binary_content.py` (NEW)

Run: `cd src && alembic revision -m "add_report_binary_content"`

Then edit the generated file:
```python
"""add_report_binary_content

Revision ID: <auto-generated>
"""
from alembic import op
import sqlalchemy as sa

def upgrade() -> None:
    op.add_column('reports', sa.Column('binary_content', sa.LargeBinary(), nullable=True))

def downgrade() -> None:
    op.drop_column('reports', 'binary_content')
```

### 7. Add download endpoint

**File:** `src/api/v1/reports.py`

Add this endpoint:
```python
from fastapi.responses import Response

@router.get("/projects/{project_id}/reports/{report_id}/download")
async def download_report(
    project_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Download report in original format (markdown or xlsx)."""
    report = (
        db.query(Report)
        .filter(Report.id == report_id, Report.project_id == project_id)
        .first()
    )

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    # Sanitize filename
    safe_title = "".join(c for c in report.title if c.isalnum() or c in " -_")[:50]

    if report.format == "xlsx" and report.binary_content:
        return Response(
            content=report.binary_content,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{safe_title}.xlsx"'},
        )

    return Response(
        content=report.content,
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{safe_title}.md"'},
    )
```

### 8. Update generate() to store binary content

**File:** `src/services/reports/service.py`

In the `generate()` method, update the TABLE handling and report creation:

```python
# In generate() method, after generating content:
binary_content = None
report_format = "md"

if request.type == ReportType.TABLE:
    md_content, excel_bytes = await self._generate_table_report(
        data=data,
        title=title,
        columns=request.columns,
        output_format=request.output_format,
    )
    content = md_content
    if excel_bytes:
        binary_content = excel_bytes
        report_format = "xlsx"

# Update Report creation:
report = Report(
    project_id=project_id,
    type=request.type.value,
    title=title,
    content=content,
    source_groups=request.source_groups,
    categories=request.categories or [],
    extraction_ids=[],
    format=report_format,
    binary_content=binary_content,  # Add this
)
```

## Tests to Write

**File:** `tests/test_excel_formatter.py` (NEW)

```python
"""Tests for Excel formatter."""

import pytest
from openpyxl import load_workbook
from io import BytesIO

from services.reports.excel_formatter import ExcelFormatter


class TestExcelFormatter:
    def test_create_workbook_basic(self):
        """Test basic workbook creation."""
        formatter = ExcelFormatter()
        rows = [
            {"name": "Company A", "has_feature": True, "count": 100},
            {"name": "Company B", "has_feature": False, "count": 50},
        ]
        columns = ["name", "has_feature", "count"]

        result = formatter.create_workbook(rows, columns)

        assert isinstance(result, bytes)
        wb = load_workbook(BytesIO(result))
        ws = wb.active
        assert ws.cell(1, 1).value == "Name"
        assert ws.cell(2, 1).value == "Company A"
        assert ws.cell(2, 2).value == "Yes"
        assert ws.cell(3, 2).value == "No"

    def test_format_value_handles_types(self):
        """Test value formatting for different types."""
        formatter = ExcelFormatter()

        assert formatter._format_value(None) == "N/A"
        assert formatter._format_value(True) == "Yes"
        assert formatter._format_value(False) == "No"
        assert formatter._format_value(["a", "b"]) == "a, b"
        assert formatter._format_value("text") == "text"

    def test_humanize(self):
        """Test field name humanization."""
        formatter = ExcelFormatter()

        assert formatter._humanize("field_name") == "Field Name"
        assert formatter._humanize("manufactures_gearboxes") == "Manufactures Gearboxes"
```

**File:** `tests/test_report_table.py` (NEW)

```python
"""Tests for TABLE report generation."""

import pytest
from unittest.mock import MagicMock, AsyncMock
from uuid import uuid4

from models import ReportRequest, ReportType
from services.reports.service import ReportService, ReportData


class TestTableReportGeneration:
    @pytest.fixture
    def report_service(self):
        """Create ReportService with mocked dependencies."""
        return ReportService(
            extraction_repo=MagicMock(),
            entity_repo=MagicMock(),
            llm_client=MagicMock(),
            db_session=MagicMock(),
        )

    @pytest.fixture
    def sample_data(self):
        """Sample report data for testing."""
        return ReportData(
            extractions_by_group={
                "CompanyA": [
                    {"data": {"has_feature": True, "count": 100}, "confidence": 0.9},
                    {"data": {"has_feature": True, "count": 150}, "confidence": 0.8},
                ],
                "CompanyB": [
                    {"data": {"has_feature": False, "count": 50}, "confidence": 0.95},
                ],
            },
            entities_by_group={},
            source_groups=["CompanyA", "CompanyB"],
        )

    async def test_aggregate_for_table_boolean_majority(self, report_service, sample_data):
        """Test boolean aggregation uses majority vote."""
        rows, columns = await report_service._aggregate_for_table(sample_data, None)

        assert len(rows) == 2
        company_a = next(r for r in rows if r["source_group"] == "CompanyA")
        assert company_a["has_feature"] is True  # Both True

    async def test_aggregate_for_table_numeric_max(self, report_service, sample_data):
        """Test numeric aggregation uses max value."""
        rows, columns = await report_service._aggregate_for_table(sample_data, None)

        company_a = next(r for r in rows if r["source_group"] == "CompanyA")
        assert company_a["count"] == 150  # Max of 100, 150

    async def test_build_markdown_table(self, report_service):
        """Test markdown table generation."""
        rows = [
            {"source_group": "A", "feature": True},
            {"source_group": "B", "feature": False},
        ]
        columns = ["source_group", "feature"]

        result = report_service._build_markdown_table(rows, columns, "Test")

        assert "# Test" in result
        assert "| Source Group | Feature |" in result
        assert "| A | Yes |" in result
        assert "| B | No |" in result

    async def test_generate_table_report_xlsx(self, report_service, sample_data):
        """Test Excel report generation."""
        md, excel = await report_service._generate_table_report(
            data=sample_data,
            title="Test Report",
            columns=None,
            output_format="xlsx",
        )

        assert excel is not None
        assert isinstance(excel, bytes)
        assert md is not None  # Markdown also generated
```

## Verification

After completing all tasks:

1. Run tests:
   ```bash
   cd src && pytest tests/test_excel_formatter.py tests/test_report_table.py -v
   ```

2. Run linting:
   ```bash
   ruff check src/services/reports/ src/models.py src/api/v1/reports.py
   ruff format src/services/reports/ src/models.py src/api/v1/reports.py
   ```

3. Run migration:
   ```bash
   cd src && alembic upgrade head
   ```

4. Manual test (if services running):
   ```bash
   # Create table report
   curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/reports" \
     -H "Content-Type: application/json" \
     -H "X-API-Key: test-key" \
     -d '{"type": "table", "source_groups": ["CompanyA", "CompanyB"], "output_format": "xlsx"}'

   # Download
   curl "http://localhost:8000/api/v1/projects/{project_id}/reports/{report_id}/download" \
     -H "X-API-Key: test-key" -o report.xlsx
   ```

## Constraints

- Do NOT modify existing SINGLE or COMPARISON report logic
- Do NOT add CSV export (future enhancement)
- Do NOT add conditional formatting in Excel (future enhancement)
- Keep ExcelFormatter simple - no complex styling beyond headers

## Files Summary

| File | Action |
|------|--------|
| `requirements.txt` | Add openpyxl |
| `src/models.py` | Add TABLE enum, columns/output_format fields |
| `src/services/reports/excel_formatter.py` | CREATE |
| `src/services/reports/service.py` | Add table methods |
| `src/orm_models.py` | Add binary_content column |
| `alembic/versions/*_add_report_binary_content.py` | CREATE migration |
| `src/api/v1/reports.py` | Add download endpoint |
| `tests/test_excel_formatter.py` | CREATE |
| `tests/test_report_table.py` | CREATE |
