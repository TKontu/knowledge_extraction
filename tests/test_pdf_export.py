"""Tests for PDF export functionality."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from fastapi.testclient import TestClient

from src.services.reports.pdf import PDFConverter, PDFConversionError

# Mock the scheduler to prevent startup issues in tests
with patch("src.main.start_scheduler", new_callable=AsyncMock):
    with patch("src.main.stop_scheduler", new_callable=AsyncMock):
        from src.database import get_db
        from src.main import app


@pytest.fixture
def mock_report():
    """Create a mock report object."""
    report = MagicMock()
    report.id = uuid4()
    report.project_id = uuid4()
    report.content = "# Report\n\nContent"
    report.title = "Test Report"
    return report


@pytest.fixture
def mock_db_session(mock_report):
    """Create a mock database session."""
    mock_session = MagicMock()
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = mock_report
    mock_session.query.return_value = mock_query
    return mock_session


@pytest.fixture
def pdf_client(mock_db_session, valid_api_key):
    """Create test client with mocked database."""
    def override_get_db():
        yield mock_db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


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


@pytest.mark.skip(reason="Integration test - requires running database and services")
class TestPDFExportEndpoint:
    def test_export_pdf_returns_pdf_response(self, pdf_client, mock_report, valid_api_key):
        """Should return PDF with correct headers."""
        with patch("src.api.v1.reports.PDFConverter") as MockConverter:
            mock_converter = MagicMock()
            mock_converter.is_available = AsyncMock(return_value=True)
            mock_converter.convert = AsyncMock(return_value=b"%PDF-1.4 test")
            MockConverter.return_value = mock_converter

            response = pdf_client.get(
                f"/api/v1/projects/{mock_report.project_id}/reports/{mock_report.id}/pdf",
                headers={"X-API-Key": valid_api_key},
            )

            assert response.status_code == 200
            assert response.headers["content-type"] == "application/pdf"
            assert "attachment" in response.headers.get("content-disposition", "")

    def test_export_pdf_returns_404_when_not_found(self, mock_db_session, valid_api_key):
        """Should return 404 when report doesn't exist."""
        # Override the fixture to return None
        mock_db_session.query.return_value.filter.return_value.first.return_value = None

        def override_get_db():
            yield mock_db_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        project_id = uuid4()
        report_id = uuid4()

        response = client.get(
            f"/api/v1/projects/{project_id}/reports/{report_id}/pdf",
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 404

        app.dependency_overrides.clear()

    def test_export_pdf_returns_503_when_pandoc_unavailable(self, pdf_client, mock_report, valid_api_key):
        """Should return 503 when Pandoc is not installed."""
        with patch("src.api.v1.reports.PDFConverter") as MockConverter:
            mock_converter = MagicMock()
            mock_converter.is_available = AsyncMock(return_value=False)
            MockConverter.return_value = mock_converter

            response = pdf_client.get(
                f"/api/v1/projects/{mock_report.project_id}/reports/{mock_report.id}/pdf",
                headers={"X-API-Key": valid_api_key},
            )

            assert response.status_code == 503
