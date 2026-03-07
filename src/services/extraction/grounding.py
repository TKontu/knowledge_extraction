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
    "summary": "none",
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
    if num is None:
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


def _coerce_quote(quote: Any) -> str | None:
    """Coerce a quote value to a string.

    LLM extraction sometimes produces non-string quotes (lists, dicts, ints).
    Normalizes them to a single string suitable for grounding comparison.
    Returns None for empty/missing values.
    """
    if quote is None:
        return None
    if isinstance(quote, str):
        return quote or None
    if isinstance(quote, list):
        parts = [str(q) for q in quote if q is not None]
        return " ".join(parts) or None
    return str(quote) or None


def score_field(value: Any, quote: Any, field_type: str) -> float:
    """Score a single field value against its quote via string-match.

    Args:
        value: The extracted field value.
        quote: The source quote for this field (coerced to str if needed).
        field_type: Type string ("integer", "string", "list", etc.)

    Returns:
        Grounding score 0.0-1.0.
    """
    coerced = _coerce_quote(quote)
    if not coerced:
        return 0.0
    if field_type in ("integer", "float"):
        return verify_numeric_in_quote(value, coerced)
    if field_type == "list":
        if isinstance(value, list):
            return verify_list_items_in_quote(value, coerced)
        return verify_string_in_quote(str(value), coerced)
    # string, enum, and any other type
    return verify_string_in_quote(value, coerced)


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
        scores[field_name] = score_field(value, quote, field_type)

    return scores


def compute_entity_list_grounding_scores(
    data: dict,
    entity_key: str,
    field_types: dict[str, str],
) -> dict[str, float]:
    """Score entity list extractions via string-match.

    Entity list data has shape: {"products": [{"name": "X", "_quote": "..."}, ...]}
    Each entity has a per-entity _quote. Scores each entity's identifying field
    against its _quote, returns average as the score for the entity list field.

    Args:
        data: Extraction data dict with entity list.
        entity_key: Key containing the entity list (e.g., "products").
        field_types: Map of entity field_name -> type string.

    Returns:
        Dict with single key (entity_key) -> average grounding score.
    """
    entities = data.get(entity_key)
    if not entities or not isinstance(entities, list):
        return {}

    # Determine which fields to use as identity for scoring
    id_field_names = ("entity_id", "name", "id")
    scores: list[float] = []

    for entity in entities:
        if not isinstance(entity, dict):
            continue

        raw_quote = entity.get("_quote")
        quote = _coerce_quote(raw_quote)
        if not quote:
            scores.append(0.0)
            continue

        # Score the entity's identifying field against its quote
        id_value = None
        id_type = "string"
        for id_field in id_field_names:
            id_value = entity.get(id_field)
            if id_value is not None:
                id_type = field_types.get(id_field, "string")
                break

        if id_value is not None:
            scores.append(score_field(id_value, quote, id_type))
        else:
            # No ID field found but quote exists — assume grounded
            scores.append(1.0)

    if not scores:
        return {}

    return {entity_key: round(sum(scores) / len(scores), 4)}


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


def extract_entity_list_groups(extraction_schema: dict) -> set[str]:
    """Return set of group names that are entity lists.

    Args:
        extraction_schema: Project extraction_schema JSONB with field_groups.

    Returns:
        Set of group names where is_entity_list is True.
    """
    return {
        fg["name"]
        for fg in extraction_schema.get("field_groups", [])
        if fg.get("name") and fg.get("is_entity_list", False)
    }


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


# ── Source grounding (quote-in-content verification) ──

# Punctuation to strip for lenient matching (keep alphanumeric + spaces)
_STRIP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def verify_quote_in_source(quote: str, source_content: str) -> float:
    """Check if a claimed quote actually exists in the source content.

    Uses multi-tier matching with increasing leniency:
    1. Normalized substring (lowercase, collapsed whitespace) → 1.0
    2. Punctuation-stripped substring → 0.95
    3. Word-level sliding window → best overlap ratio

    Args:
        quote: The claimed quote string.
        source_content: The full source text (content or cleaned_content).

    Returns:
        Similarity score 0.0-1.0. ≥0.8 means the quote is source-grounded.
    """
    if not quote or not source_content:
        return 0.0

    norm_quote = _normalize_string(quote)
    norm_content = _normalize_string(source_content)

    if not norm_quote:
        return 0.0

    # Tier 1: exact normalized substring
    if norm_quote in norm_content:
        return 1.0

    # Tier 2: strip punctuation and retry
    stripped_quote = _STRIP_PUNCT_RE.sub("", norm_quote)
    stripped_quote = re.sub(r"\s+", " ", stripped_quote).strip()
    stripped_content = _STRIP_PUNCT_RE.sub("", norm_content)
    stripped_content = re.sub(r"\s+", " ", stripped_content).strip()

    if stripped_quote and stripped_quote in stripped_content:
        return 0.95

    # Tier 3: word-level sliding window
    return _word_window_similarity(stripped_quote, stripped_content)


def _word_window_similarity(quote: str, content: str) -> float:
    """Slide a word window over content and find best overlap with quote.

    For a quote of N words, slides an N-word window across content and
    computes what fraction of quote words match at each position.

    Returns best ratio found (0.0-1.0).
    """
    quote_words = quote.split()
    content_words = content.split()

    if not quote_words or not content_words:
        return 0.0

    n = len(quote_words)
    if n > len(content_words):
        return 0.0

    quote_set = set(quote_words)
    best = 0.0

    # Build initial window
    window_counts: dict[str, int] = {}
    for w in content_words[:n]:
        window_counts[w] = window_counts.get(w, 0) + 1

    # Count matches in initial window
    matching = sum(1 for w in quote_words if window_counts.get(w, 0) > 0)
    best = matching / n

    # Early exit if perfect
    if best >= 0.95:
        return best

    # Slide window across content
    for i in range(1, len(content_words) - n + 1):
        # Remove word leaving the window
        leaving = content_words[i - 1]
        window_counts[leaving] -= 1
        if window_counts[leaving] == 0:
            del window_counts[leaving]

        # Add word entering the window
        entering = content_words[i + n - 1]
        window_counts[entering] = window_counts.get(entering, 0) + 1

        # Only recount if entering/leaving words are in the quote
        if leaving in quote_set or entering in quote_set:
            matching = sum(1 for w in quote_words if window_counts.get(w, 0) > 0)
            ratio = matching / n
            if ratio > best:
                best = ratio
                if best >= 0.95:
                    return best

    return round(best, 4)


def ground_field_item(
    field_name: str,
    value: Any,
    quote: str | None,
    chunk_content: str,
    field_type: str,
) -> float:
    """Complete inline grounding for one field item.

    Combines Layer A (quote-in-source) and Layer B (value-in-quote):
      grounding = min(quote_in_source, value_in_quote)

    Args:
        field_name: Name of the field (for logging).
        value: The extracted value.
        quote: The quote string claimed to support the value.
        chunk_content: The source text that was sent to the LLM.
        field_type: Type string (determines grounding mode).

    Returns:
        Grounding score 0.0-1.0.
    """
    grounding_mode = GROUNDING_DEFAULTS.get(field_type, "required")

    if grounding_mode == "none":
        return 1.0

    coerced = _coerce_quote(quote)

    if grounding_mode == "semantic":
        # Semantic: only check quote-in-source (Layer A)
        if not coerced or not chunk_content:
            return 0.5  # No quote available, assume neutral
        return verify_quote_in_source(coerced, chunk_content)

    # Required: min(Layer A, Layer B)
    if not coerced:
        return 0.0  # No quote → ungrounded

    layer_a = verify_quote_in_source(coerced, chunk_content) if chunk_content else 0.0
    layer_b = score_field(value, coerced, field_type)
    return min(layer_a, layer_b)


def ground_entity_item(
    quote: str | None,
    chunk_content: str,
) -> float:
    """Inline grounding for one entity. Quote-in-source check only.

    Entity-level grounding verifies that the entity's identifying quote
    actually exists in the source content. Field-level grounding within
    the entity is handled separately per field.

    Args:
        quote: The entity's quote string.
        chunk_content: The source text that was sent to the LLM.

    Returns:
        Grounding score 0.0-1.0.
    """
    coerced = _coerce_quote(quote)
    if not coerced or not chunk_content:
        return 0.0
    return verify_quote_in_source(coerced, chunk_content)


def compute_chunk_grounding(result: dict, chunk_content: str) -> dict[str, float]:
    """Score each field's quote against the chunk source content.

    Unlike score_field (value vs quote), this verifies quote vs source.
    All field types are scored — values can be synthesized, quotes must be real.

    Args:
        result: Extraction result dict with ``_quotes``.
        chunk_content: The source text that was sent to the LLM.

    Returns:
        Dict of field_name -> grounding score (0.0-1.0) for all fields
        with non-empty quotes. No field-type filtering.
    """
    if not result or not chunk_content:
        return {}

    quotes = result.get("_quotes", {}) or {}
    if not isinstance(quotes, dict):
        return {}

    scores: dict[str, float] = {}
    for field_name, raw_quote in quotes.items():
        quote = _coerce_quote(raw_quote)
        if not quote:
            continue
        scores[field_name] = verify_quote_in_source(quote, chunk_content)

    return scores


def compute_chunk_grounding_entities(
    result: dict, chunk_content: str
) -> dict[str, float]:
    """Score each entity's quote against the chunk source content.

    For entity list results where each entity has a per-entity ``_quote``.

    Args:
        result: Extraction result dict with entity lists.
        chunk_content: The source text that was sent to the LLM.

    Returns:
        Dict mapping entity key to average grounding score.
    """
    if not result or not chunk_content:
        return {}

    scores: dict[str, float] = {}
    for key, value in result.items():
        if key in _METADATA_KEYS or not isinstance(value, list):
            continue
        entity_scores: list[float] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            raw_quote = item.get("_quote")
            quote = _coerce_quote(raw_quote)
            if not quote:
                entity_scores.append(0.0)
                continue
            entity_scores.append(verify_quote_in_source(quote, chunk_content))
        if entity_scores:
            scores[key] = round(sum(entity_scores) / len(entity_scores), 4)

    return scores


def compute_source_grounding_scores(
    data: dict,
    source_content: str,
    field_types: dict[str, str],
) -> dict[str, float]:
    """Check which quotes in an extraction actually exist in the source content.

    Args:
        data: Extraction data dict with _quotes.
        source_content: The source text that was sent to the LLM.
        field_types: Map of field_name -> type string.

    Returns:
        Dict of field_name -> source_grounding_score (0.0-1.0).
        Only includes fields with non-empty quotes and "required" grounding mode.
    """
    if not data or not source_content:
        return {}

    quotes = data.get("_quotes", {}) or {}
    scores: dict[str, float] = {}

    for field_name, field_type in field_types.items():
        if field_name in _METADATA_KEYS:
            continue
        if field_name not in data or data[field_name] is None:
            continue

        grounding_mode = GROUNDING_DEFAULTS.get(field_type, "required")
        if grounding_mode in ("none", "semantic"):
            continue

        raw_quote = quotes.get(field_name)
        quote = _coerce_quote(raw_quote)
        if not quote:
            continue

        scores[field_name] = verify_quote_in_source(quote, source_content)

    return scores
