"""Tests for schema-based extraction."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_extractor import SchemaExtractor

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
