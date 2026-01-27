"""Tests for template-driven prompting and extraction adaptation."""

import pytest


class TestSchemaAdapterPromptHints:
    """Test that prompt hints are generated properly."""

    @pytest.fixture
    def adapter(self):
        from services.extraction.schema_adapter import SchemaAdapter

        return SchemaAdapter()

    def test_generate_prompt_hint_includes_field_info(self, adapter):
        """Auto-generated hint should include field information."""
        field_group_def = {
            "name": "company_facts",
            "description": "Key facts about the company",
            "fields": [
                {
                    "name": "revenue",
                    "field_type": "text",
                    "description": "Annual revenue",
                },
                {
                    "name": "founded",
                    "field_type": "integer",
                    "description": "Year founded",
                },
                {
                    "name": "public",
                    "field_type": "boolean",
                    "description": "Is publicly traded",
                },
            ],
        }

        hint = adapter.generate_prompt_hint(field_group_def)

        # Should mention what to extract
        assert "company" in hint.lower() or "facts" in hint.lower()
        # Should mention key fields or their types
        assert len(hint) > 30  # Not just a trivial hint

    def test_generate_prompt_hint_for_entity_list(self, adapter):
        """Entity list groups should get entity-specific hints."""
        field_group_def = {
            "name": "products_list",
            "description": "Product catalog items",
            "is_entity_list": True,
            "fields": [
                {
                    "name": "product_name",
                    "field_type": "text",
                    "description": "Product name",
                },
                {"name": "price", "field_type": "float", "description": "Price in USD"},
            ],
        }

        hint = adapter.generate_prompt_hint(field_group_def)

        # Should mention it's a list/multiple items
        assert (
            "each" in hint.lower()
            or "list" in hint.lower()
            or "multiple" in hint.lower()
        )

    def test_explicit_prompt_hint_preserved(self, adapter):
        """Explicit prompt_hint in schema should be used as-is."""
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "test_group",
                    "description": "Test group",
                    "prompt_hint": "CUSTOM HINT: Look for specific patterns XYZ",
                    "fields": [
                        {"name": "f1", "field_type": "text", "description": "Field 1"},
                    ],
                },
            ],
        }

        field_groups = adapter.convert_to_field_groups(schema)

        assert (
            field_groups[0].prompt_hint == "CUSTOM HINT: Look for specific patterns XYZ"
        )


class TestEntityListOutputKey:
    """Test that entity list extraction uses correct output key."""

    @pytest.fixture
    def extractor_prompt_builder(self):
        """Get the prompt builder without full extractor deps."""
        try:
            from services.extraction.field_groups import FieldDefinition, FieldGroup
            from services.extraction.schema_extractor import SchemaExtractor

            # Create minimal mock settings
            class MockSettings:
                llm_model = "test"
                openai_base_url = "http://test"
                openai_api_key = "test"
                llm_http_timeout = 30

            extractor = SchemaExtractor(MockSettings(), llm_queue=None)
            return extractor, FieldGroup, FieldDefinition
        except ImportError:
            pytest.skip("Dependencies not available")

    def test_entity_list_prompt_uses_group_name(self, extractor_prompt_builder):
        """Entity list prompt should use group name as output key."""
        extractor, FieldGroup, FieldDefinition = extractor_prompt_builder

        group = FieldGroup(
            name="employees",  # NOT "products"
            description="Employee directory",
            fields=[
                FieldDefinition(
                    name="employee_id",
                    field_type="text",
                    description="Employee ID",
                    required=True,
                ),
                FieldDefinition(
                    name="name",
                    field_type="text",
                    description="Full name",
                ),
            ],
            prompt_hint="Extract employee records",
            is_entity_list=True,
        )

        prompt = extractor._build_entity_list_system_prompt(group)

        # Should use "employees" not "products"
        assert '"employees"' in prompt or "'employees'" in prompt
        assert '"products"' not in prompt

    def test_entity_list_prompt_uses_entity_id_field(self, extractor_prompt_builder):
        """Entity list with entity_id field should reference it in prompt."""
        extractor, FieldGroup, FieldDefinition = extractor_prompt_builder

        group = FieldGroup(
            name="locations",
            description="Office locations",
            fields=[
                FieldDefinition(
                    name="entity_id",
                    field_type="text",
                    description="Location ID",
                    required=True,
                ),
                FieldDefinition(
                    name="city",
                    field_type="text",
                    description="City name",
                ),
            ],
            prompt_hint="Extract office locations",
            is_entity_list=True,
        )

        prompt = extractor._build_entity_list_system_prompt(group)

        # Should reference entity_id field
        assert "entity_id" in prompt
        assert '"locations"' in prompt or "'locations'" in prompt


class TestLegacyFieldGroupsRemoval:
    """Test that legacy ALL_FIELD_GROUPS is not used."""

    def test_all_field_groups_not_imported_in_orchestrator(self):
        """SchemaOrchestrator should not import ALL_FIELD_GROUPS."""
        import inspect

        try:
            from services.extraction import schema_orchestrator

            source = inspect.getsource(schema_orchestrator)

            # Should not have ALL_FIELD_GROUPS import
            assert "ALL_FIELD_GROUPS" not in source
        except ImportError:
            pytest.skip("Dependencies not available")

    def test_all_field_groups_not_imported_in_pipeline(self):
        """Pipeline should not import ALL_FIELD_GROUPS."""
        import inspect

        try:
            from services.extraction import pipeline

            source = inspect.getsource(pipeline)

            # Should not have ALL_FIELD_GROUPS import
            assert "ALL_FIELD_GROUPS" not in source
        except ImportError:
            pytest.skip("Dependencies not available")
