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
                    {"id": "ext-1", "data": {"has_feature": True, "count": 100}, "confidence": 0.9},
                    {"id": "ext-2", "data": {"has_feature": True, "count": 150}, "confidence": 0.8},
                ],
                "CompanyB": [
                    {"id": "ext-3", "data": {"has_feature": False, "count": 50}, "confidence": 0.95},
                ],
            },
            entities_by_group={},
            source_groups=["CompanyA", "CompanyB"],
            extraction_ids=["ext-1", "ext-2", "ext-3"],
            entity_count=0,
        )

    async def test_aggregate_for_table_boolean_any(
        self, report_service, sample_data
    ):
        """Test boolean aggregation uses any() - True if any extraction is True."""
        rows, columns, labels = report_service._aggregate_for_table(sample_data, None)

        assert len(rows) == 2
        company_a = next(r for r in rows if r["source_group"] == "CompanyA")
        assert company_a["has_feature"] is True  # Both True -> True

    async def test_aggregate_for_table_numeric_max(self, report_service, sample_data):
        """Test numeric aggregation uses max value."""
        rows, columns, labels = report_service._aggregate_for_table(sample_data, None)

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
        md, excel = report_service._generate_table_report(
            data=sample_data,
            title="Test Report",
            columns=None,
            output_format="xlsx",
        )

        assert excel is not None
        assert isinstance(excel, bytes)
        assert md is not None  # Markdown also generated

    async def test_aggregate_for_table_group_by_extraction(self, report_service):
        """Test group_by='extraction' creates one row per extraction."""
        data = ReportData(
            extractions_by_group={
                "CompanyA": [
                    {
                        "id": "ext-1",
                        "data": {"fact": "Fact A1", "category": "info"},
                        "confidence": 0.9,
                        "source_uri": "https://a.com/page1",
                        "source_title": "Page 1",
                    },
                    {
                        "id": "ext-2",
                        "data": {"fact": "Fact A2", "category": "info"},
                        "confidence": 0.85,
                        "source_uri": "https://a.com/page2",
                        "source_title": "Page 2",
                    },
                ],
                "CompanyB": [
                    {
                        "id": "ext-3",
                        "data": {"fact": "Fact B1", "category": "info"},
                        "confidence": 0.95,
                        "source_uri": "https://b.com/page1",
                        "source_title": "B Page",
                    },
                ],
            },
            entities_by_group={},
            source_groups=["CompanyA", "CompanyB"],
            extraction_ids=["ext-1", "ext-2", "ext-3"],
            entity_count=0,
        )

        rows, columns, labels = report_service._aggregate_for_table(
            data, None, extraction_schema=None, group_by="extraction"
        )

        # Should have 3 rows (one per extraction)
        assert len(rows) == 3

        # Check that each row has source metadata
        for row in rows:
            assert "source_group" in row
            assert "source_url" in row
            assert "source_title" in row
            assert "confidence" in row

        # Check specific values
        row_a1 = rows[0]
        assert row_a1["source_group"] == "CompanyA"
        assert row_a1["source_url"] == "https://a.com/page1"
        assert row_a1["source_title"] == "Page 1"
        assert row_a1["fact"] == "Fact A1"
        assert row_a1["confidence"] == 0.9

        # Check columns include metadata
        assert "source_group" in columns
        assert "source_url" in columns
        assert "source_title" in columns
        assert "confidence" in columns
        assert "fact" in columns

        # Check labels
        assert labels["source_url"] == "URL"
        assert labels["source_title"] == "Title"
        assert labels["confidence"] == "Confidence"

    async def test_aggregate_for_table_group_by_source_group_default(
        self, report_service, sample_data
    ):
        """Test that default group_by='source_group' aggregates extractions."""
        rows, columns, labels = report_service._aggregate_for_table(
            sample_data, None, extraction_schema=None, group_by="source_group"
        )

        # Should have 2 rows (one per source_group)
        assert len(rows) == 2
        assert rows[0]["source_group"] == "CompanyA"
        assert rows[1]["source_group"] == "CompanyB"

        # Should NOT have source_url or source_title columns
        assert "source_url" not in columns
        assert "source_title" not in columns

    async def test_aggregate_for_table_confidence_column_at_end(self, report_service):
        """Test that confidence column appears at the end in extraction mode."""
        data = ReportData(
            extractions_by_group={
                "CompanyA": [
                    {
                        "id": "ext-1",
                        "data": {"zebra": "Z", "apple": "A", "banana": "B"},
                        "confidence": 0.9,
                        "source_uri": "https://a.com",
                        "source_title": "Page",
                    },
                ],
            },
            entities_by_group={},
            source_groups=["CompanyA"],
            extraction_ids=["ext-1"],
            entity_count=0,
        )

        rows, columns, labels = report_service._aggregate_for_table(
            data, None, extraction_schema=None, group_by="extraction"
        )

        # Confidence should be the last column
        assert columns[-1] == "confidence"
        # Data columns should be alphabetical before confidence
        data_cols = [c for c in columns if c not in ["source_group", "source_url", "source_title", "confidence"]]
        assert data_cols == ["apple", "banana", "zebra"]

    async def test_build_markdown_table_handles_newlines(self, report_service):
        """Test that newlines in values don't break markdown table."""
        rows = [
            {"source_group": "A", "fact": "Line 1.\nLine 2.\nLine 3."},
        ]
        columns = ["source_group", "fact"]

        result = report_service._build_markdown_table(rows, columns, "Test")

        # Should have newlines replaced with spaces
        assert "Line 1. Line 2. Line 3." in result
        # Should not have broken table structure (no bare newlines in data)
        lines = result.split("\n")
        for line in lines:
            if line.startswith("|") and "Line" in line:
                # This data row should be a single line
                assert line.count("|") == 3  # | col1 | col2 |

    async def test_build_markdown_table_escapes_pipe_chars(self, report_service):
        """Test that pipe characters are escaped to preserve table structure."""
        rows = [
            {"source_group": "A", "fact": "value | with | pipes"},
        ]
        columns = ["source_group", "fact"]

        result = report_service._build_markdown_table(rows, columns, None)

        # Pipes should be escaped as \|
        assert r"value \| with \| pipes" in result
        # Data row should have exactly 3 pipe separators (| col1 | col2 |)
        data_line = [line for line in result.split("\n") if "value" in line][0]
        # Count unescaped pipes only
        unescaped_pipes = data_line.replace(r"\|", "").count("|")
        assert unescaped_pipes == 3

    async def test_build_markdown_table_sanitizes_list_items(self, report_service):
        """Test that list items with newlines and pipes are sanitized."""
        rows = [
            {"source_group": "A", "items": ["item1", "item2\nbroken", "a|b"]},
        ]
        columns = ["source_group", "items"]

        result = report_service._build_markdown_table(rows, columns, None)

        # Newlines in list items should be replaced with spaces
        assert "item2 broken" in result
        # Pipes in list items should be escaped
        assert r"a\|b" in result
        # Table structure should be intact (single data row)
        data_lines = [
            line for line in result.split("\n")
            if line.startswith("|") and "item" in line
        ]
        assert len(data_lines) == 1
