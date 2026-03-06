"""Consolidation strategies for merging multiple extractions into one record.

All pure functions with zero external dependencies. Turns 10-26 raw
extractions per entity into 1 reliable consolidated record with
provenance tracking.

Strategy defaults by field type:
    string   -> frequency
    integer  -> weighted_median
    float    -> weighted_median
    boolean  -> any_true
    text     -> longest_top_k
    list     -> union_dedup
    enum     -> frequency
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from services.extraction.grounding import GROUNDING_DEFAULTS

# Default consolidation strategy per field type
STRATEGY_DEFAULTS: dict[str, str] = {
    "string": "frequency",
    "integer": "weighted_median",
    "float": "weighted_median",
    "boolean": "any_true",
    "text": "longest_top_k",
    "list": "union_dedup",
    "enum": "frequency",
}


@dataclass(frozen=True)
class WeightedValue:
    """A value with its quality weight."""

    value: Any
    weight: float
    source_id: str = ""


@dataclass(frozen=True)
class ConsolidatedField:
    """Result of consolidating one field across multiple extractions."""

    value: Any
    strategy: str
    source_count: int
    grounded_count: int = 0
    agreement: float = 0.0
    top_sources: list[str] = field(default_factory=list)


@dataclass
class ConsolidatedRecord:
    """One consolidated record per (source_group, extraction_type)."""

    source_group: str
    extraction_type: str
    fields: dict[str, ConsolidatedField] = field(default_factory=dict)


# ── Strategy functions ──


def frequency(values: list[WeightedValue]) -> Any:
    """Most frequent non-null value, case-insensitive for strings.

    Ties broken by total weight. Returns the original-case most common form.
    """
    if not values:
        return None

    non_null = [v for v in values if v.value is not None]
    if not non_null:
        return None

    # Group by normalized key
    groups: dict[str, list[WeightedValue]] = {}
    for wv in non_null:
        key = (
            str(wv.value).strip().lower()
            if isinstance(wv.value, str)
            else str(wv.value)
        )
        groups.setdefault(key, []).append(wv)

    # Pick group with most occurrences, ties broken by total weight
    best_key = max(
        groups, key=lambda k: (len(groups[k]), sum(v.weight for v in groups[k]))
    )
    best_group = groups[best_key]

    # Return the most common original-case form from winning group
    if isinstance(best_group[0].value, str):
        form_counts: dict[str, int] = {}
        for wv in best_group:
            form_counts[wv.value] = form_counts.get(wv.value, 0) + 1
        return max(form_counts, key=form_counts.get)

    return best_group[0].value


def weighted_frequency(values: list[WeightedValue]) -> Any:
    """Sum weights per unique value, pick highest.

    For headquarters_location, string detail fields.
    """
    if not values:
        return None

    non_null = [v for v in values if v.value is not None]
    if not non_null:
        return None

    weight_sums: dict[str, float] = {}
    originals: dict[str, Any] = {}
    for wv in non_null:
        key = (
            str(wv.value).strip().lower()
            if isinstance(wv.value, str)
            else str(wv.value)
        )
        weight_sums[key] = weight_sums.get(key, 0.0) + wv.weight
        if key not in originals:
            originals[key] = wv.value

    best_key = max(weight_sums, key=weight_sums.get)
    return originals[best_key]


def weighted_median(values: list[WeightedValue]) -> float | int | None:
    """Weighted median of numeric values.

    Excludes weight=0 values. Falls back to unweighted median if all
    weights are 0.
    """
    if not values:
        return None

    # Filter to numeric non-null values
    numeric = [
        (wv.value, wv.weight) for wv in values if isinstance(wv.value, (int, float))
    ]
    if not numeric:
        return None

    # Check if all weights are zero -> fallback to unweighted
    total_weight = sum(w for _, w in numeric)
    if total_weight == 0:
        sorted_vals = sorted(v for v, _ in numeric)
        return _median(sorted_vals)

    # Exclude zero-weight values
    weighted = [(v, w) for v, w in numeric if w > 0]
    if not weighted:
        sorted_vals = sorted(v for v, _ in numeric)
        return _median(sorted_vals)

    # Sort by value
    weighted.sort(key=lambda x: x[0])
    total = sum(w for _, w in weighted)

    # Walk through cumulative weight to find median point
    cumulative = 0.0
    half = total / 2.0

    for i, (val, w) in enumerate(weighted):
        cumulative += w
        if cumulative >= half:
            # If we're exactly at halfway with even number, average with next
            if cumulative == half and i + 1 < len(weighted):
                next_val = weighted[i + 1][0]
                result = (val + next_val) / 2
            else:
                result = val

            # Preserve int type if all inputs were int
            if all(isinstance(v, int) for v, _ in numeric):
                return int(round(result))
            return result

    # Shouldn't reach here, but return last value
    return weighted[-1][0]


def any_true(values: list[WeightedValue], min_count: int = 2) -> bool | None:
    """True if min_count+ values are True with weight > 0.

    Returns None if insufficient evidence (fewer than min_count weighted
    True values). Returns False if all values are False.
    """
    if not values:
        return None

    weighted_true_count = sum(1 for v in values if v.value is True and v.weight > 0)
    has_any_true = any(v.value is True for v in values)
    all_weighted_false = all(
        v.value is False for v in values if v.weight > 0 and isinstance(v.value, bool)
    )

    if weighted_true_count >= min_count:
        return True
    if all_weighted_false and not has_any_true:
        return False
    if not has_any_true and any(v.value is False for v in values):
        return False
    return None


def longest_top_k(values: list[WeightedValue], k: int = 3) -> str | None:
    """Longest value from top-K by weight.

    For descriptions and free text fields.
    """
    if not values:
        return None

    non_null = [v for v in values if v.value is not None and isinstance(v.value, str)]
    if not non_null:
        return None

    # Sort by weight descending, take top K
    top_k = sorted(non_null, key=lambda v: v.weight, reverse=True)[:k]

    # Return the longest string among top K
    return max(top_k, key=lambda v: len(v.value)).value


def union_dedup(values: list[WeightedValue]) -> list:
    """Union all list values, deduplicate by normalized name.

    Handles both string lists and entity dicts (deduped by 'name' key).
    Keeps first occurrence as canonical form.
    """
    if not values:
        return []

    # Flatten all values into a single list
    all_items: list = []
    for wv in values:
        if isinstance(wv.value, list):
            all_items.extend(wv.value)
        elif wv.value is not None:
            all_items.append(wv.value)

    if not all_items:
        return []

    # Detect if items are dicts (entity lists)
    if isinstance(all_items[0], dict):
        return _dedup_dicts(all_items)

    return _dedup_strings(all_items)


# ── Weight calculation ──


def effective_weight(
    confidence: float,
    grounding_score: float | None,
    grounding_mode: str,
) -> float:
    """Compute effective weight from confidence and grounding.

    required + score < 0.5 -> 0.0 (exclude ungrounded)
    required + score >= 0.5 -> confidence * grounding_score
    semantic/none -> confidence only
    """
    if grounding_mode == "required":
        if grounding_score is None or grounding_score < 0.5:
            return 0.0
        return confidence * grounding_score
    # semantic or none: grounding doesn't affect weight
    return confidence


# ── Orchestrator ──


def consolidate_field(
    values: list[WeightedValue],
    strategy: str,
    **kwargs: Any,
) -> ConsolidatedField:
    """Apply a named strategy to a list of weighted values."""
    if not values:
        return ConsolidatedField(
            value=None,
            strategy=strategy,
            source_count=0,
            grounded_count=0,
            agreement=0.0,
        )

    strategies = {
        "frequency": frequency,
        "weighted_frequency": weighted_frequency,
        "weighted_median": weighted_median,
        "any_true": any_true,
        "longest_top_k": longest_top_k,
        "union_dedup": union_dedup,
    }

    func = strategies.get(strategy, frequency)
    result_value = func(values, **kwargs) if kwargs else func(values)

    # Compute agreement: fraction of values matching result
    if result_value is not None and not isinstance(result_value, list):
        matching = sum(
            1
            for v in values
            if v.value is not None
            and (
                str(v.value).strip().lower() == str(result_value).strip().lower()
                if isinstance(v.value, str)
                else v.value == result_value
            )
        )
        agreement = matching / len(values) if values else 0.0
    else:
        agreement = 1.0 if result_value is not None else 0.0

    grounded_count = sum(1 for v in values if v.weight > 0)
    top_sources = [v.source_id for v in values if v.source_id][:5]

    return ConsolidatedField(
        value=result_value,
        strategy=strategy,
        source_count=len(values),
        grounded_count=grounded_count,
        agreement=round(agreement, 4),
        top_sources=top_sources,
    )


def consolidate_extractions(
    extractions: list[dict],
    field_definitions: list[dict],
    source_group: str,
    extraction_type: str,
) -> ConsolidatedRecord:
    """Produce one consolidated record from N extractions.

    Args:
        extractions: List of dicts with keys: data, confidence,
            grounding_scores, source_id.
        field_definitions: From schema: name, field_type,
            optional consolidation_strategy.
        source_group: Source group identifier.
        extraction_type: Extraction type name.

    Returns:
        ConsolidatedRecord with one ConsolidatedField per field.
    """
    record = ConsolidatedRecord(
        source_group=source_group,
        extraction_type=extraction_type,
    )

    if not extractions or not field_definitions:
        return record

    for field_def in field_definitions:
        field_name = field_def["name"]
        field_type = field_def.get("field_type", "string")
        strategy = field_def.get("consolidation_strategy") or STRATEGY_DEFAULTS.get(
            field_type, "frequency"
        )
        grounding_mode = GROUNDING_DEFAULTS.get(field_type, "required")

        # Build weighted values from all extractions
        weighted_values: list[WeightedValue] = []
        for ext in extractions:
            data = ext.get("data", {})
            value = data.get(field_name)
            if value is None:
                continue

            confidence = ext.get("confidence", 0.5)
            grounding_scores = ext.get("grounding_scores") or {}
            grounding_score = grounding_scores.get(field_name)
            source_id = ext.get("source_id", "")

            weight = effective_weight(confidence, grounding_score, grounding_mode)
            weighted_values.append(WeightedValue(value, weight, str(source_id)))

        if not weighted_values:
            continue

        result = consolidate_field(weighted_values, strategy)
        record.fields[field_name] = result

    return record


# ── Internal helpers ──


def _median(sorted_vals: list) -> Any:
    """Simple unweighted median of a sorted list."""
    n = len(sorted_vals)
    if n == 0:
        return None
    if n % 2 == 1:
        return sorted_vals[n // 2]
    mid = n // 2
    result = (sorted_vals[mid - 1] + sorted_vals[mid]) / 2
    if all(isinstance(v, int) for v in sorted_vals):
        return int(round(result))
    return result


def _dedup_strings(items: list) -> list:
    """Deduplicate string items, keeping first occurrence."""
    seen: set[str] = set()
    result: list = []
    for item in items:
        key = str(item).strip().lower()
        if key not in seen:
            seen.add(key)
            result.append(item)
    return result


def _dedup_dicts(items: list[dict]) -> list[dict]:
    """Deduplicate dict items by 'name' field, keeping first occurrence."""
    seen: set[str] = set()
    result: list[dict] = []
    for item in items:
        name = item.get("name") or item.get("product_name") or item.get("id", "")
        key = str(name).strip().lower()
        if key and key not in seen:
            seen.add(key)
            result.append(item)
        elif not key:
            result.append(item)
    return result
