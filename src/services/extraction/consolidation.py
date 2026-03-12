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

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

from services.extraction.grounding import GROUNDING_DEFAULTS

# Default consolidation strategy per field type
VALID_CONSOLIDATION_STRATEGIES = frozenset(
    {"frequency", "weighted_frequency", "weighted_median", "any_true", "longest_top_k", "union_dedup"}
)

STRATEGY_DEFAULTS: dict[str, str] = {
    "string": "frequency",
    "integer": "weighted_median",
    "float": "weighted_median",
    "boolean": "any_true",
    "text": "longest_top_k",
    "list": "union_dedup",
    "enum": "frequency",
    "summary": "longest_top_k",
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
    winning_weight: float = 0.0
    top_sources: list[str] = field(default_factory=list)
    entity_provenance: list[dict] | None = None


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


def any_true(values: list[WeightedValue], min_count: int = 1) -> bool | None:
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

    Uses min(confidence, grounding) so that poorly grounded data is
    capped regardless of confidence. The grounding gate filters out
    fabricated data before consolidation, so remaining data should
    have grounding >= 0.8 or 0.0 (ungrounded).

    required  -> min(confidence, grounding_score)
    semantic  -> min(confidence, grounding_score) — booleans need
                 grounding evidence too; 0.0 means no supporting text
    none      -> confidence only (text/summary always grounded 1.0)
    """
    if grounding_mode == "none":
        return confidence
    if grounding_score is None:
        return confidence  # Unknown grounding (v1 data) → no penalty
    return min(confidence, grounding_score)


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
            winning_weight=0.0,
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

    # Compute winning_weight: quality signal for the chosen value
    if result_value is not None:
        if isinstance(result_value, list):
            # union_dedup: average weight of all contributors
            non_zero = [v.weight for v in values if v.weight > 0]
            winning_weight = sum(non_zero) / len(non_zero) if non_zero else 0.0
        else:
            # Scalar: max weight among values matching result
            matching_weights = [
                v.weight
                for v in values
                if v.value is not None
                and (
                    str(v.value).strip().lower() == str(result_value).strip().lower()
                    if isinstance(v.value, str)
                    else v.value == result_value
                )
            ]
            winning_weight = max(matching_weights) if matching_weights else 0.0
    else:
        winning_weight = 0.0

    grounded_count = sum(1 for v in values if v.weight > 0)

    # Top sources: only grounded values that match the winning result,
    # sorted by weight descending so the strongest evidence comes first.
    if result_value is not None:
        if isinstance(result_value, list):
            # union_dedup: all grounded contributors
            contributing = sorted(
                (v for v in values if v.source_id and v.weight > 0),
                key=lambda v: v.weight,
                reverse=True,
            )
        else:
            # Scalar: only sources whose value matches the winning result
            contributing = sorted(
                (
                    v
                    for v in values
                    if v.source_id
                    and v.weight > 0
                    and v.value is not None
                    and (
                        str(v.value).strip().lower()
                        == str(result_value).strip().lower()
                        if isinstance(v.value, str)
                        else v.value == result_value
                    )
                ),
                key=lambda v: v.weight,
                reverse=True,
            )
        top_sources = [v.source_id for v in contributing][:5]
    else:
        top_sources = []

    return ConsolidatedField(
        value=result_value,
        strategy=strategy,
        source_count=len(values),
        grounded_count=grounded_count,
        agreement=round(agreement, 4),
        winning_weight=round(winning_weight, 4),
        top_sources=top_sources,
    )


def consolidate_extractions(
    extractions: list[dict],
    field_definitions: list[dict],
    source_group: str,
    extraction_type: str,
    entity_list_key: str | None = None,
) -> ConsolidatedRecord:
    """Produce one consolidated record from N extractions.

    Args:
        extractions: List of dicts with keys: data, confidence,
            grounding_scores, source_id.
        field_definitions: From schema: name, field_type,
            optional consolidation_strategy.
        source_group: Source group identifier.
        extraction_type: Extraction type name.
        entity_list_key: If set, data is entity list format:
            {"key": [entity_dicts], "confidence": ...}. Consolidation
            unions all entity lists weighted by extraction quality.

    Returns:
        ConsolidatedRecord with one ConsolidatedField per field.
    """
    record = ConsolidatedRecord(
        source_group=source_group,
        extraction_type=extraction_type,
    )

    if not extractions or not field_definitions:
        return record

    if entity_list_key:
        return _consolidate_entity_list(
            extractions, field_definitions, record, entity_list_key
        )

    for field_def in field_definitions:
        field_name = field_def["name"]
        field_type = field_def.get("field_type", "string")
        strategy = field_def.get("consolidation_strategy") or STRATEGY_DEFAULTS.get(
            field_type, "frequency"
        )
        grounding_mode = field_def.get("grounding_mode") or GROUNDING_DEFAULTS.get(
            field_type, "required"
        )

        # Build weighted values from all extractions
        weighted_values: list[WeightedValue] = []
        for ext in extractions:
            data = ext.get("data", {})
            data_version = ext.get("data_version", 1)
            source_id = ext.get("source_id", "")

            if data_version >= 2:
                # v2: per-field confidence and grounding inside data
                field_data = data.get(field_name)
                if not isinstance(field_data, dict):
                    continue
                value = field_data.get("value")
                if value is None:
                    continue
                confidence = float(field_data.get("confidence", 0.5))
                grounding_score = float(field_data.get("grounding", 1.0))
            else:
                # v1: flat format
                value = data.get(field_name)
                if value is None:
                    continue
                confidence = ext.get("confidence", 0.5)
                grounding_scores = ext.get("grounding_scores") or {}
                grounding_score = grounding_scores.get(field_name)

            weight = effective_weight(confidence, grounding_score, grounding_mode)
            weighted_values.append(WeightedValue(value, weight, str(source_id)))

        if not weighted_values:
            continue

        result = consolidate_field(weighted_values, strategy)
        record.fields[field_name] = result

    return record


def _consolidate_entity_list(
    extractions: list[dict],
    field_definitions: list[dict],
    record: ConsolidatedRecord,
    entity_key: str,
) -> ConsolidatedRecord:
    """Consolidate entity list extractions via weighted union_dedup.

    Entity list data shape: {"products": [{"name": "X", ...}, ...], "confidence": 0.8}

    Strategy: collect entity lists from all extractions, weight each
    extraction's entities by confidence * grounding, then union_dedup
    the combined list. Produces a single consolidated field keyed by
    entity_key containing the deduped entity list.
    """
    weighted_values: list[WeightedValue] = []

    for ext in extractions:
        data = ext.get("data", {})
        data_version = ext.get("data_version", 1)
        source_id = ext.get("source_id", "")

        if data_version >= 2:
            # v2: entities are in data[entity_key]["items"]
            entity_data = data.get(entity_key, {})
            items = entity_data.get("items", []) if isinstance(entity_data, dict) else []
            if not items:
                continue
            # Extract fields from v2 entity items and compute weight per entity
            cleaned = []
            weights: list[float] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                fields = item.get("fields", item)
                cleaned.append({k: v for k, v in fields.items() if not str(k).startswith("_")})
                conf = float(item.get("confidence", 0.5))
                gnd = float(item.get("grounding", 1.0))
                weights.append(effective_weight(conf, gnd, "required"))
            if cleaned:
                avg_weight = sum(weights) / len(weights) if weights else 0.5
                weighted_values.append(WeightedValue(cleaned, avg_weight, str(source_id)))
        else:
            # v1: flat entity list
            entities = data.get(entity_key)
            if not entities or not isinstance(entities, list):
                continue

            confidence = ext.get("confidence", 0.5)
            grounding_scores = ext.get("grounding_scores") or {}
            grounding_score = grounding_scores.get(entity_key)

            weight = effective_weight(confidence, grounding_score, "required")

            cleaned = [
                {k: v for k, v in entity.items() if k != "_quote"}
                for entity in entities
                if isinstance(entity, dict)
            ]
            if cleaned:
                weighted_values.append(WeightedValue(cleaned, weight, str(source_id)))

    if not weighted_values:
        return record

    result = consolidate_field(weighted_values, "union_dedup")

    # Compute per-entity provenance by tracing each deduped entity
    # back to its source extraction(s).
    entity_prov = _compute_entity_provenance(result.value, weighted_values)

    # Replace the field with entity_provenance attached
    record.fields[entity_key] = ConsolidatedField(
        value=result.value,
        strategy=result.strategy,
        source_count=result.source_count,
        grounded_count=result.grounded_count,
        agreement=result.agreement,
        winning_weight=result.winning_weight,
        top_sources=result.top_sources,
        entity_provenance=entity_prov,
    )

    return record


# ── Internal helpers ──


def _entity_match_key(entity: dict) -> str:
    """Compute a dedup key for an entity dict, matching _dedup_dicts logic."""
    name = entity.get("name") or entity.get("product_name") or entity.get("id", "")
    key = str(name).strip().lower()
    if not key:
        key = hashlib.sha256(
            json.dumps(entity, sort_keys=True).encode()
        ).hexdigest()[:16]
    return key


def _compute_entity_provenance(
    deduped_entities: list[dict] | None,
    weighted_values: list[WeightedValue],
) -> list[dict]:
    """Compute per-entity provenance by tracing each entity back to sources.

    For each entity in the deduped result, find which source extraction(s)
    contributed it and compute winning_weight as the max weight among those.

    Returns:
        List of dicts (one per entity), each with winning_weight and top_sources.
    """
    if not deduped_entities:
        return []

    # Build index: match_key → [(weight, source_id)] from all input extractions
    source_index: dict[str, list[tuple[float, str]]] = {}
    for wv in weighted_values:
        if not isinstance(wv.value, list):
            continue
        for entity in wv.value:
            if not isinstance(entity, dict):
                continue
            key = _entity_match_key(entity)
            source_index.setdefault(key, []).append((wv.weight, wv.source_id))

    # For each deduped entity, look up its sources
    result: list[dict] = []
    for entity in deduped_entities:
        key = _entity_match_key(entity)
        sources = source_index.get(key, [])
        # Filter to grounded sources (weight > 0), sorted by weight desc
        grounded = sorted(
            ((w, sid) for w, sid in sources if w > 0 and sid),
            key=lambda x: x[0],
            reverse=True,
        )
        result.append({
            "winning_weight": round(grounded[0][0], 4) if grounded else 0.0,
            "top_sources": [sid for _, sid in grounded][:5],
        })

    return result


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
    """Deduplicate dict items by name, merging attributes across occurrences.

    When duplicates are found, attributes from later occurrences fill in
    keys that were None or missing in the first occurrence.
    """
    groups: dict[str, list[dict]] = {}
    order: list[str] = []

    for item in items:
        name = item.get("name") or item.get("product_name") or item.get("id", "")
        key = str(name).strip().lower()
        if not key:
            key = hashlib.sha256(
                json.dumps(item, sort_keys=True).encode()
            ).hexdigest()[:16]
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    result: list[dict] = []
    for key in order:
        occurrences = groups[key]
        if len(occurrences) == 1:
            result.append(occurrences[0])
        else:
            # Merge: first occurrence is canonical, fill gaps from others
            merged = dict(occurrences[0])
            for occ in occurrences[1:]:
                for k, v in occ.items():
                    if merged.get(k) is None and v is not None:
                        merged[k] = v
            result.append(merged)
    return result
