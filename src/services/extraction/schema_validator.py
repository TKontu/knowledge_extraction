"""Schema-aware validation for extraction results."""

from typing import Any

import structlog

from services.extraction.field_groups import FieldDefinition, FieldGroup

logger = structlog.get_logger(__name__)

# Keys that are metadata, not extracted fields
_METADATA_KEYS = {"confidence", "_quotes", "_conflicts", "_validation"}


class SchemaValidator:
    """Validates and coerces extraction results against field group schemas.

    Performs:
    - Type coercion (string "42" → int 42, "true" → bool True)
    - Enum validation (case-insensitive match, nullify invalid)
    - List wrapping (single value → [value] for list fields)
    - Confidence gating (suppress all fields below threshold)
    """

    def __init__(self, min_confidence: float = 0.0) -> None:
        self.min_confidence = min_confidence

    def validate(
        self, data: dict[str, Any], group: FieldGroup
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Validate and coerce data against field group schema.

        Args:
            data: Extraction result dict (may contain _quotes, _conflicts, confidence).
            group: Field group definition.

        Returns:
            Tuple of (cleaned_data, violations). Metadata keys are preserved.
        """
        violations: list[dict[str, str]] = []

        # Confidence gating: suppress all fields if below threshold
        confidence = data.get("confidence", 0.0)
        if self.min_confidence > 0 and confidence < self.min_confidence:
            cleaned = {k: v for k, v in data.items() if k in _METADATA_KEYS}
            violations.append({
                "field": "*",
                "issue": "confidence_below_threshold",
                "detail": f"confidence {confidence} < threshold {self.min_confidence}",
            })
            # Set all field values to None
            for field in group.fields:
                cleaned[field.name] = None
            cleaned["_validation"] = violations
            return cleaned, violations

        if group.is_entity_list:
            return self._validate_entity_list(data, group, violations)

        cleaned = {}
        # Preserve metadata keys
        for key in _METADATA_KEYS:
            if key in data:
                cleaned[key] = data[key]

        for field in group.fields:
            value = data.get(field.name)
            if value is None:
                cleaned[field.name] = value
                continue

            coerced, violation = self._coerce_value(value, field)
            cleaned[field.name] = coerced
            if violation:
                violations.append(violation)

        if violations:
            cleaned["_validation"] = violations
            logger.info(
                "schema_validation_violations",
                group=group.name,
                count=len(violations),
            )

        return cleaned, violations

    def _validate_entity_list(
        self,
        data: dict[str, Any],
        group: FieldGroup,
        violations: list[dict[str, str]],
    ) -> tuple[dict[str, Any], list[dict[str, str]]]:
        """Validate entity list results."""
        cleaned = {}
        for key in _METADATA_KEYS:
            if key in data:
                cleaned[key] = data[key]

        # Find entity list key
        entity_key = None
        for key, value in data.items():
            if key not in _METADATA_KEYS and isinstance(value, list):
                entity_key = key
                break

        if not entity_key:
            cleaned["_validation"] = violations
            return cleaned, violations

        validated_entities = []
        for i, entity in enumerate(data[entity_key]):
            if not isinstance(entity, dict):
                continue
            validated_entity = {}
            for field in group.fields:
                value = entity.get(field.name)
                if value is None:
                    validated_entity[field.name] = value
                    continue
                coerced, violation = self._coerce_value(value, field)
                validated_entity[field.name] = coerced
                if violation:
                    violation["entity_index"] = str(i)
                    violations.append(violation)
            # Preserve _quote if present
            if "_quote" in entity:
                validated_entity["_quote"] = entity["_quote"]
            validated_entities.append(validated_entity)

        cleaned[entity_key] = validated_entities
        if violations:
            cleaned["_validation"] = violations
            logger.info(
                "schema_validation_violations",
                group=group.name,
                count=len(violations),
            )
        return cleaned, violations

    def _coerce_value(
        self, value: Any, field: FieldDefinition
    ) -> tuple[Any, dict[str, str] | None]:
        """Coerce a value to match field type.

        Returns:
            Tuple of (coerced_value, violation_dict_or_None).
        """
        if field.field_type == "boolean":
            return self._coerce_bool(value, field)
        elif field.field_type == "integer":
            return self._coerce_int(value, field)
        elif field.field_type == "float":
            return self._coerce_float(value, field)
        elif field.field_type == "enum":
            return self._coerce_enum(value, field)
        elif field.field_type == "list":
            return self._coerce_list(value, field)
        # text: pass through as-is
        return value, None

    def _coerce_bool(
        self, value: Any, field: FieldDefinition
    ) -> tuple[bool | None, dict[str, str] | None]:
        if isinstance(value, bool):
            return value, None
        if isinstance(value, str):
            if value.lower() in ("true", "yes", "1"):
                return True, {
                    "field": field.name,
                    "issue": "type_coerced",
                    "detail": f"string '{value}' → True",
                }
            if value.lower() in ("false", "no", "0"):
                return False, {
                    "field": field.name,
                    "issue": "type_coerced",
                    "detail": f"string '{value}' → False",
                }
        if isinstance(value, (int, float)):
            return bool(value), {
                "field": field.name,
                "issue": "type_coerced",
                "detail": f"number {value} → {bool(value)}",
            }
        return None, {
            "field": field.name,
            "issue": "invalid_type",
            "detail": f"cannot coerce {type(value).__name__} to bool",
        }

    def _coerce_int(
        self, value: Any, field: FieldDefinition
    ) -> tuple[int | None, dict[str, str] | None]:
        if isinstance(value, int) and not isinstance(value, bool):
            return value, None
        if isinstance(value, float):
            return int(value), {
                "field": field.name,
                "issue": "type_coerced",
                "detail": f"float {value} → int {int(value)}",
            }
        if isinstance(value, str):
            # Strip common formatting
            cleaned = value.replace(",", "").replace(" ", "").strip()
            try:
                return int(float(cleaned)), {
                    "field": field.name,
                    "issue": "type_coerced",
                    "detail": f"string '{value}' → int {int(float(cleaned))}",
                }
            except (ValueError, OverflowError):
                pass
        return None, {
            "field": field.name,
            "issue": "invalid_type",
            "detail": f"cannot coerce '{value}' to int",
        }

    def _coerce_float(
        self, value: Any, field: FieldDefinition
    ) -> tuple[float | None, dict[str, str] | None]:
        if isinstance(value, float):
            return value, None
        if isinstance(value, int) and not isinstance(value, bool):
            return float(value), None
        if isinstance(value, str):
            cleaned = value.replace(",", "").replace(" ", "").strip()
            try:
                return float(cleaned), {
                    "field": field.name,
                    "issue": "type_coerced",
                    "detail": f"string '{value}' → float {float(cleaned)}",
                }
            except (ValueError, OverflowError):
                pass
        return None, {
            "field": field.name,
            "issue": "invalid_type",
            "detail": f"cannot coerce '{value}' to float",
        }

    def _coerce_enum(
        self, value: Any, field: FieldDefinition
    ) -> tuple[str | None, dict[str, str] | None]:
        if not field.enum_values:
            return str(value), None

        str_value = str(value)
        # Exact match
        if str_value in field.enum_values:
            return str_value, None

        # Case-insensitive match
        lower_map = {ev.lower(): ev for ev in field.enum_values}
        if str_value.lower() in lower_map:
            matched = lower_map[str_value.lower()]
            return matched, {
                "field": field.name,
                "issue": "type_coerced",
                "detail": f"enum case corrected '{str_value}' → '{matched}'",
            }

        # No match → nullify
        return None, {
            "field": field.name,
            "issue": "invalid_enum",
            "detail": f"'{str_value}' not in {field.enum_values}",
        }

    def _coerce_list(
        self, value: Any, field: FieldDefinition
    ) -> tuple[list, dict[str, str] | None]:
        if isinstance(value, list):
            return value, None

        # Single value → wrap in list
        return [value], {
            "field": field.name,
            "issue": "type_coerced",
            "detail": f"single value wrapped in list",
        }
