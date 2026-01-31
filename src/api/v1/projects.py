"""Project CRUD API endpoints."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import get_db
from models import (
    ProjectCreate,
    ProjectFromTemplate,
    ProjectResponse,
    ProjectUpdate,
    TemplateListResponse,
    TemplateResponse,
)
from orm_models import Extraction
from services.projects.repository import ProjectRepository
from services.projects.template_loader import (
    get_all_templates,
    get_template,
    list_template_names,
)

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/projects", tags=["projects"])


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
async def create_project(
    project: ProjectCreate,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Create a new extraction project."""
    repo = ProjectRepository(db)

    # Check name uniqueness
    existing = repo.get_by_name(project.name)
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
    db_project = repo.create(
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
    projects = repo.list_all(include_inactive=include_inactive)
    return [ProjectResponse.model_validate(p) for p in projects]


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


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Get project by ID."""
    repo = ProjectRepository(db)
    project = repo.get(project_id)
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
    force: bool = Query(
        default=False,
        description="Allow schema/entity_types changes even with existing extractions. "
        "Required when modifying extraction_schema or entity_types on a project "
        "that already has extractions.",
    ),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    """Update an existing project.

    Args:
        project_id: UUID of the project to update.
        project_update: Fields to update.
        response: FastAPI response object for headers.
        force: If True, allow schema/entity_types changes even with existing extractions.
        db: Database session.

    Returns:
        Updated project.

    Raises:
        HTTPException: 404 if project not found, 409 if schema change blocked.
    """
    repo = ProjectRepository(db)

    # Check if project exists
    existing = repo.get(project_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Check if schema or entity_types are being updated
    schema_changed = project_update.extraction_schema is not None
    entities_changed = project_update.entity_types is not None

    # Block schema changes when extractions exist (unless force=True)
    if schema_changed or entities_changed:
        extraction_count = db.execute(
            select(func.count(Extraction.id)).where(Extraction.project_id == project_id)
        ).scalar()

        if extraction_count > 0 and not force:
            changed_fields = []
            if schema_changed:
                changed_fields.append("extraction_schema")
            if entities_changed:
                changed_fields.append("entity_types")

            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Schema modification blocked: Project has {extraction_count} existing "
                    f"extractions. Modifying {', '.join(changed_fields)} may cause data "
                    f"inconsistencies. Add ?force=true to proceed anyway, or delete "
                    f"existing extractions first."
                ),
            )

        if extraction_count > 0 and force:
            # Log when force is used
            logger.warning(
                "schema_update_forced",
                project_id=str(project_id),
                extraction_count=extraction_count,
                schema_changed=schema_changed,
                entities_changed=entities_changed,
            )
            response.headers["X-Extraction-Warning"] = (
                f"Schema updated with force=true. {extraction_count} existing extractions "
                "may be inconsistent with new schema."
            )

    # Update project
    updates = project_update.model_dump(exclude_unset=True)
    updated_project = repo.update(project_id, updates)
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
    success = repo.delete(project_id)
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
    # Get template from registry
    template = get_template(request.template)
    if template is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Template '{request.template}' not found. Available: {list_template_names()}",
        )

    repo = ProjectRepository(db)

    # Check name uniqueness
    existing = repo.get_by_name(request.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Project with name '{request.name}' already exists",
        )

    # Build extraction_schema with embedded config sections
    # This preserves crawl_config, extraction_context, and classification_config
    # inside extraction_schema for later retrieval without schema migration
    extraction_schema = dict(template["extraction_schema"])
    if template.get("crawl_config"):
        extraction_schema["crawl_config"] = template["crawl_config"]
    if template.get("extraction_context"):
        extraction_schema["extraction_context"] = template["extraction_context"]
    if template.get("classification_config"):
        extraction_schema["classification_config"] = template["classification_config"]

    # Create project from template
    db_project = repo.create(
        name=request.name,
        description=request.description or template["description"],
        source_config=template["source_config"],
        extraction_schema=extraction_schema,
        entity_types=template["entity_types"],
        prompt_templates=template.get("prompt_templates", {}),
        is_template=False,
    )
    db.commit()
    db.refresh(db_project)

    return ProjectResponse.model_validate(db_project)
