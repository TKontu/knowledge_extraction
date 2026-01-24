"""Integration tests for template-driven extraction."""

import pytest
from uuid import uuid4

from services.extraction.schema_adapter import SchemaAdapter, ValidationResult
from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE


class TestSchemaAdapterIntegration:
    """Test SchemaAdapter with real templates."""

    def test_default_template_converts_to_field_groups(self):
        """Default template should convert to 3 field groups."""
        adapter = SchemaAdapter()
        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

        # Validate first
        result = adapter.validate_extraction_schema(schema)
        assert result.is_valid, f"Validation errors: {result.errors}"

        # Convert
        field_groups = adapter.convert_to_field_groups(schema)
        assert len(field_groups) == 3

        # Check group names
        group_names = [g.name for g in field_groups]
        assert "entity_info" in group_names
        assert "key_facts" in group_names
        assert "contact_info" in group_names

    def test_custom_schema_round_trip(self):
        """Custom schema should validate and convert correctly."""
        adapter = SchemaAdapter()
        custom_schema = {
            "name": "test_schema",
            "field_groups": [
                {
                    "name": "test_group",
                    "description": "Test field group",
                    "fields": [
                        {
                            "name": "test_field",
                            "field_type": "text",
                            "description": "A test field",
                            "required": True,
                            "default": "",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(custom_schema)
        assert result.is_valid

        field_groups = adapter.convert_to_field_groups(custom_schema)
        assert len(field_groups) == 1
        assert field_groups[0].name == "test_group"
        assert len(field_groups[0].fields) == 1
        assert field_groups[0].fields[0].name == "test_field"


class TestProjectCreationWithDefault:
    """Test project creation with default schema."""

    def test_project_create_model_applies_default(self):
        """ProjectCreate should apply default schema when None."""
        from models import ProjectCreate

        # Create without schema
        project = ProjectCreate(name="test_project")

        # Should have default schema
        assert project.extraction_schema is not None
        assert project.extraction_schema.get("name") == "generic_facts"
        assert len(project.extraction_schema.get("field_groups", [])) == 3
