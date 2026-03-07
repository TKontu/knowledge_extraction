"""Tests for grounding verification pure functions.

TDD: Tests written first, implementation follows.
"""

from services.extraction.grounding import (
    GROUNDING_DEFAULTS,
    _coerce_quote,
    compute_chunk_grounding,
    compute_chunk_grounding_entities,
    compute_entity_list_grounding_scores,
    compute_grounding_scores,
    compute_source_grounding_scores,
    extract_entity_list_groups,
    score_field,
    verify_list_items_in_quote,
    verify_numeric_in_quote,
    verify_quote_in_source,
    verify_string_in_quote,
)


class TestVerifyNumericInQuote:
    def test_exact_match(self):
        assert verify_numeric_in_quote(140000, "approximately 140,000 employees") == 1.0

    def test_no_separator(self):
        assert verify_numeric_in_quote(267, "The company has 267 employees") == 1.0

    def test_european_format(self):
        """European style: 30.000 means thirty thousand."""
        assert verify_numeric_in_quote(30000, "30.000 colaboradores") == 1.0

    def test_french_format(self):
        """French style: 30 000 with non-breaking or regular space."""
        assert verify_numeric_in_quote(30000, "30 000 employés") == 1.0

    def test_no_match(self):
        """140-year history should NOT match 140000."""
        assert verify_numeric_in_quote(140000, "more than 140-year history") == 0.0

    def test_partial_number_no_false_positive(self):
        """500 in text should NOT match 5000."""
        assert verify_numeric_in_quote(5000, "over 500 employees") == 0.0

    def test_zero_value(self):
        """Zero is a valid numeric value and should ground when present."""
        assert verify_numeric_in_quote(0, "0 errors found in the system") == 1.0

    def test_zero_not_in_quote(self):
        """Zero should not ground when absent from quote."""
        assert verify_numeric_in_quote(0, "no numbers here") == 0.0

    def test_float_value(self):
        assert verify_numeric_in_quote(2.9, "rated at 2.9 kW output") == 1.0

    def test_float_european_thousands(self):
        """1.500 could be European for 1500."""
        assert verify_numeric_in_quote(1500, "1.500 Mitarbeiter weltweit") == 1.0

    def test_none_value(self):
        assert verify_numeric_in_quote(None, "some text") == 0.0

    def test_empty_quote(self):
        assert verify_numeric_in_quote(140000, "") == 0.0

    def test_none_quote(self):
        assert verify_numeric_in_quote(140000, None) == 0.0

    def test_small_integer(self):
        assert verify_numeric_in_quote(35, "employs 35 people") == 1.0

    def test_comma_thousands(self):
        assert verify_numeric_in_quote(1500, "1,500 workers globally") == 1.0

    def test_negative_number(self):
        assert verify_numeric_in_quote(-10, "temperature of -10 degrees") == 1.0

    def test_string_value_coerced(self):
        """String that looks like a number should be handled."""
        assert (
            verify_numeric_in_quote("140000", "approximately 140,000 employees") == 1.0
        )

    def test_non_numeric_string_returns_zero(self):
        assert verify_numeric_in_quote("not a number", "some text") == 0.0


class TestVerifyStringInQuote:
    def test_exact_match(self):
        assert (
            verify_string_in_quote("ABB", "ABB is a leading technology company") == 1.0
        )

    def test_case_insensitive(self):
        assert (
            verify_string_in_quote("abb", "ABB is a leading technology company") == 1.0
        )

    def test_substring_match(self):
        """Value found as substring of quote."""
        assert (
            verify_string_in_quote("igus", "igus® GmbH produces motion plastics") == 1.0
        )

    def test_whitespace_normalization(self):
        assert verify_string_in_quote("New  York", "based in New York City") == 1.0

    def test_no_match(self):
        assert (
            verify_string_in_quote("Siemens", "ABB is a leading technology company")
            == 0.0
        )

    def test_empty_value(self):
        assert verify_string_in_quote("", "some text") == 0.0

    def test_none_value(self):
        assert verify_string_in_quote(None, "some text") == 0.0

    def test_empty_quote(self):
        assert verify_string_in_quote("ABB", "") == 0.0

    def test_none_quote(self):
        assert verify_string_in_quote("ABB", None) == 0.0

    def test_hyphen_normalization(self):
        """Hyphens stripped for matching."""
        assert verify_string_in_quote("e-drive", "The edrive system provides") >= 0.5

    def test_multiword_value(self):
        assert (
            verify_string_in_quote(
                "Flender GmbH", "Flender GmbH is headquartered in Bocholt"
            )
            == 1.0
        )

    def test_partial_match_scores_lower(self):
        """A partial/fuzzy match should score > 0 but < 1."""
        score = verify_string_in_quote(
            "Bonfiglioli Riduttori", "Bonfiglioli is an Italian company"
        )
        assert 0.0 < score < 1.0


class TestVerifyListItemsInQuote:
    def test_all_found(self):
        items = ["gearboxes", "motors", "drives"]
        quote = "We manufacture gearboxes, motors, and drives for industrial use"
        assert verify_list_items_in_quote(items, quote) == 1.0

    def test_partial_found(self):
        items = ["gearboxes", "motors", "turbines"]
        quote = "We manufacture gearboxes and motors for industrial use"
        score = verify_list_items_in_quote(items, quote)
        assert abs(score - 2.0 / 3.0) < 0.01

    def test_none_found(self):
        items = ["turbines", "compressors"]
        quote = "We manufacture gearboxes and motors"
        assert verify_list_items_in_quote(items, quote) == 0.0

    def test_empty_list(self):
        assert verify_list_items_in_quote([], "some text") == 0.0

    def test_empty_quote(self):
        assert verify_list_items_in_quote(["gearboxes"], "") == 0.0

    def test_case_insensitive(self):
        items = ["Gearboxes", "MOTORS"]
        quote = "we produce gearboxes and motors"
        assert verify_list_items_in_quote(items, quote) == 1.0

    def test_none_items_filtered(self):
        """None items in list should be ignored."""
        items = ["gearboxes", None, "motors"]
        quote = "gearboxes and motors are produced"
        assert verify_list_items_in_quote(items, quote) == 1.0

    def test_dict_items_with_name_key(self):
        """Entity list items are dicts with 'name' key."""
        items = [{"name": "G Series"}, {"name": "P Series"}]
        quote = "The G Series and P Series gearboxes"
        assert verify_list_items_in_quote(items, quote) == 1.0


class TestComputeGroundingScores:
    def test_full_extraction_with_quotes(self):
        """Realistic company_info extraction with quotes."""
        data = {
            "company_name": "ABB",
            "employee_count": 105000,
            "headquarters_location": "Zurich, Switzerland",
            "_quotes": {
                "company_name": "ABB is a leading technology company",
                "employee_count": "approximately 105,000 employees worldwide",
                "headquarters_location": "headquartered in Zurich, Switzerland",
            },
        }
        field_types = {
            "company_name": "string",
            "employee_count": "integer",
            "headquarters_location": "string",
        }
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_name"] == 1.0
        assert scores["employee_count"] == 1.0
        assert scores["headquarters_location"] == 1.0

    def test_missing_quotes(self):
        """No _quotes key → all scorable fields get 0.0."""
        data = {"company_name": "ABB", "employee_count": 105000}
        field_types = {"company_name": "string", "employee_count": "integer"}
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_name"] == 0.0
        assert scores["employee_count"] == 0.0

    def test_empty_quotes_dict(self):
        data = {"company_name": "ABB", "_quotes": {}}
        field_types = {"company_name": "string"}
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_name"] == 0.0

    def test_boolean_skipped(self):
        """Boolean fields have grounding_mode='semantic' → not scored."""
        data = {
            "manufactures_gearboxes": True,
            "_quotes": {"manufactures_gearboxes": "we produce gearboxes"},
        }
        field_types = {"manufactures_gearboxes": "boolean"}
        scores = compute_grounding_scores(data, field_types)
        assert "manufactures_gearboxes" not in scores

    def test_text_skipped(self):
        """Text (description) fields have grounding_mode='none' → not scored."""
        data = {
            "description": "ABB is a global technology leader",
            "_quotes": {"description": "ABB is a global technology leader"},
        }
        field_types = {"description": "text"}
        scores = compute_grounding_scores(data, field_types)
        assert "description" not in scores

    def test_empty_data(self):
        scores = compute_grounding_scores({}, {"company_name": "string"})
        assert scores == {}

    def test_null_field_value_skipped(self):
        """Fields with None value should not be scored."""
        data = {"company_name": None, "_quotes": {"company_name": "ABB Corp"}}
        field_types = {"company_name": "string"}
        scores = compute_grounding_scores(data, field_types)
        assert "company_name" not in scores

    def test_field_not_in_field_types_skipped(self):
        """Fields not declared in field_types are ignored."""
        data = {
            "unknown_field": "value",
            "_quotes": {"unknown_field": "some quote"},
        }
        field_types = {"company_name": "string"}
        scores = compute_grounding_scores(data, field_types)
        assert "unknown_field" not in scores

    def test_hallucinated_number_detected(self):
        """Quote about 140 year history should NOT ground 140000 employees."""
        data = {
            "employee_count": 140000,
            "_quotes": {"employee_count": "more than 140-year history"},
        }
        field_types = {"employee_count": "integer"}
        scores = compute_grounding_scores(data, field_types)
        assert scores["employee_count"] == 0.0

    def test_enum_field_scored(self):
        data = {
            "company_type": "manufacturer",
            "_quotes": {"company_type": "leading manufacturer of gearboxes"},
        }
        field_types = {"company_type": "enum"}
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_type"] == 1.0

    def test_float_field_scored(self):
        data = {
            "power_rating_kw": 2.9,
            "_quotes": {"power_rating_kw": "rated at 2.9 kW"},
        }
        field_types = {"power_rating_kw": "float"}
        scores = compute_grounding_scores(data, field_types)
        assert scores["power_rating_kw"] == 1.0

    def test_list_field_scored(self):
        data = {
            "products": ["gearboxes", "motors"],
            "_quotes": {"products": "manufactures gearboxes and motors"},
        }
        field_types = {"products": "list"}
        scores = compute_grounding_scores(data, field_types)
        assert scores["products"] == 1.0

    def test_mixed_field_types(self):
        """Multiple field types in one extraction."""
        data = {
            "company_name": "ABB",
            "employee_count": 105000,
            "manufactures_gears": True,
            "description": "A tech company",
            "_quotes": {
                "company_name": "ABB is a leader",
                "employee_count": "has 105,000 employees",
                "manufactures_gears": "makes gears",
                "description": "A tech company that...",
            },
        }
        field_types = {
            "company_name": "string",
            "employee_count": "integer",
            "manufactures_gears": "boolean",
            "description": "text",
        }
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_name"] == 1.0
        assert scores["employee_count"] == 1.0
        assert "manufactures_gears" not in scores  # boolean → semantic
        assert "description" not in scores  # text → none

    def test_metadata_keys_ignored(self):
        """_quotes, confidence, _conflicts should not be scored."""
        data = {
            "company_name": "ABB",
            "confidence": 0.9,
            "_quotes": {"company_name": "ABB Corp"},
            "_conflicts": {},
        }
        field_types = {"company_name": "string"}
        scores = compute_grounding_scores(data, field_types)
        assert "confidence" not in scores
        assert "_quotes" not in scores
        assert "_conflicts" not in scores


class TestGroundingDefaults:
    def test_string_required(self):
        assert GROUNDING_DEFAULTS["string"] == "required"

    def test_integer_required(self):
        assert GROUNDING_DEFAULTS["integer"] == "required"

    def test_float_required(self):
        assert GROUNDING_DEFAULTS["float"] == "required"

    def test_boolean_semantic(self):
        assert GROUNDING_DEFAULTS["boolean"] == "semantic"

    def test_text_none(self):
        assert GROUNDING_DEFAULTS["text"] == "none"

    def test_enum_required(self):
        assert GROUNDING_DEFAULTS["enum"] == "required"


class TestCoerceQuote:
    """Test _coerce_quote handles all JSON types from extraction data."""

    def test_string_passthrough(self):
        assert _coerce_quote("hello world") == "hello world"

    def test_none_returns_none(self):
        assert _coerce_quote(None) is None

    def test_empty_string_returns_none(self):
        assert _coerce_quote("") is None

    def test_list_of_strings(self):
        result = _coerce_quote(["first quote", "second quote"])
        assert result == "first quote second quote"

    def test_list_with_none(self):
        result = _coerce_quote(["quote text", None, "more text"])
        assert result == "quote text more text"

    def test_empty_list_returns_none(self):
        assert _coerce_quote([]) is None

    def test_list_of_only_none_returns_none(self):
        assert _coerce_quote([None, None]) is None

    def test_int_coerced(self):
        assert _coerce_quote(42) == "42"

    def test_dict_coerced(self):
        result = _coerce_quote({"text": "a quote"})
        assert isinstance(result, str)
        assert "a quote" in result


class TestScoreFieldNonStringQuotes:
    """Test that score_field handles non-string quotes without crashing."""

    def test_list_quote_with_string_field(self):
        score = score_field("Siemens", ["Siemens AG is a company"], "string")
        assert score > 0.0

    def test_list_quote_with_numeric_field(self):
        score = score_field(140000, ["approximately 140,000 employees"], "integer")
        assert score == 1.0

    def test_list_quote_with_list_field(self):
        score = score_field(["motors", "drives"], ["motors and drives division"], "list")
        assert score > 0.0

    def test_none_quote_returns_zero(self):
        assert score_field("value", None, "string") == 0.0

    def test_empty_list_quote_returns_zero(self):
        assert score_field("value", [], "string") == 0.0

    def test_dict_quote(self):
        # Should not crash, returns some score
        score = score_field("keyword", {"text": "keyword here"}, "string")
        assert isinstance(score, float)

    def test_int_quote(self):
        score = score_field("500", 500, "string")
        assert isinstance(score, float)


class TestComputeGroundingScoresNonStringQuotes:
    """Test compute_grounding_scores with non-string quotes in _quotes dict."""

    def test_list_quote_does_not_crash(self):
        data = {
            "company_name": "Siemens",
            "_quotes": {"company_name": ["Siemens AG is a global company"]},
        }
        field_types = {"company_name": "string"}
        scores = compute_grounding_scores(data, field_types)
        assert "company_name" in scores
        assert scores["company_name"] > 0.0

    def test_mixed_quote_types(self):
        data = {
            "company_name": "ABB",
            "employee_count": 105000,
            "products": ["motors", "drives"],
            "_quotes": {
                "company_name": "ABB Ltd is a technology company",
                "employee_count": ["about 105,000 employees worldwide"],
                "products": None,
            },
        }
        field_types = {
            "company_name": "string",
            "employee_count": "integer",
            "products": "list",
        }
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_name"] > 0.0
        assert scores["employee_count"] == 1.0
        # products quote is None → score 0.0
        assert scores["products"] == 0.0


class TestVerifyQuoteInSource:
    """Test source-grounding: does the quote exist in the source content?"""

    SOURCE = (
        "ABB Ltd is a leading technology company headquartered in Zurich, Switzerland. "
        "The company has approximately 105,000 employees worldwide across 100+ countries. "
        "ABB specializes in electrification, robotics, automation, and motion products."
    )

    def test_exact_substring(self):
        quote = "approximately 105,000 employees worldwide"
        assert verify_quote_in_source(quote, self.SOURCE) == 1.0

    def test_case_insensitive(self):
        quote = "ABB LTD IS A LEADING TECHNOLOGY COMPANY"
        assert verify_quote_in_source(quote, self.SOURCE) == 1.0

    def test_extra_whitespace(self):
        quote = "approximately  105,000   employees  worldwide"
        assert verify_quote_in_source(quote, self.SOURCE) == 1.0

    def test_missing_comma(self):
        """Punctuation-stripped tier catches minor punctuation differences."""
        quote = "approximately 105000 employees worldwide"
        assert verify_quote_in_source(quote, self.SOURCE) >= 0.9

    def test_fabricated_quote(self):
        quote = "ABB is the world's largest automation company with 200,000 employees"
        assert verify_quote_in_source(quote, self.SOURCE) < 0.8

    def test_partial_match_high_overlap(self):
        """Most words present but not all — should score high."""
        quote = "leading technology company headquartered in Zurich"
        assert verify_quote_in_source(quote, self.SOURCE) >= 0.8

    def test_completely_unrelated(self):
        quote = "Founded in 1886 by Charles Brown and Walter Boveri"
        assert verify_quote_in_source(quote, self.SOURCE) < 0.3

    def test_empty_quote(self):
        assert verify_quote_in_source("", self.SOURCE) == 0.0

    def test_empty_content(self):
        assert verify_quote_in_source("some quote", "") == 0.0

    def test_none_inputs(self):
        assert verify_quote_in_source("", "") == 0.0

    def test_multilingual_quote_not_in_english_source(self):
        """A translated quote should not match the English source."""
        quote = "environ 105 000 employés dans le monde"
        assert verify_quote_in_source(quote, self.SOURCE) < 0.5

    def test_short_quote(self):
        """Short quotes that do exist should match."""
        quote = "ABB Ltd"
        assert verify_quote_in_source(quote, self.SOURCE) == 1.0


class TestComputeSourceGroundingScores:
    """Test compute_source_grounding_scores end-to-end."""

    SOURCE = (
        "Siemens AG is a global technology powerhouse. "
        "The company has around 300,000 employees in more than 200 countries. "
        "Siemens focuses on electrification, automation, and digitalization."
    )

    def test_all_quotes_grounded(self):
        data = {
            "company_name": "Siemens",
            "employee_count": 300000,
            "_quotes": {
                "company_name": "Siemens AG is a global technology powerhouse",
                "employee_count": "around 300,000 employees",
            },
        }
        field_types = {"company_name": "string", "employee_count": "integer"}
        scores = compute_source_grounding_scores(data, self.SOURCE, field_types)
        assert scores["company_name"] == 1.0
        assert scores["employee_count"] == 1.0

    def test_fabricated_quote_scores_low(self):
        data = {
            "company_name": "Siemens",
            "_quotes": {
                "company_name": "Siemens was founded in Berlin in 1847 by Werner von Siemens",
            },
        }
        field_types = {"company_name": "string"}
        scores = compute_source_grounding_scores(data, self.SOURCE, field_types)
        assert scores["company_name"] < 0.5

    def test_no_quotes_returns_empty(self):
        data = {"company_name": "Siemens"}
        field_types = {"company_name": "string"}
        scores = compute_source_grounding_scores(data, self.SOURCE, field_types)
        assert scores == {}

    def test_skips_semantic_fields(self):
        data = {
            "is_public": True,
            "_quotes": {"is_public": "Siemens AG"},
        }
        field_types = {"is_public": "boolean"}
        scores = compute_source_grounding_scores(data, self.SOURCE, field_types)
        # boolean has "semantic" grounding mode — skipped
        assert "is_public" not in scores


class TestComputeEntityListGroundingScores:
    """Test grounding scores for entity list data shape."""

    def test_entities_with_matching_quotes(self):
        data = {
            "products": [
                {"name": "Motor X", "_quote": "Motor X series"},
                {"name": "Drive Y", "_quote": "Drive Y controller"},
            ],
            "confidence": 0.9,
        }
        field_types = {"name": "string", "type": "string"}
        scores = compute_entity_list_grounding_scores(data, "products", field_types)
        assert scores == {"products": 1.0}

    def test_entities_without_quotes(self):
        data = {
            "products": [{"name": "Motor X"}, {"name": "Drive Y"}],
            "confidence": 0.9,
        }
        field_types = {"name": "string"}
        scores = compute_entity_list_grounding_scores(data, "products", field_types)
        # No quotes → 0.0 per entity → average 0.0
        assert scores == {"products": 0.0}

    def test_mixed_grounded_and_ungrounded(self):
        data = {
            "products": [
                {"name": "ABB", "_quote": "ABB Corporation"},
                {"name": "Siemens", "_quote": "completely unrelated text"},
            ],
            "confidence": 0.8,
        }
        field_types = {"name": "string"}
        scores = compute_entity_list_grounding_scores(data, "products", field_types)
        # ABB matches → 1.0, Siemens doesn't → 0.0, average 0.5
        assert scores["products"] == 0.5

    def test_empty_entity_list(self):
        data = {"products": [], "confidence": 0.5}
        field_types = {"name": "string"}
        scores = compute_entity_list_grounding_scores(data, "products", field_types)
        assert scores == {}

    def test_missing_entity_key(self):
        data = {"confidence": 0.5}
        field_types = {"name": "string"}
        scores = compute_entity_list_grounding_scores(data, "products", field_types)
        assert scores == {}

    def test_entity_with_non_string_quote(self):
        data = {
            "products": [
                {"name": "Motor X", "_quote": ["Motor X", "series"]},
            ],
            "confidence": 0.8,
        }
        field_types = {"name": "string"}
        scores = compute_entity_list_grounding_scores(data, "products", field_types)
        # Coerced to "Motor X series" → "Motor X" found → 1.0
        assert scores == {"products": 1.0}

    def test_entity_no_id_field_with_quote(self):
        """Entity with _quote but no recognized ID field → assume grounded."""
        data = {
            "items": [
                {"description": "A motor", "_quote": "industrial motor"},
            ],
            "confidence": 0.8,
        }
        field_types = {"description": "text"}
        scores = compute_entity_list_grounding_scores(data, "items", field_types)
        assert scores == {"items": 1.0}


class TestComputeChunkGrounding:
    """Test compute_chunk_grounding: all field types scored (quote vs source)."""

    SOURCE = (
        "ABB Ltd is a leading technology company headquartered in Zurich, Switzerland. "
        "The company has approximately 105,000 employees worldwide. "
        "ABB is a publicly traded manufacturer of industrial equipment."
    )

    def test_all_field_types_scored(self):
        """String, integer, text, boolean — all get scored."""
        result = {
            "company_name": "ABB",
            "employee_count": 105000,
            "description": "A global tech leader",
            "is_public": True,
            "_quotes": {
                "company_name": "ABB Ltd is a leading technology company",
                "employee_count": "approximately 105,000 employees",
                "description": "leading technology company headquartered in Zurich",
                "is_public": "publicly traded manufacturer",
            },
        }
        scores = compute_chunk_grounding(result, self.SOURCE)
        # All quotes exist in source → all scored high
        assert scores["company_name"] == 1.0
        assert scores["employee_count"] == 1.0
        assert scores["description"] == 1.0
        assert scores["is_public"] == 1.0

    def test_fabricated_quote_scores_low(self):
        result = {
            "company_name": "ABB",
            "_quotes": {
                "company_name": "Founded in 1988 by Percy Barnevik in Sweden",
            },
        }
        scores = compute_chunk_grounding(result, self.SOURCE)
        assert scores["company_name"] < 0.5

    def test_empty_result(self):
        assert compute_chunk_grounding({}, self.SOURCE) == {}

    def test_empty_content(self):
        result = {"_quotes": {"x": "some quote"}}
        assert compute_chunk_grounding(result, "") == {}

    def test_no_quotes(self):
        result = {"company_name": "ABB"}
        assert compute_chunk_grounding(result, self.SOURCE) == {}

    def test_empty_quote_skipped(self):
        result = {"_quotes": {"company_name": "", "employee_count": None}}
        scores = compute_chunk_grounding(result, self.SOURCE)
        assert scores == {}

    def test_non_string_quote_coerced(self):
        result = {
            "_quotes": {"company_name": ["ABB Ltd", "is a leading"]},
        }
        scores = compute_chunk_grounding(result, self.SOURCE)
        assert scores["company_name"] >= 0.9


class TestComputeChunkGroundingEntities:
    """Test compute_chunk_grounding_entities: entity quotes vs source."""

    SOURCE = "We produce the Motor X series and the Drive Y controller."

    def test_all_entities_grounded(self):
        result = {
            "products": [
                {"name": "Motor X", "_quote": "Motor X series"},
                {"name": "Drive Y", "_quote": "Drive Y controller"},
            ],
            "confidence": 0.8,
        }
        scores = compute_chunk_grounding_entities(result, self.SOURCE)
        assert scores["products"] == 1.0

    def test_mixed_grounded_and_fabricated(self):
        result = {
            "products": [
                {"name": "Motor X", "_quote": "Motor X series"},
                {"name": "Drive Y", "_quote": "hydraulic pump system"},
            ],
            "confidence": 0.8,
        }
        scores = compute_chunk_grounding_entities(result, self.SOURCE)
        # Motor X grounded (1.0), Drive Y fabricated (<0.8)
        assert 0.3 < scores["products"] < 0.8

    def test_no_quotes_scores_zero(self):
        result = {
            "products": [{"name": "Motor X"}],
            "confidence": 0.8,
        }
        scores = compute_chunk_grounding_entities(result, self.SOURCE)
        assert scores["products"] == 0.0

    def test_empty_result(self):
        assert compute_chunk_grounding_entities({}, self.SOURCE) == {}

    def test_empty_content(self):
        result = {"products": [{"name": "X", "_quote": "X"}]}
        assert compute_chunk_grounding_entities(result, "") == {}


class TestExtractEntityListGroups:
    def test_extracts_entity_list_groups(self):
        schema = {
            "field_groups": [
                {"name": "company_info", "fields": [], "is_entity_list": False},
                {"name": "products", "fields": [], "is_entity_list": True},
                {"name": "services", "fields": [], "is_entity_list": True},
            ]
        }
        assert extract_entity_list_groups(schema) == {"products", "services"}

    def test_no_entity_lists(self):
        schema = {
            "field_groups": [
                {"name": "company_info", "fields": []},
            ]
        }
        assert extract_entity_list_groups(schema) == set()

    def test_empty_schema(self):
        assert extract_entity_list_groups({}) == set()
        assert extract_entity_list_groups({"field_groups": []}) == set()
