"""Tests for pipeline integration with ExtractionContext."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from services.extraction.schema_adapter import SchemaAdapter


class TestPipelineExtractSource:
    """Test that pipeline's extract_source uses source_context parameter."""

    @pytest.fixture
    def mock_db(self):
        """Mock database session."""
        db = MagicMock()
        db.add = MagicMock()
        db.flush = MagicMock()
        return db

    @pytest.fixture
    async def mock_orchestrator_with_context(self):
        """Mock orchestrator with context."""
        orchestrator = MagicMock()
        orchestrator.extract_all_groups = AsyncMock(
            return_value=[
                {
                    "extraction_type": "test_group",
                    "data": {"field1": "value1"},
                    "confidence": 0.9,
                }
            ]
        )
        return orchestrator

    @pytest.fixture
    def mock_source(self):
        """Mock source object."""
        source = MagicMock()
        source.id = uuid4()
        source.project_id = uuid4()
        source.content = "Test content"
        return source

    @pytest.mark.asyncio
    async def test_extract_source_accepts_source_context(
        self, mock_orchestrator_with_context, mock_db, mock_source
    ):
        """extract_source should accept source_context parameter."""
        from services.extraction.field_groups import FieldDefinition, FieldGroup
        from services.extraction.pipeline import SchemaExtractionPipeline

        pipeline = SchemaExtractionPipeline(mock_orchestrator_with_context, mock_db)

        field_groups = [
            FieldGroup(
                name="test_group",
                description="Test group",
                fields=[
                    FieldDefinition(
                        name="field1",
                        field_type="text",
                        description="Test field",
                    ),
                ],
                prompt_hint="Test",
                is_entity_list=False,
            )
        ]

        # Should accept source_context parameter
        extractions = await pipeline.extract_source(
            source=mock_source,
            source_context="Test Company",
            field_groups=field_groups,
            schema_name="test_schema",
        )

        # Orchestrator should be called with source_context
        mock_orchestrator_with_context.extract_all_groups.assert_called_once()
        call_args = mock_orchestrator_with_context.extract_all_groups.call_args
        assert call_args.kwargs.get("source_context") == "Test Company"

    @pytest.mark.asyncio
    async def test_extract_source_backward_compatible_company_name(
        self, mock_orchestrator_with_context, mock_db, mock_source
    ):
        """extract_source should still accept company_name for backward compatibility."""
        from services.extraction.field_groups import FieldDefinition, FieldGroup
        from services.extraction.pipeline import SchemaExtractionPipeline

        pipeline = SchemaExtractionPipeline(mock_orchestrator_with_context, mock_db)

        field_groups = [
            FieldGroup(
                name="test_group",
                description="Test group",
                fields=[
                    FieldDefinition(
                        name="field1",
                        field_type="text",
                        description="Test field",
                    ),
                ],
                prompt_hint="Test",
                is_entity_list=False,
            )
        ]

        # Should still accept company_name parameter
        extractions = await pipeline.extract_source(
            source=mock_source,
            company_name="Test Company",
            field_groups=field_groups,
            schema_name="test_schema",
        )

        # Orchestrator should be called with source_context
        mock_orchestrator_with_context.extract_all_groups.assert_called_once()


class TestParseTemplateInPipeline:
    """Test that pipeline uses parse_template to get context."""

    def test_adapter_parse_template_integration(self):
        """Adapter should parse full template with context."""
        adapter = SchemaAdapter()

        template = {
            "name": "test_template",
            "extraction_context": {
                "source_type": "recipe blog",
                "source_label": "Recipe Site",
                "entity_id_fields": ["recipe_name"],
            },
            "extraction_schema": {
                "name": "recipes",
                "field_groups": [
                    {
                        "name": "recipes",
                        "description": "Recipe information",
                        "fields": [
                            {
                                "name": "recipe_name",
                                "field_type": "text",
                                "description": "Recipe name",
                            },
                        ],
                    },
                ],
            },
        }

        field_groups, context = adapter.parse_template(template)

        assert len(field_groups) == 1
        assert context.source_type == "recipe blog"
        assert context.source_label == "Recipe Site"
        assert context.entity_id_fields == ["recipe_name"]
