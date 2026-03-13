"""Project CRUD API endpoints."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
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
from services.extraction.extraction_items import safe_data_version
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


@router.post("/{project_id}/backfill-grounding")
async def backfill_grounding(
    project_id: UUID,
    dry_run: bool = Query(default=False, description="Compute scores without writing to DB"),
    batch_size: int = Query(default=500, ge=1, le=5000, description="Batch size"),
    db: Session = Depends(get_db),
) -> dict:
    """Backfill string-match grounding scores for all extractions in a project.

    Computes grounding scores by matching extracted values against source quotes.
    This is a CPU-only operation (no LLM calls) and runs synchronously.
    """
    from collections import defaultdict

    from services.extraction.grounding import (
        compute_entity_list_grounding_scores,
        compute_grounding_scores,
        extract_entity_list_groups,
        extract_field_types_from_schema,
    )
    from services.storage.repositories.extraction import (
        ExtractionFilters,
        ExtractionRepository,
    )

    repo = ProjectRepository(db)
    project = repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    schema = project.extraction_schema
    if not schema or not schema.get("field_groups"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project has no extraction schema with field_groups",
        )

    field_types_by_group = extract_field_types_from_schema(schema)
    entity_list_groups = extract_entity_list_groups(schema)
    ext_repo = ExtractionRepository(db)
    filters = ExtractionFilters(project_id=project_id)
    total_count = ext_repo.count(filters)

    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    processed = 0
    updated = 0
    skipped = 0
    offset = 0

    while offset < total_count:
        extractions = ext_repo.list(filters, limit=batch_size, offset=offset)
        if not extractions:
            break

        updates: list[tuple[UUID, dict[str, float]]] = []
        for ext in extractions:
            # v2 extractions have inline grounding — skip backfill
            if safe_data_version(ext) >= 2:
                skipped += 1
                continue

            field_types = field_types_by_group.get(ext.extraction_type, {})
            if not field_types:
                skipped += 1
                continue

            if ext.extraction_type in entity_list_groups:
                scores = compute_entity_list_grounding_scores(
                    ext.data, ext.extraction_type, field_types
                )
            else:
                scores = compute_grounding_scores(ext.data, field_types)
            if scores:
                updates.append((ext.id, scores))
                for field_name, score in scores.items():
                    bucket = "grounded" if score >= 0.5 else "ungrounded"
                    stats[field_name][bucket] += 1

            processed += 1

        if updates and not dry_run:
            updated += ext_repo.update_grounding_scores_batch(updates)
            db.commit()

        offset += batch_size

    return {
        "total_extractions": total_count,
        "processed": processed,
        "updated": updated,
        "skipped_no_field_types": skipped,
        "dry_run": dry_run,
        "field_stats": {
            field: dict(counts) for field, counts in sorted(stats.items())
        },
    }


@router.post("/{project_id}/backfill-grounding-v2")
async def backfill_grounding_v2(
    project_id: UUID,
    dry_run: bool = Query(default=True, description="Compute scores without writing to DB"),
    batch_size: int = Query(default=100, ge=1, le=5000, description="Batch size"),
    db: Session = Depends(get_db),
) -> dict:
    """Backfill grounding scores for v2 extractions.

    V2 extractions store per-field grounding inline in the data JSONB column.
    This re-computes grounding using ground_field_item() with current defaults
    (text fields now use semantic grounding instead of none).
    """
    from collections import defaultdict

    from services.extraction.extraction_items import safe_data_version
    from services.extraction.grounding import (
        GROUNDING_DEFAULTS,
        ground_field_item,
    )
    from services.storage.repositories.extraction import (
        ExtractionFilters,
        ExtractionRepository,
    )

    repo = ProjectRepository(db)
    project = repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    schema = project.extraction_schema
    if not schema or not schema.get("field_groups"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Project has no extraction schema with field_groups",
        )

    # Build field_type and grounding_mode maps per (group, field)
    field_info_by_group: dict[str, dict[str, tuple[str, str | None]]] = {}
    for fg in schema.get("field_groups", []):
        group_name = fg.get("name", "")
        if not group_name:
            continue
        fields: dict[str, tuple[str, str | None]] = {}
        for f in fg.get("fields", []):
            name = f.get("name", "")
            ftype = f.get("field_type", "") or f.get("type", "")
            gmode = f.get("grounding_mode")
            if name and ftype:
                fields[name] = (ftype, gmode)
        if fields:
            field_info_by_group[group_name] = fields

    ext_repo = ExtractionRepository(db)
    filters = ExtractionFilters(project_id=project_id)
    total_count = ext_repo.count(filters)

    stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    processed = 0
    updated = 0
    skipped = 0
    offset = 0
    source_content_cache: dict[UUID, str] = {}

    while offset < total_count:
        extractions = ext_repo.list(filters, limit=batch_size, offset=offset)
        if not extractions:
            break

        updates: list[tuple[UUID, dict]] = []
        for ext in extractions:
            if safe_data_version(ext) < 2:
                skipped += 1
                continue

            field_info = field_info_by_group.get(ext.extraction_type, {})
            if not field_info:
                skipped += 1
                continue

            # Get source content (cached)
            if ext.source_id not in source_content_cache:
                source = ext.source
                source_content_cache[ext.source_id] = (
                    source.cleaned_content or source.content or ""
                ) if source else ""
            source_content = source_content_cache[ext.source_id]

            data = ext.data
            if not isinstance(data, dict):
                skipped += 1
                continue

            changed = False
            for field_name, (field_type, gmode_override) in field_info.items():
                field_data = data.get(field_name)
                if not isinstance(field_data, dict):
                    continue
                value = field_data.get("value")
                if value is None:
                    continue
                quote = field_data.get("quote")

                effective_mode = gmode_override or GROUNDING_DEFAULTS.get(field_type, "required")
                if effective_mode == "none":
                    continue

                new_score = ground_field_item(
                    field_name, value, quote, source_content, field_type,
                    grounding_mode=gmode_override,
                )
                old_score = float(field_data.get("grounding", 1.0))

                if abs(new_score - old_score) > 0.001:
                    field_data["grounding"] = round(new_score, 4)
                    changed = True
                    bucket = "grounded" if new_score >= 0.5 else "ungrounded"
                    stats[field_name][bucket] += 1
                    if old_score >= 0.5 and new_score < 0.5:
                        stats[field_name]["downgraded"] += 1

            if changed:
                updates.append((ext.id, data))
            processed += 1

        if updates and not dry_run:
            updated += ext_repo.update_v2_data_batch(updates)
            db.commit()
        elif updates:
            updated += len(updates)

        # Clear source cache between batches to limit memory
        source_content_cache.clear()
        offset += batch_size

    return {
        "total_extractions": total_count,
        "processed": processed,
        "updated": updated,
        "skipped": skipped,
        "dry_run": dry_run,
        "field_stats": {
            field: dict(counts) for field, counts in sorted(stats.items())
        },
    }


@router.post("/{project_id}/consolidate")
async def consolidate_project(
    project_id: UUID,
    source_group: str | None = Query(
        default=None, description="Consolidate a single source group"
    ),
    use_llm: bool = Query(
        default=False, description="Use LLM synthesis for llm_summarize fields"
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Trigger consolidation for a project (or single source_group).

    Merges multiple raw extractions per entity into one consolidated record
    with grounding-weighted strategies and provenance tracking.

    When use_llm=True, creates a background job and returns 202 with a job_id.
    Poll GET /jobs/{job_id} for status. When use_llm=False, runs synchronously
    and returns results immediately.
    """
    from uuid import uuid4

    from constants import JobStatus, JobType
    from orm_models import Job
    from services.extraction.consolidation_service import ConsolidationService

    repo = ProjectRepository(db)
    project = repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # LLM consolidation runs as a background job to avoid HTTP timeouts
    if use_llm:
        job = Job(
            id=uuid4(),
            type=JobType.CONSOLIDATE,
            status=JobStatus.QUEUED,
            project_id=project_id,
            payload={
                "project_id": str(project_id),
                "source_group": source_group,
                "use_llm": True,
            },
        )
        db.add(job)
        db.commit()
        return JSONResponse(
            status_code=status.HTTP_202_ACCEPTED,
            content={
                "job_id": str(job.id),
                "status": "queued",
                "project_id": str(project_id),
            },
        )

    # Non-LLM consolidation runs inline (fast)
    service = ConsolidationService(db, repo)

    try:
        if source_group:
            records = await service.consolidate_source_group(
                project_id, source_group,
            )
            db.commit()
            return {
                "source_groups": 1,
                "records_created": len(records),
                "errors": 0,
            }

        result = await service.consolidate_project(project_id)
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
