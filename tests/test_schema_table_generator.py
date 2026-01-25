"""Tests for SchemaTableGenerator."""

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.reports.schema_table_generator import SchemaTableGenerator


class TestSchemaTableGenerator:
    """Tests for template-agnostic table generation."""

    @pytest.fixture
    def generator(self) -> SchemaTableGenerator:
        """Create generator instance."""
        return SchemaTableGenerator()

    @pytest.fixture
    def research_schema(self) -> dict:
        """Non-drivetrain schema for testing template agnosticity."""
        return {
            "name": "research_findings",
            "field_groups": [
                {
                    "name": "findings",
                    "description": "Key research findings",
                    "is_entity_list": False,
                    "fields": [
                        {
                            "name": "finding_text",
                            "field_type": "text",
                            "description": "Key finding",
                        },
                        {
                            "name": "category",
                            "field_type": "enum",
                            "description": "Finding category",
                            "enum_values": ["positive", "negative", "neutral"],
                        },
                        {
                            "name": "confidence_score",
                            "field_type": "float",
                            "description": "Confidence",
                        },
                    ],
                },
            ],
        }

    @pytest.fixture
    def entity_list_schema(self) -> dict:
        """Schema with entity_list group."""
        return {
            "name": "product_catalog",
            "field_groups": [
                {
                    "name": "products",
                    "description": "Product list",
                    "is_entity_list": True,
                    "fields": [
                        {
                            "name": "product_name",
                            "field_type": "text",
                            "description": "Product name",
                        },
                        {
                            "name": "price",
                            "field_type": "float",
                            "description": "Price",
                        },
                        {
                            "name": "power_kw",
                            "field_type": "float",
                            "description": "Power rating",
                        },
                    ],
                },
            ],
        }

    @pytest.fixture
    def mixed_schema(self) -> dict:
        """Schema with both regular and entity_list groups."""
        return {
            "name": "company_profile",
            "field_groups": [
                {
                    "name": "company_info",
                    "description": "Company information",
                    "is_entity_list": False,
                    "fields": [
                        {
                            "name": "company_name",
                            "field_type": "text",
                            "description": "Company name",
                        },
                        {
                            "name": "employee_count",
                            "field_type": "integer",
                            "description": "Number of employees",
                        },
                    ],
                },
                {
                    "name": "products",
                    "description": "Products offered",
                    "is_entity_list": True,
                    "fields": [
                        {
                            "name": "name",
                            "field_type": "text",
                            "description": "Product name",
                        },
                        {
                            "name": "torque_nm",
                            "field_type": "float",
                            "description": "Torque rating",
                        },
                    ],
                },
            ],
        }


class TestGetColumnsFromSchema:
    """Tests for column derivation from schema."""

    @pytest.fixture
    def generator(self) -> SchemaTableGenerator:
        return SchemaTableGenerator()

    def test_returns_source_group_first(
        self, generator: SchemaTableGenerator, research_schema: dict
    ):
        """Source group column should always be first."""
        columns, labels, _ = generator.get_columns_from_schema(research_schema)
        assert columns[0] == "source_group"
        assert labels["source_group"] == "Source"

    def test_includes_all_regular_fields(
        self, generator: SchemaTableGenerator, research_schema: dict
    ):
        """All fields from regular (non-entity-list) groups should be columns."""
        columns, _, _ = generator.get_columns_from_schema(research_schema)
        assert "finding_text" in columns
        assert "category" in columns
        assert "confidence_score" in columns

    def test_labels_from_description(
        self, generator: SchemaTableGenerator, research_schema: dict
    ):
        """Labels should come from field description."""
        _, labels, _ = generator.get_columns_from_schema(research_schema)
        assert labels["finding_text"] == "Key finding"
        assert labels["category"] == "Finding category"
        assert labels["confidence_score"] == "Confidence"

    def test_entity_list_becomes_list_column(
        self, generator: SchemaTableGenerator, entity_list_schema: dict
    ):
        """Entity list groups should become {name}_list columns."""
        columns, labels, _ = generator.get_columns_from_schema(entity_list_schema)
        assert "products_list" in columns
        assert labels["products_list"] == "Products"

    def test_field_definitions_returned(
        self, generator: SchemaTableGenerator, research_schema: dict
    ):
        """Field definitions should be returned for type info."""
        _, _, field_defs = generator.get_columns_from_schema(research_schema)
        assert field_defs["finding_text"].field_type == "text"
        assert field_defs["confidence_score"].field_type == "float"

    def test_entity_list_field_def_is_none(
        self, generator: SchemaTableGenerator, entity_list_schema: dict
    ):
        """Entity list columns should have None as field_def (marker)."""
        _, _, field_defs = generator.get_columns_from_schema(entity_list_schema)
        assert field_defs["products_list"] is None

    @pytest.fixture
    def research_schema(self) -> dict:
        return {
            "name": "research_findings",
            "field_groups": [
                {
                    "name": "findings",
                    "description": "Key research findings",
                    "is_entity_list": False,
                    "fields": [
                        {
                            "name": "finding_text",
                            "field_type": "text",
                            "description": "Key finding",
                        },
                        {
                            "name": "category",
                            "field_type": "enum",
                            "description": "Finding category",
                            "enum_values": ["positive", "negative", "neutral"],
                        },
                        {
                            "name": "confidence_score",
                            "field_type": "float",
                            "description": "Confidence",
                        },
                    ],
                },
            ],
        }

    @pytest.fixture
    def entity_list_schema(self) -> dict:
        return {
            "name": "product_catalog",
            "field_groups": [
                {
                    "name": "products",
                    "description": "Product list",
                    "is_entity_list": True,
                    "fields": [
                        {
                            "name": "product_name",
                            "field_type": "text",
                            "description": "Product name",
                        },
                        {
                            "name": "price",
                            "field_type": "float",
                            "description": "Price",
                        },
                        {
                            "name": "power_kw",
                            "field_type": "float",
                            "description": "Power rating",
                        },
                    ],
                },
            ],
        }


class TestGetEntityListGroups:
    """Tests for entity list group extraction."""

    @pytest.fixture
    def generator(self) -> SchemaTableGenerator:
        return SchemaTableGenerator()

    def test_returns_only_entity_lists(self, generator: SchemaTableGenerator):
        """Should only return groups with is_entity_list=True."""
        schema = {
            "name": "mixed",
            "field_groups": [
                {
                    "name": "info",
                    "description": "Info",
                    "is_entity_list": False,
                    "fields": [
                        {"name": "name", "field_type": "text", "description": "Name"}
                    ],
                },
                {
                    "name": "items",
                    "description": "Items",
                    "is_entity_list": True,
                    "fields": [
                        {"name": "item", "field_type": "text", "description": "Item"}
                    ],
                },
            ],
        }
        groups = generator.get_entity_list_groups(schema)
        assert "items" in groups
        assert "info" not in groups
        assert len(groups) == 1


class TestFormatEntityList:
    """Tests for entity list formatting."""

    @pytest.fixture
    def generator(self) -> SchemaTableGenerator:
        return SchemaTableGenerator()

    @pytest.fixture
    def product_field_group(self) -> FieldGroup:
        """Create a product field group for testing."""
        return FieldGroup(
            name="products",
            description="Product list",
            fields=[
                FieldDefinition(
                    name="product_name",
                    field_type="text",
                    description="Product name",
                ),
                FieldDefinition(
                    name="power_kw",
                    field_type="float",
                    description="Power rating",
                ),
                FieldDefinition(
                    name="torque_nm",
                    field_type="float",
                    description="Torque rating",
                ),
            ],
            prompt_hint="",
            is_entity_list=True,
        )

    def test_empty_list_returns_na(
        self, generator: SchemaTableGenerator, product_field_group: FieldGroup
    ):
        """Empty list should return N/A."""
        result = generator.format_entity_list([], product_field_group)
        assert result == "N/A"

    def test_includes_product_name(
        self, generator: SchemaTableGenerator, product_field_group: FieldGroup
    ):
        """Should include product name in output."""
        items = [{"product_name": "Widget A", "power_kw": 5.0}]
        result = generator.format_entity_list(items, product_field_group)
        assert "Widget A" in result

    def test_includes_specs_with_units(
        self, generator: SchemaTableGenerator, product_field_group: FieldGroup
    ):
        """Should include spec fields with inferred units."""
        items = [{"product_name": "Widget A", "power_kw": 5.0, "torque_nm": 100}]
        result = generator.format_entity_list(items, product_field_group)
        assert "5.0kW" in result
        assert "100Nm" in result

    def test_multiple_items_semicolon_separated(
        self, generator: SchemaTableGenerator, product_field_group: FieldGroup
    ):
        """Multiple items should be semicolon-separated."""
        items = [
            {"product_name": "Widget A"},
            {"product_name": "Widget B"},
        ]
        result = generator.format_entity_list(items, product_field_group)
        assert "Widget A" in result
        assert "Widget B" in result
        assert ";" in result

    def test_truncates_long_lists(
        self, generator: SchemaTableGenerator, product_field_group: FieldGroup
    ):
        """Should truncate lists longer than max_items."""
        items = [{"product_name": f"Widget {i}"} for i in range(15)]
        result = generator.format_entity_list(items, product_field_group, max_items=10)
        assert "+5 more" in result


class TestInferUnit:
    """Tests for unit inference from field names."""

    @pytest.fixture
    def generator(self) -> SchemaTableGenerator:
        return SchemaTableGenerator()

    def test_infers_kw(self, generator: SchemaTableGenerator):
        assert generator._infer_unit("power_rating_kw") == "kW"
        assert generator._infer_unit("power_kw") == "kW"

    def test_infers_nm(self, generator: SchemaTableGenerator):
        assert generator._infer_unit("torque_nm") == "Nm"
        assert generator._infer_unit("max_torque_nm") == "Nm"

    def test_infers_rpm(self, generator: SchemaTableGenerator):
        assert generator._infer_unit("speed_rpm") == "RPM"

    def test_infers_percent(self, generator: SchemaTableGenerator):
        assert generator._infer_unit("efficiency_percent") == "%"

    def test_unknown_returns_empty(self, generator: SchemaTableGenerator):
        assert generator._infer_unit("some_field") == ""
        assert generator._infer_unit("value") == ""


class TestFindIdField:
    """Tests for identifying field detection."""

    @pytest.fixture
    def generator(self) -> SchemaTableGenerator:
        return SchemaTableGenerator()

    def test_finds_product_name(self, generator: SchemaTableGenerator):
        fields = [
            FieldDefinition("power_kw", "float", "Power"),
            FieldDefinition("product_name", "text", "Name"),
        ]
        assert generator._find_id_field(fields) == "product_name"

    def test_finds_name(self, generator: SchemaTableGenerator):
        fields = [
            FieldDefinition("power_kw", "float", "Power"),
            FieldDefinition("name", "text", "Name"),
        ]
        assert generator._find_id_field(fields) == "name"

    def test_finds_entity_id(self, generator: SchemaTableGenerator):
        fields = [
            FieldDefinition("power_kw", "float", "Power"),
            FieldDefinition("entity_id", "text", "ID"),
        ]
        assert generator._find_id_field(fields) == "entity_id"

    def test_fallback_to_first_text(self, generator: SchemaTableGenerator):
        fields = [
            FieldDefinition("power_kw", "float", "Power"),
            FieldDefinition("description", "text", "Description"),
        ]
        assert generator._find_id_field(fields) == "description"

    def test_no_text_field_returns_none(self, generator: SchemaTableGenerator):
        fields = [
            FieldDefinition("power_kw", "float", "Power"),
            FieldDefinition("count", "integer", "Count"),
        ]
        assert generator._find_id_field(fields) is None


class TestHumanize:
    """Tests for snake_case to Title Case conversion."""

    @pytest.fixture
    def generator(self) -> SchemaTableGenerator:
        return SchemaTableGenerator()

    def test_single_word(self, generator: SchemaTableGenerator):
        assert generator._humanize("name") == "Name"

    def test_multiple_words(self, generator: SchemaTableGenerator):
        assert generator._humanize("product_name") == "Product Name"

    def test_many_words(self, generator: SchemaTableGenerator):
        assert generator._humanize("max_power_rating_kw") == "Max Power Rating Kw"
