"""Schema validation for dynamic extraction schemas."""

from typing import Any
from pydantic import BaseModel, ValidationError, create_model


class SchemaValidator:
    """Validates extraction data against project schema using dynamic Pydantic models."""

    def __init__(self, extraction_schema: dict):
        """Initialize validator with extraction schema.

        Args:
            extraction_schema: JSONB extraction schema from project
        """
        self.schema = extraction_schema
        self._model = self._build_pydantic_model()

    def _build_pydantic_model(self) -> type[BaseModel]:
        """Dynamically create Pydantic model from schema.

        Returns:
            Pydantic model class for validation
        """
        fields = {}

        for field_def in self.schema.get("fields", []):
            field_type = self._map_type(field_def["type"])
            is_required = field_def.get("required", False)
            default = field_def.get("default")

            if is_required:
                # Required field with no default
                fields[field_def["name"]] = (field_type, ...)
            elif default is not None:
                # Optional field with default
                fields[field_def["name"]] = (field_type, default)
            else:
                # Optional field without default (nullable)
                fields[field_def["name"]] = (field_type | None, None)

        # Create dynamic model
        return create_model("DynamicExtraction", **fields)

    def _map_type(self, type_str: str) -> type:
        """Map schema type string to Python type.

        Args:
            type_str: Type string from schema (e.g., "text", "integer")

        Returns:
            Corresponding Python type
        """
        type_map = {
            "text": str,
            "string": str,
            "integer": int,
            "float": float,
            "boolean": bool,
            "json": dict,
            "list": list,
            "enum": str,  # Enum validation done separately
            "date": str,  # ISO format string
        }
        return type_map.get(type_str, Any)

    def validate(self, data: dict) -> tuple[bool, list[str]]:
        """Validate data against schema.

        Args:
            data: Dictionary of extraction data to validate

        Returns:
            Tuple of (is_valid, error_messages)
        """
        try:
            # Validate with Pydantic model
            self._model(**data)

            # Additional enum validation
            enum_errors = self._validate_enums(data)
            if enum_errors:
                return False, enum_errors

            return True, []

        except ValidationError as e:
            # Convert Pydantic errors to readable messages
            errors = []
            for err in e.errors():
                field = err["loc"][0] if err["loc"] else "unknown"
                msg = err["msg"]
                errors.append(f"{field}: {msg}")
            return False, errors

    def _validate_enums(self, data: dict) -> list[str]:
        """Validate enum field values against allowed values.

        Args:
            data: Data dictionary to validate

        Returns:
            List of error messages (empty if valid)
        """
        errors = []

        for field_def in self.schema.get("fields", []):
            if field_def["type"] != "enum":
                continue

            field_name = field_def["name"]
            if field_name not in data:
                continue

            value = data[field_name]
            allowed = field_def.get("values", [])

            if value not in allowed:
                errors.append(
                    f"{field_name}: must be one of {allowed}, got '{value}'"
                )

        return errors

    def get_field_names(self) -> list[str]:
        """Get list of all field names from schema.

        Returns:
            List of field names
        """
        return [f["name"] for f in self.schema.get("fields", [])]

    def get_required_fields(self) -> list[str]:
        """Get list of required field names.

        Returns:
            List of required field names
        """
        return [
            f["name"] for f in self.schema.get("fields", []) if f.get("required", False)
        ]
