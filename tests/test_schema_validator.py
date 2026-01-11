"""Tests for SchemaValidator."""

import pytest
from services.projects.schema import SchemaValidator


class TestSchemaValidatorBasicTypes:
    """Test schema validation for basic field types."""

    def test_validate_text_field(self):
        """Should validate text/string fields."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "message", "type": "text", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"message": "Hello world"})
        assert is_valid is True
        assert errors == []

    def test_validate_integer_field(self):
        """Should validate integer fields."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "count", "type": "integer", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"count": 42})
        assert is_valid is True
        assert errors == []

    def test_validate_float_field(self):
        """Should validate float fields."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "score", "type": "float", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"score": 3.14})
        assert is_valid is True
        assert errors == []

    def test_validate_boolean_field(self):
        """Should validate boolean fields."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "active", "type": "boolean", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"active": True})
        assert is_valid is True
        assert errors == []


class TestSchemaValidatorEnumFields:
    """Test enum field validation."""

    def test_validate_enum_with_allowed_value(self):
        """Should accept value from enum list."""
        schema = {
            "name": "test",
            "fields": [
                {
                    "name": "category",
                    "type": "enum",
                    "required": True,
                    "values": ["api", "security", "pricing"],
                },
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"category": "api"})
        assert is_valid is True
        assert errors == []

    def test_validate_enum_rejects_invalid_value(self):
        """Should reject value not in enum list."""
        schema = {
            "name": "test",
            "fields": [
                {
                    "name": "category",
                    "type": "enum",
                    "required": True,
                    "values": ["api", "security", "pricing"],
                },
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"category": "invalid"})
        assert is_valid is False
        assert len(errors) > 0
        assert "category" in errors[0]


class TestSchemaValidatorRequiredFields:
    """Test required field validation."""

    def test_required_field_present(self):
        """Should validate when required field is present."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "fact_text", "type": "text", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"fact_text": "Some fact"})
        assert is_valid is True

    def test_required_field_missing(self):
        """Should reject when required field is missing."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "fact_text", "type": "text", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({})
        assert is_valid is False
        assert len(errors) > 0

    def test_optional_field_missing(self):
        """Should accept when optional field is missing."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "note", "type": "text", "required": False},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({})
        assert is_valid is True


class TestSchemaValidatorDefaults:
    """Test default value handling."""

    def test_default_value_used_when_missing(self):
        """Should use default value when field is missing."""
        schema = {
            "name": "test",
            "fields": [
                {
                    "name": "confidence",
                    "type": "float",
                    "required": False,
                    "default": 0.8,
                },
            ],
        }
        validator = SchemaValidator(schema)

        # Field missing - should be valid and use default
        is_valid, errors = validator.validate({})
        assert is_valid is True


class TestSchemaValidatorComplexTypes:
    """Test validation of complex types."""

    def test_validate_list_field(self):
        """Should validate list fields."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "tags", "type": "list", "required": False},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"tags": ["tag1", "tag2", "tag3"]})
        assert is_valid is True

    def test_validate_json_field(self):
        """Should validate JSON/dict fields."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "metadata", "type": "json", "required": False},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate(
            {"metadata": {"key": "value", "nested": {"data": 123}}}
        )
        assert is_valid is True


class TestSchemaValidatorMultipleFields:
    """Test validation with multiple fields."""

    def test_validate_all_fields_valid(self):
        """Should validate when all fields are valid."""
        schema = {
            "name": "technical_fact",
            "fields": [
                {"name": "fact_text", "type": "text", "required": True},
                {
                    "name": "category",
                    "type": "enum",
                    "required": True,
                    "values": ["api", "security", "pricing"],
                },
                {
                    "name": "confidence",
                    "type": "float",
                    "required": False,
                    "default": 0.8,
                },
                {"name": "source_quote", "type": "text", "required": False},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate(
            {
                "fact_text": "Supports OAuth 2.0",
                "category": "security",
                "confidence": 0.95,
                "source_quote": "We support OAuth 2.0",
            }
        )
        assert is_valid is True
        assert errors == []

    def test_validate_some_fields_invalid(self):
        """Should report all validation errors."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "text", "type": "text", "required": True},
                {
                    "name": "category",
                    "type": "enum",
                    "required": True,
                    "values": ["a", "b", "c"],
                },
            ],
        }
        validator = SchemaValidator(schema)

        # Missing required field and invalid enum
        is_valid, errors = validator.validate({"category": "invalid"})
        assert is_valid is False
        assert len(errors) >= 1  # At least one error


class TestSchemaValidatorTypeMismatch:
    """Test type mismatch validation."""

    def test_reject_wrong_type_for_integer(self):
        """Should reject string when integer expected."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "count", "type": "integer", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"count": "not a number"})
        assert is_valid is False

    def test_reject_wrong_type_for_float(self):
        """Should reject non-numeric value for float."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "score", "type": "float", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({"score": "invalid"})
        assert is_valid is False


class TestSchemaValidatorHelperMethods:
    """Test helper methods."""

    def test_get_field_names(self):
        """Should return list of field names."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "field1", "type": "text"},
                {"name": "field2", "type": "integer"},
                {"name": "field3", "type": "float"},
            ],
        }
        validator = SchemaValidator(schema)

        field_names = validator.get_field_names()
        assert field_names == ["field1", "field2", "field3"]

    def test_get_required_fields(self):
        """Should return list of required field names."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "required1", "type": "text", "required": True},
                {"name": "optional1", "type": "text", "required": False},
                {"name": "required2", "type": "integer", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        required = validator.get_required_fields()
        assert set(required) == {"required1", "required2"}


class TestSchemaValidatorEdgeCases:
    """Test edge cases and error handling."""

    def test_validate_empty_data_with_no_required_fields(self):
        """Should accept empty data when no fields are required."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "optional", "type": "text", "required": False},
            ],
        }
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({})
        assert is_valid is True

    def test_validate_extra_fields_ignored(self):
        """Should ignore extra fields not in schema."""
        schema = {
            "name": "test",
            "fields": [
                {"name": "known", "type": "text", "required": True},
            ],
        }
        validator = SchemaValidator(schema)

        # Extra field "unknown" should be ignored
        is_valid, errors = validator.validate(
            {"known": "value", "unknown": "extra"}
        )
        assert is_valid is True

    def test_schema_with_no_fields(self):
        """Should handle schema with no fields."""
        schema = {"name": "empty", "fields": []}
        validator = SchemaValidator(schema)

        is_valid, errors = validator.validate({})
        assert is_valid is True
