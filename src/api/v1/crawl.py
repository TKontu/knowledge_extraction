"""Crawl API endpoints."""

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import CrawlRequest, CrawlResponse, CrawlStatusResponse
from orm_models import Job
from services.filtering.language import LanguageCode
from services.filtering.patterns import generate_language_exclusion_patterns

logger = structlog.get_logger(__name__)

# Default: No include_paths filter - crawl everything within the domain
# This allows maximum content discovery across varied URL structures
# Filtering happens during extraction, not crawling
DEFAULT_COMPANY_INCLUDE_PATHS = None

router = APIRouter(prefix="/api/v1", tags=["crawl"])


@router.post("/crawl", status_code=status.HTTP_202_ACCEPTED)
async def create_crawl_job(
    request: CrawlRequest, db: Session = Depends(get_db)
) -> CrawlResponse:
    """Create a new crawl job."""
    job_id = uuid4()

    # Use default (no filter) if none specified
    include_paths = request.include_paths
    if include_paths is None:
        include_paths = DEFAULT_COMPANY_INCLUDE_PATHS

    # Auto-generate language exclusion patterns (Layer 1: URL Pattern Pre-Filtering)
    exclude_paths = list(request.exclude_paths) if request.exclude_paths else []
    if request.prefer_english_only and settings.language_filtering_enabled:
        # Determine which languages to exclude
        allowed_languages = request.allowed_languages or ["en"]
        excluded_langs = [
            lang
            for lang in settings.excluded_language_codes
            if lang not in allowed_languages
        ]

        # Generate patterns and extend exclude_paths
        lang_patterns = generate_language_exclusion_patterns(
            [LanguageCode(code) for code in excluded_langs]
        )
        exclude_paths.extend(lang_patterns)

        logger.info(
            "language_filtering_enabled",
            job_id=str(job_id),
            excluded_languages=excluded_langs,
            pattern_count=len(lang_patterns),
        )

    logger.info(
        "crawl_job_created",
        job_id=str(job_id),
        url=request.url,
        project_id=str(request.project_id),
        max_depth=request.max_depth,
        limit=request.limit,
        include_paths_count=len(include_paths) if include_paths else 0,
        exclude_paths_count=len(exclude_paths),
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
            "exclude_paths": exclude_paths,
            "allow_backward_links": request.allow_backward_links,
            "auto_extract": request.auto_extract,
            "profile": request.profile,
            "language_detection_enabled": request.language_detection_enabled,
            "allowed_languages": request.allowed_languages or ["en"],
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
