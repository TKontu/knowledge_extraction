"""Field group definitions for schema-based extraction."""

from dataclasses import dataclass
from typing import Any


@dataclass
class FieldDefinition:
    """Definition of a single extraction field."""

    name: str
    field_type: str  # "boolean", "integer", "text", "list", "float", "enum"
    description: str
    required: bool = False
    default: Any = None
    enum_values: list[str] | None = None


@dataclass
class FieldGroup:
    """Group of related fields for focused extraction."""

    name: str  # Used as extraction_type
    description: str
    fields: list[FieldDefinition]
    prompt_hint: str  # Additional context for LLM
    is_entity_list: bool = False  # True for product groups
