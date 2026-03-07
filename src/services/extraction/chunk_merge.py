"""Cardinality-based merge for v2 chunk extraction results.

Merges ChunkExtractionResult objects from multiple chunks into a single
per-source result using strategies appropriate for each field's cardinality.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from services.extraction.extraction_items import (
    ChunkExtractionResult,
    EntityItem,
    FieldItem,
    ListValueItem,
    to_v2_data,
)
from services.extraction.field_groups import FieldDefinition, FieldGroup


def field_cardinality(field: FieldDefinition) -> str:
    """Resolve cardinality for a field definition.

    Returns:
        "single", "boolean", "multi_value", or "summary".
    """
    if field.field_type == "boolean":
        return "boolean"
    if field.field_type == "list":
        return "multi_value"
    if field.field_type == "summary":
        return "summary"
    return "single"


def merge_single_answer(
    field: FieldDefinition,
    chunk_results: list[ChunkExtractionResult],
) -> FieldItem:
    """Best item by grounding * confidence. Alternatives preserved.

    Args:
        field: Field definition.
        chunk_results: Results from all chunks.

    Returns:
        Best FieldItem with alternatives attached.
    """
    candidates: list[FieldItem] = []
    for cr in chunk_results:
        item = cr.field_items.get(field.name)
        if item is not None and item.value is not None:
            candidates.append(item)

    if not candidates:
        return FieldItem(
            value=None, confidence=0.0, quote=None, grounding=0.0, location=None
        )

    # Sort by score = grounding * confidence (descending)
    candidates.sort(key=lambda it: it.grounding * it.confidence, reverse=True)
    best = candidates[0]

    # Attach alternatives (losers) as an attribute
    if len(candidates) > 1:
        best.alternatives = candidates[1:]  # type: ignore[attr-defined]

    return best


def merge_boolean(
    field: FieldDefinition,
    chunk_results: list[ChunkExtractionResult],
) -> FieldItem:
    """Credible True wins (any True with confidence >= 0.5).

    LLMs default to False when a chunk lacks evidence, so a single credible
    True is more meaningful than majority vote.
    """
    candidates: list[FieldItem] = []
    for cr in chunk_results:
        item = cr.field_items.get(field.name)
        if item is not None and item.value is not None:
            candidates.append(item)

    if not candidates:
        return FieldItem(
            value=False, confidence=0.0, quote=None, grounding=0.0, location=None
        )

    # Any credible True wins
    true_items = [c for c in candidates if c.value is True and c.confidence >= 0.5]
    if true_items:
        best = max(true_items, key=lambda it: it.grounding * it.confidence)
        return best

    # All False or low-confidence True → pick best False
    false_items = [c for c in candidates if c.value is False]
    if false_items:
        return max(false_items, key=lambda it: it.confidence)

    # Edge case: only low-confidence True
    return max(candidates, key=lambda it: it.confidence)


def merge_list_values(
    field: FieldDefinition,
    chunk_results: list[ChunkExtractionResult],
) -> list[ListValueItem]:
    """Union across chunks, per-item dedup by normalized value."""
    seen: set[str] = set()
    merged: list[ListValueItem] = []

    for cr in chunk_results:
        items = cr.list_items.get(field.name, [])
        for item in items:
            if item.value is None:
                continue
            key = _normalize_for_dedup(item.value)
            if key not in seen:
                seen.add(key)
                merged.append(item)

    return merged


def merge_summary(
    field: FieldDefinition,
    chunk_results: list[ChunkExtractionResult],
) -> FieldItem:
    """Longest confident text wins."""
    candidates: list[FieldItem] = []
    for cr in chunk_results:
        item = cr.field_items.get(field.name)
        if item is not None and item.value is not None and str(item.value).strip():
            candidates.append(item)

    if not candidates:
        return FieldItem(
            value=None, confidence=0.0, quote=None, grounding=1.0, location=None
        )

    # Filter to confident candidates (>= 0.3)
    confident = [c for c in candidates if c.confidence >= 0.3] or candidates
    return max(confident, key=lambda it: len(str(it.value)))


def merge_entities(
    chunk_results: list[ChunkExtractionResult],
    group: FieldGroup,
    entity_id_fields: list[str] | None = None,
) -> list[EntityItem]:
    """Union + dedup by ID fields. Per-entity grounding preserved.

    Args:
        chunk_results: Results from all chunks.
        group: Field group definition (entity list).
        entity_id_fields: Fields to use for dedup (default: ["entity_id", "name", "id"]).

    Returns:
        Deduplicated list of EntityItem.
    """
    id_fields = entity_id_fields or ["entity_id", "name", "id"]
    seen_ids: set[str] = set()
    merged: list[EntityItem] = []

    for cr in chunk_results:
        items = cr.entity_items.get(group.name, [])
        for entity in items:
            entity_id = _entity_id(entity, id_fields)
            if entity_id and entity_id not in seen_ids:
                seen_ids.add(entity_id)
                merged.append(entity)
            elif not entity_id:
                # No ID field — dedupe by content hash
                content_hash = hashlib.sha256(
                    json.dumps(entity.fields, sort_keys=True).encode()
                ).hexdigest()[:16]
                if content_hash not in seen_ids:
                    seen_ids.add(content_hash)
                    merged.append(entity)

    return merged


def merge_chunk_results(
    chunk_results: list[ChunkExtractionResult],
    group: FieldGroup,
    entity_id_fields: list[str] | None = None,
) -> dict:
    """Merge chunk results using cardinality-appropriate strategies.

    Returns v2 structured data dict ready for Extraction.data.
    """
    if not chunk_results:
        return {}

    if group.is_entity_list:
        entities = merge_entities(chunk_results, group, entity_id_fields)
        return to_v2_data({}, {}, {group.name: entities}, group.name)

    field_items: dict[str, FieldItem] = {}
    list_items: dict[str, list[ListValueItem]] = {}

    for field_def in group.fields:
        card = field_cardinality(field_def)
        if card == "single":
            field_items[field_def.name] = merge_single_answer(field_def, chunk_results)
        elif card == "boolean":
            field_items[field_def.name] = merge_boolean(field_def, chunk_results)
        elif card == "multi_value":
            list_items[field_def.name] = merge_list_values(field_def, chunk_results)
        elif card == "summary":
            field_items[field_def.name] = merge_summary(field_def, chunk_results)

    return to_v2_data(field_items, list_items, {}, group.name)


# ── Helpers ──


def _normalize_for_dedup(value: Any) -> str:
    """Normalize a value for dedup comparison."""
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True)
    return str(value).strip().lower()


def _entity_id(entity: EntityItem, id_fields: list[str]) -> str | None:
    """Extract entity ID for dedup."""
    for field in id_fields:
        val = entity.fields.get(field)
        if val is not None and str(val).strip():
            return str(val).strip().lower()
    return None
