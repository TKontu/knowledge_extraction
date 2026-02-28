"""Tests for source quoting in extraction prompts and merge."""

from unittest.mock import MagicMock, patch

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_extractor import SchemaExtractor
from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator


# Reusable fixtures
MANUFACTURING_GROUP = FieldGroup(
    name="manufacturing",
    description="Manufacturing capabilities",
    fields=[
        FieldDefinition(
            name="manufactures_gearboxes",
            field_type="boolean",
            description="Company manufactures gearboxes",
            default=False,
        ),
        FieldDefinition(
            name="manufactures_motors",
            field_type="boolean",
            description="Company manufactures motors",
            default=False,
        ),
    ],
    prompt_hint="Look for manufacturing evidence.",
)

PRODUCTS_GROUP = FieldGroup(
    name="products",
    description="Product list",
    fields=[
        FieldDefinition(
            name="product_name",
            field_type="text",
            description="Product name",
        ),
    ],
    prompt_hint="Extract products.",
    is_entity_list=True,
)


@pytest.fixture
def mock_settings():
    s = MagicMock()
    s.openai_base_url = "http://localhost:9003/v1"
    s.openai_api_key = "test"
    s.llm_http_timeout = 60
    s.llm_model = "test-model"
    s.llm_max_retries = 3
    s.llm_base_temperature = 0.1
    s.llm_retry_temperature_increment = 0.05
    s.llm_retry_backoff_min = 2
    s.llm_retry_backoff_max = 30
    s.llm_max_tokens = 4096
    return s


@pytest.fixture
def mock_extractor():
    from unittest.mock import AsyncMock, Mock
    extractor = Mock()
    extractor.extract_field_group = AsyncMock(return_value={"test": "data"})
    return extractor


class TestPromptQuoting:
    """Test that quoting instructions appear/disappear based on flag."""

    def test_non_entity_prompt_includes_quotes_when_enabled(self, mock_settings):
        with patch("services.extraction.schema_extractor.global_settings") as gs:
            gs.extraction_source_quoting_enabled = True
            extractor = SchemaExtractor(mock_settings)
            prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
            assert "_quotes" in prompt
            assert "verbatim excerpt" in prompt

    def test_non_entity_prompt_excludes_quotes_when_disabled(self, mock_settings):
        with patch("services.extraction.schema_extractor.global_settings") as gs:
            gs.extraction_source_quoting_enabled = False
            extractor = SchemaExtractor(mock_settings)
            prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
            assert "_quotes" not in prompt

    def test_entity_prompt_includes_quote_when_enabled(self, mock_settings):
        with patch("services.extraction.schema_extractor.global_settings") as gs:
            gs.extraction_source_quoting_enabled = True
            extractor = SchemaExtractor(mock_settings)
            prompt = extractor._build_entity_list_system_prompt(PRODUCTS_GROUP)
            assert "_quote" in prompt
            assert "verbatim excerpt" in prompt

    def test_entity_prompt_excludes_quote_when_disabled(self, mock_settings):
        with patch("services.extraction.schema_extractor.global_settings") as gs:
            gs.extraction_source_quoting_enabled = False
            extractor = SchemaExtractor(mock_settings)
            prompt = extractor._build_entity_list_system_prompt(PRODUCTS_GROUP)
            assert "_quote" not in prompt


class TestQuoteMerge:
    """Test that quotes merge correctly in orchestrator."""

    @pytest.fixture
    def orchestrator(self, mock_extractor):
        return SchemaExtractionOrchestrator(mock_extractor)

    def test_quotes_merged_from_best_chunk(self, orchestrator):
        """Quotes from higher-confidence chunk should win."""
        chunk_results = [
            {
                "manufactures_gearboxes": True,
                "confidence": 0.9,
                "_quotes": {"manufactures_gearboxes": "we produce gearboxes"},
            },
            {
                "manufactures_gearboxes": True,
                "confidence": 0.6,
                "_quotes": {"manufactures_gearboxes": "gearbox production"},
            },
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = True
            s.extraction_conflict_detection_enabled = False
            merged = orchestrator._merge_chunk_results(chunk_results, MANUFACTURING_GROUP)
        assert merged.get("_quotes", {}).get("manufactures_gearboxes") == "we produce gearboxes"

    def test_missing_quotes_handled_gracefully(self, orchestrator):
        """Chunks without _quotes should not cause errors."""
        chunk_results = [
            {"manufactures_gearboxes": True, "confidence": 0.8},
            {"manufactures_gearboxes": True, "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = True
            s.extraction_conflict_detection_enabled = False
            merged = orchestrator._merge_chunk_results(chunk_results, MANUFACTURING_GROUP)
        # No error, and no _quotes key (since none were provided)
        assert "_quotes" not in merged

    def test_quotes_not_added_when_disabled(self, orchestrator):
        """When quoting is disabled, no _quotes key should appear."""
        chunk_results = [
            {
                "manufactures_gearboxes": True,
                "confidence": 0.8,
                "_quotes": {"manufactures_gearboxes": "we produce gearboxes"},
            },
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = False
            merged = orchestrator._merge_chunk_results(chunk_results, MANUFACTURING_GROUP)
        assert "_quotes" not in merged
