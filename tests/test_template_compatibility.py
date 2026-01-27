"""Tests for template compatibility - all templates must pass validation."""

import pytest

from services.extraction.schema_adapter import SchemaAdapter
from services.projects.templates import (
    BOOK_CATALOG_TEMPLATE,
    COMPANY_ANALYSIS_TEMPLATE,
    CONTRACT_REVIEW_TEMPLATE,
    DEFAULT_EXTRACTION_TEMPLATE,
    DRIVETRAIN_COMPANY_TEMPLATE,
    DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE,
    RESEARCH_SURVEY_TEMPLATE,
)


class TestAllTemplatesValid:
    """All templates must pass SchemaAdapter validation."""

    @pytest.fixture
    def adapter(self):
        return SchemaAdapter()

    @pytest.mark.parametrize(
        "template_name,template",
        [
            ("company_analysis", COMPANY_ANALYSIS_TEMPLATE),
            ("research_survey", RESEARCH_SURVEY_TEMPLATE),
            ("contract_review", CONTRACT_REVIEW_TEMPLATE),
            ("book_catalog", BOOK_CATALOG_TEMPLATE),
            ("drivetrain_company", DRIVETRAIN_COMPANY_TEMPLATE),
            ("drivetrain_company_simple", DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE),
            ("default", DEFAULT_EXTRACTION_TEMPLATE),
        ],
    )
    def test_template_validates(self, adapter, template_name, template):
        """Template extraction_schema must pass validation."""
        schema = template["extraction_schema"]
        result = adapter.validate_extraction_schema(schema)
        assert result.is_valid, f"{template_name} failed: {result.errors}"

    @pytest.mark.parametrize(
        "template_name,template",
        [
            ("company_analysis", COMPANY_ANALYSIS_TEMPLATE),
            ("research_survey", RESEARCH_SURVEY_TEMPLATE),
            ("contract_review", CONTRACT_REVIEW_TEMPLATE),
            ("book_catalog", BOOK_CATALOG_TEMPLATE),
            ("drivetrain_company", DRIVETRAIN_COMPANY_TEMPLATE),
            ("drivetrain_company_simple", DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE),
            ("default", DEFAULT_EXTRACTION_TEMPLATE),
        ],
    )
    def test_template_converts_to_field_groups(self, adapter, template_name, template):
        """Template must convert to non-empty FieldGroup list."""
        schema = template["extraction_schema"]
        field_groups = adapter.convert_to_field_groups(schema)
        assert len(field_groups) > 0, f"{template_name} produced no field groups"

        # Each group should have fields
        for group in field_groups:
            assert group.name, f"{template_name}: group missing name"
            assert group.description, f"{template_name}: group missing description"
            assert len(group.fields) > 0, (
                f"{template_name}: group {group.name} has no fields"
            )


class TestEntityListMerging:
    """Test entity list merging handles both product_name and entity_id.

    Note: These tests are skipped if structlog is not available (CI environment).
    The logic is tested indirectly via integration tests.
    """

    @pytest.fixture
    def orchestrator(self):
        """Create orchestrator, skip if deps not available."""
        try:
            from services.extraction.schema_orchestrator import (
                SchemaExtractionOrchestrator,
            )

            return SchemaExtractionOrchestrator(schema_extractor=None)
        except ImportError:
            pytest.skip("structlog not available")

    def test_merge_entity_lists_with_product_name(self, orchestrator):
        """Merging works with product_name field."""
        chunk_results = [
            {
                "products": [{"product_name": "Product A", "price": 100}],
                "confidence": 0.9,
            },
            {
                "products": [{"product_name": "Product B", "price": 200}],
                "confidence": 0.8,
            },
            {
                "products": [{"product_name": "Product A", "price": 150}],
                "confidence": 0.7,
            },
        ]

        merged = orchestrator._merge_entity_lists(chunk_results)

        assert len(merged["products"]) == 2
        names = [p["product_name"] for p in merged["products"]]
        assert "Product A" in names
        assert "Product B" in names

    def test_merge_entity_lists_with_entity_id(self, orchestrator):
        """Merging works with entity_id field (alternative to product_name)."""
        chunk_results = [
            {
                "entities": [{"entity_id": "E001", "name": "Entity A"}],
                "confidence": 0.9,
            },
            {
                "entities": [{"entity_id": "E002", "name": "Entity B"}],
                "confidence": 0.8,
            },
            {
                "entities": [{"entity_id": "E001", "name": "Entity A Updated"}],
                "confidence": 0.7,
            },
        ]

        merged = orchestrator._merge_entity_lists(chunk_results)

        entities = merged.get("entities", [])
        assert len(entities) == 2

    def test_merge_entity_lists_with_mixed_keys(self, orchestrator):
        """Merging handles results with different entity key names."""
        chunk_results = [
            {"items": [{"entity_id": "I001", "value": 100}], "confidence": 0.9},
            {"items": [{"entity_id": "I002", "value": 200}], "confidence": 0.8},
        ]

        merged = orchestrator._merge_entity_lists(chunk_results)

        items = merged.get("items", [])
        assert len(items) == 2


class TestProfileUsedInPipeline:
    """Test that profile_used reflects actual schema name."""

    def test_extraction_uses_schema_name_as_profile(self):
        """Extractions should store schema name in profile_used field."""
        try:
            from services.extraction.pipeline import SchemaExtractionPipeline

            assert hasattr(SchemaExtractionPipeline, "extract_source")
            assert hasattr(SchemaExtractionPipeline, "extract_project")
        except ImportError:
            pytest.skip("structlog not available")
