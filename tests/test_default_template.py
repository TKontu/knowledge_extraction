"""Tests for DEFAULT_EXTRACTION_TEMPLATE."""

from services.extraction.schema_adapter import SchemaAdapter


class TestDefaultTemplate:
    """Test the default extraction template."""

    def test_default_template_exists(self):
        """Import should succeed."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        assert DEFAULT_EXTRACTION_TEMPLATE is not None
        assert "extraction_schema" in DEFAULT_EXTRACTION_TEMPLATE

    def test_default_template_has_3_field_groups(self):
        """Exactly 3 field groups."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]
        assert len(schema["field_groups"]) == 3

    def test_default_template_passes_validation(self):
        """Use SchemaAdapter to validate."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        adapter = SchemaAdapter()
        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]
        result = adapter.validate_extraction_schema(schema)

        assert result.is_valid, f"Validation errors: {result.errors}"
        assert len(result.errors) == 0

    def test_default_template_field_types_valid(self):
        """All field_type values are valid."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]
        valid_types = {"boolean", "integer", "float", "text", "list", "enum"}

        for fg in schema["field_groups"]:
            for field in fg["fields"]:
                assert field["field_type"] in valid_types, (
                    f"Invalid field_type '{field['field_type']}' "
                    f"in field '{field['name']}'"
                )

    def test_default_template_has_expected_name(self):
        """Schema name is 'generic_facts'."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]
        assert schema["name"] == "generic_facts"

    def test_default_template_has_expected_groups(self):
        """Expected group names are present."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]
        group_names = [g["name"] for g in schema["field_groups"]]

        assert "entity_info" in group_names
        assert "key_facts" in group_names
        assert "contact_info" in group_names

    def test_default_template_converts_to_field_groups(self):
        """Can convert to FieldGroup objects."""
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

        adapter = SchemaAdapter()
        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

        field_groups = adapter.convert_to_field_groups(schema)
        assert len(field_groups) == 3
        assert all(hasattr(g, "name") for g in field_groups)
        assert all(hasattr(g, "fields") for g in field_groups)
