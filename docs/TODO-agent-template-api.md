# TODO: Add Template Details API Endpoint

**Agent:** agent-template-api
**Branch:** `feat/template-details-api`
**Priority:** High

## Context

The template system was refactored to use YAML files with a `TemplateRegistry` that provides:
- `get_template(name)` - Returns full template dict
- `list_template_names()` - Returns list of names
- `get_all_templates()` - Returns all templates

Currently the API only exposes `GET /api/v1/projects/templates` which returns template names (list of strings). There's no way to get template details (description, field groups, entity types) via the API.

This is needed for the MCP server to help AI assistants choose the right template.

## Objective

Add two new API endpoints to expose template details:
1. `GET /api/v1/projects/templates/{name}` - Get single template details
2. `GET /api/v1/projects/templates` - Enhanced to optionally return full details

## Tasks

### Task 1: Add Pydantic Models for Template Response

**File:** `src/models.py` (MODIFY)

Add after `ProjectFromTemplate` class (around line 355):

```python
class FieldDefinitionResponse(BaseModel):
    """Field definition within a field group."""

    name: str = Field(..., description="Field name")
    field_type: str = Field(..., description="Field type: text, integer, float, boolean, enum, list")
    description: str | None = Field(None, description="Field description")
    required: bool = Field(default=False, description="Whether field is required")
    default: Any | None = Field(None, description="Default value")
    enum_values: list[str] | None = Field(None, description="Allowed values for enum type")


class FieldGroupResponse(BaseModel):
    """Field group within extraction schema."""

    name: str = Field(..., description="Field group name")
    description: str | None = Field(None, description="Field group description")
    is_entity_list: bool = Field(default=False, description="Whether this extracts a list of entities")
    fields: list[FieldDefinitionResponse] = Field(..., description="Fields in this group")


class EntityTypeResponse(BaseModel):
    """Entity type definition."""

    name: str = Field(..., description="Entity type name")
    description: str | None = Field(None, description="Entity type description")


class TemplateResponse(BaseModel):
    """Full template details response."""

    name: str = Field(..., description="Template name (use with create_from_template)")
    description: str = Field(..., description="Template description")
    field_groups: list[FieldGroupResponse] = Field(..., description="What this template extracts")
    entity_types: list[EntityTypeResponse] = Field(..., description="Entity types created")

    @classmethod
    def from_template(cls, template: dict) -> "TemplateResponse":
        """Create response from template dict."""
        schema = template.get("extraction_schema", {})
        field_groups = []

        for fg in schema.get("field_groups", []):
            fields = [
                FieldDefinitionResponse(
                    name=f["name"],
                    field_type=f.get("field_type", "text"),
                    description=f.get("description"),
                    required=f.get("required", False),
                    default=f.get("default"),
                    enum_values=f.get("enum_values"),
                )
                for f in fg.get("fields", [])
            ]
            field_groups.append(FieldGroupResponse(
                name=fg["name"],
                description=fg.get("description"),
                is_entity_list=fg.get("is_entity_list", False),
                fields=fields,
            ))

        entity_types = [
            EntityTypeResponse(
                name=et["name"],
                description=et.get("description"),
            )
            for et in template.get("entity_types", [])
        ]

        return cls(
            name=template["name"],
            description=template.get("description", ""),
            field_groups=field_groups,
            entity_types=entity_types,
        )


class TemplateListResponse(BaseModel):
    """Response for template list with optional details."""

    templates: list[TemplateResponse] = Field(..., description="List of templates")
    count: int = Field(..., description="Number of templates")
```

---

### Task 2: Add Template Detail Endpoints

**File:** `src/api/v1/projects.py` (MODIFY)

**Change 1:** Update imports (line 11):
```python
from models import (
    ProjectCreate,
    ProjectFromTemplate,
    ProjectResponse,
    ProjectUpdate,
    TemplateResponse,
    TemplateListResponse,
)
```

**Change 2:** Add import for get_all_templates (line 14):
```python
from services.projects.template_loader import get_template, get_all_templates, list_template_names
```

**Change 3:** Update the list_templates endpoint (around line 70-73):

Replace:
```python
@router.get("/templates", response_model=list[str])
async def list_templates() -> list[str]:
    """List available project templates."""
    return list_template_names()
```

With:
```python
@router.get("/templates")
async def list_templates(
    details: bool = False,
) -> list[str] | TemplateListResponse:
    """List available project templates.

    Args:
        details: If True, return full template details. If False, return names only.

    Returns:
        List of template names (default) or TemplateListResponse with full details.
    """
    if not details:
        return list_template_names()

    all_templates = get_all_templates()
    templates = [
        TemplateResponse.from_template(t)
        for t in all_templates.values()
    ]
    return TemplateListResponse(templates=templates, count=len(templates))
```

**Change 4:** Add new endpoint for single template details (after list_templates, around line 90):

```python
@router.get("/templates/{template_name}", response_model=TemplateResponse)
async def get_template_details(template_name: str) -> TemplateResponse:
    """Get detailed information about a specific template.

    Args:
        template_name: Name of the template (e.g., 'company_analysis', 'default')

    Returns:
        Full template details including field groups and entity types.

    Raises:
        HTTPException: 404 if template not found.
    """
    template = get_template(template_name)
    if template is None:
        available = list_template_names()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{template_name}' not found. Available: {available}",
        )

    return TemplateResponse.from_template(template)
```

---

### Task 3: Add Tests

**File:** `tests/test_template_api.py` (NEW)

```python
"""Tests for template API endpoints."""

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


class TestListTemplates:
    """Tests for GET /api/v1/projects/templates."""

    def test_list_templates_names_only(self):
        """Default returns list of template names."""
        response = client.get("/api/v1/projects/templates")
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert "default" in data
        assert "company_analysis" in data

    def test_list_templates_with_details(self):
        """With details=true returns full template info."""
        response = client.get("/api/v1/projects/templates?details=true")
        assert response.status_code == 200

        data = response.json()
        assert "templates" in data
        assert "count" in data
        assert data["count"] > 0

        # Check structure of first template
        template = data["templates"][0]
        assert "name" in template
        assert "description" in template
        assert "field_groups" in template
        assert "entity_types" in template


class TestGetTemplateDetails:
    """Tests for GET /api/v1/projects/templates/{name}."""

    def test_get_template_details_success(self):
        """Returns full details for valid template."""
        response = client.get("/api/v1/projects/templates/company_analysis")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "company_analysis"
        assert "description" in data
        assert len(data["field_groups"]) > 0
        assert len(data["entity_types"]) > 0

        # Check field group structure
        fg = data["field_groups"][0]
        assert "name" in fg
        assert "fields" in fg
        assert len(fg["fields"]) > 0

        # Check field structure
        field = fg["fields"][0]
        assert "name" in field
        assert "field_type" in field

    def test_get_template_details_default(self):
        """Returns details for default template."""
        response = client.get("/api/v1/projects/templates/default")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "default"

    def test_get_template_details_not_found(self):
        """Returns 404 for unknown template."""
        response = client.get("/api/v1/projects/templates/nonexistent")
        assert response.status_code == 404

        data = response.json()
        assert "not found" in data["detail"].lower()
        assert "Available" in data["detail"]

    def test_get_template_details_drivetrain(self):
        """Returns details for drivetrain template with multiple field groups."""
        response = client.get("/api/v1/projects/templates/drivetrain_company_analysis")
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "drivetrain_company_analysis"
        # Drivetrain has many field groups
        assert len(data["field_groups"]) >= 5
```

---

## Constraints

- Do NOT modify template_loader.py - it's already complete
- Do NOT modify YAML template files
- Do NOT change existing endpoint behavior (list_templates without params still returns list[str])
- Keep backward compatibility

## Test Scope

```bash
pytest tests/test_template_api.py -v
```

## Lint Scope

```bash
ruff check src/api/v1/projects.py src/models.py tests/test_template_api.py
```

## Verification

1. `curl http://localhost:8000/api/v1/projects/templates` - Returns list of strings
2. `curl http://localhost:8000/api/v1/projects/templates?details=true` - Returns full details
3. `curl http://localhost:8000/api/v1/projects/templates/company_analysis` - Returns single template
4. `curl http://localhost:8000/api/v1/projects/templates/invalid` - Returns 404

## Definition of Done

- [ ] Pydantic models added (TemplateResponse, FieldGroupResponse, etc.)
- [ ] `GET /templates` enhanced with `details` query param
- [ ] `GET /templates/{name}` endpoint added
- [ ] All tests pass
- [ ] Lint clean
- [ ] PR created with title: `feat: Add template details API endpoints`
