"""Tests for schema-based extraction."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.extraction.field_groups import MANUFACTURING_GROUP, PRODUCTS_GEARBOX_GROUP
from services.extraction.schema_extractor import SchemaExtractor


class TestSchemaExtractor:
    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
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
