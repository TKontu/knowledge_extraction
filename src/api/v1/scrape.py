"""Scrape API endpoints."""

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import JobStatusResponse, ScrapeRequest, ScrapeResponse
from orm_models import Job

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["scrape"])


@router.post("/scrape", status_code=status.HTTP_202_ACCEPTED)
async def create_scrape_job(
    request: ScrapeRequest, db: Session = Depends(get_db)
) -> ScrapeResponse:
    """
    Create a new scrape job.

    Args:
        request: Scrape job parameters (urls, company, optional profile)
        db: Database session

    Returns:
        ScrapeResponse with job_id and metadata
    """
    # Create job ID
    job_id = uuid4()

    logger.info(
        "scrape_job_created",
        job_id=str(job_id),
        url_count=len(request.urls),
        company=request.company,
        profile=request.profile,
    )

    # Create Job ORM instance
    job = Job(
        id=job_id,
        type="scrape",
        status="queued",
        payload={
            "urls": request.urls,
            "company": request.company,
            "profile": request.profile,
        },
    )

    # Persist to database
    db.add(job)
    db.commit()
    db.refresh(job)

    # Create response
    return ScrapeResponse(
        job_id=str(job.id),
        status=job.status,
        url_count=len(request.urls),
        company=request.company,
        profile=request.profile,
    )


@router.get("/scrape/{job_id}", status_code=status.HTTP_200_OK)
async def get_job_status(
    job_id: str, db: Session = Depends(get_db)
) -> JobStatusResponse:
    """
    Get the status of a scrape job.

    Args:
        job_id: Job identifier (UUID format)
        db: Database session

    Returns:
        JobStatusResponse with job details and current status

    Raises:
        HTTPException: 404 if job not found, 422 if invalid UUID format
    """
    # Validate UUID format
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job_id format. Must be a valid UUID.",
        )

    # Query database for job
    job = db.query(Job).filter(Job.id == job_uuid).first()

    # Check if job exists
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    # Extract data from Job ORM model
    return JobStatusResponse(
        job_id=str(job.id),
        status=job.status,
        company=job.payload.get("company", ""),
        url_count=len(job.payload.get("urls", [])),
        profile=job.payload.get("profile"),
        urls=job.payload.get("urls"),
        created_at=job.created_at.isoformat(),
        error=job.error,
    )
