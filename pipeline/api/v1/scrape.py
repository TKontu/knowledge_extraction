"""Scrape API endpoints."""

from fastapi import APIRouter, status

from models import ScrapeRequest, ScrapeResponse

router = APIRouter(prefix="/api/v1", tags=["scrape"])


@router.post("/scrape", status_code=status.HTTP_202_ACCEPTED)
async def create_scrape_job(request: ScrapeRequest) -> ScrapeResponse:
    """
    Create a new scrape job.

    Args:
        request: Scrape job parameters (urls, company, optional profile)

    Returns:
        ScrapeResponse with job_id and metadata
    """
    return ScrapeResponse.create(request)
