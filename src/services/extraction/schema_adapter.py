"""Schema adapter for converting JSONB extraction schemas to FieldGroup objects."""

from dataclasses import dataclass
from typing import Any


@dataclass
class ValidationResult:
    """Result of schema validation."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]


class SchemaAdapter:
    """Converts extraction_schema JSONB to FieldGroup objects."""

    VALID_FIELD_TYPES = {"boolean", "integer", "float", "text", "list", "enum"}
    MAX_FIELD_GROUPS = 20
    MAX_FIELDS_PER_GROUP = 30

    def validate_extraction_schema(self, schema: dict) -> ValidationResult:
        """Validate schema structure.

        Args:
            schema: The extraction schema to validate.

        Returns:
            ValidationResult with is_valid flag and list of errors.
        """
        errors = []
        warnings = []

        # Rule 1: Schema must have keys: name, field_groups
        if "name" not in schema:
            errors.append("Schema must have 'name' field")

        if "field_groups" not in schema:
            errors.append("Schema must have 'field_groups' field")
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

        if not isinstance(schema["field_groups"], list):
            errors.append("'field_groups' must be a list")
            return ValidationResult(is_valid=False, errors=errors, warnings=warnings)

        # Rule 10: Max 20 field_groups per schema
        if len(schema["field_groups"]) > self.MAX_FIELD_GROUPS:
            errors.append(
                f"Schema has {len(schema['field_groups'])} field groups, "
                f"maximum is {self.MAX_FIELD_GROUPS}"
            )

        # Rule 8: No duplicate field_group names
        group_names = []
        for fg in schema["field_groups"]:
            if isinstance(fg, dict) and "name" in fg:
                if fg["name"] in group_names:
                    errors.append(
                        f"Duplicate field_group name: '{fg['name']}'"
                    )
                group_names.append(fg["name"])

        # Validate each field_group
        for i, fg in enumerate(schema["field_groups"]):
            if not isinstance(fg, dict):
                errors.append(f"field_groups[{i}] must be a dict")
                continue

            # Rule 2: Each field_group must have: name, description, fields
            if "name" not in fg:
                errors.append(f"field_groups[{i}] missing 'name'")
            if "description" not in fg:
                errors.append(f"field_groups[{i}] missing 'description'")
            if "fields" not in fg:
                errors.append(f"field_groups[{i}] missing 'fields'")
                continue

            if not isinstance(fg["fields"], list):
                errors.append(f"field_groups[{i}]['fields'] must be a list")
                continue

            # Rule 11: Max 30 fields per field_group
            if len(fg["fields"]) > self.MAX_FIELDS_PER_GROUP:
                errors.append(
                    f"field_groups[{i}] has {len(fg['fields'])} fields, "
                    f"maximum is {self.MAX_FIELDS_PER_GROUP}"
                )

            # Rule 7: is_entity_list groups must have product_name or entity_id field
            if fg.get("is_entity_list", False):
                field_names = [f.get("name") for f in fg["fields"] if isinstance(f, dict)]
                if "product_name" not in field_names and "entity_id" not in field_names:
                    errors.append(
                        f"field_groups[{i}] has is_entity_list=true but no "
                        "'product_name' or 'entity_id' field"
                    )

            # Rule 9: No duplicate field names within group
            field_names_in_group = []
            for j, field in enumerate(fg["fields"]):
                if not isinstance(field, dict):
                    errors.append(f"field_groups[{i}]['fields'][{j}] must be a dict")
                    continue

                # Rule 3: Each field must have: name, field_type, description
                if "name" not in field:
                    errors.append(f"field_groups[{i}]['fields'][{j}] missing 'name'")
                    continue

                if "field_type" not in field:
                    errors.append(f"field_groups[{i}]['fields'][{j}] missing 'field_type'")
                    continue

                if "description" not in field:
                    errors.append(f"field_groups[{i}]['fields'][{j}] missing 'description'")

                # Check duplicate field names
                if field["name"] in field_names_in_group:
                    errors.append(
                        f"field_groups[{i}] has duplicate field name: '{field['name']}'"
                    )
                field_names_in_group.append(field["name"])

                # Rule 4: field_type must be valid
                if field["field_type"] not in self.VALID_FIELD_TYPES:
                    errors.append(
                        f"field_groups[{i}]['fields'][{j}] has invalid field_type: "
                        f"'{field['field_type']}'. Valid types: {self.VALID_FIELD_TYPES}"
                    )

                # Rule 5: Enum fields must have enum_values (non-empty)
                if field["field_type"] == "enum":
                    if "enum_values" not in field:
                        errors.append(
                            f"field_groups[{i}]['fields'][{j}] is enum but missing 'enum_values'"
                        )
                    elif not field["enum_values"] or len(field["enum_values"]) == 0:
                        errors.append(
                            f"field_groups[{i}]['fields'][{j}] is enum but 'enum_values' is empty"
                        )

                # Rule 6: Required fields must have default value
                if field.get("required", False):
                    if "default" not in field:
                        errors.append(
                            f"field_groups[{i}]['fields'][{j}] is required but missing 'default'"
                        )

        return ValidationResult(
            is_valid=len(errors) == 0,
            errors=errors,
            warnings=warnings,
        )

    def convert_to_field_groups(self, schema: dict) -> list:
        """Convert JSONB schema to list of FieldGroup objects.

        Args:
            schema: The validated extraction schema.

        Returns:
            List of FieldGroup objects.
        """
        from services.extraction.field_groups import FieldDefinition, FieldGroup

        field_groups = []
        for fg_def in schema.get("field_groups", []):
            fields = []
            for f_def in fg_def.get("fields", []):
                fields.append(
                    FieldDefinition(
                        name=f_def["name"],
                        field_type=f_def["field_type"],
                        description=f_def["description"],
                        required=f_def.get("required", False),
                        default=f_def.get("default"),
                        enum_values=f_def.get("enum_values"),
                    )
                )

            field_groups.append(
                FieldGroup(
                    name=fg_def["name"],
                    description=fg_def["description"],
                    fields=fields,
                    prompt_hint=fg_def.get("prompt_hint")
                    or self.generate_prompt_hint(fg_def),
                    is_entity_list=fg_def.get("is_entity_list", False),
                )
            )

        return field_groups

    def generate_prompt_hint(self, field_group_def: dict) -> str:
        """Generate LLM prompt hint from field group definition.

        Builds a useful prompt hint by analyzing the field definitions,
        including field names and types to guide the LLM.

        Args:
            field_group_def: The field group definition dict.

        Returns:
            Generated prompt hint string.
        """
        description = field_group_def.get("description", "")
        name = field_group_def.get("name", "")
        is_entity_list = field_group_def.get("is_entity_list", False)
        fields = field_group_def.get("fields", [])

        # Build field summary
        field_names = [f.get("name", "") for f in fields if f.get("name")]
        boolean_fields = [f.get("name") for f in fields if f.get("field_type") == "boolean"]
        enum_fields = [f.get("name") for f in fields if f.get("field_type") == "enum"]

        hints = []

        if is_entity_list:
            hints.append(f"Extract each {name.rstrip('s')} item found in the content.")
            hints.append(f"Return a list of {name} with the following fields.")
        else:
            hints.append(f"Extract {description.lower()}.")

        # Add field-specific guidance
        if boolean_fields:
            hints.append(
                f"For boolean fields ({', '.join(boolean_fields)}), "
                "only return true if there is explicit evidence."
            )

        if enum_fields:
            hints.append(
                f"For enum fields ({', '.join(enum_fields)}), "
                "select the most appropriate value from the allowed options."
            )

        if field_names:
            hints.append(f"Key fields to extract: {', '.join(field_names[:5])}.")

        return " ".join(hints)
