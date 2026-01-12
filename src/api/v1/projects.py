"""Project CRUD API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status, Response
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from database import get_db
from models import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectFromTemplate
from orm_models import Extraction
from services.projects.repository import ProjectRepository
from services.projects.templates import (
    COMPANY_ANALYSIS_TEMPLATE,
    RESEARCH_SURVEY_TEMPLATE,
    CONTRACT_REVIEW_TEMPLATE,
    BOOK_CATALOG_TEMPLATE,
)

# Template registry for lookup
TEMPLATES = {
    "company_analysis": COMPANY_ANALYSIS_TEMPLATE,
    "research_survey": RESEARCH_SURVEY_TEMPLATE,
    "contract_review": CONTRACT_REVIEW_TEMPLATE,
    "book_catalog": BOOK_CATALOG_TEMPLATE,
}

router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
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

    # Create project
    db_project = await repo.create(
        name=project.name,
        description=project.description,
        source_config=project.source_config,
        extraction_schema=project.extraction_schema,
        entity_types=project.entity_types,
        prompt_templates=project.prompt_templates,
        is_template=project.is_template,
    )
    db.commit()
    db.refresh(db_project)

    return ProjectResponse.model_validate(db_project)


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    include_inactive: bool = False,
    db: Session = Depends(get_db),
) -> list[ProjectResponse]:
    """List all projects."""
    repo = ProjectRepository(db)
    projects = await repo.list_all(include_inactive=include_inactive)
    return [ProjectResponse.model_validate(p) for p in projects]


@router.get("/templates", response_model=list[str])
async def list_templates() -> list[str]:
    """List available project templates."""
    return list(TEMPLATES.keys())


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
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


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: UUID,
    project_update: ProjectUpdate,
    response: Response,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Update an existing project."""
    repo = ProjectRepository(db)

    # Check if project exists
    existing = await repo.get(project_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Count existing extractions for warning
    extraction_count = db.execute(
        select(func.count(Extraction.id)).where(Extraction.project_id == project_id)
    ).scalar()

    # Check if schema or entity_types are being updated
    schema_changed = project_update.extraction_schema is not None
    entities_changed = project_update.entity_types is not None

    if extraction_count > 0 and (schema_changed or entities_changed):
        response.headers["X-Extraction-Warning"] = (
            f"Project has {extraction_count} existing extractions. "
            "Schema changes may cause inconsistencies."
        )

    # Update project
    updates = project_update.model_dump(exclude_unset=True)
    updated_project = await repo.update(project_id, updates)
    db.commit()
    db.refresh(updated_project)

    return ProjectResponse.model_validate(updated_project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_project(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> None:
    """Soft delete a project."""
    repo = ProjectRepository(db)
    success = await repo.delete(project_id)
    if not success:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    db.commit()


@router.post(
    "/from-template",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_from_template(
    request: ProjectFromTemplate,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Create a new project from a template."""
    # Check if template exists
    if request.template not in TEMPLATES:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{request.template}' not found. Available: {list(TEMPLATES.keys())}",
        )

    repo = ProjectRepository(db)

    # Check name uniqueness
    existing = await repo.get_by_name(request.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with name '{request.name}' already exists",
        )

    # Clone from template
    template = TEMPLATES[request.template].copy()

    # Create project from template
    db_project = await repo.create(
        name=request.name,
        description=request.description or template["description"],
        source_config=template["source_config"],
        extraction_schema=template["extraction_schema"],
        entity_types=template["entity_types"],
        prompt_templates=template.get("prompt_templates", {}),
        is_template=False,
    )
    db.commit()
    db.refresh(db_project)

    return ProjectResponse.model_validate(db_project)
