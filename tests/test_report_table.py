"""Tests for TABLE report generation with new source/domain grouping."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.reports.schema_table_generator import ColumnMetadata, SchemaTableGenerator
from services.reports.service import ReportData, ReportService
from services.reports.smart_merge import MergeCandidate, MergeResult, SmartMergeService


class TestSchemaTableGenerator:
    """Tests for SchemaTableGenerator column flattening."""

    @pytest.fixture
    def generator(self):
        return SchemaTableGenerator()

    @pytest.fixture
    def sample_schema(self):
        """Sample extraction schema with multiple field groups."""
        return {
            "name": "test_schema",
            "field_groups": [
                {
                    "name": "company_info",
                    "description": "Company details",
                    "prompt_hint": "Extract company info",
                    "fields": [
                        {"name": "company_name", "field_type": "text", "description": "Name"},
                        {"name": "employee_count", "field_type": "integer", "description": "Employees"},
                    ],
                },
                {
                    "name": "products",
                    "description": "Product list",
                    "prompt_hint": "Extract products",
                    "is_entity_list": True,
                    "fields": [
                        {"name": "product_name", "field_type": "text", "description": "Product"},
                        {"name": "price", "field_type": "float", "description": "Price"},
                    ],
                },
            ],
        }

    def test_get_flattened_columns_includes_metadata(self, generator, sample_schema):
        """Test that metadata columns are included at the start."""
        columns, labels, metadata = generator.get_flattened_columns_for_source(sample_schema)

        # Metadata columns first
        assert columns[0] == "source_url"
        assert columns[1] == "source_title"
        assert columns[2] == "domain"

        # Labels correct
        assert labels["source_url"] == "URL"
        assert labels["source_title"] == "Page Title"
        assert labels["domain"] == "Domain"

    def test_get_flattened_columns_includes_fields(self, generator, sample_schema):
        """Test that field group fields are flattened."""
        columns, labels, metadata = generator.get_flattened_columns_for_source(sample_schema)

        # Company info fields
        assert "company_name" in columns
        assert "employee_count" in columns

        # Products entity list becomes single column
        assert "products" in columns

        # Confidence at end
        assert columns[-1] == "avg_confidence"

    def test_get_flattened_columns_collision_detection(self, generator):
        """Test that colliding field names get prefixed."""
        schema = {
            "name": "collision_test",
            "field_groups": [
                {
                    "name": "group_a",
                    "description": "Group A",
                    "prompt_hint": "A",
                    "fields": [
                        {"name": "name", "field_type": "text", "description": "Name in A"},
                    ],
                },
                {
                    "name": "group_b",
                    "description": "Group B",
                    "prompt_hint": "B",
                    "fields": [
                        {"name": "name", "field_type": "text", "description": "Name in B"},
                    ],
                },
            ],
        }

        columns, labels, metadata = generator.get_flattened_columns_for_source(schema)

        # Both should be prefixed due to collision
        assert "group_a.name" in columns
        assert "group_b.name" in columns
        assert "name" not in columns  # Unprefixed should not exist

    def test_get_extraction_type_to_fields(self, generator, sample_schema):
        """Test mapping extraction types to field names."""
        mapping = generator.get_extraction_type_to_fields(sample_schema)

        assert "company_info" in mapping
        assert "products" in mapping

        assert "company_name" in mapping["company_info"]
        assert "employee_count" in mapping["company_info"]
        assert "products" in mapping["products"]  # Entity list maps to group name


class TestSmartMergeService:
    """Tests for LLM-based smart merge."""

    @pytest.fixture
    def mock_llm_client(self):
        client = MagicMock()
        client.complete = AsyncMock()
        return client

    @pytest.fixture
    def merge_service(self, mock_llm_client):
        return SmartMergeService(mock_llm_client, max_candidates=10, min_confidence=0.3)

    @pytest.fixture
    def column_meta(self):
        return ColumnMetadata(
            name="test_field",
            label="Test Field",
            field_type="text",
            description="A test field",
            field_group="test",
        )

    async def test_merge_all_null_returns_null(self, merge_service, column_meta):
        """Test that all null candidates returns null without LLM call."""
        candidates = [
            MergeCandidate(value=None, source_url="url1", source_title="T1", confidence=0.9),
            MergeCandidate(value=None, source_url="url2", source_title="T2", confidence=0.8),
        ]

        result = await merge_service.merge_column("test", column_meta, candidates)

        assert result.value is None
        assert result.confidence == 0.0
        assert "null" in result.reasoning.lower()

    async def test_merge_single_value_returns_it(self, merge_service, column_meta):
        """Test that single non-null value returns without LLM."""
        candidates = [
            MergeCandidate(value="only value", source_url="url1", source_title="T1", confidence=0.9),
            MergeCandidate(value=None, source_url="url2", source_title="T2", confidence=0.8),
        ]

        result = await merge_service.merge_column("test", column_meta, candidates)

        assert result.value == "only value"
        assert result.confidence == 0.9
        assert result.sources_used == ["url1"]

    async def test_merge_identical_values_no_llm(self, merge_service, column_meta):
        """Test that identical values merge without LLM call."""
        candidates = [
            MergeCandidate(value="same", source_url="url1", source_title="T1", confidence=0.9),
            MergeCandidate(value="same", source_url="url2", source_title="T2", confidence=0.8),
        ]

        result = await merge_service.merge_column("test", column_meta, candidates)

        assert result.value == "same"
        assert result.confidence > 0.85  # Boosted for agreement
        assert len(result.sources_used) == 2
        assert "agree" in result.reasoning.lower()

    async def test_merge_different_values_calls_llm(self, merge_service, column_meta, mock_llm_client):
        """Test that different values trigger LLM synthesis."""
        # LLMClient.complete() returns a parsed dict, not a string
        mock_llm_client.complete.return_value = {
            "value": "merged",
            "confidence": 0.9,
            "sources_used": ["url1"],
            "reasoning": "LLM decided",
        }

        candidates = [
            MergeCandidate(value="value A", source_url="url1", source_title="T1", confidence=0.9),
            MergeCandidate(value="value B", source_url="url2", source_title="T2", confidence=0.8),
        ]

        result = await merge_service.merge_column("test", column_meta, candidates)

        # LLM should have been called
        mock_llm_client.complete.assert_called_once()

        # Result should be from LLM
        assert result.value == "merged"
        assert result.reasoning == "LLM decided"

    async def test_merge_filters_low_confidence(self, merge_service, column_meta):
        """Test that low confidence candidates are filtered."""
        candidates = [
            MergeCandidate(value="good", source_url="url1", source_title="T1", confidence=0.9),
            MergeCandidate(value="low", source_url="url2", source_title="T2", confidence=0.1),  # Below 0.3
        ]

        result = await merge_service.merge_column("test", column_meta, candidates)

        # Should return the single good value
        assert result.value == "good"
        assert result.sources_used == ["url1"]

    async def test_merge_llm_error_fallback(self, merge_service, column_meta, mock_llm_client):
        """Test fallback to highest confidence on LLM error."""
        mock_llm_client.complete.side_effect = Exception("LLM failed")

        candidates = [
            MergeCandidate(value="low conf", source_url="url1", source_title="T1", confidence=0.7),
            MergeCandidate(value="high conf", source_url="url2", source_title="T2", confidence=0.95),
        ]

        result = await merge_service.merge_column("test", column_meta, candidates)

        # Should fall back to highest confidence
        assert result.value == "high conf"
        assert "failed" in result.reasoning.lower()


class TestMarkdownTable:
    """Tests for markdown table generation."""

    @pytest.fixture
    def report_service(self):
        return ReportService(
            extraction_repo=MagicMock(),
            entity_repo=MagicMock(),
            llm_client=MagicMock(),
            db_session=MagicMock(),
        )

    def test_build_markdown_table_basic(self, report_service):
        """Test basic markdown table generation."""
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

    def test_build_markdown_table_handles_newlines(self, report_service):
        """Test that newlines in values don't break markdown table."""
        rows = [
            {"source_group": "A", "fact": "Line 1.\nLine 2.\nLine 3."},
        ]
        columns = ["source_group", "fact"]

        result = report_service._build_markdown_table(rows, columns, "Test")

        # Should have newlines replaced with spaces
        assert "Line 1. Line 2. Line 3." in result

    def test_build_markdown_table_escapes_pipe_chars(self, report_service):
        """Test that pipe characters are escaped."""
        rows = [
            {"source_group": "A", "fact": "value | with | pipes"},
        ]
        columns = ["source_group", "fact"]

        result = report_service._build_markdown_table(rows, columns, None)

        # Pipes should be escaped as \|
        assert r"value \| with \| pipes" in result

    def test_build_markdown_table_sanitizes_list_items(self, report_service):
        """Test that list items with newlines and pipes are sanitized."""
        rows = [
            {"source_group": "A", "items": ["item1", "item2\nbroken", "a|b"]},
        ]
        columns = ["source_group", "items"]

        result = report_service._build_markdown_table(rows, columns, None)

        assert "item2 broken" in result
        assert r"a\|b" in result
