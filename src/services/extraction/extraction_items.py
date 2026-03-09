"""Per-field structured extraction data model (v2).

Dataclasses and utilities for the v2 extraction format where every extracted
value carries independent confidence, quote, grounding score, and source
location. Used by schema_extractor (response parsing), schema_orchestrator
(merge), and downstream consumers (consolidation, reports, embedding).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceLocation:
    """Where in the source content a value was found."""

    heading_path: list[str]  # e.g. ["Products", "Gearboxes"] from chunk header
    char_offset: int | None  # Start position of quote in full source content
    char_end: int | None  # End position of quote in full source content
    chunk_index: int  # Which chunk produced this value
    match_tier: int = 0  # 1-4 match tier, 0=unmatched
    match_quality: float = 1.0  # 0.0-1.0 match confidence


@dataclass
class FieldItem:
    """Single extracted value with full provenance."""

    value: Any
    confidence: float
    quote: str | None
    grounding: float  # min(quote_in_source, value_in_quote)
    location: SourceLocation | None


@dataclass
class ListValueItem:
    """One item from a multi-value list field."""

    value: Any
    confidence: float
    quote: str | None
    grounding: float
    location: SourceLocation | None


@dataclass
class EntityItem:
    """One entity from an entity list."""

    fields: dict[str, Any]  # {field_name: value, ...}
    confidence: float
    quote: str | None
    grounding: float
    location: SourceLocation | None


@dataclass
class ChunkExtractionResult:
    """Structured result from one chunk, before merge."""

    chunk_index: int
    field_items: dict[str, FieldItem] = field(default_factory=dict)
    list_items: dict[str, list[ListValueItem]] = field(default_factory=dict)
    entity_items: dict[str, list[EntityItem]] = field(default_factory=dict)
    truncated: bool = False


def safe_data_version(obj: Any) -> int:
    """Safely read data_version from an ORM object, dict, or mock.

    Returns 1 if the attribute is missing, not an int, or is a mock object.
    """
    val = (
        getattr(obj, "data_version", None)
        if not isinstance(obj, dict)
        else obj.get("data_version")
    )
    return val if isinstance(val, int) else 1


# ── Utility functions ──


def locate_in_source(
    quote: str | None,
    full_content: str,
    chunk: Any,
    content_maps: Any | None = None,
) -> SourceLocation | None:
    """Compute SourceLocation by finding quote in full source content.

    Uses the 4-tier offset-mapped algorithm for accurate original-content
    positions. Optionally accepts pre-computed ContentMaps for performance.

    Args:
        quote: The extracted quote string.
        full_content: The complete source text (pre-chunking).
        chunk: A chunk object with ``header_path`` (list[str]) and
            ``chunk_index`` (int) attributes.
        content_maps: Optional pre-computed ContentMaps from
            ``grounding.precompute_content_maps()``.

    Returns:
        SourceLocation or None if quote is empty.
    """
    if not quote:
        return None

    from services.extraction.grounding import (
        ground_and_locate,
        ground_and_locate_precomputed,
    )

    chunk_index = getattr(chunk, "chunk_index", 0)
    heading_path = getattr(chunk, "header_path", None) or []

    if content_maps is not None:
        result = ground_and_locate_precomputed(quote, full_content, content_maps)
    else:
        result = ground_and_locate(quote, full_content)

    return SourceLocation(
        heading_path=list(heading_path),
        char_offset=result.source_offset,
        char_end=result.source_end,
        chunk_index=chunk_index,
        match_tier=result.match_tier,
        match_quality=result.score,
    )


def read_field_value(data: dict, field_name: str, data_version: int = 1) -> Any:
    """Universal reader: extracts value from v1 (flat) or v2 (structured) format.

    Args:
        data: Extraction data dict.
        field_name: Name of the field to read.
        data_version: 1 for flat format, 2 for per-field structured.

    Returns:
        The field value, or None if not present.
    """
    if not data or field_name not in data:
        # v2 fields may be nested under the field name directly
        if data_version == 2 and isinstance(data, dict):
            field_data = data.get(field_name)
            if isinstance(field_data, dict):
                if "items" in field_data:
                    return [item.get("value") for item in field_data["items"]]
                return field_data.get("value")
        return data.get(field_name) if data else None

    if data_version == 1:
        return data[field_name]

    # v2 format
    field_data = data[field_name]
    if not isinstance(field_data, dict):
        # Already a plain value (shouldn't happen in v2 but be safe)
        return field_data

    # Multi-value list field
    if "items" in field_data:
        return [item.get("value") for item in field_data["items"]]

    # Single-value field
    return field_data.get("value")


def to_v2_data(
    field_items: dict[str, FieldItem],
    list_items: dict[str, list[ListValueItem]],
    entity_items: dict[str, list[EntityItem]],
    group_name: str,
) -> dict:
    """Serialize extraction results into v2 JSON for Extraction.data.

    Args:
        field_items: Single-answer field results.
        list_items: Multi-value list field results.
        entity_items: Entity list field results.
        group_name: Name of the field group (stored as metadata).

    Returns:
        Dict suitable for storing as Extraction.data with data_version=2.
    """
    result: dict[str, Any] = {}

    # Single-answer fields (including summary)
    for name, item in field_items.items():
        entry: dict[str, Any] = {
            "value": item.value,
            "confidence": item.confidence,
            "grounding": item.grounding,
        }
        if item.quote is not None:
            entry["quote"] = item.quote
        if item.location is not None:
            entry["location"] = _location_to_dict(item.location)
        # Include alternatives if present
        if hasattr(item, "alternatives") and item.alternatives:
            entry["alternatives"] = [
                _field_item_to_dict(alt) for alt in item.alternatives
            ]
        result[name] = entry

    # Multi-value list fields
    for name, items in list_items.items():
        result[name] = {
            "items": [_list_value_to_dict(lv) for lv in items],
        }

    # Entity list fields
    for name, entities in entity_items.items():
        result[name] = {
            "items": [_entity_to_dict(e) for e in entities],
        }

    result["_meta"] = {"group": group_name, "data_version": 2}
    return result


def v2_to_flat(data: dict) -> dict:
    """Convert v2 structured data to v1-compatible flat dict.

    Enables backward compatibility for consumers that expect flat format.
    Strips provenance metadata, keeps only values.

    Args:
        data: v2 structured data dict.

    Returns:
        Flat dict compatible with v1 format: {field: value, confidence: X, _quotes: {...}}.
    """
    if not data:
        return {}

    flat: dict[str, Any] = {}
    quotes: dict[str, str] = {}
    confidences: list[float] = []

    for key, value in data.items():
        if key.startswith("_"):
            continue
        if not isinstance(value, dict):
            flat[key] = value
            continue

        if "items" in value:
            # List/entity field
            items = value["items"]
            if items and isinstance(items[0], dict) and "fields" in items[0]:
                # Entity list: flatten to list of field dicts
                flat[key] = [item["fields"] for item in items]
                for item in items:
                    if item.get("quote"):
                        quotes.setdefault(key, "")
                        if quotes[key]:
                            quotes[key] += " | "
                        quotes[key] += item["quote"]
                    if "confidence" in item:
                        confidences.append(item["confidence"])
            else:
                # Value list
                flat[key] = [item.get("value") for item in items]
                for item in items:
                    if item.get("quote"):
                        quotes.setdefault(key, "")
                        if quotes[key]:
                            quotes[key] += " | "
                        quotes[key] += item["quote"]
                    if "confidence" in item:
                        confidences.append(item["confidence"])
        else:
            # Single-value field
            flat[key] = value.get("value")
            if value.get("quote"):
                quotes[key] = value["quote"]
            if "confidence" in value:
                confidences.append(value["confidence"])

    if quotes:
        flat["_quotes"] = quotes
    if confidences:
        flat["confidence"] = sum(confidences) / len(confidences)

    return flat


# ── Serialization helpers ──


def _location_to_dict(loc: SourceLocation) -> dict:
    d: dict[str, Any] = {
        "heading_path": loc.heading_path,
        "char_offset": loc.char_offset,
        "char_end": loc.char_end,
        "chunk_index": loc.chunk_index,
    }
    if loc.match_tier:
        d["match_tier"] = loc.match_tier
    if loc.match_quality != 1.0:
        d["match_quality"] = loc.match_quality
    return d


def _field_item_to_dict(item: FieldItem) -> dict:
    d: dict[str, Any] = {
        "value": item.value,
        "confidence": item.confidence,
        "grounding": item.grounding,
    }
    if item.quote is not None:
        d["quote"] = item.quote
    if item.location is not None:
        d["location"] = _location_to_dict(item.location)
    return d


def _list_value_to_dict(item: ListValueItem) -> dict:
    d: dict[str, Any] = {
        "value": item.value,
        "confidence": item.confidence,
        "grounding": item.grounding,
    }
    if item.quote is not None:
        d["quote"] = item.quote
    if item.location is not None:
        d["location"] = _location_to_dict(item.location)
    return d


def _entity_to_dict(item: EntityItem) -> dict:
    d: dict[str, Any] = {
        "fields": item.fields,
        "confidence": item.confidence,
        "grounding": item.grounding,
    }
    if item.quote is not None:
        d["quote"] = item.quote
    if item.location is not None:
        d["location"] = _location_to_dict(item.location)
    return d
