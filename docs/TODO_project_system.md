# TODO: Project System

## Overview

Implements the project abstraction layer that enables the pipeline to support multiple extraction domains with custom schemas.

**Status:** Repository layer complete (40 tests passing)

**Completed:**
- [x] **ProjectRepository** - Full implementation (9 methods, 19 tests)
- [x] **SchemaValidator** - Dynamic Pydantic model generation (21 tests)
- [x] **Project ORM model** - With relationships to sources, extractions, entities
- [x] **COMPANY_ANALYSIS_TEMPLATE** - Default project template

**Pending:**
- [ ] Project CRUD API endpoints
- [ ] Additional templates (research_survey, contract_review)
- [ ] Seed script for default project
- [ ] Clone project from template functionality

**Related Documentation:**
- See `docs/TODO_generalization.md` for overall architecture
- See `docs/TODO_storage.md` for repository patterns

---

## Core Concept

A **Project** encapsulates all configuration for a specific extraction use case:
- What sources to accept (web, PDF, etc.)
- How to group sources (by company, paper, contract)
- What fields to extract (custom schema)
- What entities to recognize
- How to prompt the LLM

---

## Database Schema

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,

    -- Source configuration
    source_config JSONB NOT NULL DEFAULT '{
        "type": "web",
        "group_by": "company"
    }',

    -- Extraction schema (validated at app layer)
    extraction_schema JSONB NOT NULL,

    -- Entity types to recognize
    entity_types JSONB NOT NULL DEFAULT '[]',

    -- Custom prompt templates (optional overrides)
    prompt_templates JSONB NOT NULL DEFAULT '{}',

    -- Settings
    is_template BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_projects_name ON projects(name);
CREATE INDEX idx_projects_active ON projects(is_active);
```

---

## ORM Model

```python
# src/orm_models.py (addition)
from sqlalchemy import Column, String, Boolean, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.sql import func
import uuid

class Project(Base):
    __tablename__ = "projects"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(String, unique=True, nullable=False)
    description = Column(String, nullable=True)

    source_config = Column(JSONB, nullable=False, default={"type": "web", "group_by": "company"})
    extraction_schema = Column(JSONB, nullable=False)
    entity_types = Column(JSONB, nullable=False, default=[])
    prompt_templates = Column(JSONB, nullable=False, default={})

    is_template = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Relationships
    sources = relationship("Source", back_populates="project", cascade="all, delete-orphan")
    extractions = relationship("Extraction", back_populates="project", cascade="all, delete-orphan")
```

---

## Pydantic Models

```python
# src/models/project.py
from pydantic import BaseModel, Field
from uuid import UUID
from datetime import datetime

class FieldDefinition(BaseModel):
    """Definition of a field in the extraction schema."""
    name: str
    type: str  # text, integer, float, boolean, enum, json, list
    required: bool = False
    description: str | None = None
    default: Any = None
    # For enum type
    values: list[str] | None = None
    # For numeric types
    min: float | None = None
    max: float | None = None


class ExtractionSchema(BaseModel):
    """Schema defining what to extract."""
    name: str  # e.g., "technical_fact", "research_finding"
    fields: list[FieldDefinition]


class EntityTypeDefinition(BaseModel):
    """Definition of an entity type to recognize."""
    name: str
    description: str | None = None
    attributes: list[dict] = []  # [{name, type, ...}]


class SourceConfig(BaseModel):
    """Configuration for source handling."""
    type: str = "web"  # web, pdf, api, text
    group_by: str = "company"  # How to group sources


class ProjectCreate(BaseModel):
    """Request to create a new project."""
    name: str
    description: str | None = None
    source_config: SourceConfig = SourceConfig()
    extraction_schema: ExtractionSchema
    entity_types: list[EntityTypeDefinition] = []
    is_template: bool = False


class ProjectResponse(BaseModel):
    """Project response model."""
    id: UUID
    name: str
    description: str | None
    source_config: dict
    extraction_schema: dict
    entity_types: list
    is_template: bool
    is_active: bool
    created_at: datetime
    updated_at: datetime


class ProjectFromTemplate(BaseModel):
    """Create project from template."""
    template: str  # Template name to clone from
    name: str
    description: str | None = None
    customizations: dict = {}  # Override specific fields
```

---

## Project Repository

```python
# src/services/projects/repository.py
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from uuid import UUID

class ProjectRepository:
    def __init__(self, session: AsyncSession):
        self._session = session

    async def create(self, project: ProjectCreate) -> Project:
        """Create a new project."""
        db_project = Project(
            name=project.name,
            description=project.description,
            source_config=project.source_config.model_dump(),
            extraction_schema=project.extraction_schema.model_dump(),
            entity_types=[et.model_dump() for et in project.entity_types],
            is_template=project.is_template,
        )
        self._session.add(db_project)
        await self._session.flush()
        return db_project

    async def get(self, project_id: UUID) -> Project | None:
        """Get project by ID."""
        result = await self._session.execute(
            select(Project).where(Project.id == project_id)
        )
        return result.scalar_one_or_none()

    async def get_by_name(self, name: str) -> Project | None:
        """Get project by name."""
        result = await self._session.execute(
            select(Project).where(Project.name == name)
        )
        return result.scalar_one_or_none()

    async def list_all(self, include_inactive: bool = False) -> list[Project]:
        """List all projects."""
        query = select(Project)
        if not include_inactive:
            query = query.where(Project.is_active == True)
        result = await self._session.execute(query.order_by(Project.name))
        return list(result.scalars().all())

    async def list_templates(self) -> list[Project]:
        """List template projects."""
        result = await self._session.execute(
            select(Project)
            .where(Project.is_template == True)
            .where(Project.is_active == True)
            .order_by(Project.name)
        )
        return list(result.scalars().all())

    async def update(self, project_id: UUID, updates: dict) -> Project | None:
        """Update project fields."""
        project = await self.get(project_id)
        if not project:
            return None
        for key, value in updates.items():
            if hasattr(project, key):
                setattr(project, key, value)
        await self._session.flush()
        return project

    async def delete(self, project_id: UUID) -> bool:
        """Soft delete by setting is_active = False."""
        project = await self.get(project_id)
        if not project:
            return False
        project.is_active = False
        await self._session.flush()
        return True

    async def get_default_project(self) -> Project:
        """Get or create the default company_analysis project."""
        project = await self.get_by_name("company_analysis")
        if project:
            return project

        # Create default project
        from .templates import COMPANY_ANALYSIS_TEMPLATE
        return await self.create(COMPANY_ANALYSIS_TEMPLATE)
```

---

## Project Templates

```python
# src/services/projects/templates.py
from ..models.project import ProjectCreate, ExtractionSchema, FieldDefinition, EntityTypeDefinition, SourceConfig

COMPANY_ANALYSIS_TEMPLATE = ProjectCreate(
    name="company_analysis",
    description="Extract technical facts from company documentation",
    is_template=True,
    source_config=SourceConfig(type="web", group_by="company"),
    extraction_schema=ExtractionSchema(
        name="technical_fact",
        fields=[
            FieldDefinition(name="fact_text", type="text", required=True,
                          description="The extracted factual statement"),
            FieldDefinition(name="category", type="enum", required=True,
                          values=["specs", "api", "security", "pricing", "features", "integration"]),
            FieldDefinition(name="confidence", type="float", min=0.0, max=1.0, default=0.8),
            FieldDefinition(name="source_quote", type="text", required=False,
                          description="Brief quote from source"),
        ],
    ),
    entity_types=[
        EntityTypeDefinition(name="plan", description="Pricing tier or plan"),
        EntityTypeDefinition(name="feature", description="Product capability"),
        EntityTypeDefinition(name="limit", description="Quota or threshold",
                           attributes=[{"name": "numeric_value", "type": "number"},
                                      {"name": "unit", "type": "text"}]),
        EntityTypeDefinition(name="certification", description="Security certification"),
        EntityTypeDefinition(name="pricing", description="Cost or price point"),
    ],
)

RESEARCH_SURVEY_TEMPLATE = ProjectCreate(
    name="research_survey",
    description="Extract findings from academic papers",
    is_template=True,
    source_config=SourceConfig(type="pdf", group_by="paper"),
    extraction_schema=ExtractionSchema(
        name="research_finding",
        fields=[
            FieldDefinition(name="finding", type="text", required=True),
            FieldDefinition(name="finding_type", type="enum",
                          values=["result", "claim", "limitation", "future_work"]),
            FieldDefinition(name="methodology", type="text"),
            FieldDefinition(name="confidence", type="float", min=0.0, max=1.0, default=0.8),
        ],
    ),
    entity_types=[
        EntityTypeDefinition(name="model", description="ML model or architecture"),
        EntityTypeDefinition(name="dataset", description="Training or evaluation dataset"),
        EntityTypeDefinition(name="metric", description="Evaluation metric"),
    ],
)

CONTRACT_REVIEW_TEMPLATE = ProjectCreate(
    name="contract_review",
    description="Extract clauses and risks from legal contracts",
    is_template=True,
    source_config=SourceConfig(type="pdf", group_by="contract"),
    extraction_schema=ExtractionSchema(
        name="clause",
        fields=[
            FieldDefinition(name="clause_text", type="text", required=True),
            FieldDefinition(name="clause_type", type="enum",
                          values=["liability", "termination", "payment", "confidentiality",
                                 "ip", "indemnity", "warranty"]),
            FieldDefinition(name="risk_level", type="enum",
                          values=["low", "medium", "high", "critical"]),
            FieldDefinition(name="negotiable", type="boolean", default=True),
        ],
    ),
    entity_types=[
        EntityTypeDefinition(name="party", description="Contract party"),
        EntityTypeDefinition(name="monetary_amount", description="Dollar amount"),
        EntityTypeDefinition(name="date", description="Important date or deadline"),
    ],
)

# Registry of all templates
PROJECT_TEMPLATES = {
    "company_analysis": COMPANY_ANALYSIS_TEMPLATE,
    "research_survey": RESEARCH_SURVEY_TEMPLATE,
    "contract_review": CONTRACT_REVIEW_TEMPLATE,
}
```

---

## Schema Validator

```python
# src/services/projects/schema.py
from pydantic import BaseModel, ValidationError, create_model, Field
from typing import Any
import structlog

logger = structlog.get_logger(__name__)


class SchemaValidator:
    """Validates extraction data against project schema."""

    def __init__(self, extraction_schema: dict):
        self.schema = extraction_schema
        self._model = self._build_pydantic_model()

    def _build_pydantic_model(self) -> type[BaseModel]:
        """Dynamically create Pydantic model from schema."""
        fields = {}

        for field_def in self.schema.get("fields", []):
            field_type = self._map_type(field_def["type"])
            is_required = field_def.get("required", False)
            default = field_def.get("default")

            if is_required:
                fields[field_def["name"]] = (field_type, ...)
            elif default is not None:
                fields[field_def["name"]] = (field_type, default)
            else:
                fields[field_def["name"]] = (field_type | None, None)

        return create_model("DynamicExtraction", **fields)

    def _map_type(self, type_str: str) -> type:
        """Map schema type to Python type."""
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
        """Validate data against schema. Returns (is_valid, errors)."""
        try:
            self._model(**data)
            # Additional enum validation
            errors = self._validate_enums(data)
            if errors:
                return False, errors
            return True, []
        except ValidationError as e:
            return False, [f"{err['loc'][0]}: {err['msg']}" for err in e.errors()]

    def _validate_enums(self, data: dict) -> list[str]:
        """Validate enum values."""
        errors = []
        for field_def in self.schema.get("fields", []):
            if field_def["type"] == "enum" and field_def["name"] in data:
                value = data[field_def["name"]]
                allowed = field_def.get("values", [])
                if value not in allowed:
                    errors.append(f"{field_def['name']}: must be one of {allowed}")
        return errors

    def get_field_names(self) -> list[str]:
        """Get list of field names from schema."""
        return [f["name"] for f in self.schema.get("fields", [])]

    def get_required_fields(self) -> list[str]:
        """Get list of required field names."""
        return [f["name"] for f in self.schema.get("fields", []) if f.get("required")]
```

---

## API Endpoints

```python
# src/api/v1/projects.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from ...database import get_db
from ...models.project import ProjectCreate, ProjectResponse, ProjectFromTemplate
from ...services.projects.repository import ProjectRepository
from ...services.projects.templates import PROJECT_TEMPLATES

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project: ProjectCreate,
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """Create a new extraction project."""
    repo = ProjectRepository(db)

    # Check name uniqueness
    existing = await repo.get_by_name(project.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with name '{project.name}' already exists",
        )

    db_project = await repo.create(project)
    await db.commit()
    return ProjectResponse.model_validate(db_project)


@router.post("/from-template", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_from_template(
    request: ProjectFromTemplate,
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """Create a new project from a template."""
    if request.template not in PROJECT_TEMPLATES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{request.template}' not found. Available: {list(PROJECT_TEMPLATES.keys())}",
        )

    template = PROJECT_TEMPLATES[request.template]
    repo = ProjectRepository(db)

    # Check name uniqueness
    existing = await repo.get_by_name(request.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with name '{request.name}' already exists",
        )

    # Clone template with customizations
    project_data = template.model_dump()
    project_data["name"] = request.name
    project_data["description"] = request.description or template.description
    project_data["is_template"] = False

    # Apply customizations
    for key, value in request.customizations.items():
        if key in project_data:
            if isinstance(project_data[key], dict) and isinstance(value, dict):
                project_data[key].update(value)
            else:
                project_data[key] = value

    project = ProjectCreate(**project_data)
    db_project = await repo.create(project)
    await db.commit()
    return ProjectResponse.model_validate(db_project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    include_inactive: bool = False,
    db: AsyncSession = Depends(get_db),
) -> list[ProjectResponse]:
    """List all projects."""
    repo = ProjectRepository(db)
    projects = await repo.list_all(include_inactive=include_inactive)
    return [ProjectResponse.model_validate(p) for p in projects]


@router.get("/templates", response_model=list[str])
async def list_templates() -> list[str]:
    """List available project templates."""
    return list(PROJECT_TEMPLATES.keys())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> ProjectResponse:
    """Get project by ID."""
    repo = ProjectRepository(db)
    project = await repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return ProjectResponse.model_validate(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Soft delete a project."""
    repo = ProjectRepository(db)
    success = await repo.delete(project_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    await db.commit()
```

---

## Implementation Tasks

### Phase 1: Core Infrastructure
- [ ] Add Project ORM model to `orm_models.py`
- [ ] Create `projects` table in init.sql or migration
- [ ] Create Pydantic models for project DTOs
- [ ] Create ProjectRepository with CRUD operations

### Phase 2: Templates & Validation
- [ ] Create project templates (company_analysis, research_survey, contract_review)
- [ ] Implement SchemaValidator for dynamic validation
- [ ] Add template cloning functionality
- [ ] Create seed script for default project

### Phase 3: API Layer
- [ ] Create projects router with CRUD endpoints
- [ ] Add project endpoints to main app
- [ ] Add project ID to job payloads
- [ ] Create project-scoped endpoints pattern

### Phase 4: Integration
- [ ] Update scraper to use project context
- [ ] Update extraction to use project schema
- [ ] Update entity extraction to use project entity_types
- [ ] Add default project fallback for legacy endpoints

---

## File Structure

```
src/
├── services/
│   └── projects/
│       ├── __init__.py
│       ├── repository.py    # ProjectRepository
│       ├── templates.py     # PROJECT_TEMPLATES
│       └── schema.py        # SchemaValidator
├── models/
│   └── project.py           # Pydantic models
└── api/
    └── v1/
        └── projects.py      # API endpoints
```

---

## Testing Checklist

- [ ] Unit: Create project with valid schema
- [ ] Unit: Schema validation rejects invalid data
- [ ] Unit: Clone project from template
- [ ] Unit: Template customization works
- [ ] Integration: CRUD operations work
- [ ] Integration: Default project created on first access
- [ ] Integration: Project-scoped queries work
