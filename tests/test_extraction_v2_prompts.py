"""Tests for v2 prompt format, response parsing, and validation."""

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_extractor import SchemaExtractor
from services.extraction.schema_validator import SchemaValidator


def _make_llm_config():
    """Create minimal LLM config for testing."""
    from config import LLMConfig

    return LLMConfig(
        base_url="http://localhost:9003/v1",
        embedding_base_url="http://localhost:9003/v1",
        api_key="test",
        model="test-model",
        embedding_model="test-embed",
        embedding_dimension=1024,
        http_timeout=30,
        max_tokens=4096,
        max_retries=1,
        retry_backoff_min=1,
        retry_backoff_max=10,
        base_temperature=0.0,
        retry_temperature_increment=0.1,
    )


def _make_field_group(is_entity_list=False):
    return FieldGroup(
        name="company_info",
        description="company information",
        fields=[
            FieldDefinition("company_name", "text", "The company name"),
            FieldDefinition("employee_count", "integer", "Number of employees"),
            FieldDefinition(
                "is_manufacturer", "boolean", "Whether company manufactures"
            ),
        ],
        prompt_hint="Focus on main company identity.",
        is_entity_list=is_entity_list,
    )


def _make_entity_group():
    return FieldGroup(
        name="products",
        description="products manufactured",
        fields=[
            FieldDefinition("name", "text", "Product name"),
            FieldDefinition("type", "text", "Product type"),
        ],
        prompt_hint="Extract all products.",
        is_entity_list=True,
        max_items=50,
    )


class TestV2PromptOutput:
    def test_v2_prompt_contains_per_field_format(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=2)
        group = _make_field_group()
        prompt = extractor._build_system_prompt_v2(group)
        assert '"fields"' in prompt
        assert '"value"' in prompt
        assert '"confidence"' in prompt
        assert '"quote"' in prompt

    def test_v1_prompt_uses_flat_format(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=1)
        group = _make_field_group()
        prompt = extractor._build_system_prompt_v1(group)
        assert '"fields"' not in prompt
        assert '"_quotes"' in prompt or "_quotes" in prompt

    def test_dispatcher_uses_v2_for_version_2(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=2)
        group = _make_field_group()
        prompt = extractor._build_system_prompt(group)
        assert '"fields"' in prompt

    def test_dispatcher_uses_v1_for_version_1(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=1)
        group = _make_field_group()
        prompt = extractor._build_system_prompt(group)
        assert '"fields"' not in prompt

    def test_v2_entity_prompt_has_more_and_confidence(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=2)
        group = _make_entity_group()
        prompt = extractor._build_entity_list_system_prompt_v2(group)
        assert '"has_more"' in prompt
        assert '"_confidence"' in prompt
        assert '"_quote"' in prompt

    def test_v2_entity_prompt_with_already_found(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=2)
        group = _make_entity_group()
        prompt = extractor._build_entity_list_system_prompt_v2(
            group, already_found=["Widget A", "Widget B"]
        )
        assert "Widget A" in prompt
        assert "Widget B" in prompt
        assert "DO NOT repeat" in prompt

    def test_v2_entity_prompt_without_already_found(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=2)
        group = _make_entity_group()
        prompt = extractor._build_entity_list_system_prompt_v2(group)
        assert "DO NOT repeat" not in prompt

    def test_v2_strict_quoting(self):
        extractor = SchemaExtractor(_make_llm_config(), data_version=2)
        group = _make_field_group()
        prompt = extractor._build_system_prompt_v2(group, strict_quoting=True)
        assert "CRITICAL" in prompt
        assert "EXACT" in prompt


class TestDetectResponseFormat:
    def test_v2_format(self):
        raw = {
            "fields": {
                "company_name": {"value": "Acme", "confidence": 0.9, "quote": "Acme"},
            }
        }
        assert SchemaExtractor.detect_response_format(raw) == 2

    def test_v1_flat_format(self):
        raw = {
            "company_name": "Acme",
            "confidence": 0.9,
            "_quotes": {"company_name": "Acme"},
        }
        assert SchemaExtractor.detect_response_format(raw) == 1

    def test_v1_fields_key_but_not_structured(self):
        # "fields" exists but values aren't dicts with "value"
        raw = {"fields": {"company_name": "Acme"}}
        assert SchemaExtractor.detect_response_format(raw) == 1

    def test_empty_dict(self):
        assert SchemaExtractor.detect_response_format({}) == 1


class TestParseV2Response:
    def test_valid_v2_response(self):
        raw = {
            "fields": {
                "company_name": {
                    "value": "Acme Corp",
                    "confidence": 0.95,
                    "quote": "Acme Corp is",
                },
                "employee_count": {
                    "value": 500,
                    "confidence": 0.7,
                    "quote": "500 employees",
                },
                "is_manufacturer": {
                    "value": True,
                    "confidence": 0.8,
                    "quote": "manufactures",
                },
            }
        }
        group = _make_field_group()
        result = SchemaExtractor.parse_v2_response(raw, group)
        assert "fields" in result
        assert result["fields"]["company_name"]["value"] == "Acme Corp"
        assert result["fields"]["company_name"]["confidence"] == 0.95
        assert result["fields"]["employee_count"]["value"] == 500

    def test_v1_fallback(self):
        """If LLM returns v1 flat format, parse_v2_response converts to v2."""
        raw = {
            "company_name": "Acme",
            "employee_count": 500,
            "is_manufacturer": True,
            "confidence": 0.8,
            "_quotes": {"company_name": "Acme Corp"},
        }
        group = _make_field_group()
        result = SchemaExtractor.parse_v2_response(raw, group)
        assert "fields" in result
        assert result["fields"]["company_name"]["value"] == "Acme"
        assert result["fields"]["company_name"]["confidence"] == 0.8
        assert result["fields"]["company_name"]["quote"] == "Acme Corp"

    def test_missing_fields_default_to_null(self):
        raw = {
            "fields": {
                "company_name": {"value": "Acme", "confidence": 0.9},
                # employee_count and is_manufacturer missing
            }
        }
        group = _make_field_group()
        result = SchemaExtractor.parse_v2_response(raw, group)
        assert result["fields"]["employee_count"]["value"] is None
        assert result["fields"]["employee_count"]["confidence"] == 0.0

    def test_unstructured_field_wrapped(self):
        """If a field value is not a dict, wrap it."""
        raw = {
            "fields": {
                "company_name": "Acme",  # Not {"value": ..., "confidence": ...}
                "employee_count": {"value": 500, "confidence": 0.7},
                "is_manufacturer": {"value": False, "confidence": 0.3},
            }
        }
        group = _make_field_group()
        result = SchemaExtractor.parse_v2_response(raw, group)
        assert result["fields"]["company_name"]["value"] == "Acme"
        assert result["fields"]["company_name"]["confidence"] == 0.5  # default

    def test_empty_v1_response(self):
        raw = {"confidence": 0.0}
        group = _make_field_group()
        result = SchemaExtractor.parse_v2_response(raw, group)
        assert all(result["fields"][f.name]["value"] is None for f in group.fields)


class TestParseV2EntityResponse:
    def test_valid_response(self):
        raw = {
            "products": [
                {
                    "name": "Widget A",
                    "type": "gearbox",
                    "_confidence": 0.9,
                    "_quote": "Widget A",
                },
                {
                    "name": "Widget B",
                    "type": "bearing",
                    "_confidence": 0.7,
                    "_quote": "Widget B",
                },
            ],
            "has_more": True,
        }
        group = _make_entity_group()
        result = SchemaExtractor.parse_v2_entity_response(raw, group)
        assert len(result["products"]) == 2
        assert result["products"][0]["fields"]["name"] == "Widget A"
        assert result["products"][0]["_confidence"] == 0.9
        assert result["has_more"] is True

    def test_empty_entity_list(self):
        raw = {"products": [], "has_more": False}
        group = _make_entity_group()
        result = SchemaExtractor.parse_v2_entity_response(raw, group)
        assert result["products"] == []
        assert result["has_more"] is False

    def test_missing_has_more_defaults_false(self):
        raw = {"products": [{"name": "X", "type": "Y"}]}
        group = _make_entity_group()
        result = SchemaExtractor.parse_v2_entity_response(raw, group)
        assert result["has_more"] is False

    def test_fallback_confidence_key(self):
        """Some LLMs use 'confidence' instead of '_confidence'."""
        raw = {
            "products": [
                {"name": "X", "type": "Y", "confidence": 0.8, "quote": "X is Y"},
            ],
        }
        group = _make_entity_group()
        result = SchemaExtractor.parse_v2_entity_response(raw, group)
        assert result["products"][0]["_confidence"] == 0.8
        assert result["products"][0]["_quote"] == "X is Y"


class TestSchemaValidatorV2:
    def test_v2_format_detected_and_validated(self):
        data = {
            "fields": {
                "company_name": {"value": "Acme", "confidence": 0.9, "quote": "Acme"},
                "employee_count": {"value": "500", "confidence": 0.7, "quote": "500"},
                "is_manufacturer": {"value": "true", "confidence": 0.8, "quote": "mfg"},
            }
        }
        group = _make_field_group()
        validator = SchemaValidator()
        cleaned, violations = validator.validate(data, group)

        # Should keep v2 structure
        assert "fields" in cleaned
        # Integer coercion
        assert cleaned["fields"]["employee_count"]["value"] == 500
        # Boolean coercion
        assert cleaned["fields"]["is_manufacturer"]["value"] is True
        # Violations recorded for coercions
        assert len(violations) >= 2

    def test_v2_confidence_clamped(self):
        data = {
            "fields": {
                "company_name": {"value": "Acme", "confidence": 1.5, "quote": "x"},
                "employee_count": {"value": None, "confidence": -0.5},
                "is_manufacturer": {"value": None, "confidence": "bad"},
            }
        }
        group = _make_field_group()
        validator = SchemaValidator()
        cleaned, violations = validator.validate(data, group)
        assert cleaned["fields"]["company_name"]["confidence"] == 1.0
        assert cleaned["fields"]["employee_count"]["confidence"] == 0.0
        assert cleaned["fields"]["is_manufacturer"]["confidence"] == 0.5

    def test_v2_missing_field_defaults_null(self):
        data = {"fields": {}}  # All fields missing
        group = _make_field_group()
        validator = SchemaValidator()
        cleaned, violations = validator.validate(data, group)
        for f in group.fields:
            assert cleaned["fields"][f.name]["value"] is None

    def test_v1_format_still_works(self):
        data = {
            "company_name": "Acme",
            "employee_count": 500,
            "is_manufacturer": True,
            "confidence": 0.9,
        }
        group = _make_field_group()
        validator = SchemaValidator()
        cleaned, violations = validator.validate(data, group)
        assert "fields" not in cleaned
        assert cleaned["company_name"] == "Acme"

    def test_summary_field_type_passthrough(self):
        group = FieldGroup(
            name="test",
            description="test",
            fields=[FieldDefinition("overview", "summary", "A summary")],
            prompt_hint="",
        )
        data = {
            "fields": {
                "overview": {
                    "value": "This is a long summary text.",
                    "confidence": 0.8,
                    "quote": None,
                }
            }
        }
        validator = SchemaValidator()
        cleaned, violations = validator.validate(data, group)
        assert cleaned["fields"]["overview"]["value"] == "This is a long summary text."
        assert len(violations) == 0
