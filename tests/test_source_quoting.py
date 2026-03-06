"""Tests for source quoting in extraction prompts and merge."""

from unittest.mock import MagicMock, Mock, patch

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
def llm_config():
    from config import LLMConfig
    return LLMConfig(
        base_url="http://localhost:9003/v1",
        embedding_base_url="http://localhost:9003/v1",
        api_key="test",
        model="test-model",
        embedding_model="bge-m3",
        embedding_dimension=1024,
        http_timeout=60,
        max_tokens=4096,
        max_retries=3,
        retry_backoff_min=2,
        retry_backoff_max=30,
        base_temperature=0.1,
        retry_temperature_increment=0.05,
    )


@pytest.fixture
def mock_extractor():
    from unittest.mock import AsyncMock, Mock
    extractor = Mock()
    extractor.extract_field_group = AsyncMock(return_value={"test": "data"})
    return extractor


class TestPromptQuoting:
    """Test that quoting instructions appear/disappear based on flag."""

    def test_non_entity_prompt_includes_quotes_when_enabled(self, llm_config):
            extractor = SchemaExtractor(llm_config, source_quoting=True)
            prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
            assert "_quotes" in prompt
            assert "verbatim excerpt" in prompt

    def test_non_entity_prompt_excludes_quotes_when_disabled(self, llm_config):
            extractor = SchemaExtractor(llm_config, source_quoting=False)
            prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
            assert "_quotes" not in prompt

    def test_entity_prompt_includes_quote_when_enabled(self, llm_config):
            extractor = SchemaExtractor(llm_config, source_quoting=True)
            prompt = extractor._build_entity_list_system_prompt(PRODUCTS_GROUP)
            assert "_quote" in prompt
            assert "verbatim excerpt" in prompt

    def test_entity_prompt_excludes_quote_when_disabled(self, llm_config):
            extractor = SchemaExtractor(llm_config, source_quoting=False)
            prompt = extractor._build_entity_list_system_prompt(PRODUCTS_GROUP)
            assert "_quote" not in prompt

    def test_entity_prompt_strict_quoting(self, llm_config):
            """strict_quoting=True adds stricter quoting instructions for entity lists."""
            extractor = SchemaExtractor(llm_config, source_quoting=True)
            prompt = extractor._build_entity_list_system_prompt(
                PRODUCTS_GROUP, strict_quoting=True
            )
            assert "CRITICAL QUOTING REQUIREMENT" in prompt
            assert "word-for-word" in prompt

    def test_entity_prompt_normal_quoting(self, llm_config):
            """strict_quoting=False uses standard quoting instructions."""
            extractor = SchemaExtractor(llm_config, source_quoting=True)
            prompt = extractor._build_entity_list_system_prompt(
                PRODUCTS_GROUP, strict_quoting=False
            )
            assert "CRITICAL QUOTING REQUIREMENT" not in prompt
            assert "verbatim excerpt" in prompt

    def test_strict_quoting_routed_to_entity_list(self, llm_config):
            """_build_system_prompt passes strict_quoting to entity list builder."""
            extractor = SchemaExtractor(llm_config, source_quoting=True)
            prompt = extractor._build_system_prompt(
                PRODUCTS_GROUP, strict_quoting=True
            )
            assert "CRITICAL QUOTING REQUIREMENT" in prompt


class TestQuoteMerge:
    """Test that quotes merge correctly in orchestrator."""

    @staticmethod
    def _make_config(*, quoting=True, conflicts=False):
        cfg = Mock()
        cfg.source_quoting_enabled = quoting
        cfg.conflict_detection_enabled = conflicts
        return cfg

    @pytest.fixture
    def orchestrator(self, mock_extractor):
        return SchemaExtractionOrchestrator(
            mock_extractor,
            extraction_config=self._make_config(quoting=True),
        )

    def test_quotes_merged_from_best_chunk(self, mock_extractor):
        """Quotes from higher-confidence chunk should win."""
        orch = SchemaExtractionOrchestrator(
            mock_extractor, extraction_config=self._make_config(quoting=True),
        )
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
        merged = orch._merge_chunk_results(chunk_results, MANUFACTURING_GROUP)
        assert merged.get("_quotes", {}).get("manufactures_gearboxes") == "we produce gearboxes"

    def test_missing_quotes_handled_gracefully(self, mock_extractor):
        """Chunks without _quotes should not cause errors."""
        orch = SchemaExtractionOrchestrator(
            mock_extractor, extraction_config=self._make_config(quoting=True),
        )
        chunk_results = [
            {"manufactures_gearboxes": True, "confidence": 0.8},
            {"manufactures_gearboxes": True, "confidence": 0.7},
        ]
        merged = orch._merge_chunk_results(chunk_results, MANUFACTURING_GROUP)
        # No error, and no _quotes key (since none were provided)
        assert "_quotes" not in merged

    def test_quotes_not_added_when_disabled(self, mock_extractor):
        """When quoting is disabled, no _quotes key should appear."""
        orch = SchemaExtractionOrchestrator(
            mock_extractor, extraction_config=self._make_config(quoting=False),
        )
        chunk_results = [
            {
                "manufactures_gearboxes": True,
                "confidence": 0.8,
                "_quotes": {"manufactures_gearboxes": "we produce gearboxes"},
            },
        ]
        merged = orch._merge_chunk_results(chunk_results, MANUFACTURING_GROUP)
        assert "_quotes" not in merged
