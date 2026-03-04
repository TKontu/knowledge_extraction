"""Extraction API endpoints."""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from api.dependencies import get_embedding_service, get_qdrant_repository
from constants import JobStatus, JobType, SourceStatus
from database import get_db
from models import (
    ExtractionListResponse,
    ExtractRequest,
    ExtractResponse,
    RecoverySummaryResponse,
)
from orm_models import Job
from services.projects.repository import ProjectRepository
from services.storage.repositories.extraction import (
    ExtractionFilters,
    ExtractionRepository,
)
from services.storage.repositories.source import SourceRepository

if TYPE_CHECKING:
    from services.storage.embedding import EmbeddingService
    from services.storage.qdrant.repository import QdrantRepository

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
    project = project_repo.get(project_uuid)
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

        # Verify sources exist and belong to project (single batch query)
        source_repo = SourceRepository(db)
        sources = source_repo.get_batch(source_uuids)
        found_ids = {s.id for s in sources}
        missing = [sid for sid in source_uuids if sid not in found_ids]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Sources not found: {[str(m) for m in missing[:5]]}",
            )
        wrong_project = [s for s in sources if s.project_id != project_uuid]
        if wrong_project:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Source {wrong_project[0].id} does not belong to project {project_id}",
            )
        source_count = len(source_uuids)
    else:
        # Count sources that will be processed based on force flag
        # Worker processes: "ready" + "pending", and "extracted" if force=True
        from orm_models import Source

        allowed_statuses = [SourceStatus.READY, SourceStatus.PENDING]
        if request.force:
            allowed_statuses.append(SourceStatus.EXTRACTED)

        source_count = (
            db.query(Source)
            .filter(
                Source.project_id == project_uuid,
                Source.status.in_(allowed_statuses),
                Source.content.isnot(None),
            )
            .count()
        )

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
        type=JobType.EXTRACT,
        status=JobStatus.QUEUED,
        payload={
            "project_id": project_id,
            "source_ids": request.source_ids,
            "profile": request.profile,
            "force": request.force,
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
    project = project_repo.get(project_uuid)
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

    # Query extractions with DB-level pagination
    extraction_repo = ExtractionRepository(db)
    total = extraction_repo.count(filters)
    paginated_extractions = extraction_repo.list(filters, limit=limit, offset=offset)

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


@router.post(
    "/projects/{project_id}/extractions/recover", status_code=status.HTTP_200_OK
)
async def recover_orphaned_extractions(
    project_id: str,
    max_batches: int = Query(
        default=10, le=100, description="Maximum batches to process"
    ),
    db: Session = Depends(get_db),
    embedding_service: EmbeddingService = Depends(get_embedding_service),
    qdrant_repo: QdrantRepository = Depends(get_qdrant_repository),
) -> RecoverySummaryResponse:
    """
    Manually trigger recovery of orphaned extractions.

    Finds extractions with embedding_id IS NULL and retries embedding generation.
    This operation is idempotent and safe to run multiple times.

    Args:
        project_id: Project UUID to recover extractions for.
        max_batches: Maximum number of batches to process (default 10, max 100).
        db: Database session.
        embedding_service: Embedding service (injected).
        qdrant_repo: Qdrant repository (injected).

    Returns:
        RecoverySummaryResponse with recovery statistics.

    Raises:
        HTTPException: 404 if project not found, 422 if invalid UUID format.
    """
    from services.extraction.embedding_recovery import EmbeddingRecoveryService

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
    project = project_repo.get(project_uuid)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    logger.info(
        "embedding_recovery_endpoint_called",
        project_id=project_id,
        max_batches=max_batches,
    )

    extraction_repo = ExtractionRepository(db)

    # Create recovery service
    recovery_service = EmbeddingRecoveryService(
        db=db,
        embedding_service=embedding_service,
        qdrant_repo=qdrant_repo,
        extraction_repo=extraction_repo,
        batch_size=50,
    )

    # Run recovery
    summary = await recovery_service.run_recovery(
        project_id=project_uuid,
        max_batches=max_batches,
    )

    # Commit embedding_id updates to persist changes
    # Without this, autocommit=False causes rollback on session close
    db.commit()

    logger.info(
        "embedding_recovery_completed",
        project_id=project_id,
        total_found=summary.total_found,
        total_recovered=summary.total_recovered,
        total_failed=summary.total_failed,
        batches_processed=summary.batches_processed,
    )

    return RecoverySummaryResponse(
        total_found=summary.total_found,
        total_recovered=summary.total_recovered,
        total_failed=summary.total_failed,
        batches_processed=summary.batches_processed,
        errors=summary.errors,
    )
