# TODO: Template-Driven Extraction System

**Agent:** agent-template-extraction
**Branch:** `feat/template-driven-extraction`
**Priority:** High

## Context

The extraction system has two critical issues:

1. **No default template**: Projects require `extraction_schema` at creation. Without one, project creation fails.

2. **Hardcoded extraction logic**: `SchemaExtractionOrchestrator` imports hardcoded `ALL_FIELD_GROUPS` from `field_groups.py`. The `project.extraction_schema` stored in the database is **completely ignored** during extraction.

**Current state:**
- `src/services/extraction/schema_orchestrator.py:9` imports `ALL_FIELD_GROUPS` directly
- `src/services/extraction/schema_orchestrator.py:40` uses `groups = field_groups or ALL_FIELD_GROUPS`
- `src/services/extraction/pipeline.py:385-389` calls orchestrator WITHOUT loading `project.extraction_schema`
- `src/models.py:297` has `extraction_schema: dict = Field(...)` as **required**
- `src/services/projects/templates.py` has 6 templates but no `DEFAULT_EXTRACTION_TEMPLATE`

**Key dataclasses (in `field_groups.py`):**
```python
@dataclass
class FieldDefinition:
    name: str
    field_type: str  # "boolean", "integer", "text", "list", "float", "enum"
    description: str
    required: bool = False
    default: Any = None
    enum_values: list[str] | None = None

@dataclass
class FieldGroup:
    name: str  # Used as extraction_type
    description: str
    fields: list[FieldDefinition]
    prompt_hint: str
    is_entity_list: bool = False
```

## Objective

Implement a schema adapter that converts `project.extraction_schema` (JSONB) to `FieldGroup[]` objects at runtime, auto-assigns a default template when schema is missing, and makes the extraction pipeline use the project's schema instead of hardcoded field groups.

## Tasks

### Phase 1: Foundation

#### Task 1.1: Create SchemaAdapter

**File:** `src/services/extraction/schema_adapter.py` (NEW)

Create a `SchemaAdapter` class with these methods:

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class ValidationResult:
    is_valid: bool
    errors: list[str]
    warnings: list[str]

class SchemaAdapter:
    """Converts extraction_schema JSONB to FieldGroup objects."""

    def validate_extraction_schema(self, schema: dict) -> ValidationResult:
        """Validate schema structure. Return ValidationResult with errors."""

    def convert_to_field_groups(self, schema: dict) -> list[FieldGroup]:
        """Convert JSONB schema to list of FieldGroup objects."""

    def generate_prompt_hint(self, field_group_def: dict) -> str:
        """Generate LLM prompt hint from field group definition."""
```

**Validation Rules (all must pass):**
1. Schema must have keys: `name`, `field_groups` (list)
2. Each field_group must have: `name`, `description`, `fields` (list)
3. Each field must have: `name`, `field_type`, `description`
4. `field_type` must be one of: `boolean`, `integer`, `float`, `text`, `list`, `enum`
5. Enum fields must have `enum_values` (non-empty list)
6. Required fields (`required=true`) must have `default` value
7. `is_entity_list=true` groups must have a field named `product_name` or `entity_id`
8. No duplicate `field_group.name` within schema
9. No duplicate `field.name` within a field_group
10. Max 20 field_groups per schema
11. Max 30 fields per field_group

**Conversion logic:**
```python
def convert_to_field_groups(self, schema: dict) -> list[FieldGroup]:
    from services.extraction.field_groups import FieldDefinition, FieldGroup

    field_groups = []
    for fg_def in schema.get("field_groups", []):
        fields = []
        for f_def in fg_def.get("fields", []):
            fields.append(FieldDefinition(
                name=f_def["name"],
                field_type=f_def["field_type"],
                description=f_def["description"],
                required=f_def.get("required", False),
                default=f_def.get("default"),
                enum_values=f_def.get("enum_values"),
            ))

        field_groups.append(FieldGroup(
            name=fg_def["name"],
            description=fg_def["description"],
            fields=fields,
            prompt_hint=fg_def.get("prompt_hint") or self.generate_prompt_hint(fg_def),
            is_entity_list=fg_def.get("is_entity_list", False),
        ))

    return field_groups
```

**Tests:** `tests/test_schema_adapter.py`
- `test_valid_schema_conversion` - valid schema converts correctly
- `test_validation_rule_1_missing_name` - error if schema missing `name`
- `test_validation_rule_1_missing_field_groups` - error if missing `field_groups`
- `test_validation_rule_2_field_group_missing_name` - error per rule
- `test_validation_rule_3_field_missing_field_type` - error per rule
- `test_validation_rule_4_invalid_field_type` - error for unknown type
- `test_validation_rule_5_enum_without_values` - error if enum has no values
- `test_validation_rule_6_required_without_default` - error if required but no default
- `test_validation_rule_7_entity_list_without_product_name` - error for entity_list
- `test_validation_rule_8_duplicate_field_group_names` - error on duplicate
- `test_validation_rule_9_duplicate_field_names` - error on duplicate
- `test_validation_rule_10_too_many_field_groups` - error if >20 groups
- `test_validation_rule_11_too_many_fields` - error if >30 fields in group
- `test_prompt_hint_generation` - auto-generates hint from description
- `test_is_entity_list_flag_preserved` - entity_list groups converted correctly

---

#### Task 1.2: Add DEFAULT_EXTRACTION_TEMPLATE

**File:** `src/services/projects/templates.py` (MODIFY)

Add at the end of the file (before `__all__`):

```python
# Default template for projects without custom schema
DEFAULT_EXTRACTION_TEMPLATE = {
    "name": "default",
    "description": "Generic extraction template for any content type",
    "source_config": {"type": "web", "group_by": "source"},
    "extraction_schema": {
        "name": "generic_facts",
        "version": "1.0",
        "description": "Generic fact extraction schema",
        "field_groups": [
            {
                "name": "entity_info",
                "description": "Basic entity identification",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "entity_name",
                        "field_type": "text",
                        "description": "Name of the primary entity or subject",
                        "required": True,
                        "default": "",
                    },
                    {
                        "name": "entity_type",
                        "field_type": "enum",
                        "description": "Type of entity",
                        "required": True,
                        "default": "unknown",
                        "enum_values": ["company", "product", "person", "organization", "location", "unknown"],
                    },
                    {
                        "name": "description",
                        "field_type": "text",
                        "description": "Brief description of the entity",
                        "required": False,
                    },
                ],
            },
            {
                "name": "key_facts",
                "description": "Important factual information",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "fact_category",
                        "field_type": "enum",
                        "description": "Category of fact",
                        "required": True,
                        "default": "general",
                        "enum_values": ["general", "technical", "financial", "operational", "historical"],
                    },
                    {
                        "name": "fact_text",
                        "field_type": "text",
                        "description": "The factual statement",
                        "required": True,
                        "default": "",
                    },
                    {
                        "name": "confidence",
                        "field_type": "float",
                        "description": "Confidence score 0.0-1.0",
                        "required": True,
                        "default": 0.8,
                    },
                ],
            },
            {
                "name": "contact_info",
                "description": "Contact and location information",
                "is_entity_list": False,
                "fields": [
                    {
                        "name": "locations",
                        "field_type": "list",
                        "description": "List of locations (city, country)",
                        "required": False,
                    },
                    {
                        "name": "website",
                        "field_type": "text",
                        "description": "Website URL",
                        "required": False,
                    },
                    {
                        "name": "contact_email",
                        "field_type": "text",
                        "description": "Contact email address",
                        "required": False,
                    },
                ],
            },
        ],
    },
    "entity_types": [
        {"name": "entity", "description": "Generic named entity"},
        {"name": "fact", "description": "Factual statement"},
    ],
    "prompt_templates": {},
    "is_template": True,
}
```

Update `__all__` to include it:
```python
__all__ = [
    "COMPANY_ANALYSIS_TEMPLATE",
    "RESEARCH_SURVEY_TEMPLATE",
    "CONTRACT_REVIEW_TEMPLATE",
    "BOOK_CATALOG_TEMPLATE",
    "DRIVETRAIN_COMPANY_TEMPLATE",
    "DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE",
    "DEFAULT_EXTRACTION_TEMPLATE",
]
```

**Tests:** `tests/test_default_template.py`
- `test_default_template_exists` - import succeeds
- `test_default_template_has_3_field_groups` - exactly 3 groups
- `test_default_template_passes_validation` - use SchemaAdapter to validate
- `test_default_template_field_types_valid` - all field_type values are valid

---

#### Task 1.3: Make extraction_schema Optional in Models

**File:** `src/models.py` (MODIFY)

Change line 297 in `ProjectCreate` class from:
```python
extraction_schema: dict = Field(..., description="JSONB extraction schema")
```

To:
```python
extraction_schema: dict | None = Field(
    default=None,
    description="JSONB extraction schema. If omitted, uses default template.",
)

@field_validator("extraction_schema", mode="before")
@classmethod
def apply_default_schema(cls, v):
    """Apply default extraction schema if not provided or empty."""
    if v is None or v == {}:
        from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE
        return DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]
    return v
```

**Tests:** `tests/test_models.py` (extend existing or create)
- `test_project_create_without_schema_gets_default` - None → default schema
- `test_project_create_with_empty_dict_gets_default` - {} → default schema
- `test_project_create_with_schema_keeps_it` - custom schema preserved
- `test_default_schema_has_expected_name` - schema name is "generic_facts"

---

### Phase 2: Pipeline Integration

#### Task 2.1: Update SchemaExtractionOrchestrator

**File:** `src/services/extraction/schema_orchestrator.py` (MODIFY)

**Change 1:** Remove line 9:
```python
# DELETE THIS LINE:
from services.extraction.field_groups import ALL_FIELD_GROUPS, FieldGroup
# REPLACE WITH:
from services.extraction.field_groups import FieldGroup
```

**Change 2:** Update `extract_all_groups` method signature and add deprecation warning (around line 22-40):

```python
async def extract_all_groups(
    self,
    source_id: UUID,
    markdown: str,
    company_name: str,
    field_groups: list[FieldGroup],  # NOW REQUIRED (was Optional)
) -> list[dict]:
    """Extract all field groups from source content.

    Args:
        source_id: Source UUID for tracking.
        markdown: Markdown content.
        company_name: Company name for context.
        field_groups: Field groups to extract (REQUIRED).

    Returns:
        List of extraction results, one per field group.
    """
    if not field_groups:
        logger.error(
            "extract_all_groups_no_field_groups",
            source_id=str(source_id),
            message="field_groups parameter is required but was empty",
        )
        return []

    groups = field_groups  # Remove the `or ALL_FIELD_GROUPS` fallback
    # ... rest of method unchanged
```

**Tests:** `tests/test_schema_orchestrator.py` (extend if exists)
- `test_extract_all_groups_requires_field_groups` - returns empty list if field_groups is empty
- `test_extract_all_groups_logs_error_if_no_groups` - verify error logged

---

#### Task 2.2: Update SchemaExtractionPipeline

**File:** `src/services/extraction/pipeline.py` (MODIFY)

**Change 1:** Add import at top (around line 31):
```python
from services.extraction.schema_adapter import SchemaAdapter
from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE
```

**Change 2:** Update `extract_source` method (around line 364-407):

```python
async def extract_source(
    self,
    source,  # Source ORM object
    company_name: str,
    field_groups: list | None = None,  # NEW PARAMETER
) -> list:  # list[Extraction]
    """Extract all field groups from a source.

    Args:
        source: Source ORM object with markdown content.
        company_name: Company name (source_group).
        field_groups: Pre-converted FieldGroup objects (optional, loaded from project if not provided).

    Returns:
        List of created Extraction objects.
    """
    from orm_models import Extraction

    if not source.content:
        logger.warning("source_has_no_content", source_id=str(source.id))
        return []

    # Use provided field_groups or require caller to provide them
    if not field_groups:
        logger.error(
            "extract_source_no_field_groups",
            source_id=str(source.id),
            message="field_groups must be provided",
        )
        return []

    # Run extraction for all field groups
    results = await self._orchestrator.extract_all_groups(
        source_id=source.id,
        markdown=source.content,
        company_name=company_name,
        field_groups=field_groups,  # Pass explicitly
    )
    # ... rest unchanged
```

**Change 3:** Update `extract_project` method (around line 409-488):

```python
async def extract_project(
    self,
    project_id: UUID,
    source_groups: list[str] | None = None,
    skip_extracted: bool = True,
) -> dict:
    """Extract all sources in a project.

    Args:
        project_id: Project UUID.
        source_groups: Optional filter by company names.
        skip_extracted: If True, skip sources with 'extracted' status.

    Returns:
        Summary dict with extraction counts.
    """
    from orm_models import Project, Source

    # Load project to get extraction_schema
    project = self._db.query(Project).filter(Project.id == project_id).first()
    if not project:
        logger.error("project_not_found", project_id=str(project_id))
        return {"error": "Project not found", "project_id": str(project_id)}

    # Convert project schema to field groups
    adapter = SchemaAdapter()
    schema = project.extraction_schema

    # Fallback to default if schema is missing or invalid
    if not schema:
        logger.warning(
            "project_missing_schema_using_default",
            project_id=str(project_id),
        )
        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

    validation = adapter.validate_extraction_schema(schema)
    if not validation.is_valid:
        logger.error(
            "invalid_extraction_schema_using_default",
            project_id=str(project_id),
            errors=validation.errors,
        )
        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

    field_groups = adapter.convert_to_field_groups(schema)

    logger.info(
        "using_project_schema",
        project_id=str(project_id),
        schema_name=schema.get("name", "unknown"),
        field_groups_count=len(field_groups),
    )

    # Build list of allowed statuses based on skip_extracted flag
    allowed_statuses = ["ready", "pending"]
    if not skip_extracted:
        allowed_statuses.append("extracted")

    # Include sources that are ready (and optionally extracted)
    query = self._db.query(Source).filter(
        Source.project_id == project_id,
        Source.status.in_(allowed_statuses),
        Source.content.isnot(None),
    )

    if source_groups:
        query = query.filter(Source.source_group.in_(source_groups))

    sources = query.all()

    logger.info(
        "project_extraction_started",
        project_id=str(project_id),
        source_count=len(sources),
        field_groups_count=len(field_groups),
    )

    # Process sources in parallel
    semaphore = asyncio.Semaphore(10)

    async def extract_with_limit(source) -> int:
        async with semaphore:
            extractions = await self.extract_source(
                source=source,
                company_name=source.source_group,
                field_groups=field_groups,  # Pass converted groups
            )
            return len(extractions)

    extraction_counts = await asyncio.gather(
        *[extract_with_limit(s) for s in sources],
        return_exceptions=True,
    )

    total_extractions = sum(
        c for c in extraction_counts if isinstance(c, int)
    )

    for i, result in enumerate(extraction_counts):
        if isinstance(result, Exception):
            logger.error(
                "schema_extraction_failed",
                source_id=str(sources[i].id),
                error=str(result),
            )

    self._db.commit()

    return {
        "project_id": str(project_id),
        "sources_processed": len(sources),
        "extractions_created": total_extractions,
        "field_groups": len(field_groups),
        "schema_name": schema.get("name", "unknown"),
    }
```

**Tests:** `tests/test_extraction_pipeline.py` (extend or create)
- `test_extract_project_loads_project_schema` - verify schema loaded from project
- `test_extract_project_uses_default_on_missing_schema` - fallback works
- `test_extract_project_uses_default_on_invalid_schema` - fallback on validation error
- `test_extract_project_logs_schema_name` - verify "using_project_schema" logged

---

#### Task 2.3: Update Extraction API Logging

**File:** `src/api/v1/extraction.py` (MODIFY)

In `extract_schema` function (around line 241-304), add logging for schema info.

After loading project (around line 277), add:
```python
# Log which schema will be used
schema_name = (
    project.extraction_schema.get("name", "unknown")
    if project.extraction_schema
    else "default"
)
logger.info(
    "extract_schema_endpoint_called",
    project_id=project_id,
    schema_name=schema_name,
    source_groups=source_groups,
)
```

No functional changes required, just logging.

---

#### Task 2.4: Update Project API to Register Default Template

**File:** `src/api/v1/projects.py` (MODIFY)

**Change 1:** Add import (around line 14):
```python
from services.projects.templates import (
    COMPANY_ANALYSIS_TEMPLATE,
    RESEARCH_SURVEY_TEMPLATE,
    CONTRACT_REVIEW_TEMPLATE,
    BOOK_CATALOG_TEMPLATE,
    DEFAULT_EXTRACTION_TEMPLATE,  # ADD THIS
)
```

**Change 2:** Update TEMPLATES dict (around line 21-26):
```python
TEMPLATES = {
    "company_analysis": COMPANY_ANALYSIS_TEMPLATE,
    "research_survey": RESEARCH_SURVEY_TEMPLATE,
    "contract_review": CONTRACT_REVIEW_TEMPLATE,
    "book_catalog": BOOK_CATALOG_TEMPLATE,
    "default": DEFAULT_EXTRACTION_TEMPLATE,  # ADD THIS
}
```

**Change 3:** In `create_project` function (around line 31-60), add logging when default is applied:

After line 38 (`repo = ProjectRepository(db)`), add:
```python
import structlog
logger = structlog.get_logger(__name__)

# Log if default template will be applied
if not project.extraction_schema or project.extraction_schema == {}:
    logger.info(
        "default_template_assigned",
        project_name=project.name,
    )
```

Note: The model validator in `models.py` already applies the default, so by the time we reach the API, `project.extraction_schema` will have the default value. To log this properly, check the **original request** before validation. Update:

```python
@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Create a new extraction project."""
    import structlog
    logger = structlog.get_logger(__name__)

    repo = ProjectRepository(db)

    # Check name uniqueness
    existing = await repo.get_by_name(project.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with name '{project.name}' already exists",
        )

    # Log if default template was applied (check schema name)
    if project.extraction_schema and project.extraction_schema.get("name") == "generic_facts":
        logger.info(
            "default_template_assigned",
            project_name=project.name,
        )

    # Create project
    # ... rest unchanged
```

---

### Phase 3: Integration Tests

#### Task 3.1: Create Integration Test

**File:** `tests/integration/test_template_extraction.py` (NEW)

```python
"""Integration tests for template-driven extraction."""

import pytest
from uuid import uuid4

from services.extraction.schema_adapter import SchemaAdapter, ValidationResult
from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE


class TestSchemaAdapterIntegration:
    """Test SchemaAdapter with real templates."""

    def test_default_template_converts_to_field_groups(self):
        """Default template should convert to 3 field groups."""
        adapter = SchemaAdapter()
        schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

        # Validate first
        result = adapter.validate_extraction_schema(schema)
        assert result.is_valid, f"Validation errors: {result.errors}"

        # Convert
        field_groups = adapter.convert_to_field_groups(schema)
        assert len(field_groups) == 3

        # Check group names
        group_names = [g.name for g in field_groups]
        assert "entity_info" in group_names
        assert "key_facts" in group_names
        assert "contact_info" in group_names

    def test_custom_schema_round_trip(self):
        """Custom schema should validate and convert correctly."""
        adapter = SchemaAdapter()
        custom_schema = {
            "name": "test_schema",
            "field_groups": [
                {
                    "name": "test_group",
                    "description": "Test field group",
                    "fields": [
                        {
                            "name": "test_field",
                            "field_type": "text",
                            "description": "A test field",
                            "required": True,
                            "default": "",
                        },
                    ],
                },
            ],
        }

        result = adapter.validate_extraction_schema(custom_schema)
        assert result.is_valid

        field_groups = adapter.convert_to_field_groups(custom_schema)
        assert len(field_groups) == 1
        assert field_groups[0].name == "test_group"
        assert len(field_groups[0].fields) == 1
        assert field_groups[0].fields[0].name == "test_field"


class TestProjectCreationWithDefault:
    """Test project creation with default schema."""

    def test_project_create_model_applies_default(self):
        """ProjectCreate should apply default schema when None."""
        from models import ProjectCreate

        # Create without schema
        project = ProjectCreate(name="test_project")

        # Should have default schema
        assert project.extraction_schema is not None
        assert project.extraction_schema.get("name") == "generic_facts"
        assert len(project.extraction_schema.get("field_groups", [])) == 3
```

---

## Constraints

- Do NOT delete `src/services/extraction/field_groups.py` - it's still needed for FieldGroup/FieldDefinition dataclasses
- Do NOT modify `src/services/extraction/schema_extractor.py` - it continues using FieldGroup as-is
- Do NOT modify `src/orm_models.py` - extraction_schema field already exists as JSONB
- Do NOT run `pytest` without arguments - only run scoped tests
- Do NOT run `ruff check src/` - only lint modified/created files
- Keep backward compatibility: existing projects with hardcoded extractions should continue to work

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
# Unit tests
pytest tests/test_schema_adapter.py -v
pytest tests/test_default_template.py -v
pytest tests/test_models.py -v -k "project_create"

# Integration tests
pytest tests/integration/test_template_extraction.py -v

# Existing tests to ensure no regression
pytest tests/test_schema.py -v
pytest tests/test_extraction.py -v -k "schema" --ignore=tests/integration/
```

## Lint Scope

**ONLY lint these files:**

```bash
ruff check src/services/extraction/schema_adapter.py \
           src/services/extraction/schema_orchestrator.py \
           src/services/extraction/pipeline.py \
           src/services/projects/templates.py \
           src/models.py \
           src/api/v1/extraction.py \
           src/api/v1/projects.py \
           tests/test_schema_adapter.py \
           tests/test_default_template.py \
           tests/integration/test_template_extraction.py
```

## Verification

Before creating PR, run:

1. `pytest tests/test_schema_adapter.py -v` - All adapter tests pass
2. `pytest tests/test_default_template.py -v` - Template tests pass
3. `pytest tests/test_models.py -v -k "project_create"` - Model tests pass
4. `pytest tests/integration/test_template_extraction.py -v` - Integration tests pass
5. `ruff check {files listed above}` - No lint errors

## Definition of Done

- [ ] `SchemaAdapter` class created with validate/convert/generate methods
- [ ] All 11 validation rules implemented and tested
- [ ] `DEFAULT_EXTRACTION_TEMPLATE` added to templates.py
- [ ] `extraction_schema` made optional in `ProjectCreate` with validator
- [ ] `SchemaExtractionOrchestrator` no longer imports `ALL_FIELD_GROUPS`
- [ ] `SchemaExtractionPipeline.extract_project` loads schema from project
- [ ] Logging added: "using_project_schema", "default_template_assigned"
- [ ] All scoped tests pass
- [ ] Lint clean on modified files
- [ ] PR created with title: `feat: Template-driven extraction system`

## PR Description Template

```markdown
## Summary

- Add SchemaAdapter to convert project.extraction_schema (JSONB) to FieldGroup objects
- Add DEFAULT_EXTRACTION_TEMPLATE with 3 generic field groups
- Make extraction_schema optional in ProjectCreate (auto-assigns default)
- Update extraction pipeline to load and use project's schema instead of hardcoded groups

## Test plan

- [x] Unit tests for SchemaAdapter (11 validation rules)
- [x] Unit tests for default template
- [x] Unit tests for ProjectCreate model
- [x] Integration tests for template extraction
- [x] Existing schema tests pass (no regression)

Closes: Template-driven extraction implementation
```
