"""Schema-driven table report generation."""

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_adapter import SchemaAdapter


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
