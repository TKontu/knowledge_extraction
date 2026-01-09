"""Scrape API endpoints."""

from datetime import datetime, UTC
from uuid import UUID

from fastapi import APIRouter, HTTPException, status

from models import JobStatusResponse, ScrapeRequest, ScrapeResponse

router = APIRouter(prefix="/api/v1", tags=["scrape"])

# In-memory job storage (temporary until database integration)
_job_store: dict[str, dict] = {}


@router.post("/scrape", status_code=status.HTTP_202_ACCEPTED)
async def create_scrape_job(request: ScrapeRequest) -> ScrapeResponse:
    """
    Create a new scrape job.

    Args:
        request: Scrape job parameters (urls, company, optional profile)

    Returns:
        ScrapeResponse with job_id and metadata
    """
    response = ScrapeResponse.create(request)

    # Store job in memory
    _job_store[response.job_id] = {
        "job_id": response.job_id,
        "status": response.status,
        "company": response.company,
        "url_count": response.url_count,
        "profile": response.profile,
        "urls": request.urls,
        "created_at": datetime.now(UTC).isoformat(),
    }

    return response


@router.get("/scrape/{job_id}", status_code=status.HTTP_200_OK)
async def get_job_status(job_id: str) -> JobStatusResponse:
    """
    Get the status of a scrape job.

    Args:
        job_id: Job identifier (UUID format)

    Returns:
        JobStatusResponse with job details and current status

    Raises:
        HTTPException: 404 if job not found, 422 if invalid UUID format
    """
    # Validate UUID format
    try:
        UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job_id format. Must be a valid UUID.",
        )

    # Check if job exists
    if job_id not in _job_store:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Job {job_id} not found",
        )

    job_data = _job_store[job_id]
    return JobStatusResponse(**job_data)
