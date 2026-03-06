"""Tests for consolidation pure functions."""

import pytest

from services.extraction.consolidation import (
    ConsolidatedField,
    ConsolidatedRecord,
    WeightedValue,
    _dedup_dicts,
    any_true,
    consolidate_extractions,
    consolidate_field,
    effective_weight,
    frequency,
    longest_top_k,
    union_dedup,
    weighted_frequency,
    weighted_median,
)

# ── Strategy: frequency ──


class TestFrequency:
    def test_clear_winner(self):
        values = [
            WeightedValue("ABB", 0.9),
            WeightedValue("ABB", 0.8),
            WeightedValue("Abb Ltd", 0.7),
        ]
        assert frequency(values) == "ABB"

    def test_tie_broken_by_weight(self):
        values = [
            WeightedValue("ABB", 0.9),
            WeightedValue("Siemens", 0.3),
        ]
        assert frequency(values) == "ABB"

    def test_case_insensitive_grouping(self):
        """ABB, abb, Abb all grouped together."""
        values = [
            WeightedValue("ABB", 0.9),
            WeightedValue("abb", 0.8),
            WeightedValue("Abb", 0.7),
            WeightedValue("Siemens", 0.95),
        ]
        # ABB group has 3 occurrences vs Siemens 1
        assert frequency(values).lower() == "abb"

    def test_returns_original_case(self):
        """Should return the most common original-case form."""
        values = [
            WeightedValue("ABB", 0.9),
            WeightedValue("ABB", 0.8),
            WeightedValue("abb", 0.7),
        ]
        assert frequency(values) == "ABB"

    def test_single_value(self):
        assert frequency([WeightedValue("ABB", 0.9)]) == "ABB"

    def test_all_none(self):
        values = [WeightedValue(None, 0.9), WeightedValue(None, 0.8)]
        assert frequency(values) is None

    def test_empty_list(self):
        assert frequency([]) is None

    def test_non_string_values(self):
        """frequency should work for non-string types too."""
        values = [
            WeightedValue(42, 0.9),
            WeightedValue(42, 0.8),
            WeightedValue(99, 0.7),
        ]
        assert frequency(values) == 42


# ── Strategy: weighted_frequency ──


class TestWeightedFrequency:
    def test_higher_weight_wins(self):
        values = [
            WeightedValue("Zurich", 0.9),
            WeightedValue("Basel", 0.3),
            WeightedValue("Zurich", 0.1),
        ]
        assert weighted_frequency(values) == "Zurich"

    def test_single_high_weight_beats_count(self):
        values = [
            WeightedValue("Basel", 0.1),
            WeightedValue("Basel", 0.1),
            WeightedValue("Zurich", 0.9),
        ]
        assert weighted_frequency(values) == "Zurich"

    def test_empty(self):
        assert weighted_frequency([]) is None


# ── Strategy: weighted_median ──


class TestWeightedMedian:
    def test_single_grounded_value(self):
        values = [WeightedValue(140000, 0.9)]
        assert weighted_median(values) == 140000

    def test_multiple_with_outlier(self):
        """Outlier with weight 0 should be excluded."""
        values = [
            WeightedValue(140000, 0.9),
            WeightedValue(5000, 0.0),
            WeightedValue(140000, 0.85),
        ]
        assert weighted_median(values) == 140000

    def test_excludes_zero_weight(self):
        values = [
            WeightedValue(100, 0.9),
            WeightedValue(999999, 0.0),
        ]
        assert weighted_median(values) == 100

    def test_fallback_when_all_zero_weight(self):
        """When all weights are 0, use unweighted median."""
        values = [
            WeightedValue(100, 0.0),
            WeightedValue(200, 0.0),
            WeightedValue(300, 0.0),
        ]
        assert weighted_median(values) == 200

    def test_integer_output(self):
        """int input -> int output."""
        values = [WeightedValue(100, 0.9), WeightedValue(200, 0.8)]
        result = weighted_median(values)
        assert isinstance(result, int)

    def test_float_output(self):
        """float input -> float output."""
        values = [WeightedValue(2.5, 0.9), WeightedValue(3.5, 0.8)]
        result = weighted_median(values)
        assert isinstance(result, float)

    def test_even_count_equal_weight(self):
        """Even number of values with equal weight: average of two."""
        values = [
            WeightedValue(100, 0.5),
            WeightedValue(200, 0.5),
        ]
        result = weighted_median(values)
        assert result == 150

    def test_empty(self):
        assert weighted_median([]) is None

    def test_all_none_values(self):
        values = [WeightedValue(None, 0.9)]
        assert weighted_median(values) is None

    def test_weighted_median_picks_center(self):
        """Weighted median should pick the value at the center of cumulative weight."""
        values = [
            WeightedValue(10, 0.1),
            WeightedValue(50, 0.8),
            WeightedValue(90, 0.1),
        ]
        assert weighted_median(values) == 50


# ── Strategy: any_true ──


class TestAnyTrue:
    def test_multiple_true(self):
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(True, 0.8),
            WeightedValue(False, 0.7),
        ]
        assert any_true(values, min_count=2) is True

    def test_single_true_below_min(self):
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(False, 0.7),
        ]
        assert any_true(values, min_count=2) is None

    def test_all_false(self):
        values = [
            WeightedValue(False, 0.9),
            WeightedValue(False, 0.8),
        ]
        assert any_true(values) is False

    def test_ignores_zero_weight(self):
        """True values with weight=0 don't count."""
        values = [
            WeightedValue(True, 0.0),
            WeightedValue(True, 0.0),
            WeightedValue(False, 0.9),
        ]
        assert any_true(values, min_count=2) is None

    def test_empty(self):
        assert any_true([]) is None

    def test_default_min_count_is_3(self):
        """Default min_count=3 matches trial findings (86% accuracy)."""
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(True, 0.8),
            WeightedValue(False, 0.7),
        ]
        assert any_true(values) is None  # only 2 True, min_count=3 default

    def test_default_min_count_met(self):
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(True, 0.8),
            WeightedValue(True, 0.7),
            WeightedValue(False, 0.6),
        ]
        assert any_true(values) is True  # 3 True, min_count=3 default


# ── Strategy: longest_top_k ──


class TestLongestTopK:
    def test_picks_longest_from_top_3(self):
        values = [
            WeightedValue("short", 0.9),
            WeightedValue("medium length text", 0.85),
            WeightedValue("this is the longest description of all", 0.8),
            WeightedValue("low weight long text that should be excluded", 0.1),
        ]
        result = longest_top_k(values, k=3)
        assert result == "this is the longest description of all"

    def test_single_value(self):
        assert longest_top_k([WeightedValue("only", 0.5)]) == "only"

    def test_respects_weight_ranking(self):
        """Low-weight long strings should not beat high-weight shorter ones."""
        values = [
            WeightedValue("short", 0.9),
            WeightedValue("a very long string but low weight", 0.01),
        ]
        result = longest_top_k(values, k=1)
        assert result == "short"

    def test_empty(self):
        assert longest_top_k([]) is None


# ── Strategy: union_dedup ──


class TestUnionDedup:
    def test_merges_lists(self):
        values = [
            WeightedValue(["A", "B"], 0.9),
            WeightedValue(["B", "C"], 0.8),
        ]
        result = union_dedup(values)
        assert set(result) == {"A", "B", "C"}

    def test_normalized_dedup(self):
        """Case-insensitive dedup, keeps first occurrence."""
        values = [
            WeightedValue(["G Series"], 0.9),
            WeightedValue(["G SERIES", "g series"], 0.8),
        ]
        result = union_dedup(values)
        assert len(result) == 1
        assert result[0] == "G Series"  # keeps first seen

    def test_empty_lists(self):
        values = [WeightedValue([], 0.9)]
        assert union_dedup(values) == []

    def test_empty_input(self):
        assert union_dedup([]) == []

    def test_preserves_order_by_first_seen(self):
        values = [
            WeightedValue(["C", "A"], 0.9),
            WeightedValue(["B", "A"], 0.8),
        ]
        result = union_dedup(values)
        assert result == ["C", "A", "B"]

    def test_non_list_values_wrapped(self):
        """Single string values should be treated as single-item lists."""
        values = [
            WeightedValue("gearbox", 0.9),
            WeightedValue("motor", 0.8),
        ]
        result = union_dedup(values)
        assert set(result) == {"gearbox", "motor"}

    def test_dict_items_deduped_by_name(self):
        """Entity dicts deduped by 'name' field."""
        values = [
            WeightedValue(
                [{"name": "G Series", "power": 10}, {"name": "P Series", "power": 20}],
                0.9,
            ),
            WeightedValue(
                [{"name": "g series", "power": 15}],
                0.8,
            ),
        ]
        result = union_dedup(values)
        assert len(result) == 2
        names = {item["name"] for item in result}
        assert "G Series" in names
        assert "P Series" in names


# ── effective_weight ──


class TestEffectiveWeight:
    def test_required_grounded(self):
        assert effective_weight(0.9, 1.0, "required") == pytest.approx(0.9)

    def test_required_ungrounded_has_floor(self):
        """Ungrounded data gets floor weight (0.1), not zero."""
        assert effective_weight(0.9, 0.0, "required") == pytest.approx(0.09)

    def test_required_partial_grounding(self):
        assert effective_weight(0.9, 0.6, "required") == pytest.approx(0.54)

    def test_required_below_old_threshold_still_contributes(self):
        """Score < 0.5 now contributes (no cliff). 0.9 * 0.4 = 0.36."""
        assert effective_weight(0.9, 0.4, "required") == pytest.approx(0.36)

    def test_required_none_score_gets_floor(self):
        """None grounding score treated as 0.0, gets floor of 0.1."""
        assert effective_weight(0.9, None, "required") == pytest.approx(0.09)

    def test_grounded_dominates_ungrounded(self):
        """High-conf ungrounded (0.08) < low-conf grounded (0.36)."""
        ungrounded = effective_weight(0.8, 0.0, "required")
        grounded = effective_weight(0.45, 1.0, "required")
        assert grounded > ungrounded

    def test_semantic_no_grounding(self):
        assert effective_weight(0.9, None, "semantic") == 0.9

    def test_semantic_ignores_score(self):
        assert effective_weight(0.9, 0.0, "semantic") == 0.9

    def test_none_mode(self):
        assert effective_weight(0.9, None, "none") == 0.9

    def test_none_mode_ignores_score(self):
        assert effective_weight(0.9, 0.3, "none") == 0.9


# ── consolidate_field ──


class TestConsolidateField:
    def test_dispatches_frequency(self):
        values = [WeightedValue("ABB", 0.9), WeightedValue("ABB", 0.8)]
        result = consolidate_field(values, "frequency")
        assert isinstance(result, ConsolidatedField)
        assert result.value == "ABB"
        assert result.strategy == "frequency"
        assert result.source_count == 2

    def test_dispatches_weighted_median(self):
        values = [WeightedValue(100, 0.9), WeightedValue(200, 0.8)]
        result = consolidate_field(values, "weighted_median")
        # Weighted median: cumulative weight at 100 is 0.9, half is 0.85 -> picks 100
        assert result.value == 100

    def test_dispatches_any_true(self):
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(True, 0.8),
            WeightedValue(True, 0.7),
        ]
        result = consolidate_field(values, "any_true")
        assert result.value is True

    def test_dispatches_union_dedup(self):
        values = [
            WeightedValue(["A", "B"], 0.9),
            WeightedValue(["B", "C"], 0.8),
        ]
        result = consolidate_field(values, "union_dedup")
        assert set(result.value) == {"A", "B", "C"}

    def test_agreement_calculated(self):
        values = [
            WeightedValue("ABB", 0.9),
            WeightedValue("ABB", 0.8),
            WeightedValue("Siemens", 0.7),
        ]
        result = consolidate_field(values, "frequency")
        assert result.agreement == pytest.approx(2 / 3, abs=0.01)

    def test_empty_values(self):
        result = consolidate_field([], "frequency")
        assert result.value is None
        assert result.source_count == 0

    def test_unknown_strategy_falls_back(self):
        """Unknown strategy name should fall back to frequency."""
        values = [WeightedValue("X", 0.9)]
        result = consolidate_field(values, "unknown_strategy")
        assert result.value == "X"


# ── consolidate_extractions ──


class TestConsolidateExtractions:
    def test_realistic_company_info(self):
        """ABB-like data with 5 extractions."""
        extractions = [
            {
                "data": {
                    "company_name": "ABB",
                    "employee_count": 105000,
                    "_quotes": {
                        "company_name": "ABB is a leader",
                        "employee_count": "105,000 employees",
                    },
                },
                "confidence": 0.9,
                "grounding_scores": {"company_name": 1.0, "employee_count": 1.0},
                "source_id": "s1",
            },
            {
                "data": {
                    "company_name": "ABB",
                    "employee_count": 105000,
                    "_quotes": {
                        "company_name": "ABB Ltd",
                        "employee_count": "approximately 105,000",
                    },
                },
                "confidence": 0.85,
                "grounding_scores": {"company_name": 1.0, "employee_count": 1.0},
                "source_id": "s2",
            },
            {
                "data": {
                    "company_name": "Abb",
                    "employee_count": 140000,
                    "_quotes": {
                        "company_name": "Abb Group",
                        "employee_count": "140-year history",
                    },
                },
                "confidence": 0.7,
                "grounding_scores": {"company_name": 1.0, "employee_count": 0.0},
                "source_id": "s3",
            },
        ]
        field_definitions = [
            {"name": "company_name", "field_type": "string"},
            {"name": "employee_count", "field_type": "integer"},
        ]
        record = consolidate_extractions(
            extractions, field_definitions, "test_group", "company_info"
        )
        assert isinstance(record, ConsolidatedRecord)
        assert record.fields["company_name"].value == "ABB"
        # 140000 has weight 0 (ungrounded) -> excluded from median
        assert record.fields["employee_count"].value == 105000

    def test_all_ungrounded_fallback(self):
        """When all values are ungrounded, use best available."""
        extractions = [
            {
                "data": {"employee_count": 5000},
                "confidence": 0.9,
                "grounding_scores": {"employee_count": 0.0},
                "source_id": "s1",
            },
            {
                "data": {"employee_count": 3000},
                "confidence": 0.7,
                "grounding_scores": {"employee_count": 0.0},
                "source_id": "s2",
            },
        ]
        field_definitions = [{"name": "employee_count", "field_type": "integer"}]
        record = consolidate_extractions(
            extractions, field_definitions, "g", "company_info"
        )
        # Fallback to unweighted median when all weights are 0
        result = record.fields["employee_count"].value
        assert result is not None

    def test_provenance_tracking(self):
        extractions = [
            {
                "data": {"company_name": "X"},
                "confidence": 0.9,
                "grounding_scores": {"company_name": 1.0},
                "source_id": "src-1",
            },
            {
                "data": {"company_name": "X"},
                "confidence": 0.8,
                "grounding_scores": {"company_name": 1.0},
                "source_id": "src-2",
            },
        ]
        field_definitions = [{"name": "company_name", "field_type": "string"}]
        record = consolidate_extractions(
            extractions, field_definitions, "g", "company_info"
        )
        assert record.fields["company_name"].source_count == 2
        assert record.fields["company_name"].grounded_count == 2

    def test_empty_extractions(self):
        record = consolidate_extractions([], [], "g", "t")
        assert record.fields == {}

    def test_mixed_field_types(self):
        extractions = [
            {
                "data": {
                    "company_name": "ABB",
                    "employee_count": 100,
                    "is_public": True,
                    "description": "A global technology leader",
                },
                "confidence": 0.9,
                "grounding_scores": {
                    "company_name": 1.0,
                    "employee_count": 1.0,
                },
                "source_id": "s1",
            },
            {
                "data": {
                    "company_name": "ABB",
                    "employee_count": 100,
                    "is_public": True,
                    "description": "ABB is a pioneering technology leader in electrification and automation",
                },
                "confidence": 0.85,
                "grounding_scores": {
                    "company_name": 1.0,
                    "employee_count": 1.0,
                },
                "source_id": "s2",
            },
            {
                "data": {
                    "company_name": "ABB",
                    "is_public": True,
                },
                "confidence": 0.8,
                "grounding_scores": {"company_name": 1.0},
                "source_id": "s3",
            },
        ]
        field_definitions = [
            {"name": "company_name", "field_type": "string"},
            {"name": "employee_count", "field_type": "integer"},
            {"name": "is_public", "field_type": "boolean"},
            {"name": "description", "field_type": "text"},
        ]
        record = consolidate_extractions(
            extractions, field_definitions, "g", "company_info"
        )
        assert record.fields["company_name"].value == "ABB"
        assert record.fields["employee_count"].value == 100
        assert record.fields["is_public"].value is True
        # Longest description picked
        assert "pioneering" in record.fields["description"].value

    def test_source_group_and_type_set(self):
        record = consolidate_extractions([], [], "my_group", "my_type")
        assert record.source_group == "my_group"
        assert record.extraction_type == "my_type"

    def test_null_values_excluded(self):
        """Fields with None value in an extraction should be skipped."""
        extractions = [
            {
                "data": {"company_name": "ABB", "employee_count": None},
                "confidence": 0.9,
                "grounding_scores": {"company_name": 1.0},
                "source_id": "s1",
            },
            {
                "data": {"company_name": "ABB", "employee_count": 100},
                "confidence": 0.8,
                "grounding_scores": {"company_name": 1.0, "employee_count": 1.0},
                "source_id": "s2",
            },
        ]
        field_definitions = [
            {"name": "company_name", "field_type": "string"},
            {"name": "employee_count", "field_type": "integer"},
        ]
        record = consolidate_extractions(extractions, field_definitions, "g", "t")
        assert record.fields["employee_count"].value == 100
        assert record.fields["employee_count"].source_count == 1


# ── Fix: _dedup_dicts should deduplicate nameless items by content hash ──


class TestDedupDictsContentHash:
    def test_identical_nameless_dicts_deduplicated(self):
        """Two identical dicts without name/id should produce one result."""
        items = [
            {"type": "pump", "size": "large"},
            {"type": "pump", "size": "large"},
        ]
        result = _dedup_dicts(items)
        assert len(result) == 1

    def test_different_nameless_dicts_kept(self):
        """Different dicts without name/id should both appear."""
        items = [
            {"type": "pump", "size": "large"},
            {"type": "motor", "size": "small"},
        ]
        result = _dedup_dicts(items)
        assert len(result) == 2

    def test_named_dicts_still_dedup_by_name(self):
        """Dicts with name field still dedup by name (existing behavior)."""
        items = [
            {"name": "Widget", "price": 10},
            {"name": "widget", "price": 20},
        ]
        result = _dedup_dicts(items)
        assert len(result) == 1

    def test_merges_attributes_across_duplicates(self):
        """H1: Duplicate entities merge attributes from later occurrences."""
        items = [
            {"name": "Product X", "power": "100 kW"},
            {"name": "Product X", "power": "100 kW", "efficiency": "92%"},
            {"name": "Product X", "weight": "50 kg"},
        ]
        result = _dedup_dicts(items)
        assert len(result) == 1
        assert result[0]["name"] == "Product X"
        assert result[0]["power"] == "100 kW"  # from first occurrence
        assert result[0]["efficiency"] == "92%"  # filled from second
        assert result[0]["weight"] == "50 kg"  # filled from third

    def test_first_occurrence_value_wins(self):
        """First non-null value for a key is kept; later values don't overwrite."""
        items = [
            {"name": "X", "power": "100 kW"},
            {"name": "X", "power": "200 kW"},
        ]
        result = _dedup_dicts(items)
        assert result[0]["power"] == "100 kW"

    def test_fills_none_values(self):
        """None values in first occurrence are filled from later ones."""
        items = [
            {"name": "X", "power": None, "weight": "50 kg"},
            {"name": "X", "power": "100 kW", "weight": None},
        ]
        result = _dedup_dicts(items)
        assert result[0]["power"] == "100 kW"
        assert result[0]["weight"] == "50 kg"


# ── Per-field grounding_mode and consolidation_strategy override ──


class TestPerFieldOverrides:
    def test_grounding_mode_override_in_consolidation(self):
        """Field-level grounding_mode overrides type default."""
        extractions = [
            {
                "data": {"description": "A great company"},
                "confidence": 0.9,
                "grounding_scores": {"description": 0.0},
                "source_id": "s1",
            },
        ]
        # Default: string -> required, so weight = 0.9 * max(0.0, 0.1) = 0.09
        field_defs_default = [{"name": "description", "field_type": "string"}]
        record_default = consolidate_extractions(
            extractions, field_defs_default, "g", "t"
        )
        default_weight = record_default.fields["description"].grounded_count

        # Override: grounding_mode=none -> weight = confidence only = 0.9
        field_defs_override = [
            {"name": "description", "field_type": "string", "grounding_mode": "none"},
        ]
        record_override = consolidate_extractions(
            extractions, field_defs_override, "g", "t"
        )
        # With mode=none, grounding_score doesn't affect weight
        assert record_override.fields["description"].value == "A great company"
        # grounded_count should still be 1 (weight > 0 in both cases)
        assert record_override.fields["description"].grounded_count == 1

    def test_consolidation_strategy_override(self):
        """Field-level consolidation_strategy overrides type default."""
        extractions = [
            {
                "data": {"count": 100},
                "confidence": 0.9,
                "grounding_scores": {"count": 1.0},
                "source_id": "s1",
            },
            {
                "data": {"count": 100},
                "confidence": 0.8,
                "grounding_scores": {"count": 1.0},
                "source_id": "s2",
            },
            {
                "data": {"count": 200},
                "confidence": 0.7,
                "grounding_scores": {"count": 1.0},
                "source_id": "s3",
            },
        ]
        # Default for integer: weighted_median -> 100
        field_defs_default = [{"name": "count", "field_type": "integer"}]
        record_default = consolidate_extractions(
            extractions, field_defs_default, "g", "t"
        )
        assert record_default.fields["count"].strategy == "weighted_median"

        # Override: frequency -> 100 (most common)
        field_defs_override = [
            {"name": "count", "field_type": "integer", "consolidation_strategy": "frequency"},
        ]
        record_override = consolidate_extractions(
            extractions, field_defs_override, "g", "t"
        )
        assert record_override.fields["count"].strategy == "frequency"
        assert record_override.fields["count"].value == 100
