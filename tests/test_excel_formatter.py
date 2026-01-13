"""Tests for Excel formatter."""

from io import BytesIO

from openpyxl import load_workbook

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
