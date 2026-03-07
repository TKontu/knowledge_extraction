"""Field group definitions for schema-based extraction."""

from dataclasses import dataclass
from typing import Any

# Valid merge strategies for field definitions
VALID_MERGE_STRATEGIES = frozenset(
    {
        "highest_confidence", "max", "min", "concat", "majority_vote",
        "merge_dedupe", "longest_confident", "llm_synthesize",
    }
)


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
    grounding_mode: str | None = None  # "required", "semantic", "none" (None = use type default)
    consolidation_strategy: str | None = None  # Override type-based consolidation default


@dataclass
class FieldGroup:
    """Group of related fields for focused extraction."""

    name: str  # Used as extraction_type
    description: str
    fields: list[FieldDefinition]
    prompt_hint: str  # Additional context for LLM
    is_entity_list: bool = False  # True for product groups
    max_items: int | None = None  # Max entities per chunk (entity lists only)
