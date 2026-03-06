"""String-match grounding verification for extraction fields.

Pure functions with zero external dependencies. Verifies that extracted values
are grounded (actually present) in their source quotes.

String-match catches 83% of product spec hallucinations and 37% of employee
count hallucinations without any LLM calls.
"""

from __future__ import annotations

import re
from typing import Any

# Grounding mode defaults by field type.
# "required" = must be verifiable in quote text.
# "semantic" = meaning-based (skip string-match, defer to LLM or confidence).
# "none" = not grounded (synthesized content like descriptions).
GROUNDING_DEFAULTS: dict[str, str] = {
    "string": "required",
    "integer": "required",
    "float": "required",
    "boolean": "semantic",
    "text": "none",
    "enum": "required",
    "list": "required",
}

# Keys in extraction data that are metadata, not fields
_METADATA_KEYS = frozenset(
    {"confidence", "_quotes", "_conflicts", "_validation", "_quote"}
)


def verify_numeric_in_quote(value: Any, quote: str | None) -> float:
    """Check if a numeric value appears in quote text.

    Handles format variants: 1000, 1,000, 1.000 (European), 1 000 (French).
    Returns 1.0 if found, 0.0 if not.
    """
    if quote is None or quote == "":
        return 0.0

    # Coerce value to number
    num = _to_number(value)
    if num is None or num == 0:
        return 0.0

    # Extract all numbers from quote in various formats
    found_numbers = _extract_numbers_from_text(quote)
    target = float(num)

    for found in found_numbers:
        if _numbers_match(target, found):
            return 1.0

    return 0.0


def verify_string_in_quote(value: Any, quote: str | None) -> float:
    """Check if a string value appears in quote text (case-insensitive, normalized).

    Returns 1.0 for exact/substring match, 0.5+ for fuzzy match, 0.0 for no match.
    """
    if value is None or quote is None:
        return 0.0

    val = str(value).strip()
    if not val or not quote:
        return 0.0

    norm_val = _normalize_string(val)
    norm_quote = _normalize_string(quote)

    if not norm_val:
        return 0.0

    # Exact substring match (normalized)
    if norm_val in norm_quote:
        return 1.0

    # Try matching without hyphens/special chars
    stripped_val = re.sub(r"[-®™©]", "", norm_val).replace("  ", " ").strip()
    stripped_quote = re.sub(r"[-®™©]", "", norm_quote).replace("  ", " ").strip()
    if stripped_val and stripped_val in stripped_quote:
        return 0.8

    # Multi-word partial match: check if significant words from value appear in quote
    words = [w for w in norm_val.split() if len(w) > 2]
    if words:
        matched = sum(1 for w in words if w in norm_quote)
        ratio = matched / len(words)
        if ratio > 0:
            return round(min(0.7, ratio * 0.7), 2)

    return 0.0


def verify_list_items_in_quote(items: list, quote: str | None) -> float:
    """Check fraction of list items grounded in quote text.

    Returns fraction: 3/5 items found -> 0.6. Handles both string lists
    and entity dicts (uses 'name' key).
    """
    if not items or not quote:
        return 0.0

    # Extract string values from items (handle dicts with 'name' key)
    string_items = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, dict):
            name = item.get("name") or item.get("product_name") or item.get("id")
            if name:
                string_items.append(str(name))
        else:
            string_items.append(str(item))

    if not string_items:
        return 0.0

    norm_quote = _normalize_string(quote)
    found = sum(1 for item in string_items if _normalize_string(item) in norm_quote)
    return round(found / len(string_items), 4)


def compute_grounding_scores(
    data: dict,
    field_types: dict[str, str],
) -> dict[str, float]:
    """Score all fields in an extraction's data dict via string-match.

    Args:
        data: Extraction data dict. May contain _quotes dict.
        field_types: Map of field_name -> type ("integer", "string", etc.)

    Returns:
        Dict of field_name -> grounding_score (0.0-1.0).
        Fields with grounding mode "none" or "semantic" are excluded.
        Fields without quotes get 0.0.
        Null-valued fields and metadata keys are excluded.
    """
    if not data:
        return {}

    quotes = data.get("_quotes", {}) or {}
    scores: dict[str, float] = {}

    for field_name, field_type in field_types.items():
        # Skip metadata keys
        if field_name in _METADATA_KEYS:
            continue

        # Skip fields not present or null in data
        if field_name not in data or data[field_name] is None:
            continue

        # Determine grounding mode
        grounding_mode = GROUNDING_DEFAULTS.get(field_type, "required")
        if grounding_mode in ("none", "semantic"):
            continue

        value = data[field_name]
        quote = quotes.get(field_name, "")

        if not quote:
            scores[field_name] = 0.0
            continue

        # Dispatch by field type
        if field_type in ("integer", "float"):
            scores[field_name] = verify_numeric_in_quote(value, quote)
        elif field_type == "list":
            if isinstance(value, list):
                scores[field_name] = verify_list_items_in_quote(value, quote)
            else:
                scores[field_name] = verify_string_in_quote(str(value), quote)
        else:
            # string, enum, and any other type
            scores[field_name] = verify_string_in_quote(value, quote)

    return scores


def extract_field_types_from_schema(
    extraction_schema: dict,
) -> dict[str, dict[str, str]]:
    """Extract field_name→field_type maps from a project's extraction_schema.

    Args:
        extraction_schema: Project extraction_schema JSONB with field_groups.

    Returns:
        Dict keyed by extraction_type (field_group name), values are
        {field_name: field_type} dicts.
    """
    result: dict[str, dict[str, str]] = {}
    for fg in extraction_schema.get("field_groups", []):
        group_name = fg.get("name", "")
        if not group_name:
            continue
        field_types: dict[str, str] = {}
        for f in fg.get("fields", []):
            name = f.get("name", "")
            ftype = f.get("field_type", "") or f.get("type", "")
            if name and ftype:
                field_types[name] = ftype
        if field_types:
            result[group_name] = field_types
    return result


# ── Internal helpers ──


def _to_number(value: Any) -> int | float | None:
    """Coerce a value to a number, or return None."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            if "." in value:
                return float(value)
            return int(value)
        except (ValueError, TypeError):
            return None
    return None


def _normalize_string(s: str) -> str:
    """Normalize a string for comparison: lowercase, collapse whitespace."""
    return re.sub(r"\s+", " ", s.lower().strip())


def _extract_numbers_from_text(text: str) -> list[float]:
    """Extract all numbers from text, handling international formats.

    Handles:
    - Plain: 140000
    - Comma thousands: 140,000
    - European dot thousands: 140.000
    - French space thousands: 140 000
    - Decimals: 2.9, 3,5 (European decimal)
    - Negative: -10
    """
    numbers: list[float] = []

    # Pattern for numbers with various thousand separators
    # Matches: 140,000 or 140.000 or 140 000 or plain 140000
    # Also matches decimals: 2.9, 0.746
    pattern = r"-?(?:\d{1,3}(?:[,.\s]\d{3})+|\d+(?:\.\d+)?)"

    for match in re.finditer(pattern, text):
        raw = match.group()
        parsed = _parse_number_string(raw)
        if parsed is not None:
            numbers.append(parsed)

    return numbers


def _parse_number_string(raw: str) -> float | None:
    """Parse a number string with possible thousand/decimal separators."""
    raw = raw.strip()
    if not raw:
        return None

    # Handle negative
    negative = raw.startswith("-")
    if negative:
        raw = raw[1:]

    # Remove spaces (French thousands: 140 000)
    cleaned = raw.replace(" ", "").replace("\u00a0", "")

    # Determine if dots/commas are thousands or decimal separators
    dot_count = cleaned.count(".")
    comma_count = cleaned.count(",")

    if dot_count == 0 and comma_count == 0:
        # Plain number
        try:
            result = float(cleaned)
            return -result if negative else result
        except ValueError:
            return None

    if dot_count == 1 and comma_count == 0:
        # Could be decimal (2.9) or European thousands (140.000)
        parts = cleaned.split(".")
        if len(parts[1]) == 3 and len(parts[0]) <= 3:
            # Likely European thousands: 140.000 → 140000
            result = float(cleaned.replace(".", ""))
        else:
            # Decimal: 2.9
            result = float(cleaned)
        return -result if negative else result

    if comma_count >= 1 and dot_count == 0:
        # Comma as thousands: 140,000 or 1,500,000
        # Check if all groups after first comma are exactly 3 digits
        parts = cleaned.split(",")
        if all(len(p) == 3 for p in parts[1:]):
            result = float(cleaned.replace(",", ""))
            return -result if negative else result
        # Single comma could be European decimal: 3,5
        if comma_count == 1 and len(parts[1]) <= 2:
            result = float(cleaned.replace(",", "."))
            return -result if negative else result
        return None

    if dot_count >= 1 and comma_count == 0:
        # Multiple dots as thousands: 1.500.000
        parts = cleaned.split(".")
        if all(len(p) == 3 for p in parts[1:]):
            result = float(cleaned.replace(".", ""))
            return -result if negative else result

    return None


def _numbers_match(target: float, found: float) -> bool:
    """Check if two numbers match (exact for integers, close for floats)."""
    if target == found:
        return True
    # For floats, allow small epsilon
    if abs(target) > 0:
        return abs(target - found) / abs(target) < 1e-6
    return False
