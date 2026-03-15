"""Tests for grounding verification pure functions.

TDD: Tests written first, implementation follows.
"""

import pytest

from services.extraction.grounding import (
    GROUNDING_DEFAULTS,
    _coerce_quote,
    compute_chunk_grounding,
    compute_chunk_grounding_entities,
    compute_entity_list_grounding_scores,
    compute_grounding_scores,
    compute_source_grounding_scores,
    extract_entity_list_groups,
    ground_entity_fields,
    is_negation_quote,
    score_entity_confidence,
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

    def test_dict_items_without_name_key(self):
        """Location dicts: each string value grounded independently."""
        items = [
            {"city": "Bocholt", "country": "Germany", "site_type": "headquarters"},
        ]
        quote = "the headquarters is in Bocholt, Germany"
        score = verify_list_items_in_quote(items, quote)
        # 3 values: "Bocholt" ✓, "Germany" ✓, "headquarters" ✓ → 3/3
        assert score == pytest.approx(1.0)

    def test_dict_items_partial_values_found(self):
        """Only some dict values found → proportional score."""
        items = [
            {"city": "Munich", "country": "Germany", "site_type": "R&D center"},
        ]
        quote = "Our facility in Munich, Germany"
        score = verify_list_items_in_quote(items, quote)
        # "Munich" ✓, "Germany" ✓, "R&D center" ✗ → 2/3
        assert score == pytest.approx(2.0 / 3.0, abs=0.01)

    def test_dict_items_no_values_found(self):
        """Location dict values not in quote → 0.0."""
        items = [
            {"city": "Bielefeld", "country": "Austria", "site_type": "factory"},
        ]
        quote = "headquartered in Bocholt, Germany"
        assert verify_list_items_in_quote(items, quote) == 0.0

    def test_mixed_named_and_unnamed_dicts(self):
        """Mix of dicts with and without 'name' key — each value grounded 1:1."""
        items = [
            {"name": "G Series"},  # uses name key → 1 item
            {"city": "Munich", "country": "Germany"},  # uses all values → 2 items
        ]
        quote = "The G Series gearbox is made in Munich, Germany"
        score = verify_list_items_in_quote(items, quote)
        # "G Series" ✓, "Munich" ✓, "Germany" ✓ → 3/3
        assert score == pytest.approx(1.0)


class TestScoreFieldDictInList:
    """Tests for score_field handling dict values from list items (v2 per-item path)."""

    def test_dict_with_name_key(self):
        value = {"name": "G Series", "type": "planetary"}
        quote = "The G Series planetary gearbox"
        assert score_field(value, quote, "list") >= 0.8

    def test_dict_without_name_key_each_value_grounded(self):
        """Location dict: each string value grounded independently, proportional score."""
        value = {"city": "Bocholt", "country": "Germany", "site_type": "headquarters"}
        quote = "the headquarters is in Bocholt, Germany since 1899"
        score = score_field(value, quote, "list")
        # "Bocholt" ✓, "Germany" ✓, "headquarters" ✓ → 3/3
        assert score == pytest.approx(1.0)

    def test_dict_partial_values_found(self):
        """Only some dict values in quote → proportional score."""
        value = {"city": "Bocholt", "country": "Germany", "site_type": "R&D lab"}
        quote = "located in Bocholt, Germany"
        score = score_field(value, quote, "list")
        # "Bocholt" ✓, "Germany" ✓, "R&D lab" ✗ → 2/3
        assert score == pytest.approx(2.0 / 3.0, abs=0.01)

    def test_dict_without_name_key_not_found(self):
        """Location dict values not in quote → 0.0."""
        value = {"city": "Bielefeld", "country": "Austria"}
        quote = "headquartered in Bocholt, Germany"
        assert score_field(value, quote, "list") == 0.0

    def test_dict_empty_string_values(self):
        """Dict with no string values → 0.0."""
        value = {"count": 5}
        quote = "some quote text"
        assert score_field(value, quote, "list") == 0.0


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

    def test_summary_skipped(self):
        """Summary fields have grounding_mode='none' → not scored."""
        data = {
            "description": "ABB is a global technology leader",
            "_quotes": {"description": "ABB is a global technology leader"},
        }
        field_types = {"description": "summary"}
        scores = compute_grounding_scores(data, field_types)
        assert "description" not in scores

    def test_text_scored(self):
        """Text fields have grounding_mode='required' → scored."""
        data = {
            "company_name": "ABB",
            "_quotes": {"company_name": "ABB Corp is a leader"},
        }
        field_types = {"company_name": "text"}
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_name"] == 1.0

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
        assert scores["description"] == 1.0  # text → required (scored)

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

    def test_text_required(self):
        assert GROUNDING_DEFAULTS["text"] == "required"

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
        score = score_field(
            ["motors", "drives"], ["motors and drives division"], "list"
        )
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


class TestIsNegationQuote:
    """Test is_negation_quote detects LLM 'not found' fabrications."""

    def test_no_mention(self):
        assert is_negation_quote("No mention of employee count in the text") is True

    def test_not_specified(self):
        assert is_negation_quote("Not specified in the source") is True

    def test_not_found(self):
        assert is_negation_quote("Not found in the document") is True

    def test_na(self):
        assert is_negation_quote("N/A - no information available") is True

    def test_na_lowercase(self):
        assert is_negation_quote("n/a no data provided") is True

    def test_none_mentioned(self):
        assert is_negation_quote("None of the certifications mentioned") is True

    def test_no_explicit_information(self):
        assert is_negation_quote("No explicit information about this field") is True

    def test_not_explicitly_mentioned(self):
        assert (
            is_negation_quote("Not explicitly mentioned in the provided data") is True
        )

    def test_no_details(self):
        assert is_negation_quote("No details available in the source") is True

    # Negatives — real quotes that happen to contain "no"
    def test_real_quote_with_no(self):
        assert is_negation_quote("The company has 500 employees") is False

    def test_real_quote_norway(self):
        assert is_negation_quote("Norway-based manufacturer") is False

    def test_real_quote_innovation(self):
        assert is_negation_quote("Notable for their innovation in gearboxes") is False

    def test_real_quote_number(self):
        assert is_negation_quote("500 employees worldwide") is False

    def test_none_input(self):
        assert is_negation_quote(None) is False

    def test_empty_string(self):
        assert is_negation_quote("") is False

    def test_whitespace_only(self):
        assert is_negation_quote("   ") is False


# ── ground_entity_fields ──


class TestGroundEntityFields:
    QUOTE = "The FZG Series planetary gearbox delivers up to 50 kW"
    CHUNK = "Our FZG Series planetary gearbox delivers up to 50 kW with 95% efficiency."
    FIELD_DEFS = [
        {"name": "product_name", "field_type": "string"},
        {"name": "power_rating_kw", "field_type": "float"},
        {"name": "subcategory", "field_type": "string"},
    ]

    def test_grounded_fields(self):
        """Field values found in source content get high grounding."""
        fields = {
            "product_name": "FZG Series",
            "power_rating_kw": 50,
            "subcategory": "planetary",
        }
        scores = ground_entity_fields(fields, self.QUOTE, self.CHUNK, self.FIELD_DEFS)
        assert scores["product_name"] >= 0.5  # "FZG Series" in source
        assert scores["power_rating_kw"] == 1.0  # 50 in source
        assert scores["subcategory"] >= 0.5  # "planetary" in source

    def test_hallucinated_value(self):
        """Value not in source should score 0."""
        fields = {"product_name": "FZG Series", "power_rating_kw": 500}
        scores = ground_entity_fields(fields, self.QUOTE, self.CHUNK, self.FIELD_DEFS)
        assert scores["power_rating_kw"] == 0.0  # 500 not in source

    def test_no_quote_uses_chunk(self):
        """Entity fields are always grounded against source content."""
        fields = {"product_name": "FZG Series", "power_rating_kw": 50}
        scores = ground_entity_fields(fields, None, self.CHUNK, self.FIELD_DEFS)
        assert scores["product_name"] >= 0.5  # "FZG Series" in source
        assert scores["power_rating_kw"] == 1.0  # 50 in source

    def test_no_source(self):
        """No quote and no chunk → all zeros."""
        fields = {"product_name": "FZG Series"}
        scores = ground_entity_fields(fields, None, "", self.FIELD_DEFS)
        assert scores["product_name"] == 0.0

    def test_empty_fields(self):
        scores = ground_entity_fields({}, self.QUOTE, self.CHUNK, self.FIELD_DEFS)
        assert scores == {}

    def test_skips_none_values(self):
        fields = {"product_name": "FZG Series", "power_rating_kw": None}
        scores = ground_entity_fields(fields, self.QUOTE, self.CHUNK, self.FIELD_DEFS)
        assert "power_rating_kw" not in scores
        assert "product_name" in scores

    def test_skips_underscore_fields(self):
        fields = {"product_name": "FZG", "_quote": "some quote"}
        scores = ground_entity_fields(fields, self.QUOTE, self.CHUNK, self.FIELD_DEFS)
        assert "_quote" not in scores

    def test_summary_type_exempt(self):
        """Summary-typed fields always get 1.0 (grounding_mode: none)."""
        field_defs = [{"name": "description", "field_type": "summary"}]
        fields = {"description": "A totally fabricated description"}
        scores = ground_entity_fields(fields, self.QUOTE, self.CHUNK, field_defs)
        assert scores["description"] == 1.0

    def test_grounding_mode_override_summary_to_required(self):
        """Summary field with grounding_mode override to 'required' uses value-in-quote check."""
        # Summary fields normally use none mode (always 1.0).
        # With override to required, value must appear in the quote.
        field_defs_no_override = [
            {"name": "description", "field_type": "summary"},
        ]
        field_defs_with_override = [
            {
                "name": "description",
                "field_type": "summary",
                "grounding_mode": "required",
            },
        ]
        fields = {"description": "Fabricated Corp"}

        # Without override: summary → none mode → always 1.0
        score_none = ground_entity_fields(
            fields,
            self.QUOTE,
            self.CHUNK,
            field_defs_no_override,
        )
        # With override: required mode. "Fabricated Corp" not found by score_field → 0.0
        score_required = ground_entity_fields(
            fields,
            self.QUOTE,
            self.CHUNK,
            field_defs_with_override,
        )
        assert score_required["description"] == 0.0
        assert score_none["description"] == 1.0

    def test_grounding_mode_override_respected_for_real_value(self):
        """Override changes behavior: summary field 'FZG Series' gets required-mode check."""
        field_defs_none = [
            {"name": "product_name", "field_type": "summary"},
        ]
        field_defs_required = [
            {
                "name": "product_name",
                "field_type": "summary",
                "grounding_mode": "required",
            },
        ]
        fields = {"product_name": "FZG Series"}

        score_none = ground_entity_fields(
            fields,
            self.QUOTE,
            self.CHUNK,
            field_defs_none,
        )
        score_required = ground_entity_fields(
            fields,
            self.QUOTE,
            self.CHUNK,
            field_defs_required,
        )
        # summary (none) always returns 1.0; required should also score well since "FZG Series" is in the source
        assert score_none["product_name"] == 1.0
        assert score_required["product_name"] >= 0.5


# ── score_entity_confidence ──


class TestScoreEntityConfidence:
    FIELD_DEFS = [
        {"name": "name", "field_type": "string"},
        {"name": "power_kw", "field_type": "float"},
        {"name": "category", "field_type": "string"},
        {"name": "model", "field_type": "string"},
    ]

    def test_fully_grounded_entity(self):
        """Entity with all fields grounded gets full confidence."""
        fields = {"name": "X", "power_kw": 50, "category": "gear", "model": "M1"}
        conf = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.8,
            field_grounding={
                "name": 1.0,
                "power_kw": 1.0,
                "category": 1.0,
                "model": 1.0,
            },
            entity_grounding=1.0,
        )
        # 0.8 * 1.0 * 1.0 = 0.8
        assert conf == 0.8

    def test_sparse_entity_not_penalized(self):
        """Entity with few fields filled is NOT penalized — sparse data is expected."""
        fields = {"name": "X", "power_kw": None, "category": None, "model": None}
        conf = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            field_grounding={"name": 1.0},
            entity_grounding=1.0,
        )
        # 0.5 * 1.0 (avg of filled) * 1.0 (entity) = 0.5
        assert conf == 0.5

    def test_entity_grounding_reduces_confidence(self):
        """Low entity grounding (quote not in source) reduces confidence."""
        fields = {"name": "X", "power_kw": 50}
        conf_high = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            entity_grounding=1.0,
        )
        conf_low = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            entity_grounding=0.3,
        )
        assert conf_high > conf_low

    def test_field_grounding_reduces_confidence(self):
        """Low field grounding should reduce confidence."""
        fields = {"name": "X", "power_kw": 500}
        conf_high_gnd = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            field_grounding={"name": 1.0, "power_kw": 1.0},
        )
        conf_low_gnd = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            field_grounding={"name": 1.0, "power_kw": 0.0},
        )
        assert conf_high_gnd > conf_low_gnd

    def test_null_field_grounding_ignored(self):
        """Grounding scores for null fields should not affect confidence."""
        fields = {"name": "X", "power_kw": None, "category": None, "model": None}
        # With only "name" filled and grounded at 1.0, extra 0.0 entries for
        # null fields should be ignored
        conf_clean = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            field_grounding={"name": 1.0},
        )
        conf_with_zeros = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            field_grounding={
                "name": 1.0,
                "power_kw": 0.0,
                "category": 0.0,
                "model": 0.0,
            },
        )
        assert conf_clean == conf_with_zeros

    def test_defaults_on_empty(self):
        """Empty fields/defs returns raw confidence."""
        assert score_entity_confidence({}, [], 0.5) == 0.5
        assert score_entity_confidence({}, self.FIELD_DEFS, 0.5) == 0.5

    def test_capped_at_one(self):
        """Confidence should never exceed 1.0."""
        fields = {"name": "X", "power_kw": 50, "category": "gear", "model": "M1"}
        conf = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.95,
            field_grounding={
                "name": 1.0,
                "power_kw": 1.0,
                "category": 1.0,
                "model": 1.0,
            },
        )
        assert conf <= 1.0
