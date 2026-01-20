"""Extraction API endpoints."""

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models import ExtractionListResponse, ExtractRequest, ExtractResponse
from orm_models import Job
from services.projects.repository import ProjectRepository
from services.storage.repositories.extraction import (
    ExtractionFilters,
    ExtractionRepository,
)
from services.storage.repositories.source import SourceFilters, SourceRepository

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["extraction"])


@router.post("/projects/{project_id}/extract", status_code=status.HTTP_202_ACCEPTED)
async def create_extraction_job(
    project_id: str,
    request: ExtractRequest,
    db: Session = Depends(get_db),
) -> ExtractResponse:
    """
    Create a new extraction job for a project.

    Args:
        project_id: Project UUID
        request: Extraction job parameters (source_ids, profile)
        db: Database session

    Returns:
        ExtractResponse with job_id and metadata

    Raises:
        HTTPException: 404 if project not found, 422 if invalid UUID format
    """
    # Validate project_id format
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid project_id format. Must be a valid UUID.",
        )

    # Verify project exists
    project_repo = ProjectRepository(db)
    project = await project_repo.get(project_uuid)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Validate source_ids if provided
    source_count = 0
    if request.source_ids:
        # Validate UUID format for all source_ids
        try:
            source_uuids = [UUID(sid) for sid in request.source_ids]
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid source_id format. Must be valid UUIDs.",
            )

        # Verify sources belong to project
        source_repo = SourceRepository(db)
        for source_uuid in source_uuids:
            source = await source_repo.get(source_uuid)
            if source is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Source {source_uuid} not found",
                )
            if source.project_id != project_uuid:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Source {source_uuid} does not belong to project {project_id}",
                )
        source_count = len(source_uuids)
    else:
        # Count pending sources for the project
        source_repo = SourceRepository(db)
        filters = SourceFilters(project_id=project_uuid, status="pending")
        pending_sources = await source_repo.list(filters)
        source_count = len(pending_sources)

    # Create job ID
    job_id = uuid4()

    logger.info(
        "extraction_job_created",
        job_id=str(job_id),
        project_id=project_id,
        source_count=source_count,
        profile=request.profile,
    )

    # Create Job ORM instance
    job = Job(
        id=job_id,
        type="extract",
        status="queued",
        payload={
            "project_id": project_id,
            "source_ids": request.source_ids,
            "profile": request.profile,
        },
    )

    # Persist to database
    db.add(job)
    db.commit()
    db.refresh(job)

    # Create response
    return ExtractResponse(
        job_id=str(job.id),
        status=job.status,
        source_count=source_count,
        project_id=project_id,
    )


@router.get("/projects/{project_id}/extractions", status_code=status.HTTP_200_OK)
async def list_extractions(
    project_id: str,
    source_id: str | None = Query(default=None, description="Filter by source UUID"),
    extraction_type: str | None = Query(
        default=None, description="Filter by extraction type"
    ),
    source_group: str | None = Query(
        default=None, description="Filter by source group"
    ),
    min_confidence: float | None = Query(
        default=None, ge=0.0, le=1.0, description="Minimum confidence threshold"
    ),
    limit: int = Query(default=50, ge=1, le=100, description="Page size"),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
) -> ExtractionListResponse:
    """
    List extractions for a project with optional filtering.

    Args:
        project_id: Project UUID
        source_id: Optional source UUID filter
        extraction_type: Optional extraction type filter
        source_group: Optional source group filter
        min_confidence: Optional minimum confidence threshold
        limit: Page size (default 50, max 100)
        offset: Pagination offset (default 0)
        db: Database session

    Returns:
        ExtractionListResponse with extractions and metadata

    Raises:
        HTTPException: 404 if project not found, 422 if invalid UUID format
    """
    # Validate project_id format
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid project_id format. Must be a valid UUID.",
        )

    # Verify project exists
    project_repo = ProjectRepository(db)
    project = await project_repo.get(project_uuid)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Validate source_id if provided
    source_uuid = None
    if source_id:
        try:
            source_uuid = UUID(source_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Invalid source_id format. Must be a valid UUID.",
            )

    # Build filters
    filters = ExtractionFilters(
        project_id=project_uuid,
        source_id=source_uuid,
        extraction_type=extraction_type,
        source_group=source_group,
        min_confidence=min_confidence,
    )

    # Query extractions
    extraction_repo = ExtractionRepository(db)
    all_extractions = await extraction_repo.list(filters)

    # Get total count
    total = len(all_extractions)

    # Apply pagination
    paginated_extractions = all_extractions[offset : offset + limit]

    # Convert to response format
    extractions_data = []
    for extraction in paginated_extractions:
        extractions_data.append(
            {
                "id": str(extraction.id),
                "source_id": str(extraction.source_id),
                "data": extraction.data,
                "extraction_type": extraction.extraction_type,
                "source_group": extraction.source_group,
                "confidence": extraction.confidence,
                "extracted_at": extraction.extracted_at.isoformat(),
                "created_at": extraction.created_at.isoformat(),
            }
        )

    return ExtractionListResponse(
        extractions=extractions_data,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/projects/{project_id}/extract-schema")
async def extract_schema(
    project_id: str,
    source_groups: list[str] | None = None,
    db: Session = Depends(get_db),
) -> dict:
    """Run schema-based extraction on project sources.

    This uses the drivetrain company template with 7 field groups,
    running multiple focused LLM calls per source.

    Args:
        project_id: Project UUID.
        source_groups: Optional filter by company names.

    Returns:
        Summary of extraction results.
    """
    from config import settings
    from orm_models import Project
    from redis_client import get_async_redis
    from services.extraction.pipeline import SchemaExtractionPipeline
    from services.extraction.schema_extractor import SchemaExtractor
    from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator
    from services.llm.queue import LLMRequestQueue

    # Validate project_id format
    try:
        project_uuid = UUID(project_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid project_id format. Must be a valid UUID.",
        )

    # Validate project exists
    project = db.query(Project).filter(Project.id == project_uuid).first()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Create LLM queue if enabled (requires async Redis client)
    llm_queue = None
    if settings.llm_queue_enabled:
        async_redis = await get_async_redis()
        llm_queue = LLMRequestQueue(
            redis=async_redis,
            stream_key="llm:requests",
            max_queue_depth=1000,
            backpressure_threshold=500,
        )

    # Create extraction pipeline
    extractor = SchemaExtractor(settings, llm_queue=llm_queue)
    orchestrator = SchemaExtractionOrchestrator(extractor)
    pipeline = SchemaExtractionPipeline(orchestrator, db)

    # Run extraction
    result = await pipeline.extract_project(
        project_id=project_uuid,
        source_groups=source_groups,
    )

    return result
