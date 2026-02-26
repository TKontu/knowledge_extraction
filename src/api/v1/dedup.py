"""Domain boilerplate deduplication API endpoints."""

from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from services.extraction.domain_dedup import DomainDedupService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects", tags=["dedup"])


@router.post(
    "/{project_id}/analyze-boilerplate",
    status_code=status.HTTP_200_OK,
)
async def analyze_boilerplate(
    project_id: UUID,
    source_groups: list[str] | None = Query(
        default=None, description="Filter by source groups"
    ),
    threshold_pct: float | None = Query(
        default=None, ge=0.1, le=1.0, description="Boilerplate threshold (default 0.7)"
    ),
    min_pages: int | None = Query(
        default=None, ge=2, le=100, description="Min pages per domain (default 5)"
    ),
    min_block_chars: int | None = Query(
        default=None,
        ge=10,
        le=500,
        description="Min block chars (default 50)",
    ),
    db: Session = Depends(get_db),
) -> dict:
    """Analyze domains for boilerplate content and clean sources.

    Scans all pages per domain, identifies repeating blocks (cookie banners,
    navs, footers), and stores cleaned versions in sources.cleaned_content.
    """
    from config import settings

    service = DomainDedupService(db, settings)

    try:
        result = service.analyze_project(
            project_id=project_id,
            source_groups=source_groups,
            threshold_pct=threshold_pct,
            min_pages=min_pages,
            min_block_chars=min_block_chars,
        )
    except Exception as e:
        logger.error(
            "boilerplate_analysis_failed",
            project_id=str(project_id),
            error=str(e),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Boilerplate analysis failed: {e}",
        ) from e

    return {
        "domains_analyzed": result.domains_analyzed,
        "domains_with_boilerplate": result.domains_with_boilerplate,
        "total_pages_cleaned": result.total_pages_cleaned,
        "total_bytes_removed": result.total_bytes_removed,
        "domains": [
            {
                "domain": d.domain,
                "pages_analyzed": d.pages_analyzed,
                "pages_cleaned": d.pages_cleaned,
                "blocks_boilerplate": d.blocks_boilerplate,
                "bytes_removed_total": d.bytes_removed_total,
            }
            for d in result.domain_results
        ],
    }


@router.get(
    "/{project_id}/boilerplate-stats",
    status_code=status.HTTP_200_OK,
)
async def get_boilerplate_stats(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> dict:
    """Get per-domain boilerplate statistics for a project."""
    from config import settings

    service = DomainDedupService(db, settings)
    stats = service.get_domain_stats(project_id)

    return {
        "project_id": str(project_id),
        "domain_count": len(stats),
        "domains": stats,
    }
