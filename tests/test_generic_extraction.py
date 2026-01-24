"""Tests for generic extraction system (non-company-specific)."""

import pytest

from services.extraction.schema_adapter import SchemaAdapter, ExtractionContext


class TestExtractionContext:
    """Test ExtractionContext dataclass and parsing."""

    def test_extraction_context_defaults(self):
        """Missing context should use generic defaults."""
        context = ExtractionContext()

        assert context.source_type == "content"
        assert context.source_label == "Source"
        # Include product_name for backward compatibility
        assert context.entity_id_fields == ["product_name", "entity_id", "name", "id"]

    def test_extraction_context_from_dict_with_all_fields(self):
        """Custom context should be parsed from dict."""
        data = {
            "source_type": "recipe blog",
            "source_label": "Recipe Site",
            "entity_id_fields": ["recipe_name", "recipe_id"],
        }

        context = ExtractionContext.from_dict(data)

        assert context.source_type == "recipe blog"
        assert context.source_label == "Recipe Site"
        assert context.entity_id_fields == ["recipe_name", "recipe_id"]

    def test_extraction_context_from_dict_partial(self):
        """Partial context should use defaults for missing fields."""
        data = {
            "source_type": "research paper",
        }

        context = ExtractionContext.from_dict(data)

        assert context.source_type == "research paper"
        assert context.source_label == "Source"  # default
        assert context.entity_id_fields == [
            "product_name",
            "entity_id",
            "name",
            "id",
        ]  # default

    def test_extraction_context_from_dict_none(self):
        """None should return default context."""
        context = ExtractionContext.from_dict(None)

        assert context.source_type == "content"
        assert context.source_label == "Source"
        assert context.entity_id_fields == ["product_name", "entity_id", "name", "id"]

    def test_extraction_context_from_dict_empty(self):
        """Empty dict should use all defaults."""
        context = ExtractionContext.from_dict({})

        assert context.source_type == "content"
        assert context.source_label == "Source"
        assert context.entity_id_fields == ["product_name", "entity_id", "name", "id"]


class TestParseTemplate:
    """Test parse_template helper that returns both field_groups and context."""

    @pytest.fixture
    def adapter(self):
        return SchemaAdapter()

    def test_parse_template_returns_tuple(self, adapter):
        """parse_template should return (field_groups, context) tuple."""
        template = {
            "name": "test_template",
            "extraction_context": {
                "source_type": "recipe website",
                "source_label": "Recipe Site",
                "entity_id_fields": ["recipe_name"],
            },
            "extraction_schema": {
                "name": "recipes",
                "version": "1.0",
                "field_groups": [
                    {
                        "name": "recipes",
                        "description": "Recipe information",
                        "fields": [
                            {
                                "name": "recipe_name",
                                "field_type": "text",
                                "description": "Name of recipe",
                            },
                        ],
                    },
                ],
            },
        }

        field_groups, context = adapter.parse_template(template)

        assert len(field_groups) == 1
        assert field_groups[0].name == "recipes"
        assert context.source_type == "recipe website"
        assert context.source_label == "Recipe Site"
        assert context.entity_id_fields == ["recipe_name"]

    def test_parse_template_without_context(self, adapter):
        """Template without extraction_context should use defaults."""
        template = {
            "name": "test_template",
            "extraction_schema": {
                "name": "test",
                "field_groups": [
                    {
                        "name": "test_group",
                        "description": "Test group",
                        "fields": [
                            {
                                "name": "field1",
                                "field_type": "text",
                                "description": "Test field",
                            },
                        ],
                    },
                ],
            },
        }

        field_groups, context = adapter.parse_template(template)

        assert len(field_groups) == 1
        assert context.source_type == "content"
        assert context.source_label == "Source"

    def test_parse_template_backward_compat_schema_only(self, adapter):
        """Template with just extraction_schema (no wrapping) should work."""
        template = {
            "name": "test",
            "field_groups": [
                {
                    "name": "test_group",
                    "description": "Test group",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                            "description": "Test field",
                        },
                    ],
                },
            ],
        }

        field_groups, context = adapter.parse_template(template)

        assert len(field_groups) == 1
        assert context.source_type == "content"


class TestValidationWithCustomEntityIdFields:
    """Test that validation warns (not errors) for entity lists without standard ID fields."""

    @pytest.fixture
    def adapter(self):
        return SchemaAdapter()

    def test_validation_warns_on_missing_common_id_field(self, adapter):
        """Entity list without common ID fields should produce warning, not error."""
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "recipes",
                    "description": "Recipe list",
                    "is_entity_list": True,
                    "fields": [
                        {
                            "name": "recipe_name",  # Not in common_id_fields
                            "field_type": "text",
                            "description": "Recipe name",
                        },
                        {
                            "name": "ingredients",
                            "field_type": "list",
                            "description": "Ingredients",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)

        # Should be valid (warning, not error)
        assert result.is_valid
        # Should have a warning about missing ID field
        assert len(result.warnings) > 0
        assert any("ID field" in w or "deduplication" in w for w in result.warnings)

    def test_validation_no_warning_with_common_id_field(self, adapter):
        """Entity list with common ID field should not produce warning."""
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "products",
                    "description": "Product list",
                    "is_entity_list": True,
                    "fields": [
                        {
                            "name": "product_name",  # In common_id_fields
                            "field_type": "text",
                            "description": "Product name",
                        },
                        {
                            "name": "price",
                            "field_type": "float",
                            "description": "Price",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)

        assert result.is_valid
        # Should not have ID field warning
        assert not any("ID field" in w or "deduplication" in w for w in result.warnings)

    def test_validation_accepts_entity_id(self, adapter):
        """Entity list with entity_id should not produce warning."""
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "items",
                    "description": "Item list",
                    "is_entity_list": True,
                    "fields": [
                        {
                            "name": "entity_id",
                            "field_type": "text",
                            "description": "Item ID",
                        },
                        {
                            "name": "value",
                            "field_type": "integer",
                            "description": "Value",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)

        assert result.is_valid
        assert not any("ID field" in w or "deduplication" in w for w in result.warnings)


class TestSchemaExtractorWithContext:
    """Test SchemaExtractor uses ExtractionContext for prompt building."""

    @pytest.fixture
    def extractor_with_context(self):
        """Create extractor with custom context."""
        try:
            from services.extraction.schema_extractor import SchemaExtractor
            from services.extraction.field_groups import FieldGroup, FieldDefinition

            # Mock settings
            class MockSettings:
                llm_model = "test"
                openai_base_url = "http://test"
                openai_api_key = "test"
                llm_http_timeout = 30

            # Custom context
            context = ExtractionContext(
                source_type="recipe blog",
                source_label="Recipe Site",
                entity_id_fields=["recipe_name", "recipe_id"],
            )

            extractor = SchemaExtractor(MockSettings(), llm_queue=None, context=context)
            return extractor, FieldGroup, FieldDefinition
        except ImportError:
            pytest.skip("Dependencies not available")

    @pytest.fixture
    def extractor_default_context(self):
        """Create extractor with default context."""
        try:
            from services.extraction.schema_extractor import SchemaExtractor
            from services.extraction.field_groups import FieldGroup, FieldDefinition

            class MockSettings:
                llm_model = "test"
                openai_base_url = "http://test"
                openai_api_key = "test"
                llm_http_timeout = 30

            extractor = SchemaExtractor(MockSettings(), llm_queue=None)
            return extractor, FieldGroup, FieldDefinition
        except ImportError:
            pytest.skip("Dependencies not available")

    def test_system_prompt_uses_context_source_type(self, extractor_with_context):
        """System prompt should use context.source_type."""
        extractor, FieldGroup, FieldDefinition = extractor_with_context

        group = FieldGroup(
            name="recipes",
            description="Recipe information",
            fields=[
                FieldDefinition(
                    name="recipe_name",
                    field_type="text",
                    description="Recipe name",
                ),
            ],
            prompt_hint="Extract recipe details",
            is_entity_list=False,
        )

        prompt = extractor._build_system_prompt(group)

        # Should use "recipe blog" not "company documentation"
        assert "recipe blog" in prompt
        assert "company documentation" not in prompt

    def test_entity_list_system_prompt_uses_context_source_type(self, extractor_with_context):
        """Entity list system prompt should use context.source_type."""
        extractor, FieldGroup, FieldDefinition = extractor_with_context

        group = FieldGroup(
            name="recipes",
            description="Recipe information",
            fields=[
                FieldDefinition(
                    name="recipe_name",
                    field_type="text",
                    description="Recipe name",
                ),
            ],
            prompt_hint="Extract all recipes",
            is_entity_list=True,
        )

        prompt = extractor._build_entity_list_system_prompt(group)

        # Should use "recipe blog" not "documentation"
        assert "recipe blog" in prompt

    def test_user_prompt_uses_context_label(self, extractor_with_context):
        """User prompt should use context.source_label."""
        extractor, FieldGroup, FieldDefinition = extractor_with_context

        group = FieldGroup(
            name="recipes",
            description="Recipe information",
            fields=[
                FieldDefinition(
                    name="recipe_name",
                    field_type="text",
                    description="Recipe name",
                ),
            ],
            prompt_hint="Extract recipes",
            is_entity_list=False,
        )

        prompt = extractor._build_user_prompt(
            content="Sample content",
            field_group=group,
            source_context="AllRecipes.com",
        )

        # Should use "Recipe Site:" not "Company:"
        assert "Recipe Site: AllRecipes.com" in prompt
        assert "Company:" not in prompt

    def test_default_context_is_generic(self, extractor_default_context):
        """Default context should use generic terms."""
        extractor, FieldGroup, FieldDefinition = extractor_default_context

        group = FieldGroup(
            name="items",
            description="Item information",
            fields=[
                FieldDefinition(
                    name="item_name",
                    field_type="text",
                    description="Item name",
                ),
            ],
            prompt_hint="Extract items",
            is_entity_list=False,
        )

        system_prompt = extractor._build_system_prompt(group)
        user_prompt = extractor._build_user_prompt(
            content="Sample content",
            field_group=group,
            source_context="Example Source",
        )

        # Should use "content" not "company documentation"
        assert "content" in system_prompt.lower()
        assert "company documentation" not in system_prompt

        # Should use "Source:" not "Company:"
        assert "Source: Example Source" in user_prompt
        assert "Company:" not in user_prompt


class TestSchemaOrchestratorWithContext:
    """Test SchemaOrchestrator uses ExtractionContext for entity merging."""

    @pytest.fixture
    def orchestrator_with_custom_context(self):
        """Create orchestrator with custom context."""
        try:
            from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator

            context = ExtractionContext(
                source_type="recipe blog",
                source_label="Recipe Site",
                entity_id_fields=["recipe_name", "recipe_id"],
            )

            orchestrator = SchemaExtractionOrchestrator(
                schema_extractor=None, context=context
            )
            return orchestrator
        except ImportError:
            pytest.skip("Dependencies not available")

    def test_orchestrator_uses_custom_entity_id_fields(
        self, orchestrator_with_custom_context
    ):
        """Orchestrator should deduplicate using context's entity_id_fields."""
        chunk_results = [
            {
                "recipes": [
                    {"recipe_name": "Chocolate Cake", "prep_time": 30},
                    {"recipe_name": "Vanilla Cake", "prep_time": 25},
                ],
                "confidence": 0.9,
            },
            {
                "recipes": [
                    {"recipe_name": "Chocolate Cake", "prep_time": 35},  # Duplicate
                    {"recipe_name": "Strawberry Cake", "prep_time": 20},
                ],
                "confidence": 0.8,
            },
        ]

        merged = orchestrator_with_custom_context._merge_entity_lists(chunk_results)

        # Should dedupe by recipe_name (from context.entity_id_fields)
        assert len(merged["recipes"]) == 3
        names = [r["recipe_name"] for r in merged["recipes"]]
        assert "Chocolate Cake" in names
        assert "Vanilla Cake" in names
        assert "Strawberry Cake" in names

    def test_orchestrator_falls_back_to_next_id_field(
        self, orchestrator_with_custom_context
    ):
        """If first ID field missing, should try next in entity_id_fields."""
        chunk_results = [
            {
                "recipes": [
                    {"recipe_id": "R001", "name": "Recipe A"},  # recipe_name missing
                    {"recipe_id": "R002", "name": "Recipe B"},
                ],
                "confidence": 0.9,
            },
            {
                "recipes": [
                    {"recipe_id": "R001", "name": "Recipe A Updated"},  # Duplicate
                    {"recipe_id": "R003", "name": "Recipe C"},
                ],
                "confidence": 0.8,
            },
        ]

        merged = orchestrator_with_custom_context._merge_entity_lists(chunk_results)

        # Should dedupe by recipe_id (second field in entity_id_fields)
        assert len(merged["recipes"]) == 3
