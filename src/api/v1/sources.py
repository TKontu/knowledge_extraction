"""Source query API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from api.dependencies import get_project_or_404
from database import get_db
from models import (
    SourceGroupCount,
    SourceListResponse,
    SourceResponse,
    SourceStatusCount,
    SourceSummaryResponse,
)
from orm_models import Project, Source

router = APIRouter(prefix="/api/v1", tags=["sources"])


@router.get(
    "/projects/{project_id}/sources",
    response_model=SourceListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_sources(
    project_id: UUID,
    source_group: str | None = Query(
        default=None, description="Filter by source group"
    ),
    status_filter: str | None = Query(
        default=None,
        alias="status",
        description="Filter by status (pending/completed/failed)",
    ),
    source_type: str | None = Query(
        default=None, description="Filter by source type (web/pdf)"
    ),
    limit: int = Query(
        default=50, ge=1, le=100, description="Number of results to return"
    ),
    offset: int = Query(default=0, ge=0, description="Pagination offset"),
    db: Session = Depends(get_db),
    project: Project = Depends(get_project_or_404),
) -> SourceListResponse:
    """List sources for a project with optional filtering and pagination."""
    # Build query
    query = db.query(Source).filter(Source.project_id == project_id)

    if source_group:
        query = query.filter(Source.source_group == source_group)
    if status_filter:
        query = query.filter(Source.status == status_filter)
    if source_type:
        query = query.filter(Source.source_type == source_type)

    # Get total count
    total = query.count()

    # Apply pagination and ordering
    sources = query.order_by(Source.created_at.desc()).offset(offset).limit(limit).all()

    # Convert to response models
    source_responses = [
        SourceResponse(
            id=str(s.id),
            uri=s.uri,
            source_group=s.source_group,
            source_type=s.source_type,
            title=s.title,
            status=s.status,
            created_at=s.created_at.isoformat(),
            fetched_at=s.fetched_at.isoformat() if s.fetched_at else None,
        )
        for s in sources
    ]

    return SourceListResponse(
        sources=source_responses,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/projects/{project_id}/sources/summary",
    response_model=SourceSummaryResponse,
    status_code=status.HTTP_200_OK,
)
async def get_source_summary(
    project_id: UUID,
    db: Session = Depends(get_db),
    project: Project = Depends(get_project_or_404),
) -> SourceSummaryResponse:
    """Get summary of sources by status and source group for a project."""
    # Get total count using SQL aggregation
    total = (
        db.query(func.count(Source.id)).filter(Source.project_id == project_id).scalar()
        or 0
    )

    # Count by status using SQL GROUP BY
    status_results = (
        db.query(Source.status, func.count(Source.id))
        .filter(Source.project_id == project_id)
        .group_by(Source.status)
        .all()
    )
    by_status = [
        SourceStatusCount(status=status_val, count=count)
        for status_val, count in status_results
    ]

    # Count by source group using SQL GROUP BY
    group_results = (
        db.query(Source.source_group, func.count(Source.id))
        .filter(Source.project_id == project_id)
        .group_by(Source.source_group)
        .all()
    )
    by_source_group = [
        SourceGroupCount(source_group=group, count=count)
        for group, count in group_results
    ]

    return SourceSummaryResponse(
        total_sources=total,
        by_status=by_status,
        by_source_group=by_source_group,
    )


@router.get(
    "/projects/{project_id}/sources/{source_id}",
    response_model=SourceResponse,
    status_code=status.HTTP_200_OK,
)
async def get_source(
    project_id: UUID,
    source_id: UUID,
    db: Session = Depends(get_db),
) -> SourceResponse:
    """Get a single source by ID."""
    # Get source
    source = db.query(Source).filter(Source.id == source_id).first()

    # Check if source exists and belongs to project
    if not source or source.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found in project {project_id}",
        )

    return SourceResponse(
        id=str(source.id),
        uri=source.uri,
        source_group=source.source_group,
        source_type=source.source_type,
        title=source.title,
        status=source.status,
        created_at=source.created_at.isoformat(),
        fetched_at=source.fetched_at.isoformat() if source.fetched_at else None,
    )
