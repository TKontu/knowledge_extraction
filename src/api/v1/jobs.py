"""Jobs API endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from api.dependencies import get_dlq_service, get_qdrant_repository
from database import get_db
from models import (
    JobCancelResponse,
    JobCleanupRequest,
    JobCleanupResponse,
    JobDeleteResponse,
    JobDetailResponse,
    JobListResponse,
    JobSummary,
)
from orm_models import Job, Source
from services.dlq.service import DLQService
from services.job.cleanup_service import JobCleanupService
from services.storage.qdrant.repository import QdrantRepository
from services.storage.repositories.job import JobRepository

router = APIRouter(prefix="/api/v1", tags=["jobs"])


@router.get("/jobs", status_code=status.HTTP_200_OK)
def list_jobs(
    type: str | None = Query(
        default=None, description="Filter by job type (scrape, extract)"
    ),
    status_filter: str | None = Query(
        default=None, alias="status", description="Filter by status"
    ),
    created_after: datetime | None = Query(
        default=None, description="Filter by creation date"
    ),
    created_before: datetime | None = Query(
        default=None, description="Filter by creation date"
    ),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> JobListResponse:
    """List all jobs with optional filtering."""
    # Build query with filters
    query = select(Job)

    if type:
        query = query.where(Job.type == type)

    if status_filter:
        query = query.where(Job.status == status_filter)

    if created_after:
        query = query.where(Job.created_at >= created_after)

    if created_before:
        query = query.where(Job.created_at <= created_before)

    # Sort by created_at descending (newest first)
    query = query.order_by(desc(Job.created_at))

    # Get total count
    count_query = select(Job.id)
    if type:
        count_query = count_query.where(Job.type == type)
    if status_filter:
        count_query = count_query.where(Job.status == status_filter)
    if created_after:
        count_query = count_query.where(Job.created_at >= created_after)
    if created_before:
        count_query = count_query.where(Job.created_at <= created_before)

    total = len(db.execute(count_query).all())

    # Apply pagination
    query = query.limit(limit).offset(offset)

    # Execute query
    jobs = db.execute(query).scalars().all()

    # Convert to response models
    job_summaries = [
        JobSummary(
            id=str(job.id),
            type=job.type,
            status=job.status,
            created_at=job.created_at.isoformat(),
            started_at=job.started_at.isoformat() if job.started_at else None,
            completed_at=job.completed_at.isoformat() if job.completed_at else None,
            error=job.error,
        )
        for job in jobs
    ]

    return JobListResponse(
        jobs=job_summaries,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/jobs/{job_id}", status_code=status.HTTP_200_OK)
def get_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    """Get detailed information about a specific job."""
    # Validate UUID format
    try:
        job_uuid = UUID(job_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job ID format",
        ) from e

    # Query job
    job = db.execute(select(Job).where(Job.id == job_uuid)).scalar_one_or_none()

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    # Convert to response model
    return JobDetailResponse(
        id=str(job.id),
        type=job.type,
        status=job.status,
        payload=job.payload,
        result=job.result,
        error=job.error,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )


@router.post("/jobs/{job_id}/cancel", status_code=status.HTTP_200_OK)
async def cancel_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> JobCancelResponse:
    """Request cancellation of a queued or running job.

    Sets the job status to 'cancelling'. Workers check for this status at
    checkpoints and will stop processing when they see it.

    Note: For crawl jobs using Firecrawl, cancellation cannot stop the external
    crawl - it will only prevent new results from being stored.
    """
    # Validate UUID format
    try:
        job_uuid = UUID(job_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job ID format",
        ) from e

    job_repo = JobRepository(db)
    job = await job_repo.request_cancellation(job_uuid)

    if not job:
        # Check if job exists to give appropriate error
        existing = await job_repo.get(job_uuid)
        if not existing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Job {job_id} not found",
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cancel job with status '{existing.status}'",
        )

    db.commit()

    # Count sources that will need cleanup
    source_count = db.execute(
        select(func.count()).select_from(Source).where(
            Source.created_by_job_id == job_uuid
        )
    ).scalar()

    return JobCancelResponse(
        job_id=job_id,
        status="cancelling",
        message="Cancellation requested. Job will stop at next checkpoint.",
        sources_to_cleanup=source_count,
    )


@router.post("/jobs/{job_id}/cleanup", status_code=status.HTTP_200_OK)
async def cleanup_job(
    job_id: str,
    request: JobCleanupRequest = JobCleanupRequest(),
    db: Session = Depends(get_db),
    dlq_service: DLQService = Depends(get_dlq_service),
    qdrant: QdrantRepository = Depends(get_qdrant_repository),
) -> JobCleanupResponse:
    """Delete all artifacts created by a job.

    Deletes sources, extractions (cascaded), embeddings, and DLQ items
    associated with the job. Optionally also deletes the job record itself.

    Only allowed for jobs in terminal states (completed, failed, cancelled).
    """
    # Validate UUID format
    try:
        job_uuid = UUID(job_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job ID format",
        ) from e

    job_repo = JobRepository(db)
    job = await job_repo.get(job_uuid)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    # Only allow cleanup of terminal states
    if job.status in ("queued", "running", "cancelling"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot cleanup job with status '{job.status}'. Cancel first.",
        )

    # Perform cleanup
    cleanup_service = JobCleanupService(db, qdrant, dlq_service)
    stats = await cleanup_service.cleanup_job_artifacts(job_uuid)

    # Optionally delete job record
    job_deleted = False
    if request.delete_job:
        job_deleted = await job_repo.delete(job_uuid)

    db.commit()

    return JobCleanupResponse(
        job_id=job_id,
        sources_deleted=stats.sources_deleted,
        extractions_deleted=stats.extractions_deleted,
        embeddings_deleted=stats.embeddings_deleted,
        dlq_items_deleted=stats.dlq_items_deleted,
        job_deleted=job_deleted,
    )


@router.delete("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def delete_job(
    job_id: str,
    cleanup: bool = Query(
        default=False, description="Also delete associated artifacts"
    ),
    db: Session = Depends(get_db),
    dlq_service: DLQService = Depends(get_dlq_service),
    qdrant: QdrantRepository = Depends(get_qdrant_repository),
) -> JobDeleteResponse:
    """Delete a job record from the database.

    Optionally also cleans up all artifacts (sources, extractions, embeddings)
    created by the job.
    """
    # Validate UUID format
    try:
        job_uuid = UUID(job_id)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job ID format",
        ) from e

    job_repo = JobRepository(db)
    job = await job_repo.get(job_uuid)

    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    # Don't allow deleting active jobs without cleanup
    if job.status in ("queued", "running", "cancelling") and not cleanup:
        if job.status == "cancelling":
            detail = "Job is cancelling. Wait for 'cancelled' or use cleanup=true."
        else:
            detail = "Cannot delete active job. Use cleanup=true or cancel first."
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=detail,
        )

    # Perform cleanup if requested
    cleanup_stats = None
    if cleanup:
        cleanup_service = JobCleanupService(db, qdrant, dlq_service)
        stats = await cleanup_service.cleanup_job_artifacts(job_uuid)
        cleanup_stats = {
            "sources_deleted": stats.sources_deleted,
            "extractions_deleted": stats.extractions_deleted,
            "embeddings_deleted": stats.embeddings_deleted,
            "dlq_items_deleted": stats.dlq_items_deleted,
        }

    # Delete job record
    deleted = await job_repo.delete(job_uuid)
    db.commit()

    return JobDeleteResponse(
        job_id=job_id,
        deleted=deleted,
        cleanup_performed=cleanup,
        cleanup_stats=cleanup_stats,
    )
