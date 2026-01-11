"""Jobs API endpoints."""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from database import get_db
from models import JobDetailResponse, JobListResponse, JobSummary
from orm_models import Job

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
