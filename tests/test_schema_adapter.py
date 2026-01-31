"""Tests for SchemaAdapter - validates and converts extraction schemas."""


from services.extraction.schema_adapter import ClassificationConfig, SchemaAdapter


class TestValidateExtractionSchema:
    """Test validation rules for extraction schemas."""

    def test_valid_schema_conversion(self):
        """Valid schema should validate and convert correctly."""
        adapter = SchemaAdapter()
        schema = {
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

        result = adapter.validate_extraction_schema(schema)
        assert result.is_valid
        assert len(result.errors) == 0

        # Convert and verify
        field_groups = adapter.convert_to_field_groups(schema)
        assert len(field_groups) == 1
        assert field_groups[0].name == "test_group"
        assert len(field_groups[0].fields) == 1
        assert field_groups[0].fields[0].name == "test_field"

    def test_validation_rule_1_missing_name(self):
        """Error if schema missing 'name'."""
        adapter = SchemaAdapter()
        schema = {
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("name" in error.lower() for error in result.errors)

    def test_validation_rule_1_missing_field_groups(self):
        """Error if missing 'field_groups'."""
        adapter = SchemaAdapter()
        schema = {"name": "test"}

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("field_groups" in error.lower() for error in result.errors)

    def test_validation_rule_2_field_group_missing_name(self):
        """Error if field_group missing 'name'."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "description": "desc",
                    "fields": [],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("name" in error.lower() for error in result.errors)

    def test_validation_rule_2_field_group_missing_description(self):
        """Error if field_group missing 'description'."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "fields": [],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("description" in error.lower() for error in result.errors)

    def test_validation_rule_2_field_group_missing_fields(self):
        """Error if field_group missing 'fields'."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("fields" in error.lower() for error in result.errors)

    def test_validation_rule_3_field_missing_field_type(self):
        """Error if field missing 'field_type'."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("field_type" in error.lower() for error in result.errors)

    def test_validation_rule_3_field_missing_name(self):
        """Error if field missing 'name'."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "field_type": "text",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("name" in error.lower() for error in result.errors)

    def test_validation_rule_3_field_missing_description(self):
        """Error if field missing 'description'."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("description" in error.lower() for error in result.errors)

    def test_validation_rule_4_invalid_field_type(self):
        """Error for unknown field_type."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "invalid_type",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("field_type" in error.lower() for error in result.errors)

    def test_validation_rule_5_enum_without_values(self):
        """Error if enum has no values."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "enum",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("enum_values" in error.lower() for error in result.errors)

    def test_validation_rule_5_enum_with_empty_values(self):
        """Error if enum_values is empty list."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "enum",
                            "description": "field desc",
                            "enum_values": [],
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("enum_values" in error.lower() for error in result.errors)

    def test_validation_rule_6_required_without_default(self):
        """Error if required but no default."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                            "description": "field desc",
                            "required": True,
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("default" in error.lower() for error in result.errors)

    def test_validation_rule_7_entity_list_without_product_name(self):
        """Warning for entity_list without product_name or entity_id."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "is_entity_list": True,
                    "fields": [
                        {
                            "name": "some_field",
                            "field_type": "text",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        # This is a warning, not an error - schema is still valid
        assert result.is_valid
        assert any("product_name" in w.lower() or "entity_id" in w.lower() for w in result.warnings)

    def test_validation_rule_8_duplicate_field_group_names(self):
        """Error on duplicate field_group names."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc1",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                            "description": "field desc",
                        },
                    ],
                },
                {
                    "name": "group",
                    "description": "desc2",
                    "fields": [
                        {
                            "name": "field2",
                            "field_type": "text",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("duplicate" in error.lower() and "group" in error.lower() for error in result.errors)

    def test_validation_rule_9_duplicate_field_names(self):
        """Error on duplicate field names within group."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                            "description": "field desc",
                        },
                        {
                            "name": "field1",
                            "field_type": "integer",
                            "description": "another field",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("duplicate" in error.lower() and "field" in error.lower() for error in result.errors)

    def test_validation_rule_10_too_many_field_groups(self):
        """Error if >20 field groups."""
        adapter = SchemaAdapter()
        field_groups = [
            {
                "name": f"group_{i}",
                "description": f"desc {i}",
                "fields": [
                    {
                        "name": "field1",
                        "field_type": "text",
                        "description": "field desc",
                    },
                ],
            }
            for i in range(21)
        ]
        schema = {
            "name": "test",
            "field_groups": field_groups,
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("20" in error or "too many" in error.lower() for error in result.errors)

    def test_validation_rule_11_too_many_fields(self):
        """Error if >30 fields in group."""
        adapter = SchemaAdapter()
        fields = [
            {
                "name": f"field_{i}",
                "field_type": "text",
                "description": f"field {i}",
            }
            for i in range(31)
        ]
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": fields,
                },
            ],
        }

        result = adapter.validate_extraction_schema(schema)
        assert not result.is_valid
        assert any("30" in error or "too many" in error.lower() for error in result.errors)


class TestConvertToFieldGroups:
    """Test conversion from JSONB to FieldGroup objects."""

    def test_prompt_hint_generation(self):
        """Auto-generates hint from description."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "Extract company information",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        field_groups = adapter.convert_to_field_groups(schema)
        assert len(field_groups) == 1
        assert field_groups[0].prompt_hint is not None
        assert len(field_groups[0].prompt_hint) > 0

    def test_prompt_hint_preserved_when_provided(self):
        """Provided prompt_hint should be preserved."""
        adapter = SchemaAdapter()
        custom_hint = "This is a custom hint"
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "Extract company information",
                    "prompt_hint": custom_hint,
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        field_groups = adapter.convert_to_field_groups(schema)
        assert field_groups[0].prompt_hint == custom_hint

    def test_is_entity_list_flag_preserved(self):
        """entity_list groups converted correctly."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
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
                            "required": True,
                            "default": "",
                        },
                    ],
                },
            ],
        }

        field_groups = adapter.convert_to_field_groups(schema)
        assert field_groups[0].is_entity_list is True

    def test_is_entity_list_false_by_default(self):
        """is_entity_list should default to False."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "field1",
                            "field_type": "text",
                            "description": "field desc",
                        },
                    ],
                },
            ],
        }

        field_groups = adapter.convert_to_field_groups(schema)
        assert field_groups[0].is_entity_list is False

    def test_all_field_types_convert_correctly(self):
        """All field types should convert properly."""
        adapter = SchemaAdapter()
        schema = {
            "name": "test",
            "field_groups": [
                {
                    "name": "group",
                    "description": "desc",
                    "fields": [
                        {
                            "name": "bool_field",
                            "field_type": "boolean",
                            "description": "A boolean",
                            "required": True,
                            "default": False,
                        },
                        {
                            "name": "int_field",
                            "field_type": "integer",
                            "description": "An integer",
                        },
                        {
                            "name": "float_field",
                            "field_type": "float",
                            "description": "A float",
                        },
                        {
                            "name": "text_field",
                            "field_type": "text",
                            "description": "A text",
                        },
                        {
                            "name": "list_field",
                            "field_type": "list",
                            "description": "A list",
                        },
                        {
                            "name": "enum_field",
                            "field_type": "enum",
                            "description": "An enum",
                            "required": True,
                            "default": "option1",
                            "enum_values": ["option1", "option2"],
                        },
                    ],
                },
            ],
        }

        field_groups = adapter.convert_to_field_groups(schema)
        assert len(field_groups[0].fields) == 6

        field_by_name = {f.name: f for f in field_groups[0].fields}
        assert field_by_name["bool_field"].field_type == "boolean"
        assert field_by_name["int_field"].field_type == "integer"
        assert field_by_name["float_field"].field_type == "float"
        assert field_by_name["text_field"].field_type == "text"
        assert field_by_name["list_field"].field_type == "list"
        assert field_by_name["enum_field"].field_type == "enum"
        assert field_by_name["enum_field"].enum_values == ["option1", "option2"]


class TestClassificationConfig:
    """Tests for ClassificationConfig dataclass."""

    def test_from_dict_with_none(self):
        """from_dict with None returns default config."""
        config = ClassificationConfig.from_dict(None)
        assert config.skip_patterns is None

    def test_from_dict_with_empty_dict(self):
        """from_dict with empty dict returns default config."""
        config = ClassificationConfig.from_dict({})
        assert config.skip_patterns is None

    def test_from_dict_with_skip_patterns_none(self):
        """from_dict with skip_patterns: null returns None patterns."""
        config = ClassificationConfig.from_dict({"skip_patterns": None})
        assert config.skip_patterns is None

    def test_from_dict_with_skip_patterns_empty_list(self):
        """from_dict with skip_patterns: [] returns empty list."""
        config = ClassificationConfig.from_dict({"skip_patterns": []})
        assert config.skip_patterns == []

    def test_from_dict_with_custom_patterns(self):
        """from_dict with custom patterns preserves them."""
        patterns = [r"/custom/", r"/pattern/"]
        config = ClassificationConfig.from_dict({"skip_patterns": patterns})
        assert config.skip_patterns == patterns

    def test_validate_with_none_patterns(self):
        """Validation passes with None patterns."""
        config = ClassificationConfig(skip_patterns=None)
        is_valid, errors = config.validate()
        assert is_valid
        assert len(errors) == 0

    def test_validate_with_empty_list(self):
        """Validation passes with empty list."""
        config = ClassificationConfig(skip_patterns=[])
        is_valid, errors = config.validate()
        assert is_valid
        assert len(errors) == 0

    def test_validate_with_valid_patterns(self):
        """Validation passes with valid regex patterns."""
        config = ClassificationConfig(skip_patterns=[r"/career|/job", r"/privacy"])
        is_valid, errors = config.validate()
        assert is_valid
        assert len(errors) == 0

    def test_validate_with_invalid_regex(self):
        """Validation fails with invalid regex pattern."""
        config = ClassificationConfig(skip_patterns=[r"[invalid"])  # Unclosed bracket
        is_valid, errors = config.validate()
        assert not is_valid
        assert len(errors) == 1
        assert "invalid regex" in errors[0].lower()

    def test_validate_with_non_string_pattern(self):
        """Validation fails with non-string pattern."""
        config = ClassificationConfig(skip_patterns=[123, "/valid/"])  # type: ignore
        is_valid, errors = config.validate()
        assert not is_valid
        assert any("must be a string" in e for e in errors)

    def test_validate_with_mixed_valid_invalid(self):
        """Validation reports all invalid patterns."""
        config = ClassificationConfig(skip_patterns=[r"/valid/", r"[invalid", r"(also[bad"])
        is_valid, errors = config.validate()
        assert not is_valid
        assert len(errors) == 2  # Two invalid patterns


class TestParseTemplateWithClassificationConfig:
    """Tests for parse_template including classification_config."""

    def test_parse_template_without_classification_config(self):
        """parse_template returns default ClassificationConfig when not present."""
        adapter = SchemaAdapter()
        template = {
            "extraction_schema": {
                "name": "test",
                "field_groups": [
                    {
                        "name": "group",
                        "description": "desc",
                        "fields": [
                            {
                                "name": "field",
                                "field_type": "text",
                                "description": "A field",
                            }
                        ],
                    }
                ],
            }
        }

        field_groups, context, classification_config, crawl_config = adapter.parse_template(template)
        assert classification_config is not None
        assert classification_config.skip_patterns is None
        assert crawl_config is None  # Not in template

    def test_parse_template_with_classification_config(self):
        """parse_template extracts classification_config."""
        adapter = SchemaAdapter()
        template = {
            "extraction_schema": {
                "name": "test",
                "field_groups": [
                    {
                        "name": "group",
                        "description": "desc",
                        "fields": [
                            {
                                "name": "field",
                                "field_type": "text",
                                "description": "A field",
                            }
                        ],
                    }
                ],
            },
            "classification_config": {
                "skip_patterns": [r"/custom/"]
            },
        }

        field_groups, context, classification_config, crawl_config = adapter.parse_template(template)
        assert classification_config.skip_patterns == [r"/custom/"]

    def test_parse_template_with_empty_skip_patterns(self):
        """parse_template handles empty skip_patterns list."""
        adapter = SchemaAdapter()
        template = {
            "extraction_schema": {
                "name": "test",
                "field_groups": [
                    {
                        "name": "group",
                        "description": "desc",
                        "fields": [
                            {
                                "name": "field",
                                "field_type": "text",
                                "description": "A field",
                            }
                        ],
                    }
                ],
            },
            "classification_config": {
                "skip_patterns": []
            },
        }

        field_groups, context, classification_config, crawl_config = adapter.parse_template(template)
        assert classification_config.skip_patterns == []
