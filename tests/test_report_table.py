"""Tests for TABLE report generation."""

from unittest.mock import MagicMock

import pytest

from services.reports.service import ReportData, ReportService


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
                    {
                        "id": "ext-1",
                        "data": {"has_feature": True, "count": 100},
                        "confidence": 0.9,
                    },
                    {
                        "id": "ext-2",
                        "data": {"has_feature": True, "count": 150},
                        "confidence": 0.8,
                    },
                ],
                "CompanyB": [
                    {
                        "id": "ext-3",
                        "data": {"has_feature": False, "count": 50},
                        "confidence": 0.95,
                    },
                ],
            },
            entities_by_group={},
            source_groups=["CompanyA", "CompanyB"],
            extraction_ids=["ext-1", "ext-2", "ext-3"],
            entity_count=0,
        )

    async def test_aggregate_for_table_boolean_any(self, report_service, sample_data):
        """Test boolean aggregation uses any() - True if any extraction is True."""
        rows, columns, labels = await report_service._aggregate_for_table(
            sample_data, None
        )

        assert len(rows) == 2
        company_a = next(r for r in rows if r["source_group"] == "CompanyA")
        assert company_a["has_feature"] is True  # Both True -> True

    async def test_aggregate_for_table_numeric_max(self, report_service, sample_data):
        """Test numeric aggregation uses max value."""
        rows, columns, labels = await report_service._aggregate_for_table(
            sample_data, None
        )

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
