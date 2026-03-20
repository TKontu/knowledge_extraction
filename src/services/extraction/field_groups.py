"""Field group definitions for schema-based extraction."""

from dataclasses import dataclass
from typing import Any

# Valid merge strategies for field definitions
VALID_MERGE_STRATEGIES = frozenset(
    {
        "highest_confidence",
        "max",
        "min",
        "concat",
        "majority_vote",
        "merge_dedupe",
        "longest_confident",
        "llm_synthesize",
    }
)

VALID_VALIDATOR_TYPES = frozenset(
    {
        "factapi_not_in_column",
        "factapi_exists_in_column",
        "factapi_fill_from_lookup",
    }
)
VALID_VALIDATOR_ACTIONS = frozenset(
    {
        "nullify",
        "warn",
        "fill_if_null",  # Fill target field only when it is empty/null
        "fill_always",  # Fill target field unconditionally
    }
)


@dataclass
class ValidatorSpec:
    """Declarative field validator backed by a factAPI collection."""

    type: str  # "factapi_not_in_column" | "factapi_exists_in_column" | "factapi_fill_from_lookup"
    collection: str  # factAPI collection, e.g. "worldcities"
    column: str  # column to match the field's value against
    action: str  # "nullify" | "warn" | "fill_if_null" | "fill_always"
    case_sensitive: bool = False
    fill_column: str | None = (
        None  # factAPI column to read fill value from (fill type only)
    )
    target_field: str | None = (
        None  # entity field to write fill value to (fill type only)
    )
    unique_only: bool = True  # Only fill when lookup maps to exactly one value


@dataclass
class FieldDefinition:
    """Definition of a single extraction field."""

    name: str
    field_type: str  # "boolean", "integer", "text", "list", "float", "enum", "summary"
    description: str
    required: bool = False
    default: Any = None
    enum_values: list[str] | None = None
    merge_strategy: str | None = None  # Override type-based merge default
    grounding_mode: str | None = (
        None  # "required", "semantic", "none" (None = use type default)
    )
    consolidation_strategy: str | None = (
        None  # Override type-based consolidation default
    )
    validators: list[ValidatorSpec] | None = None  # Declarative field validators


@dataclass
class FieldGroup:
    """Group of related fields for focused extraction."""

    name: str  # Used as extraction_type
    description: str
    fields: list[FieldDefinition]
    prompt_hint: str  # Additional context for LLM
    is_entity_list: bool = False  # True for product groups
    max_items: int | None = None  # Max entities per chunk (entity lists only)
