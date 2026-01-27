"""JSON repair utilities for handling malformed LLM responses.

LLMs sometimes produce malformed JSON due to:
- Truncation: Output hits max_tokens limit mid-string
- Syntax errors: Missing closing quotes, braces, or brackets
- Escape issues: Unescaped newlines or special characters in strings

This module provides repair strategies to recover from common failures
before triggering retries.
"""

from __future__ import annotations

import json
import re
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


def repair_json(malformed: str) -> dict[str, Any]:
    """Attempt to repair and parse malformed JSON.

    Repair strategies (in order):
    1. Direct parse (fast path)
    2. Strip markdown code fences
    3. Fix unterminated strings
    4. Balance braces/brackets
    5. Remove trailing commas
    6. Fix single quotes to double quotes

    Args:
        malformed: Potentially malformed JSON string

    Returns:
        Parsed dictionary

    Raises:
        json.JSONDecodeError: If all repair strategies fail
    """
    if not malformed or not malformed.strip():
        raise json.JSONDecodeError("Empty string", malformed, 0)

    text = malformed.strip()

    # Strategy 1: Direct parse (fast path for valid JSON)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 2: Strip markdown code fences (```json ... ```)
    text = _strip_code_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strategy 3: Fix unterminated strings
    repaired = _fix_unterminated_strings(text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 4: Balance brackets and braces
    repaired = _balance_brackets(text)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 5: Combine unterminated strings + balance brackets
    repaired = _fix_unterminated_strings(text)
    repaired = _balance_brackets(repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 6: Remove trailing commas
    repaired = _remove_trailing_commas(text)
    repaired = _balance_brackets(repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 7: Fix single quotes to double quotes
    repaired = _fix_quotes(text)
    repaired = _balance_brackets(repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # Strategy 8: Full repair chain
    repaired = _strip_code_fences(text)
    repaired = _fix_unterminated_strings(repaired)
    repaired = _remove_trailing_commas(repaired)
    repaired = _balance_brackets(repaired)
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    # All strategies failed - raise original error for debugging
    raise json.JSONDecodeError(
        f"All repair strategies failed for content length {len(malformed)}",
        malformed,
        0,
    )


def try_repair_json(text: str | None, context: str = "") -> dict[str, Any]:
    """Attempt JSON parse with repair fallback.

    This is the main entry point for use in LLM response handling.
    Logs repair attempts for observability.

    Args:
        text: JSON string to parse (None is handled gracefully)
        context: Optional context for logging (e.g., "extract_facts", "extract_entities")

    Returns:
        Parsed dictionary

    Raises:
        json.JSONDecodeError: If parsing and repair both fail, or if text is None/empty
    """
    # Handle None input - json.loads(None) raises TypeError, not JSONDecodeError
    if text is None:
        raise json.JSONDecodeError("Cannot parse None", "", 0)

    try:
        return json.loads(text)
    except json.JSONDecodeError as original_error:
        logger.warning(
            "json_parse_failed_attempting_repair",
            context=context,
            error=str(original_error),
            content_length=len(text) if text else 0,
        )

        try:
            result = repair_json(text)
            logger.info(
                "json_repair_succeeded",
                context=context,
                content_length=len(text) if text else 0,
            )
            return result
        except json.JSONDecodeError:
            logger.warning(
                "json_repair_failed",
                context=context,
                content_length=len(text) if text else 0,
                original_error=str(original_error),
            )
            # Re-raise original error for better debugging
            raise original_error


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences from JSON.

    Handles:
    - ```json ... ```
    - ``` ... ```
    - Leading/trailing whitespace
    """
    # Remove ```json or ``` at start
    text = re.sub(r"^```(?:json)?\s*\n?", "", text.strip())
    # Remove ``` at end
    text = re.sub(r"\n?```\s*$", "", text)
    return text.strip()


def _fix_unterminated_strings(text: str) -> str:
    """Fix unterminated strings by adding missing closing quotes.

    Handles truncation mid-string like: {"name": "test
    """
    # Count quotes (simple heuristic - doesn't handle escaped quotes perfectly)
    # but good enough for common LLM truncation cases
    in_string = False
    escaped = False
    last_string_start = -1
    result = list(text)

    for i, char in enumerate(text):
        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == '"':
            if in_string:
                in_string = False
            else:
                in_string = True
                last_string_start = i

    # If we ended inside a string, close it
    if in_string and last_string_start >= 0:
        # If string seems truncated (no closing quote found), add one
        result.append('"')
        return "".join(result)

    return text


def _balance_brackets(text: str) -> str:
    """Add missing closing brackets and braces.

    Handles truncation like: {"items": [1, 2
    """
    # Track bracket depth (ignore brackets inside strings)
    brace_depth = 0  # {}
    bracket_depth = 0  # []
    in_string = False
    escaped = False

    for char in text:
        if escaped:
            escaped = False
            continue

        if char == "\\":
            escaped = True
            continue

        if char == '"':
            in_string = not in_string
            continue

        if in_string:
            continue

        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth -= 1
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth -= 1

    # Add missing closers
    result = text
    # Add brackets first (inner), then braces (outer)
    if bracket_depth > 0:
        result += "]" * bracket_depth
    if brace_depth > 0:
        result += "}" * brace_depth

    return result


def _remove_trailing_commas(text: str) -> str:
    """Remove trailing commas before ] or }.

    Handles: [1, 2,] or {"a": 1,}
    """
    # Remove comma followed by optional whitespace and ] or }
    text = re.sub(r",\s*]", "]", text)
    text = re.sub(r",\s*}", "}", text)
    return text


def _fix_quotes(text: str) -> str:
    """Convert single quotes to double quotes for JSON compatibility.

    Handles: {'key': 'value'} -> {"key": "value"}

    Note: This is a simple heuristic that may not work for all cases
    (e.g., apostrophes in values), but handles common LLM outputs.
    """
    # Only apply if text looks like it uses single quotes for JSON
    if "'" in text and '"' not in text:
        return text.replace("'", '"')

    # More careful replacement: single quotes around keys/values
    # Match patterns like 'key': or : 'value'
    result = re.sub(r"'([^']*)'(\s*:)", r'"\1"\2', text)  # Keys
    result = re.sub(r"(:\s*)'([^']*)'", r'\1"\2"', result)  # String values
    return result
