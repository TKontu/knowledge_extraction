"""Search API endpoints."""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from config import settings
from database import get_db
from models import SearchRequest, SearchResponse, SearchResultItem
from services.projects.repository import ProjectRepository
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository
from services.storage.repositories.extraction import ExtractionRepository
from services.storage.search import SearchService

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.post("/projects/{project_id}/search", status_code=status.HTTP_200_OK)
async def search_extractions(
    project_id: str,
    request: SearchRequest,
    db: Session = Depends(get_db),
) -> SearchResponse:
    """
    Search extractions using hybrid semantic + structured search.

    Args:
        project_id: Project UUID
        request: Search parameters (query, limit, filters)
        db: Database session

    Returns:
        SearchResponse with matching results

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
    project = await project_repo.get(project_uuid)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Initialize services
    embedding_service = EmbeddingService(settings)
    qdrant_repo = QdrantRepository(settings)
    extraction_repo = ExtractionRepository(db)

    search_service = SearchService(
        embedding_service=embedding_service,
        qdrant_repo=qdrant_repo,
        extraction_repo=extraction_repo,
    )

    # Execute search
    results = await search_service.search(
        project_id=project_uuid,
        query=request.query,
        limit=request.limit,
        source_groups=request.source_groups,
        jsonb_filters=request.filters,
    )

    # Convert to response format
    result_items = [
        SearchResultItem(
            extraction_id=str(result.extraction_id),
            score=result.score,
            data=result.data,
            source_group=result.source_group,
            source_uri=result.source_uri,
            confidence=result.confidence,
        )
        for result in results
    ]

    return SearchResponse(
        results=result_items,
        query=request.query,
        total=len(result_items),
    )
