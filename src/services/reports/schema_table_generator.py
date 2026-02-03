"""Schema-driven table report generation."""

from dataclasses import dataclass
from typing import Any

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_adapter import SchemaAdapter


@dataclass
class ColumnMetadata:
    """Metadata for a flattened column, used for LLM merge context."""

    name: str
    label: str
    field_type: str  # boolean, integer, float, text, list, enum
    description: str
    field_group: str  # Which field group this came from
    is_entity_list: bool = False
    enum_values: list[str] | None = None


class SchemaTableGenerator:
    """Generates table columns and labels from project extraction schema.

    This class provides template-agnostic table generation by deriving
    columns, labels, and formatting from the project's extraction_schema
    rather than hardcoded field definitions.
    """

    def __init__(self, schema_adapter: SchemaAdapter | None = None):
        """Initialize with optional SchemaAdapter.

        Args:
            schema_adapter: Adapter for schema conversion. Creates one if not provided.
        """
        self._adapter = schema_adapter or SchemaAdapter()

    def get_columns_from_schema(
        self, extraction_schema: dict
    ) -> tuple[list[str], dict[str, str], dict[str, FieldDefinition | None]]:
        """Derive column order and labels from extraction schema.

        Args:
            extraction_schema: Project's JSONB extraction schema.

        Returns:
            Tuple of (column_names, column_labels, field_definitions)
            where column_names is ordered list, column_labels maps name->label,
            and field_definitions maps name->FieldDefinition for type info
            (None for entity list columns).
        """
        field_groups = self._adapter.convert_to_field_groups(extraction_schema)

        columns: list[str] = ["source_group"]  # Always first
        labels: dict[str, str] = {"source_group": "Source"}
        field_defs: dict[str, FieldDefinition | None] = {}

        for group in field_groups:
            if group.is_entity_list:
                # Entity lists become "{group_name}_list" column
                col_name = f"{group.name}_list"
                columns.append(col_name)
                labels[col_name] = self._humanize(group.name)
                # Store group reference for entity list formatting
                field_defs[col_name] = None  # Marker for entity list
            else:
                # Regular fields - add each field as column
                for field in group.fields:
                    columns.append(field.name)
                    # Use description as label if short enough, else humanize name
                    labels[field.name] = self._get_field_label(field)
                    field_defs[field.name] = field

        return columns, labels, field_defs

    def get_entity_list_groups(
        self, extraction_schema: dict
    ) -> dict[str, FieldGroup]:
        """Get entity_list field groups mapped by name.

        Args:
            extraction_schema: Project's JSONB extraction schema.

        Returns:
            Dict mapping group_name to FieldGroup for is_entity_list=True groups.
        """
        field_groups = self._adapter.convert_to_field_groups(extraction_schema)
        return {g.name: g for g in field_groups if g.is_entity_list}

    def format_entity_list(
        self,
        items: list[dict],
        field_group: FieldGroup,
        max_items: int = 10,
    ) -> str:
        """Format entity list items for table cell.

        Generic version that works with any entity_list schema.
        Shows identifying field + key spec fields.

        Args:
            items: List of entity dicts from extraction data.
            field_group: The FieldGroup defining the entity structure.
            max_items: Maximum items to include in formatted output.

        Returns:
            Formatted string for table cell, or "N/A" if empty.
        """
        if not items:
            return "N/A"

        # Find identifying field (first text field matching common patterns)
        id_field = self._find_id_field(field_group.fields)

        # Find key spec fields (numeric fields, enums) - max 3
        spec_fields = [
            f
            for f in field_group.fields
            if f.field_type in ("integer", "float", "enum") and f.name != id_field
        ][:3]

        parts = []
        for item in items[:max_items]:
            name = item.get(id_field) if id_field else None
            if name is None:
                name = "Unknown"

            specs = []
            for sf in spec_fields:
                val = item.get(sf.name)
                if val is not None:
                    # Add unit suffix if field name suggests it
                    unit = self._infer_unit(sf.name)
                    specs.append(f"{val}{unit}")

            if specs:
                parts.append(f"{name} ({', '.join(specs)})")
            else:
                parts.append(str(name))

        result = "; ".join(parts)
        if len(items) > max_items:
            result += f" (+{len(items) - max_items} more)"
        return result

    def _get_field_label(self, field: FieldDefinition) -> str:
        """Get display label for a field.

        Uses description if short enough, otherwise humanizes name.

        Args:
            field: Field definition.

        Returns:
            Human-readable label.
        """
        if field.description and len(field.description) <= 40:
            return field.description
        return self._humanize(field.name)

    def _humanize(self, name: str) -> str:
        """Convert snake_case to Title Case.

        Args:
            name: Field or group name in snake_case.

        Returns:
            Human-readable title case string.
        """
        return name.replace("_", " ").title()

    def _find_id_field(self, fields: list[FieldDefinition]) -> str | None:
        """Find the identifying field for an entity list.

        Checks for common ID field patterns, then falls back to first text field.

        Args:
            fields: List of field definitions.

        Returns:
            Field name to use as identifier, or None if not found.
        """
        # Check for common ID field patterns
        id_patterns = ["name", "product_name", "entity_id", "id", "title"]
        for pattern in id_patterns:
            for field in fields:
                if field.name == pattern or field.name.endswith(f"_{pattern}"):
                    return field.name

        # Fallback to first text field
        for field in fields:
            if field.field_type == "text":
                return field.name

        return None

    def _infer_unit(self, field_name: str) -> str:
        """Infer unit suffix from field name.

        Args:
            field_name: Name of the field.

        Returns:
            Unit suffix (e.g., "kW") or empty string.
        """
        unit_map = {
            "_kw": "kW",
            "_nm": "Nm",
            "_rpm": "RPM",
            "_percent": "%",
            "_kg": "kg",
            "_mm": "mm",
            "_m": "m",
            "_cm": "cm",
            "_hz": "Hz",
            "_mhz": "MHz",
            "_ghz": "GHz",
            "_mb": "MB",
            "_gb": "GB",
            "_tb": "TB",
        }
        lower_name = field_name.lower()
        for suffix, unit in unit_map.items():
            if lower_name.endswith(suffix):
                return unit
        return ""

    def get_flattened_columns_for_source(
        self, extraction_schema: dict
    ) -> tuple[list[str], dict[str, str], dict[str, ColumnMetadata]]:
        """Get flattened columns for per-source (per-URL) table generation.

        Flattens all field groups into a single column list. Detects name
        collisions and prefixes with field group name when needed.

        Args:
            extraction_schema: Project's JSONB extraction schema.

        Returns:
            Tuple of (column_names, column_labels, column_metadata)
            where column_names is ordered list including metadata columns,
            column_labels maps name->display label,
            and column_metadata maps name->ColumnMetadata for LLM merge context.
        """
        field_groups = self._adapter.convert_to_field_groups(extraction_schema)

        # First pass: collect all field names to detect collisions
        field_name_count: dict[str, int] = {}
        for group in field_groups:
            if group.is_entity_list:
                # Entity lists become a single column with group name
                key = f"{group.name}"
                field_name_count[key] = field_name_count.get(key, 0) + 1
            else:
                for field in group.fields:
                    field_name_count[field.name] = (
                        field_name_count.get(field.name, 0) + 1
                    )

        # Metadata columns always first
        columns: list[str] = ["source_url", "source_title", "domain"]
        labels: dict[str, str] = {
            "source_url": "URL",
            "source_title": "Page Title",
            "domain": "Domain",
        }
        metadata: dict[str, ColumnMetadata] = {}

        # Add metadata column definitions (not from schema)
        for col in columns:
            metadata[col] = ColumnMetadata(
                name=col,
                label=labels[col],
                field_type="text",
                description=f"Source {col.replace('_', ' ')}",
                field_group="_metadata",
            )

        # Second pass: build columns with prefixing for collisions
        for group in field_groups:
            if group.is_entity_list:
                # Entity lists become "{group_name}" column containing formatted list
                col_name = group.name
                columns.append(col_name)
                labels[col_name] = self._humanize(group.name)
                metadata[col_name] = ColumnMetadata(
                    name=col_name,
                    label=labels[col_name],
                    field_type="list",
                    description=group.description,
                    field_group=group.name,
                    is_entity_list=True,
                )
            else:
                for field in group.fields:
                    # Prefix if collision exists
                    if field_name_count.get(field.name, 0) > 1:
                        col_name = f"{group.name}.{field.name}"
                    else:
                        col_name = field.name

                    columns.append(col_name)
                    labels[col_name] = self._get_field_label(field)
                    metadata[col_name] = ColumnMetadata(
                        name=col_name,
                        label=labels[col_name],
                        field_type=field.field_type,
                        description=field.description,
                        field_group=group.name,
                        enum_values=field.enum_values,
                    )

        # Add confidence at the end
        columns.append("avg_confidence")
        labels["avg_confidence"] = "Confidence"
        metadata["avg_confidence"] = ColumnMetadata(
            name="avg_confidence",
            label="Confidence",
            field_type="float",
            description="Average extraction confidence across all field groups",
            field_group="_metadata",
        )

        return columns, labels, metadata

    def get_extraction_type_to_fields(
        self, extraction_schema: dict
    ) -> dict[str, list[str]]:
        """Map extraction_type (field group name) to its field names.

        Used for flattening extractions grouped by source_id.

        Args:
            extraction_schema: Project's JSONB extraction schema.

        Returns:
            Dict mapping field group name to list of field names.
        """
        field_groups = self._adapter.convert_to_field_groups(extraction_schema)

        # Detect collisions for prefixing
        field_name_count: dict[str, int] = {}
        for group in field_groups:
            if not group.is_entity_list:
                for field in group.fields:
                    field_name_count[field.name] = (
                        field_name_count.get(field.name, 0) + 1
                    )

        result: dict[str, list[str]] = {}
        for group in field_groups:
            if group.is_entity_list:
                # Entity list extractions map to single column
                result[group.name] = [group.name]
            else:
                fields = []
                for field in group.fields:
                    if field_name_count.get(field.name, 0) > 1:
                        fields.append(f"{group.name}.{field.name}")
                    else:
                        fields.append(field.name)
                result[group.name] = fields

        return result
