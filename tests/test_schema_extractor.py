"""Tests for schema-based extraction."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_extractor import (
    EXTRACTION_CONTENT_LIMIT,
    SchemaExtractor,
)
from services.llm import worker as llm_worker

# Test fixtures for field groups (mimics schema-driven groups)
MANUFACTURING_GROUP = FieldGroup(
    name="manufacturing",
    description="Manufacturing capabilities",
    fields=[
        FieldDefinition(
            name="manufactures_gearboxes",
            field_type="boolean",
            description="Company manufactures gearboxes",
            required=True,
            default=False,
        ),
        FieldDefinition(
            name="manufactures_motors",
            field_type="boolean",
            description="Company manufactures motors",
            required=True,
            default=False,
        ),
    ],
    prompt_hint="Look for manufacturing evidence.",
)

PRODUCTS_GEARBOX_GROUP = FieldGroup(
    name="products_gearbox",
    description="Gearbox products",
    fields=[
        FieldDefinition(
            name="product_name",
            field_type="text",
            description="Product name",
            required=True,
        ),
        FieldDefinition(
            name="power_rating_kw",
            field_type="float",
            description="Power rating in kW",
        ),
    ],
    prompt_hint="Extract gearbox products.",
    is_entity_list=True,
)


class TestSchemaExtractor:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        # Retry settings
        settings.llm_max_retries = 3
        settings.llm_base_temperature = 0.1
        settings.llm_retry_temperature_increment = 0.05
        settings.llm_retry_backoff_min = 2
        settings.llm_retry_backoff_max = 30
        settings.llm_max_tokens = 4096
        return settings

    async def test_extract_manufacturing_booleans(self, mock_settings):
        """Test extraction of boolean manufacturing fields."""
        extractor = SchemaExtractor(mock_settings)

        # Mock the OpenAI response
        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content='{"manufactures_gearboxes": true, "manufactures_motors": false}'
                        )
                    )
                ]
            )
        )

        result = await extractor.extract_field_group(
            content="We manufacture planetary gearboxes.",
            field_group=MANUFACTURING_GROUP,
            company_name="Test Company",
        )

        assert result["manufactures_gearboxes"] is True
        assert result["manufactures_motors"] is False

    async def test_extract_product_list(self, mock_settings):
        """Test extraction of product entity list."""
        extractor = SchemaExtractor(mock_settings)

        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content='{"products": [{"product_name": "D Series", "power_rating_kw": 100}]}'
                        )
                    )
                ]
            )
        )

        result = await extractor.extract_field_group(
            content="Our D Series gearbox offers 100kW.",
            field_group=PRODUCTS_GEARBOX_GROUP,
        )

        assert len(result["products"]) == 1
        assert result["products"][0]["product_name"] == "D Series"

    async def test_entity_list_truncation_returns_empty(self, mock_settings):
        """Test that truncated entity list returns empty list instead of error."""
        extractor = SchemaExtractor(mock_settings)

        # Simulate truncated JSON response (finish_reason="length")
        truncated_json = '{"products_gearbox": [{"product_name": "Prod1"}, {"product_name": "Prod2'
        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(content=truncated_json),
                        finish_reason="length",  # Truncation indicator
                    )
                ]
            )
        )

        result = await extractor.extract_field_group(
            content="We have many products...",
            field_group=PRODUCTS_GEARBOX_GROUP,
        )

        # Should return empty list rather than raising error
        assert result["products_gearbox"] == []
        assert result["confidence"] == 0.0

    async def test_non_entity_truncation_attempts_repair(self, mock_settings):
        """Test that truncated non-entity extraction attempts JSON repair."""
        extractor = SchemaExtractor(mock_settings)

        # Repairable truncated JSON for non-entity group
        truncated_json = '{"manufactures_gearboxes": true, "manufactures_motors": false'
        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(content=truncated_json),
                        finish_reason="length",
                    )
                ]
            )
        )

        result = await extractor.extract_field_group(
            content="We manufacture gearboxes.",
            field_group=MANUFACTURING_GROUP,
        )

        # Should repair JSON and return result
        assert result["manufactures_gearboxes"] is True
        assert result["manufactures_motors"] is False

    async def test_normal_completion_no_truncation_flag(self, mock_settings):
        """Test that normal completion (finish_reason=stop) works correctly."""
        extractor = SchemaExtractor(mock_settings)

        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content='{"manufactures_gearboxes": true, "manufactures_motors": true}'
                        ),
                        finish_reason="stop",  # Normal completion
                    )
                ]
            )
        )

        result = await extractor.extract_field_group(
            content="We manufacture gearboxes and motors.",
            field_group=MANUFACTURING_GROUP,
        )

        assert result["manufactures_gearboxes"] is True
        assert result["manufactures_motors"] is True


class TestExtractionContentLimit:
    """Test Phase 2B: content window expansion."""

    def test_constant_value(self):
        """EXTRACTION_CONTENT_LIMIT should be 20000."""
        assert EXTRACTION_CONTENT_LIMIT == 20000

    def test_worker_imports_from_schema_extractor(self):
        """Worker should use the same constant from schema_extractor."""
        assert llm_worker.EXTRACTION_CONTENT_LIMIT is EXTRACTION_CONTENT_LIMIT


class TestPromptGrounding:
    """Test Phase 2A: grounding rules in prompts."""

    @pytest.fixture
    def extractor(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_max_retries = 3
        settings.llm_base_temperature = 0.1
        settings.llm_retry_temperature_increment = 0.05
        settings.llm_retry_backoff_min = 2
        settings.llm_retry_backoff_max = 30
        settings.llm_max_tokens = 4096
        return SchemaExtractor(settings)

    def test_non_entity_prompt_has_grounding(self, extractor):
        """Non-entity system prompt should contain grounding rules."""
        prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
        assert "Do NOT use outside knowledge" in prompt
        assert "Extract ONLY from the content" in prompt

    def test_non_entity_prompt_has_confidence_guidance(self, extractor):
        """Non-entity system prompt should instruct confidence scoring."""
        prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
        assert '"confidence"' in prompt
        assert "0.0" in prompt
        assert "0.8-1.0" in prompt

    def test_non_entity_prompt_has_field_group_relevance_gate(self, extractor):
        """Non-entity system prompt should gate on content relevance."""
        prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
        assert "not relevant to Manufacturing capabilities" in prompt
        assert "null for ALL fields" in prompt

    def test_non_entity_prompt_includes_prompt_hint(self, extractor):
        """Non-entity system prompt should include prompt_hint."""
        prompt = extractor._build_system_prompt(MANUFACTURING_GROUP)
        assert "Look for manufacturing evidence." in prompt

    def test_entity_list_prompt_has_grounding(self, extractor):
        """Entity-list system prompt should contain grounding rules."""
        prompt = extractor._build_system_prompt(PRODUCTS_GEARBOX_GROUP)
        assert "Do NOT use outside knowledge" in prompt
        assert "Extract ONLY from the content" in prompt

    def test_entity_list_prompt_no_domain_specific_lines(self, extractor):
        """Entity-list prompt should NOT have domain-specific guidance."""
        prompt = extractor._build_system_prompt(PRODUCTS_GEARBOX_GROUP)
        assert "For locations:" not in prompt
        assert "For products:" not in prompt
        assert "manufacturing sites" not in prompt

    def test_entity_list_prompt_has_empty_list_guidance(self, extractor):
        """Entity-list prompt should instruct empty list on no content."""
        prompt = extractor._build_system_prompt(PRODUCTS_GEARBOX_GROUP)
        assert "return an empty list" in prompt

    def test_entity_list_prompt_keeps_max_items(self, extractor):
        """Entity-list prompt should still have max 20 items rule."""
        prompt = extractor._build_system_prompt(PRODUCTS_GEARBOX_GROUP)
        assert "max 20 items" in prompt


class TestUserPromptCleaning:
    """Test Phase 2C: content cleaning before extraction."""

    @pytest.fixture
    def extractor(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_max_retries = 3
        settings.llm_base_temperature = 0.1
        settings.llm_retry_temperature_increment = 0.05
        settings.llm_retry_backoff_min = 2
        settings.llm_retry_backoff_max = 30
        settings.llm_max_tokens = 4096
        return SchemaExtractor(settings)

    def test_user_prompt_strips_structural_junk(self, extractor):
        """User prompt should have bare nav links removed."""
        content = "* [Home](/home)\n* [About](/about)\n\n# Real Content\n\nActual data here."
        prompt = extractor._build_user_prompt(content, MANUFACTURING_GROUP, "Test Co")
        assert "* [Home](/home)" not in prompt
        assert "* [About](/about)" not in prompt
        assert "Real Content" in prompt
        assert "Actual data here." in prompt

    def test_user_prompt_strips_empty_alt_images(self, extractor):
        """User prompt should have empty-alt images removed."""
        content = "![](https://example.com/logo.png)\n\n# Content\n\nReal info."
        prompt = extractor._build_user_prompt(content, MANUFACTURING_GROUP, None)
        assert "![](https://example.com/logo.png)" not in prompt
        assert "Real info." in prompt

    def test_user_prompt_preserves_real_content(self, extractor):
        """User prompt should preserve all non-junk content."""
        content = "# Gearbox Products\n\nWe manufacture planetary gearboxes rated at 100kW."
        prompt = extractor._build_user_prompt(content, MANUFACTURING_GROUP, "Acme Corp")
        assert "Gearbox Products" in prompt
        assert "planetary gearboxes" in prompt
        assert "100kW" in prompt

    def test_user_prompt_truncates_at_limit(self, extractor):
        """User prompt should truncate cleaned content at EXTRACTION_CONTENT_LIMIT."""
        # Content longer than limit (no junk, so no cleaning shrinkage)
        long_content = "x" * 25000
        prompt = extractor._build_user_prompt(long_content, MANUFACTURING_GROUP, None)
        # The content between --- markers should be at most EXTRACTION_CONTENT_LIMIT
        content_section = prompt.split("---")[1]
        assert len(content_section.strip()) == EXTRACTION_CONTENT_LIMIT

    def test_user_prompt_cleaning_before_truncation(self, extractor):
        """Cleaning should happen before truncation to reclaim window space."""
        # 1000 chars of junk + 19500 chars of real content = 20500 total
        # Without cleaning: truncated at 20000, losing 500 chars of real content
        # With cleaning: junk removed first, all 19500 chars of real content fit
        junk = "* [Nav1](/nav1)\n" * 67  # ~1072 chars of bare nav links
        real = "A" * 19500
        content = junk + real
        prompt = extractor._build_user_prompt(content, MANUFACTURING_GROUP, None)
        # All real content should be present (junk removed, then truncation at 20K)
        assert "A" * 19500 in prompt

    def test_user_prompt_includes_source_context(self, extractor):
        """User prompt should include source context when provided."""
        prompt = extractor._build_user_prompt(
            "content", MANUFACTURING_GROUP, "Test Company"
        )
        assert "Test Company" in prompt

    def test_user_prompt_grounding_language(self, extractor):
        """User prompt should say 'ONLY the content below'."""
        prompt = extractor._build_user_prompt(
            "content", MANUFACTURING_GROUP, None
        )
        assert "ONLY the content below" in prompt
