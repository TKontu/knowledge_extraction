# TODO: PDF Export for Reports

**Agent ID**: `agent-pdf`
**Branch**: `feat/pdf-export`
**Priority**: 3

## Objective

Add PDF export capability for reports using Pandoc, allowing users to download reports as PDF files.

## Context

- Reports are stored with markdown content in `reports.content`
- Current export endpoint (`/api/v1/projects/{id}/export/`) handles CSV/JSON for entities/extractions
- ReportService in `src/services/reports/service.py` generates markdown content
- No PDF generation exists currently
- Pandoc is the standard tool for markdown-to-PDF conversion

## Tasks

### 1. Add PDF converter utility

**File**: `src/services/reports/pdf.py` (new file)

```python
"""PDF generation from markdown using Pandoc."""

import asyncio
import tempfile
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)


class PDFConversionError(Exception):
    """Raised when PDF conversion fails."""

    pass


class PDFConverter:
    """Convert markdown to PDF using Pandoc."""

    def __init__(
        self,
        pandoc_path: str = "pandoc",
        pdf_engine: str = "xelatex",
    ):
        """Initialize PDF converter.

        Args:
            pandoc_path: Path to pandoc executable.
            pdf_engine: PDF engine (xelatex, pdflatex, etc.).
        """
        self.pandoc_path = pandoc_path
        self.pdf_engine = pdf_engine

    async def convert(
        self,
        markdown: str,
        title: str | None = None,
    ) -> bytes:
        """Convert markdown content to PDF.

        Args:
            markdown: Markdown content to convert.
            title: Optional document title.

        Returns:
            PDF file contents as bytes.

        Raises:
            PDFConversionError: If conversion fails.
        """
        # Create temp files for input/output
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        ) as md_file:
            md_file.write(markdown)
            md_path = md_file.name

        pdf_path = md_path.replace(".md", ".pdf")

        try:
            # Build pandoc command
            cmd = [
                self.pandoc_path,
                md_path,
                "-o",
                pdf_path,
                f"--pdf-engine={self.pdf_engine}",
                "--standalone",
            ]

            if title:
                cmd.extend(["--metadata", f"title={title}"])

            # Add styling options
            cmd.extend([
                "--variable", "geometry:margin=1in",
                "--variable", "fontsize=11pt",
            ])

            logger.debug("pdf_conversion_started", command=" ".join(cmd))

            # Run pandoc
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                error_msg = stderr.decode() if stderr else "Unknown error"
                logger.error(
                    "pdf_conversion_failed",
                    returncode=process.returncode,
                    error=error_msg,
                )
                raise PDFConversionError(f"Pandoc failed: {error_msg}")

            # Read generated PDF
            pdf_content = Path(pdf_path).read_bytes()

            logger.info(
                "pdf_conversion_completed",
                input_size=len(markdown),
                output_size=len(pdf_content),
            )

            return pdf_content

        finally:
            # Cleanup temp files
            Path(md_path).unlink(missing_ok=True)
            Path(pdf_path).unlink(missing_ok=True)

    async def is_available(self) -> bool:
        """Check if Pandoc is available.

        Returns:
            True if pandoc is installed and working.
        """
        try:
            process = await asyncio.create_subprocess_exec(
                self.pandoc_path,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await process.communicate()
            return process.returncode == 0
        except FileNotFoundError:
            return False
```

### 2. Add PDF export endpoint

**File**: `src/api/v1/reports.py`

Add endpoint to export report as PDF:

```python
from fastapi.responses import Response
from services.reports.pdf import PDFConverter, PDFConversionError

@router.get(
    "/projects/{project_id}/reports/{report_id}/pdf",
    responses={
        200: {
            "content": {"application/pdf": {}},
            "description": "PDF file",
        },
        404: {"description": "Report not found"},
        503: {"description": "PDF conversion unavailable"},
    },
)
async def export_report_pdf(
    project_id: UUID,
    report_id: UUID,
    db: Session = Depends(get_db),
) -> Response:
    """Export a report as PDF.

    Args:
        project_id: Project UUID
        report_id: Report UUID
        db: Database session

    Returns:
        PDF file response

    Raises:
        HTTPException: If report not found or PDF conversion fails
    """
    # Get report
    report = db.query(Report).filter(
        Report.id == report_id,
        Report.project_id == project_id,
    ).first()

    if not report:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Report {report_id} not found",
        )

    if not report.content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Report has no content to export",
        )

    # Convert to PDF
    converter = PDFConverter()

    # Check if pandoc is available
    if not await converter.is_available():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF export not available (Pandoc not installed)",
        )

    try:
        pdf_content = await converter.convert(
            markdown=report.content,
            title=report.title,
        )
    except PDFConversionError as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"PDF conversion failed: {str(e)}",
        )

    # Generate filename
    filename = f"report_{report_id}.pdf"
    if report.title:
        # Sanitize title for filename
        safe_title = "".join(c for c in report.title if c.isalnum() or c in " -_")[:50]
        filename = f"{safe_title}.pdf"

    return Response(
        content=pdf_content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )
```

### 3. Add Pandoc to Docker image

**File**: `Dockerfile`

Add Pandoc installation:

```dockerfile
# Add after python base image and before COPY
RUN apt-get update && apt-get install -y --no-install-recommends \
    pandoc \
    texlive-xetex \
    texlive-fonts-recommended \
    && rm -rf /var/lib/apt/lists/*
```

### 4. Add PDF config settings

**File**: `src/config.py`

Add optional PDF settings:

```python
# PDF Export
pdf_enabled: bool = Field(
    default=True,
    description="Enable PDF export (requires Pandoc)",
)
pandoc_path: str = Field(
    default="pandoc",
    description="Path to Pandoc executable",
)
```

### 5. Write tests

**File**: `tests/test_pdf_export.py`

```python
"""Tests for PDF export functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from services.reports.pdf import PDFConverter, PDFConversionError


class TestPDFConverter:
    @pytest.mark.asyncio
    async def test_is_available_returns_true_when_pandoc_exists(self):
        """Should return True when pandoc is installed."""
        converter = PDFConverter()

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b""))
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            result = await converter.is_available()
            assert result is True

    @pytest.mark.asyncio
    async def test_is_available_returns_false_when_pandoc_missing(self):
        """Should return False when pandoc is not installed."""
        converter = PDFConverter()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await converter.is_available()
            assert result is False

    @pytest.mark.asyncio
    async def test_convert_calls_pandoc_with_correct_args(self):
        """Should call pandoc with markdown input."""
        converter = PDFConverter()
        markdown = "# Test Report\n\nContent here."

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b""))
            mock_process.returncode = 0
            mock_exec.return_value = mock_process

            with patch("pathlib.Path.read_bytes", return_value=b"%PDF-1.4"):
                with patch("pathlib.Path.unlink"):
                    result = await converter.convert(markdown, title="Test")

            # Verify pandoc was called
            mock_exec.assert_called_once()
            call_args = mock_exec.call_args[0]
            assert call_args[0] == "pandoc"
            assert "--pdf-engine=xelatex" in call_args

    @pytest.mark.asyncio
    async def test_convert_raises_on_pandoc_failure(self):
        """Should raise PDFConversionError on pandoc failure."""
        converter = PDFConverter()
        markdown = "# Test"

        with patch("asyncio.create_subprocess_exec") as mock_exec:
            mock_process = MagicMock()
            mock_process.communicate = AsyncMock(return_value=(b"", b"Error!"))
            mock_process.returncode = 1
            mock_exec.return_value = mock_process

            with patch("pathlib.Path.unlink"):
                with pytest.raises(PDFConversionError, match="Pandoc failed"):
                    await converter.convert(markdown)


class TestPDFExportEndpoint:
    @pytest.mark.asyncio
    async def test_export_pdf_returns_pdf_response(self, client):
        """Should return PDF with correct headers."""
        project_id = uuid4()
        report_id = uuid4()

        # Mock report in database
        mock_report = MagicMock()
        mock_report.id = report_id
        mock_report.project_id = project_id
        mock_report.content = "# Report\n\nContent"
        mock_report.title = "Test Report"

        with patch("api.v1.reports.PDFConverter") as MockConverter:
            mock_converter = MagicMock()
            mock_converter.is_available = AsyncMock(return_value=True)
            mock_converter.convert = AsyncMock(return_value=b"%PDF-1.4 test")
            MockConverter.return_value = mock_converter

            with patch("sqlalchemy.orm.Session.query") as mock_query:
                mock_query.return_value.filter.return_value.first.return_value = mock_report

                response = await client.get(
                    f"/api/v1/projects/{project_id}/reports/{report_id}/pdf",
                    headers={"X-API-Key": "test-key"},
                )

                assert response.status_code == 200
                assert response.headers["content-type"] == "application/pdf"
                assert "attachment" in response.headers.get("content-disposition", "")

    @pytest.mark.asyncio
    async def test_export_pdf_returns_404_when_not_found(self, client):
        """Should return 404 when report doesn't exist."""
        project_id = uuid4()
        report_id = uuid4()

        with patch("sqlalchemy.orm.Session.query") as mock_query:
            mock_query.return_value.filter.return_value.first.return_value = None

            response = await client.get(
                f"/api/v1/projects/{project_id}/reports/{report_id}/pdf",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_export_pdf_returns_503_when_pandoc_unavailable(self, client):
        """Should return 503 when Pandoc is not installed."""
        project_id = uuid4()
        report_id = uuid4()

        mock_report = MagicMock()
        mock_report.content = "# Report"

        with patch("api.v1.reports.PDFConverter") as MockConverter:
            mock_converter = MagicMock()
            mock_converter.is_available = AsyncMock(return_value=False)
            MockConverter.return_value = mock_converter

            with patch("sqlalchemy.orm.Session.query") as mock_query:
                mock_query.return_value.filter.return_value.first.return_value = mock_report

                response = await client.get(
                    f"/api/v1/projects/{project_id}/reports/{report_id}/pdf",
                    headers={"X-API-Key": "test-key"},
                )

                assert response.status_code == 503
```

## Constraints

- Do NOT modify existing report generation logic
- PDF export is optional - gracefully handle missing Pandoc
- Do NOT add heavy dependencies (use subprocess for Pandoc)
- Keep PDF styling simple (standard margins, readable font)
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_pdf_export.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/reports/pdf.py src/api/v1/reports.py src/config.py
```

## Verification

Before creating PR, run ONLY the scoped commands above:

1. `pytest tests/test_pdf_export.py -v` - Must pass
2. `ruff check src/services/reports/pdf.py src/api/v1/reports.py src/config.py` - Must be clean
3. All tasks above completed

## Definition of Done

- [ ] `src/services/reports/pdf.py` created with PDFConverter
- [ ] PDF export endpoint added to reports.py
- [ ] Pandoc added to Dockerfile
- [ ] Config settings added for PDF export
- [ ] Tests written and passing
- [ ] PR created with title: `feat: add PDF export for reports`
