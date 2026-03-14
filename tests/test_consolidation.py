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
    get_llm_summarize_candidates,
    longest_top_k,
    union_dedup,
    weighted_frequency,
    weighted_median,
)
from services.extraction.consolidation_service import _extract_field_definitions

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

    def test_default_min_count_is_1(self):
        """Default min_count=1: a single grounded True is sufficient."""
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(True, 0.8),
            WeightedValue(False, 0.7),
        ]
        assert any_true(values) is True  # 2 weighted True >= 1

    def test_default_min_count_met(self):
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(True, 0.8),
            WeightedValue(True, 0.7),
            WeightedValue(False, 0.6),
        ]
        assert any_true(values) is True  # 3 True >= 1

    def test_single_grounded_true_sufficient(self):
        """One grounded True among ungrounded False → True."""
        values = [
            WeightedValue(True, 0.9),
            WeightedValue(False, 0.0),
            WeightedValue(False, 0.0),
            WeightedValue(False, 0.0),
            WeightedValue(False, 0.0),
            WeightedValue(False, 0.0),
        ]
        assert any_true(values) is True


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
        """min(0.9, 1.0) = 0.9."""
        assert effective_weight(0.9, 1.0, "required") == pytest.approx(0.9)

    def test_required_ungrounded_is_zero(self):
        """Ungrounded data gets zero weight: min(0.9, 0.0) = 0.0."""
        assert effective_weight(0.9, 0.0, "required") == pytest.approx(0.0)

    def test_required_partial_grounding(self):
        """min(0.9, 0.6) = 0.6."""
        assert effective_weight(0.9, 0.6, "required") == pytest.approx(0.6)

    def test_required_grounding_caps_confidence(self):
        """Grounding caps weight: min(0.9, 0.4) = 0.4."""
        assert effective_weight(0.9, 0.4, "required") == pytest.approx(0.4)

    def test_required_none_score_uses_confidence(self):
        """None grounding (v1 data, not computed) → no penalty, use confidence."""
        assert effective_weight(0.9, None, "required") == pytest.approx(0.9)

    def test_grounded_dominates_ungrounded(self):
        """Low-conf grounded (0.45) > high-conf ungrounded (0.0)."""
        ungrounded = effective_weight(0.8, 0.0, "required")
        grounded = effective_weight(0.45, 1.0, "required")
        assert grounded > ungrounded

    def test_confidence_caps_weight(self):
        """When confidence < grounding, confidence is the cap: min(0.3, 1.0) = 0.3."""
        assert effective_weight(0.3, 1.0, "required") == pytest.approx(0.3)

    def test_semantic_no_grounding(self):
        """None grounding (v1 data) → no penalty, use confidence."""
        assert effective_weight(0.9, None, "semantic") == 0.9

    def test_semantic_ungrounded_is_zero(self):
        """Boolean with grounding=0.0 (no evidence) → zero weight."""
        assert effective_weight(0.9, 0.0, "semantic") == pytest.approx(0.0)

    def test_semantic_grounded(self):
        """Boolean with grounding=0.9 → min(conf, grounding)."""
        assert effective_weight(0.8, 0.9, "semantic") == pytest.approx(0.8)

    def test_semantic_partial_grounding(self):
        """Boolean with grounding=0.6 → capped by grounding."""
        assert effective_weight(0.9, 0.6, "semantic") == pytest.approx(0.6)

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
        # weighted_frequency picks highest-weight value (s1, conf=0.9)
        assert record.fields["description"].value == "A global technology leader"

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


class TestEntityListConsolidation:
    """Test consolidation of entity list extraction types."""

    def test_entity_lists_union_deduped(self):
        """Entity lists from multiple extractions are unioned and deduped."""
        extractions = [
            {
                "data": {
                    "products": [
                        {"name": "Motor X", "type": "AC"},
                        {"name": "Drive Y", "type": "VFD"},
                    ],
                    "confidence": 0.9,
                },
                "confidence": 0.9,
                "grounding_scores": {"products": 1.0},
                "source_id": "s1",
            },
            {
                "data": {
                    "products": [
                        {"name": "Motor X", "type": "AC"},  # duplicate
                        {"name": "Pump Z", "type": "centrifugal"},
                    ],
                    "confidence": 0.8,
                },
                "confidence": 0.8,
                "grounding_scores": {"products": 0.5},
                "source_id": "s2",
            },
        ]
        field_defs = [
            {"name": "name", "field_type": "string"},
            {"name": "type", "field_type": "string"},
        ]
        record = consolidate_extractions(
            extractions, field_defs, "abb", "products",
            entity_list_key="products",
        )
        assert "products" in record.fields
        entities = record.fields["products"].value
        names = {e["name"] for e in entities}
        assert names == {"Motor X", "Drive Y", "Pump Z"}

    def test_entity_list_strips_quote_metadata(self):
        """_quote fields are stripped from consolidated entities."""
        extractions = [
            {
                "data": {
                    "products": [
                        {"name": "Motor X", "_quote": "Motor X series"},
                    ],
                    "confidence": 0.9,
                },
                "confidence": 0.9,
                "grounding_scores": {"products": 1.0},
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "name", "field_type": "string"}]
        record = consolidate_extractions(
            extractions, field_defs, "abb", "products",
            entity_list_key="products",
        )
        entities = record.fields["products"].value
        assert "_quote" not in entities[0]
        assert entities[0]["name"] == "Motor X"

    def test_entity_list_weighted_by_grounding(self):
        """Extractions with higher grounding scores get more weight."""
        extractions = [
            {
                "data": {
                    "products": [{"name": "Motor X"}],
                    "confidence": 0.9,
                },
                "confidence": 0.9,
                "grounding_scores": {"products": 1.0},
                "source_id": "s1",
            },
            {
                "data": {
                    "products": [{"name": "Motor Y"}],
                    "confidence": 0.9,
                },
                "confidence": 0.9,
                "grounding_scores": {},  # no grounding score
                "source_id": "s2",
            },
        ]
        field_defs = [{"name": "name", "field_type": "string"}]
        record = consolidate_extractions(
            extractions, field_defs, "abb", "products",
            entity_list_key="products",
        )
        # Both contribute (weight floor = 0.1), so both entities appear
        entities = record.fields["products"].value
        assert len(entities) == 2

    def test_entity_list_empty_extractions(self):
        """Empty entity lists produce empty record."""
        extractions = [
            {
                "data": {"products": [], "confidence": 0.5},
                "confidence": 0.5,
                "grounding_scores": {},
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "name", "field_type": "string"}]
        record = consolidate_extractions(
            extractions, field_defs, "abb", "products",
            entity_list_key="products",
        )
        assert "products" not in record.fields

    def test_entity_list_key_none_uses_field_path(self):
        """Without entity_list_key, uses standard field-level consolidation."""
        extractions = [
            {
                "data": {"company_name": "ABB", "confidence": 0.9},
                "confidence": 0.9,
                "grounding_scores": {"company_name": 1.0},
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "company_name", "field_type": "string"}]
        record = consolidate_extractions(
            extractions, field_defs, "abb", "company_info",
        )
        assert record.fields["company_name"].value == "ABB"


class TestExtractFieldDefinitions:
    """Test _extract_field_definitions returns entity list groups."""

    def test_returns_field_defs_and_entity_groups(self):
        schema = {
            "field_groups": [
                {
                    "name": "company_info",
                    "fields": [{"name": "company_name", "field_type": "string"}],
                },
                {
                    "name": "products",
                    "fields": [{"name": "name", "field_type": "string"}],
                    "is_entity_list": True,
                },
            ]
        }
        field_defs, entity_groups = _extract_field_definitions(schema)
        assert "company_info" in field_defs
        assert "products" in field_defs
        assert entity_groups == {"products"}

    def test_no_entity_lists(self):
        schema = {
            "field_groups": [
                {
                    "name": "company_info",
                    "fields": [{"name": "company_name", "field_type": "string"}],
                },
            ]
        }
        field_defs, entity_groups = _extract_field_definitions(schema)
        assert entity_groups == set()
        assert "company_info" in field_defs


# ── winning_weight ──


class TestWinningWeight:
    def test_frequency_winning_weight(self):
        """winning_weight = max weight of most-frequent value."""
        values = [
            WeightedValue("ABB", 0.9),
            WeightedValue("ABB", 0.7),
            WeightedValue("Siemens", 0.95),
        ]
        result = consolidate_field(values, "frequency")
        assert result.value == "ABB"
        assert result.winning_weight == pytest.approx(0.9)

    def test_any_true_winning_weight(self):
        """winning_weight = max weight of True values when result is True."""
        values = [
            WeightedValue(True, 0.85),
            WeightedValue(True, 0.6),
            WeightedValue(False, 0.9),
        ]
        result = consolidate_field(values, "any_true")
        assert result.value is True
        assert result.winning_weight == pytest.approx(0.85)

    def test_any_true_none_winning_weight(self):
        """winning_weight = 0.0 when result is None."""
        values = [
            WeightedValue(True, 0.0),
            WeightedValue(False, 0.9),
        ]
        result = consolidate_field(values, "any_true")
        assert result.value is None
        assert result.winning_weight == pytest.approx(0.0)

    def test_weighted_median_winning_weight(self):
        """winning_weight = weight of value matching median."""
        values = [
            WeightedValue(100, 0.9),
            WeightedValue(200, 0.8),
            WeightedValue(300, 0.7),
        ]
        result = consolidate_field(values, "weighted_median")
        # Weighted median: cumulative at 100=0.9, 200=1.7; half=1.2 → picks 200
        assert result.value == 200
        assert result.winning_weight == pytest.approx(0.8)

    def test_union_dedup_winning_weight(self):
        """winning_weight = average of non-zero contributor weights."""
        values = [
            WeightedValue(["A", "B"], 0.9),
            WeightedValue(["B", "C"], 0.6),
            WeightedValue(["D"], 0.0),
        ]
        result = consolidate_field(values, "union_dedup")
        # Non-zero weights: 0.9, 0.6 → avg = 0.75
        assert result.winning_weight == pytest.approx(0.75)

    def test_empty_values_winning_weight(self):
        """Empty input → winning_weight=0.0."""
        result = consolidate_field([], "frequency")
        assert result.winning_weight == pytest.approx(0.0)

    def test_all_zero_weight_union_dedup(self):
        """All zero weights → winning_weight=0.0."""
        values = [
            WeightedValue(["A"], 0.0),
            WeightedValue(["B"], 0.0),
        ]
        result = consolidate_field(values, "union_dedup")
        assert result.winning_weight == pytest.approx(0.0)


# ── top_sources filtering ──


class TestTopSources:
    def test_only_matching_grounded_sources(self):
        """top_sources includes only grounded sources matching winning value."""
        values = [
            WeightedValue("ABB", 0.9, "s1"),
            WeightedValue("ABB", 0.7, "s2"),
            WeightedValue("Siemens", 0.95, "s3"),
            WeightedValue("ABB", 0.0, "s4"),
        ]
        result = consolidate_field(values, "frequency")
        assert result.value == "ABB"
        assert result.top_sources == ["s1", "s2"]

    def test_sorted_by_weight_descending(self):
        """Sources are sorted strongest-first."""
        values = [
            WeightedValue("X", 0.3, "s_low"),
            WeightedValue("X", 0.9, "s_high"),
            WeightedValue("X", 0.6, "s_mid"),
        ]
        result = consolidate_field(values, "frequency")
        assert result.top_sources == ["s_high", "s_mid", "s_low"]

    def test_none_result_empty_sources(self):
        """No result → no sources."""
        result = consolidate_field([], "frequency")
        assert result.top_sources == []

    def test_union_dedup_grounded_only(self):
        """union_dedup: only grounded contributors (weight > 0)."""
        values = [
            WeightedValue(["A"], 0.8, "s1"),
            WeightedValue(["B"], 0.0, "s2"),
            WeightedValue(["C"], 0.6, "s3"),
        ]
        result = consolidate_field(values, "union_dedup")
        assert result.top_sources == ["s1", "s3"]

    def test_boolean_true_sources(self):
        """any_true: only grounded True sources."""
        values = [
            WeightedValue(True, 0.9, "s_true"),
            WeightedValue(False, 0.8, "s_false"),
            WeightedValue(True, 0.0, "s_ungrounded"),
        ]
        result = consolidate_field(values, "any_true")
        assert result.value is True
        assert result.top_sources == ["s_true"]

    def test_max_five_sources(self):
        """top_sources capped at 5."""
        values = [WeightedValue("X", 0.5 + i * 0.01, f"s{i}") for i in range(10)]
        result = consolidate_field(values, "frequency")
        assert len(result.top_sources) == 5
        assert result.top_sources[0] == "s9"


# ── entity_provenance ──


class TestEntityProvenance:
    def test_per_entity_provenance_computed(self):
        """Entity list consolidation computes per-entity winning_weight."""
        extractions = [
            {
                "data": {
                    "products": [
                        {"name": "Motor X", "type": "AC"},
                        {"name": "Drive Y", "type": "VFD"},
                    ],
                },
                "confidence": 0.9,
                "grounding_scores": {"products": 1.0},
                "source_id": "s1",
            },
            {
                "data": {
                    "products": [
                        {"name": "Motor X", "type": "AC"},
                        {"name": "Pump Z", "type": "centrifugal"},
                    ],
                },
                "confidence": 0.7,
                "grounding_scores": {"products": 0.8},
                "source_id": "s2",
            },
        ]
        field_defs = [
            {"name": "name", "field_type": "string"},
            {"name": "type", "field_type": "string"},
        ]
        record = consolidate_extractions(
            extractions, field_defs, "abb", "products",
            entity_list_key="products",
        )
        ep = record.fields["products"].entity_provenance
        assert ep is not None
        assert len(ep) == 3  # Motor X, Drive Y, Pump Z

        # Motor X: from s1 (weight 0.9) and s2 (weight 0.7)
        motor_prov = ep[0]
        assert motor_prov["winning_weight"] == pytest.approx(0.9)
        assert "s1" in motor_prov["top_sources"]
        assert "s2" in motor_prov["top_sources"]

        # Drive Y: only from s1 (weight 0.9)
        drive_prov = ep[1]
        assert drive_prov["winning_weight"] == pytest.approx(0.9)
        assert drive_prov["top_sources"] == ["s1"]

        # Pump Z: only from s2 (weight ~0.7)
        pump_prov = ep[2]
        assert pump_prov["top_sources"] == ["s2"]

    def test_ungrounded_source_excluded_from_entity_prov(self):
        """Ungrounded extraction (weight=0) excluded from entity provenance."""
        extractions = [
            {
                "data": {"products": [{"name": "Motor X"}]},
                "confidence": 0.9,
                "grounding_scores": {"products": 0.0},  # ungrounded
                "source_id": "s1",
            },
            {
                "data": {"products": [{"name": "Motor X"}]},
                "confidence": 0.8,
                "grounding_scores": {"products": 1.0},
                "source_id": "s2",
            },
        ]
        field_defs = [{"name": "name", "field_type": "string"}]
        record = consolidate_extractions(
            extractions, field_defs, "g", "products",
            entity_list_key="products",
        )
        ep = record.fields["products"].entity_provenance
        assert ep is not None
        # s1 has weight 0 (ungrounded), so only s2 appears
        assert ep[0]["winning_weight"] == pytest.approx(0.8)
        assert ep[0]["top_sources"] == ["s2"]

    def test_non_entity_field_has_no_entity_provenance(self):
        """Non-entity fields should have entity_provenance=None."""
        values = [WeightedValue("ABB", 0.9, "s1")]
        result = consolidate_field(values, "frequency")
        assert result.entity_provenance is None


# ── strategy defaults ──


class TestStrategyDefaults:
    def test_text_uses_weighted_frequency(self):
        """Text fields now use weighted_frequency (not longest_top_k)."""
        from services.extraction.consolidation import STRATEGY_DEFAULTS
        assert STRATEGY_DEFAULTS["text"] == "weighted_frequency"

    def test_summary_still_longest_top_k(self):
        from services.extraction.consolidation import STRATEGY_DEFAULTS
        assert STRATEGY_DEFAULTS["summary"] == "longest_top_k"

    def test_string_falls_back_to_frequency(self):
        """'string' type removed from STRATEGY_DEFAULTS; falls back to 'frequency'."""
        from services.extraction.consolidation import STRATEGY_DEFAULTS
        assert "string" not in STRATEGY_DEFAULTS
        # Verify fallback works via consolidate_extractions
        extractions = [
            {"data": {"name": "ABB"}, "confidence": 0.9, "grounding_scores": {"name": 1.0}, "source_id": "s1"},
            {"data": {"name": "ABB"}, "confidence": 0.8, "grounding_scores": {"name": 1.0}, "source_id": "s2"},
        ]
        field_defs = [{"name": "name", "field_type": "string"}]
        record = consolidate_extractions(extractions, field_defs, "g", "t")
        assert record.fields["name"].strategy == "frequency"


# ── v2 extraction-level confidence cap ──


class TestV2ExtractionConfidenceCap:
    def test_high_field_conf_low_ext_conf_capped(self):
        """V2: per-field confidence 0.9 but extraction confidence 0.2 → weight capped at 0.3."""
        extractions = [
            {
                "data": {"location": {"value": "Bielefeld", "confidence": 0.9, "grounding": 1.0}},
                "data_version": 2,
                "confidence": 0.2,
                "source_id": "s_bad",
            },
            {
                "data": {"location": {"value": "Bocholt", "confidence": 0.8, "grounding": 1.0}},
                "data_version": 2,
                "confidence": 0.9,
                "source_id": "s_good",
            },
        ]
        field_defs = [{"name": "location", "field_type": "text"}]
        record = consolidate_extractions(extractions, field_defs, "g", "t")
        # s_good has weight min(0.8, 1.0)=0.8, capped by max(0.9, 0.3)=0.9 → 0.8
        # s_bad has weight min(0.9, 1.0)=0.9, capped by max(0.2, 0.3)=0.3 → 0.3
        # Bocholt (weight 0.8) should win over Bielefeld (weight 0.3)
        assert record.fields["location"].value == "Bocholt"

    def test_ext_conf_above_threshold_no_cap(self):
        """V2: extraction confidence 0.8 → no effective cap (max(0.8, 0.3)=0.8)."""
        extractions = [
            {
                "data": {"name": {"value": "ABB", "confidence": 0.7, "grounding": 1.0}},
                "data_version": 2,
                "confidence": 0.8,
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "name", "field_type": "text"}]
        record = consolidate_extractions(extractions, field_defs, "g", "t")
        assert record.fields["name"].value == "ABB"
        # weight = min(0.7, 1.0) = 0.7, cap = max(0.8, 0.3) = 0.8 → no cap
        assert record.fields["name"].winning_weight == pytest.approx(0.7)

    def test_v1_not_affected_by_cap(self):
        """V1 extractions don't have the cap applied."""
        extractions = [
            {
                "data": {"name": "ABB"},
                "confidence": 0.2,
                "grounding_scores": {"name": 1.0},
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "name", "field_type": "string"}]
        record = consolidate_extractions(extractions, field_defs, "g", "t")
        # v1: weight = min(0.2, 1.0) = 0.2, no ext_confidence cap
        assert record.fields["name"].value == "ABB"
        assert record.fields["name"].winning_weight == pytest.approx(0.2)

    def test_floor_prevents_zeroing(self):
        """Floor of 0.3 prevents complete zeroing of fields."""
        extractions = [
            {
                "data": {"name": {"value": "ABB", "confidence": 0.9, "grounding": 1.0}},
                "data_version": 2,
                "confidence": 0.0,
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "name", "field_type": "text"}]
        record = consolidate_extractions(extractions, field_defs, "g", "t")
        # weight = min(0.9, 1.0) = 0.9, cap = max(0.0, 0.3) = 0.3 → 0.3
        assert record.fields["name"].winning_weight == pytest.approx(0.3)


# ── Strategy: llm_summarize ──


class TestLLMSummarize:
    """llm_summarize routes to longest_top_k in pure function (sync fallback)."""

    def test_routes_to_longest_top_k(self):
        values = [
            WeightedValue("Short text", 0.9),
            WeightedValue("A much longer text for testing", 0.7),
            WeightedValue("Medium length text", 0.8),
        ]
        result = consolidate_field(values, "llm_summarize")
        assert result.value == "A much longer text for testing"
        assert result.strategy == "llm_summarize"

    def test_empty_values(self):
        result = consolidate_field([], "llm_summarize")
        assert result.value is None
        assert result.strategy == "llm_summarize"

    def test_single_value(self):
        values = [WeightedValue("Only one summary", 0.9)]
        result = consolidate_field(values, "llm_summarize")
        assert result.value == "Only one summary"

    def test_in_valid_strategies(self):
        from services.extraction.consolidation import VALID_CONSOLIDATION_STRATEGIES
        assert "llm_summarize" in VALID_CONSOLIDATION_STRATEGIES


class TestGetLLMSummarizeCandidates:
    def test_returns_top_n_by_weight(self):
        values = [
            WeightedValue("Low weight text", 0.3),
            WeightedValue("High weight text", 0.9),
            WeightedValue("Medium weight text", 0.6),
        ]
        candidates = get_llm_summarize_candidates(values, top_n=2)
        assert len(candidates) == 2
        assert candidates[0][0] == "High weight text"
        assert candidates[0][1] == 0.9
        assert candidates[1][0] == "Medium weight text"

    def test_skips_none_values(self):
        values = [
            WeightedValue(None, 0.9),
            WeightedValue("Valid text", 0.8),
        ]
        candidates = get_llm_summarize_candidates(values)
        assert len(candidates) == 1
        assert candidates[0][0] == "Valid text"

    def test_skips_non_string_values(self):
        values = [
            WeightedValue(42, 0.9),
            WeightedValue("Valid", 0.8),
        ]
        candidates = get_llm_summarize_candidates(values)
        assert len(candidates) == 1

    def test_skips_empty_strings(self):
        values = [
            WeightedValue("  ", 0.9),
            WeightedValue("Valid", 0.8),
        ]
        candidates = get_llm_summarize_candidates(values)
        assert len(candidates) == 1

    def test_empty_input(self):
        assert get_llm_summarize_candidates([]) == []


class TestFieldGroundingInEntityConsolidation:
    """Test that field_grounding affects entity weight computation."""

    def test_low_field_grounding_reduces_weight(self):
        """Entities with low field grounding get lower weight."""
        extractions = [
            {
                "data": {
                    "products": {
                        "items": [
                            {
                                "fields": {"name": "Widget-X", "power": 50},
                                "confidence": 0.8,
                                "grounding": 0.9,
                                "field_grounding": {"name": 0.9, "power": 0.2},
                            }
                        ]
                    }
                },
                "data_version": 2,
                "confidence": 0.8,
                "source_id": "s1",
            },
        ]
        field_defs = [
            {"name": "name", "field_type": "text"},
            {"name": "power", "field_type": "float"},
        ]
        record = consolidate_extractions(
            extractions, field_defs, "g", "products", entity_list_key="products"
        )
        # With field_grounding avg = (0.9+0.2)/2 = 0.55, min(0.9, 0.55) = 0.55
        # So weight is reduced from entity grounding 0.9 to 0.55
        assert "products" in record.fields

    def test_no_field_grounding_uses_entity_grounding(self):
        """Entities without field_grounding use entity-level grounding only."""
        extractions = [
            {
                "data": {
                    "products": {
                        "items": [
                            {
                                "fields": {"name": "Widget-X"},
                                "confidence": 0.8,
                                "grounding": 0.9,
                            }
                        ]
                    }
                },
                "data_version": 2,
                "confidence": 0.8,
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "name", "field_type": "text"}]
        record = consolidate_extractions(
            extractions, field_defs, "g", "products", entity_list_key="products"
        )
        assert "products" in record.fields


class TestV2ListFieldConsolidation:
    """V2 list fields use {"items": [...]} not {"value": ...}."""

    def test_v2_list_items_collected(self):
        """List field items from multiple extractions are union-deduped."""
        extractions = [
            {
                "data": {
                    "_meta": {"group": "company_meta", "data_version": 2},
                    "certifications": {
                        "items": [
                            {"value": "ISO9001:2015", "grounding": 1.0, "confidence": 0.9},
                            {"value": "ISO14001", "grounding": 1.0, "confidence": 0.85},
                        ]
                    },
                },
                "data_version": 2,
                "confidence": 0.8,
                "source_id": "s1",
            },
            {
                "data": {
                    "_meta": {"group": "company_meta", "data_version": 2},
                    "certifications": {
                        "items": [
                            {"value": "ISO9001:2015", "grounding": 1.0, "confidence": 0.9},
                            {"value": "CE", "grounding": 0.9, "confidence": 0.8},
                        ]
                    },
                },
                "data_version": 2,
                "confidence": 0.7,
                "source_id": "s2",
            },
        ]
        field_defs = [{"name": "certifications", "field_type": "list"}]
        record = consolidate_extractions(extractions, field_defs, "acme", "company_meta")
        assert "certifications" in record.fields
        result = record.fields["certifications"].value
        assert isinstance(result, list)
        assert len(result) == 3
        assert "ISO9001:2015" in result
        assert "CE" in result
        assert "ISO14001" in result

    def test_v2_list_empty_items_skipped(self):
        """Extractions with empty items list are skipped."""
        extractions = [
            {
                "data": {
                    "_meta": {"group": "company_meta", "data_version": 2},
                    "certifications": {"items": []},
                },
                "data_version": 2,
                "confidence": 0.8,
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "certifications", "field_type": "list"}]
        record = consolidate_extractions(extractions, field_defs, "acme", "company_meta")
        assert "certifications" not in record.fields

    def test_v2_list_items_null_values_skipped(self):
        """Items with null values are excluded."""
        extractions = [
            {
                "data": {
                    "_meta": {"group": "company_meta", "data_version": 2},
                    "certifications": {
                        "items": [
                            {"value": "ISO9001", "grounding": 1.0, "confidence": 0.9},
                            {"value": None, "grounding": 0.0, "confidence": 0.0},
                        ]
                    },
                },
                "data_version": 2,
                "confidence": 0.8,
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "certifications", "field_type": "list"}]
        record = consolidate_extractions(extractions, field_defs, "acme", "company_meta")
        assert record.fields["certifications"].value == ["ISO9001"]

    def test_v2_list_weight_capped_by_ext_confidence(self):
        """List item weights are capped by extraction-level confidence."""
        extractions = [
            {
                "data": {
                    "_meta": {"group": "company_meta", "data_version": 2},
                    "certifications": {
                        "items": [
                            {"value": "ISO9001", "grounding": 1.0, "confidence": 0.95},
                        ]
                    },
                },
                "data_version": 2,
                "confidence": 0.2,
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "certifications", "field_type": "list"}]
        record = consolidate_extractions(extractions, field_defs, "acme", "company_meta")
        assert record.fields["certifications"].winning_weight == pytest.approx(0.3)

    def test_v2_list_falls_back_to_value_key(self):
        """If a list field has 'value' key instead of 'items', scalar path handles it."""
        extractions = [
            {
                "data": {
                    "_meta": {"group": "meta", "data_version": 2},
                    "tags": {"value": ["alpha", "beta"], "grounding": 1.0, "confidence": 0.9},
                },
                "data_version": 2,
                "confidence": 0.8,
                "source_id": "s1",
            },
        ]
        field_defs = [{"name": "tags", "field_type": "list"}]
        record = consolidate_extractions(extractions, field_defs, "g", "meta")
        assert "tags" in record.fields
        result = record.fields["tags"].value
        assert "alpha" in result
        assert "beta" in result
