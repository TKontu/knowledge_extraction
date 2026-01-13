"""Crawl API endpoints."""

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import CrawlRequest, CrawlResponse, CrawlStatusResponse
from orm_models import Job

logger = structlog.get_logger(__name__)

# Default paths to prioritize for company information extraction
# These patterns help crawlers find "about us", company info, and product pages
DEFAULT_COMPANY_INCLUDE_PATHS = [
    "*about*",
    "*company*",
    "*history*",
    "*who-we-are*",
    "*our-story*",
    "*corporate*",
    "*products*",
    "*solutions*",
    "*services*",
    "*capabilities*",
    "*contact*",
    "*locations*",
]

router = APIRouter(prefix="/api/v1", tags=["crawl"])


@router.post("/crawl", status_code=status.HTTP_202_ACCEPTED)
async def create_crawl_job(
    request: CrawlRequest, db: Session = Depends(get_db)
) -> CrawlResponse:
    """Create a new crawl job."""
    job_id = uuid4()

    # Use default company paths if none specified
    include_paths = request.include_paths
    if include_paths is None:
        include_paths = DEFAULT_COMPANY_INCLUDE_PATHS

    logger.info(
        "crawl_job_created",
        job_id=str(job_id),
        url=request.url,
        project_id=str(request.project_id),
        max_depth=request.max_depth,
        limit=request.limit,
        include_paths_count=len(include_paths) if include_paths else 0,
    )

    job = Job(
        id=job_id,
        type="crawl",
        status="queued",
        payload={
            "url": request.url,
            "project_id": str(request.project_id),
            "company": request.company,
            "max_depth": request.max_depth,
            "limit": request.limit,
            "include_paths": include_paths,
            "exclude_paths": request.exclude_paths,
            "allow_backward_links": request.allow_backward_links,
            "auto_extract": request.auto_extract,
            "profile": request.profile,
            "firecrawl_job_id": None,  # Set when crawl starts
        },
    )

    db.add(job)
    db.commit()
    db.refresh(job)

    return CrawlResponse(
        job_id=str(job.id),
        status=job.status,
        url=request.url,
        max_depth=request.max_depth,
        limit=request.limit,
        project_id=str(request.project_id),
        company=request.company,
    )


@router.get("/crawl/{job_id}", status_code=status.HTTP_200_OK)
async def get_crawl_status(
    job_id: str, db: Session = Depends(get_db)
) -> CrawlStatusResponse:
    """Get crawl job status."""
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job_id format",
        ) from None

    job = db.query(Job).filter(Job.id == job_uuid, Job.type == "crawl").first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Crawl job {job_id} not found",
        )

    return CrawlStatusResponse(
        job_id=str(job.id),
        status=job.status,
        url=job.payload.get("url", ""),
        pages_total=job.result.get("pages_total") if job.result else None,
        pages_completed=job.result.get("pages_completed") if job.result else None,
        sources_created=job.result.get("sources_created") if job.result else None,
        error=job.error,
        created_at=job.created_at.isoformat(),
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )
