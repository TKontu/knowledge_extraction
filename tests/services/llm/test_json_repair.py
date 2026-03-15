"""Tests for JSON repair utilities."""

import json

import pytest

from services.llm.json_repair import (
    _balance_brackets,
    _fix_quotes,
    _fix_unterminated_strings,
    _remove_trailing_commas,
    _strip_code_fences,
    repair_json,
    try_repair_json,
)


class TestRepairJson:
    """Test repair_json function."""

    def test_valid_json_passes_through(self):
        """Valid JSON should parse directly without repair."""
        valid = '{"name": "test", "value": 123}'
        result = repair_json(valid)
        assert result == {"name": "test", "value": 123}

    def test_unterminated_string_repaired(self):
        """Unterminated string should be closed."""
        # Truncated mid-string
        malformed = '{"name": "test'
        result = repair_json(malformed)
        assert result["name"] == "test"

    def test_missing_closing_brace_repaired(self):
        """Missing closing brace should be added."""
        malformed = '{"a": 1'
        result = repair_json(malformed)
        assert result == {"a": 1}

    def test_missing_closing_bracket_repaired(self):
        """Missing closing bracket should be added."""
        malformed = '{"items": [1, 2'
        result = repair_json(malformed)
        assert result == {"items": [1, 2]}

    def test_nested_incomplete_structure(self):
        """Nested incomplete structures should be balanced."""
        malformed = '{"a": {"b": [1, 2'
        result = repair_json(malformed)
        assert result == {"a": {"b": [1, 2]}}

    def test_trailing_comma_in_array_removed(self):
        """Trailing comma in array should be removed."""
        malformed = '{"items": [1, 2,]}'
        result = repair_json(malformed)
        assert result == {"items": [1, 2]}

    def test_trailing_comma_in_object_removed(self):
        """Trailing comma in object should be removed."""
        malformed = '{"a": 1, "b": 2,}'
        result = repair_json(malformed)
        assert result == {"a": 1, "b": 2}

    def test_single_quotes_converted(self):
        """Single quotes should be converted to double quotes."""
        malformed = "{'a': 1, 'b': 'value'}"
        result = repair_json(malformed)
        assert result == {"a": 1, "b": "value"}

    def test_markdown_code_fence_stripped(self):
        """Markdown code fences should be stripped."""
        malformed = '```json\n{"a": 1}\n```'
        result = repair_json(malformed)
        assert result == {"a": 1}

    def test_markdown_code_fence_no_language(self):
        """Markdown code fences without language should be stripped."""
        malformed = '```\n{"a": 1}\n```'
        result = repair_json(malformed)
        assert result == {"a": 1}

    def test_empty_string_raises(self):
        """Empty string should raise JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            repair_json("")

    def test_whitespace_only_raises(self):
        """Whitespace-only string should raise JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            repair_json("   \n\t  ")

    def test_completely_invalid_raises(self):
        """Completely invalid content should raise JSONDecodeError."""
        with pytest.raises(json.JSONDecodeError):
            repair_json("this is not json at all")

    def test_truncated_mid_key(self):
        """Truncated mid-key should attempt repair."""
        # This is a very broken case - may or may not be repairable
        malformed = '{"na'
        # Should either repair or raise cleanly
        try:
            result = repair_json(malformed)
            assert isinstance(result, dict)
        except json.JSONDecodeError:
            pass  # Acceptable - this is very broken

    def test_complex_nested_structure(self):
        """Complex nested structure with multiple issues."""
        malformed = '{"facts": [{"text": "example", "confidence": 0.9'
        result = repair_json(malformed)
        assert "facts" in result
        assert result["facts"][0]["text"] == "example"

    def test_escaped_quotes_preserved(self):
        """Escaped quotes in strings should be preserved."""
        valid = '{"text": "He said \\"hello\\""}'
        result = repair_json(valid)
        assert result["text"] == 'He said "hello"'


class TestTryRepairJson:
    """Test try_repair_json wrapper function."""

    def test_valid_json_no_repair_needed(self):
        """Valid JSON should parse without logging repair."""
        valid = '{"key": "value"}'
        result = try_repair_json(valid, context="test")
        assert result == {"key": "value"}

    def test_invalid_json_repaired(self):
        """Invalid JSON should be repaired."""
        malformed = '{"key": "value"'
        result = try_repair_json(malformed, context="test")
        assert result == {"key": "value"}

    def test_irreparable_raises_original_error(self):
        """Irreparable JSON should raise the original error."""
        malformed = "not json"
        with pytest.raises(json.JSONDecodeError) as exc_info:
            try_repair_json(malformed, context="test")
        # Should be the original error, not the repair error
        assert "Expecting value" in str(exc_info.value)

    def test_none_input_raises_json_decode_error(self):
        """None input should raise JSONDecodeError, not TypeError."""
        with pytest.raises(json.JSONDecodeError) as exc_info:
            try_repair_json(None, context="test")
        assert "Cannot parse None" in str(exc_info.value)


class TestStripCodeFences:
    """Test _strip_code_fences helper."""

    def test_json_fence(self):
        """Should strip ```json fences."""
        text = '```json\n{"a": 1}\n```'
        result = _strip_code_fences(text)
        assert result == '{"a": 1}'

    def test_plain_fence(self):
        """Should strip plain ``` fences."""
        text = '```\n{"a": 1}\n```'
        result = _strip_code_fences(text)
        assert result == '{"a": 1}'

    def test_no_fence(self):
        """Should leave unfenced content alone."""
        text = '{"a": 1}'
        result = _strip_code_fences(text)
        assert result == '{"a": 1}'

    def test_fence_with_whitespace(self):
        """Should handle whitespace around fences."""
        text = '  ```json\n{"a": 1}\n```  '
        result = _strip_code_fences(text)
        assert result == '{"a": 1}'


class TestFixUnterminatedStrings:
    """Test _fix_unterminated_strings helper."""

    def test_unterminated_at_end(self):
        """Should close string at end."""
        text = '{"key": "value'
        result = _fix_unterminated_strings(text)
        assert result.endswith('"')

    def test_complete_string_unchanged(self):
        """Complete strings should be unchanged."""
        text = '{"key": "value"}'
        result = _fix_unterminated_strings(text)
        assert result == text

    def test_escaped_quote_handled(self):
        """Escaped quotes should not be counted as terminators."""
        text = '{"key": "val\\"ue'
        result = _fix_unterminated_strings(text)
        assert result.endswith('"')


class TestBalanceBrackets:
    """Test _balance_brackets helper."""

    def test_missing_brace(self):
        """Should add missing closing brace."""
        text = '{"a": 1'
        result = _balance_brackets(text)
        assert result == '{"a": 1}'

    def test_missing_bracket(self):
        """Should add missing closing bracket."""
        text = '{"a": [1, 2'
        result = _balance_brackets(text)
        assert result == '{"a": [1, 2]}'

    def test_missing_both(self):
        """Should add both missing bracket and brace."""
        text = '{"a": [1'
        result = _balance_brackets(text)
        assert result == '{"a": [1]}'

    def test_nested_missing(self):
        """Should handle nested missing brackets."""
        text = '{"a": {"b": [1'
        result = _balance_brackets(text)
        assert result == '{"a": {"b": [1]}}'

    def test_balanced_unchanged(self):
        """Balanced brackets should be unchanged."""
        text = '{"a": [1, 2]}'
        result = _balance_brackets(text)
        assert result == text

    def test_brackets_in_strings_ignored(self):
        """Brackets inside strings should be ignored."""
        text = '{"a": "[not a real bracket'
        result = _balance_brackets(text)
        # The string is unterminated, but balance_brackets only adds closers
        # It should add the missing } but not mess with the string content
        assert result.endswith("}")


class TestRemoveTrailingCommas:
    """Test _remove_trailing_commas helper."""

    def test_array_trailing_comma(self):
        """Should remove trailing comma in array."""
        text = "[1, 2, 3,]"
        result = _remove_trailing_commas(text)
        assert result == "[1, 2, 3]"

    def test_object_trailing_comma(self):
        """Should remove trailing comma in object."""
        text = '{"a": 1, "b": 2,}'
        result = _remove_trailing_commas(text)
        assert result == '{"a": 1, "b": 2}'

    def test_comma_with_whitespace(self):
        """Should handle whitespace before closer."""
        text = "[1, 2,  ]"
        result = _remove_trailing_commas(text)
        assert result == "[1, 2]"

    def test_no_trailing_comma(self):
        """Should leave valid JSON unchanged."""
        text = '{"a": 1}'
        result = _remove_trailing_commas(text)
        assert result == text


class TestFixQuotes:
    """Test _fix_quotes helper."""

    def test_all_single_quotes(self):
        """Should convert all single quotes when no double quotes."""
        text = "{'a': 1}"
        result = _fix_quotes(text)
        assert result == '{"a": 1}'

    def test_mixed_quotes(self):
        """Should handle mixed quote styles."""
        text = "{'a': 1, \"b\": 2}"
        result = _fix_quotes(text)
        # Should attempt to fix single-quoted keys/values
        assert '"a"' in result

    def test_double_quotes_unchanged(self):
        """Double quotes should remain unchanged."""
        text = '{"a": 1}'
        result = _fix_quotes(text)
        assert result == text


class TestRealWorldExamples:
    """Test with real-world LLM output examples."""

    def test_truncated_facts_array(self):
        """Test truncated facts array from LLM."""
        malformed = """{"facts": [
            {"text": "Company offers API access", "confidence": 0.95},
            {"text": "Pricing starts at $99/month", "confidence": 0.8"""

        result = repair_json(malformed)
        assert "facts" in result
        assert len(result["facts"]) == 2

    def test_truncated_entity_extraction(self):
        """Test truncated entity extraction response."""
        malformed = '{"entities": [{"type": "company", "value": "Acme Corp'

        result = repair_json(malformed)
        assert "entities" in result
        assert result["entities"][0]["type"] == "company"

    def test_code_fence_wrapped_response(self):
        """Test LLM response wrapped in code fences."""
        malformed = """```json
{
    "facts": [
        {"text": "Feature X is available", "confidence": 0.9}
    ]
}
```"""

        result = repair_json(malformed)
        assert result["facts"][0]["text"] == "Feature X is available"
