"""Schema adapter for converting JSONB extraction schemas to FieldGroup objects."""

import re
from dataclasses import dataclass, field


@dataclass
class ClassificationConfig:
    """Configuration for page classification during extraction.

    Controls skip patterns used by PageClassifier to filter pages before
    semantic classification. This enables context-agnostic extraction where
    patterns that might be irrelevant for one use case (e.g., /careers/ for
    company analysis) could be relevant for another (e.g., HR research).

    Attributes:
        skip_patterns: URL patterns to skip during extraction.
            - None: Use system default (context-agnostic when smart classification on)
            - []: Explicitly disable all skip patterns
            - [...]: Use custom patterns only
    """

    skip_patterns: list[str] | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "ClassificationConfig":
        """Create from template's classification_config dict.

        Args:
            data: Dictionary with classification_config fields, or None.

        Returns:
            ClassificationConfig instance with parsed values.
        """
        if not data:
            return cls()
        return cls(skip_patterns=data.get("skip_patterns"))

    def validate(self) -> tuple[bool, list[str]]:
        """Validate configuration values.

        Returns:
            Tuple of (is_valid, error_messages).
        """
        errors = []

        if self.skip_patterns is not None:
            if not isinstance(self.skip_patterns, list):
                errors.append("classification_config.skip_patterns must be a list or null")
            else:
                for i, pattern in enumerate(self.skip_patterns):
                    if not isinstance(pattern, str):
                        errors.append(
                            f"classification_config.skip_patterns[{i}] must be a string"
                        )
                    else:
                        try:
                            re.compile(pattern)
                        except re.error as e:
                            errors.append(
                                f"classification_config.skip_patterns[{i}] is invalid regex: {e}"
                            )

        return len(errors) == 0, errors


@dataclass
class CrawlConfig:
    """Optional template-driven crawl configuration for smart crawl.

    Controls URL filtering behavior during smart crawl. When provided in a
    template, these settings enhance the default field_group-based filtering.

    Attributes:
        include_patterns: URL regex patterns to always include (whitelist).
        exclude_patterns: URL regex patterns to always exclude (blacklist).
        focus_terms: Semantic search/focus terms for URL filtering.
            These are used both for Firecrawl Map's search parameter and
            to enhance the embedding context for relevance scoring.
        relevance_threshold: Minimum embedding similarity for URL relevance.
            Overrides the system default (smart_crawl_default_relevance_threshold).
    """

    include_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    focus_terms: list[str] | None = None
    relevance_threshold: float | None = None

    @classmethod
    def from_dict(cls, data: dict | None) -> "CrawlConfig | None":
        """Create from template's crawl_config dict.

        Args:
            data: Dictionary with crawl_config fields, or None.

        Returns:
            CrawlConfig instance or None if no config provided.
        """
        if not data:
            return None  # Backward compatible - no config = system defaults

        return cls(
            include_patterns=data.get("include_patterns"),
            exclude_patterns=data.get("exclude_patterns"),
            focus_terms=data.get("focus_terms"),
            relevance_threshold=data.get("relevance_threshold"),
        )

    def validate(self) -> tuple[bool, list[str]]:
        """Validate configuration values.

        Returns:
            Tuple of (is_valid, error_messages).
        """
        errors = []

        # Validate include_patterns
        if self.include_patterns is not None:
            if not isinstance(self.include_patterns, list):
                errors.append("crawl_config.include_patterns must be a list or null")
            else:
                for i, pattern in enumerate(self.include_patterns):
                    if not isinstance(pattern, str):
                        errors.append(
                            f"crawl_config.include_patterns[{i}] must be a string"
                        )
                    else:
                        try:
                            re.compile(pattern)
                        except re.error as e:
                            errors.append(
                                f"crawl_config.include_patterns[{i}] is invalid regex: {e}"
                            )

        # Validate exclude_patterns
        if self.exclude_patterns is not None:
            if not isinstance(self.exclude_patterns, list):
                errors.append("crawl_config.exclude_patterns must be a list or null")
            else:
                for i, pattern in enumerate(self.exclude_patterns):
                    if not isinstance(pattern, str):
                        errors.append(
                            f"crawl_config.exclude_patterns[{i}] must be a string"
                        )
                    else:
                        try:
                            re.compile(pattern)
                        except re.error as e:
                            errors.append(
                                f"crawl_config.exclude_patterns[{i}] is invalid regex: {e}"
                            )

        # Validate focus_terms
        if self.focus_terms is not None:
            if not isinstance(self.focus_terms, list):
                errors.append("crawl_config.focus_terms must be a list or null")
            else:
                for i, term in enumerate(self.focus_terms):
                    if not isinstance(term, str):
                        errors.append(
                            f"crawl_config.focus_terms[{i}] must be a string"
                        )

        # Validate relevance_threshold
        if self.relevance_threshold is not None:
            if not isinstance(self.relevance_threshold, (int, float)):
                errors.append("crawl_config.relevance_threshold must be a number")
            elif not 0.0 <= self.relevance_threshold <= 1.0:
                errors.append(
                    "crawl_config.relevance_threshold must be between 0.0 and 1.0"
                )

        return len(errors) == 0, errors


@dataclass
class ValidationResult:
    """Result of schema validation."""

    is_valid: bool
    errors: list[str]
    warnings: list[str]


@dataclass
class ExtractionContext:
    """Context configuration for extraction prompts."""

    source_type: str = "content"
    source_label: str = "Source"
    entity_id_fields: list[str] = field(
        default_factory=lambda: ["product_name", "entity_id", "name", "id"]
    )

    @classmethod
    def from_dict(cls, data: dict | None) -> "ExtractionContext":
        """Create from template's extraction_context dict."""
        if not data:
            return cls()
        return cls(
            source_type=data.get("source_type", "content"),
            source_label=data.get("source_label", "Source"),
            entity_id_fields=data.get(
                "entity_id_fields", ["product_name", "entity_id", "name", "id"]
            ),
        )


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

            # Rule 7: is_entity_list groups should have at least one identifiable field
            # (warning, not error - some lists may not need dedup)
            if fg.get("is_entity_list", False):
                field_names = [f.get("name") for f in fg["fields"] if isinstance(f, dict)]
                # Check against common ID patterns - validation doesn't know template context
                common_id_fields = ["entity_id", "name", "id", "product_name"]
                has_id_field = any(name in field_names for name in common_id_fields)
                if not has_id_field:
                    warnings.append(
                        f"field_groups[{i}] is_entity_list=true but has no common ID field "
                        f"for deduplication. Consider adding one of: {common_id_fields}"
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

    def parse_template(
        self, template: dict
    ) -> tuple[list, ExtractionContext, ClassificationConfig, "CrawlConfig | None"]:
        """Parse template into FieldGroups, ExtractionContext, ClassificationConfig, and CrawlConfig.

        Args:
            template: Full template dict with extraction_context, extraction_schema,
                optional classification_config, and optional crawl_config.

        Returns:
            Tuple of (field_groups, context, classification_config, crawl_config).
            crawl_config is None if not present in template.
        """
        schema = template.get("extraction_schema", template)  # Backward compat
        field_groups = self.convert_to_field_groups(schema)
        context = ExtractionContext.from_dict(template.get("extraction_context"))
        classification_config = ClassificationConfig.from_dict(
            template.get("classification_config")
        )
        crawl_config = CrawlConfig.from_dict(template.get("crawl_config"))
        return field_groups, context, classification_config, crawl_config

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

        The system prompt already lists fields and types, so this hint
        focuses on extraction strategy and domain-specific guidance.

        Args:
            field_group_def: The field group definition dict.

        Returns:
            Generated prompt hint string.
        """
        description = field_group_def.get("description", "")
        name = field_group_def.get("name", "")
        is_entity_list = field_group_def.get("is_entity_list", False)
        fields = field_group_def.get("fields", [])

        hints = []

        if is_entity_list:
            # Entity lists need guidance on finding multiple items
            entity_singular = name.rstrip("s") if name.endswith("s") else name
            hints.append(f"Look for all {name} mentioned in the content.")
            hints.append(f"Each {entity_singular} should be a separate item in the list.")
        else:
            # Regular extraction - focus on where to find info
            hints.append(f"Look for {description.lower()} in the content.")

        # Add guidance for list fields (complex to extract)
        list_fields = [f.get("name") for f in fields if f.get("field_type") == "list"]
        if list_fields:
            hints.append("For list fields, collect all mentioned values.")

        return " ".join(hints)
