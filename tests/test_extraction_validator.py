"""Tests for extraction schema-aware validation (schema_validator.py)."""

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_validator import SchemaValidator


@pytest.fixture
def validator():
    return SchemaValidator()


@pytest.fixture
def strict_validator():
    return SchemaValidator(min_confidence=0.5)


@pytest.fixture
def mixed_group():
    return FieldGroup(
        name="company_info",
        description="Company information",
        fields=[
            FieldDefinition(name="name", field_type="text", description="Name"),
            FieldDefinition(name="employees", field_type="integer", description="Count"),
            FieldDefinition(name="revenue", field_type="float", description="Revenue"),
            FieldDefinition(name="is_public", field_type="boolean", description="Public?"),
            FieldDefinition(name="industry", field_type="enum", description="Industry",
                          enum_values=["manufacturing", "services", "technology"]),
            FieldDefinition(name="tags", field_type="list", description="Tags"),
        ],
        prompt_hint="",
    )


@pytest.fixture
def entity_group():
    return FieldGroup(
        name="products",
        description="Product list",
        fields=[
            FieldDefinition(name="product_name", field_type="text", description="Name"),
            FieldDefinition(name="power_kw", field_type="float", description="Power"),
        ],
        prompt_hint="",
        is_entity_list=True,
    )


class TestTypeCoercion:
    """Test type coercion for various field types."""

    def test_string_to_int(self, validator, mixed_group):
        data = {"employees": "42", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["employees"] == 42
        assert any(v["field"] == "employees" and v["issue"] == "type_coerced" for v in violations)

    def test_string_with_commas_to_int(self, validator, mixed_group):
        data = {"employees": "1,500", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["employees"] == 1500

    def test_float_to_int(self, validator, mixed_group):
        data = {"employees": 42.0, "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["employees"] == 42

    def test_string_to_float(self, validator, mixed_group):
        data = {"revenue": "3.14", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["revenue"] == pytest.approx(3.14)

    def test_int_to_float(self, validator, mixed_group):
        data = {"revenue": 100, "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["revenue"] == 100.0
        # int → float is natural, no violation
        assert not any(v["field"] == "revenue" for v in violations)

    def test_string_true_to_bool(self, validator, mixed_group):
        data = {"is_public": "true", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["is_public"] is True

    def test_string_false_to_bool(self, validator, mixed_group):
        data = {"is_public": "false", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["is_public"] is False

    def test_string_yes_to_bool(self, validator, mixed_group):
        data = {"is_public": "yes", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["is_public"] is True

    def test_invalid_type_nullified(self, validator, mixed_group):
        data = {"employees": "not a number", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["employees"] is None
        assert any(v["issue"] == "invalid_type" for v in violations)


class TestEnumValidation:
    """Test enum case matching and validation."""

    def test_exact_match(self, validator, mixed_group):
        data = {"industry": "manufacturing", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["industry"] == "manufacturing"
        assert not any(v["field"] == "industry" for v in violations)

    def test_case_insensitive_match(self, validator, mixed_group):
        data = {"industry": "Manufacturing", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["industry"] == "manufacturing"
        assert any(v["issue"] == "type_coerced" for v in violations)

    def test_invalid_enum_nullified(self, validator, mixed_group):
        data = {"industry": "agriculture", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["industry"] is None
        assert any(v["issue"] == "invalid_enum" for v in violations)


class TestListWrapping:
    """Test list field validation."""

    def test_list_passes_through(self, validator, mixed_group):
        data = {"tags": ["a", "b"], "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["tags"] == ["a", "b"]

    def test_single_value_wrapped(self, validator, mixed_group):
        data = {"tags": "single_tag", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["tags"] == ["single_tag"]
        assert any(v["issue"] == "type_coerced" for v in violations)


class TestConfidenceGating:
    """Test confidence threshold suppression."""

    def test_below_threshold_suppresses_all(self, strict_validator, mixed_group):
        data = {
            "name": "Acme",
            "employees": 100,
            "confidence": 0.3,  # Below 0.5 threshold
        }
        cleaned, violations = strict_validator.validate(data, mixed_group)
        # All field values should be None
        assert cleaned["name"] is None
        assert cleaned["employees"] is None
        assert any(v["issue"] == "confidence_below_threshold" for v in violations)

    def test_above_threshold_preserved(self, strict_validator, mixed_group):
        data = {
            "name": "Acme",
            "employees": 100,
            "confidence": 0.8,
        }
        cleaned, violations = strict_validator.validate(data, mixed_group)
        assert cleaned["name"] == "Acme"
        assert cleaned["employees"] == 100

    def test_zero_threshold_never_suppresses(self, validator, mixed_group):
        data = {"name": "Acme", "confidence": 0.01}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["name"] == "Acme"


class TestMetadataPreservation:
    """Test that metadata keys survive validation."""

    def test_quotes_preserved(self, validator, mixed_group):
        data = {
            "name": "Acme",
            "confidence": 0.8,
            "_quotes": {"name": "Acme Corp founded in"},
        }
        cleaned, _ = validator.validate(data, mixed_group)
        assert cleaned["_quotes"] == {"name": "Acme Corp founded in"}

    def test_conflicts_preserved(self, validator, mixed_group):
        data = {
            "name": "Acme",
            "confidence": 0.8,
            "_conflicts": {"name": {"values": []}},
        }
        cleaned, _ = validator.validate(data, mixed_group)
        assert "_conflicts" in cleaned

    def test_null_values_pass_through(self, validator, mixed_group):
        data = {"name": None, "employees": None, "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert cleaned["name"] is None
        assert cleaned["employees"] is None
        assert len(violations) == 0  # Nulls are fine, not violations


class TestEntityListValidation:
    """Test validation on entity list groups."""

    def test_entity_fields_coerced(self, validator, entity_group):
        data = {
            "products": [
                {"product_name": "Widget", "power_kw": "100.5"},
            ],
            "confidence": 0.8,
        }
        cleaned, violations = validator.validate(data, entity_group)
        assert cleaned["products"][0]["power_kw"] == pytest.approx(100.5)
        assert any(v["issue"] == "type_coerced" for v in violations)

    def test_entity_quote_preserved(self, validator, entity_group):
        data = {
            "products": [
                {"product_name": "Widget", "power_kw": 100.0, "_quote": "our Widget series"},
            ],
            "confidence": 0.8,
        }
        cleaned, violations = validator.validate(data, entity_group)
        assert cleaned["products"][0]["_quote"] == "our Widget series"

    def test_entity_invalid_type_nullified(self, validator, entity_group):
        data = {
            "products": [
                {"product_name": "Widget", "power_kw": "not a number"},
            ],
            "confidence": 0.8,
        }
        cleaned, violations = validator.validate(data, entity_group)
        assert cleaned["products"][0]["power_kw"] is None


class TestValidationOutput:
    """Test _validation metadata in output."""

    def test_violations_stored_in_validation_key(self, validator, mixed_group):
        data = {"employees": "not_a_number", "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert "_validation" in cleaned
        assert len(cleaned["_validation"]) > 0

    def test_no_violations_no_validation_key(self, validator, mixed_group):
        data = {"name": "Acme", "employees": 100, "confidence": 0.8}
        cleaned, violations = validator.validate(data, mixed_group)
        assert "_validation" not in cleaned
        assert len(violations) == 0
