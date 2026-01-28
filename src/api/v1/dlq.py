"""DLQ API endpoints."""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from api.dependencies import get_dlq_service
from services.dlq.service import DLQService

router = APIRouter(prefix="/api/v1", tags=["dlq"])


class DLQItemResponse(BaseModel):
    """Response model for DLQ items."""

    id: str
    source_id: str
    job_id: str | None
    error: str
    failed_at: str
    retry_count: int
    dlq_type: str


class DLQStatsResponse(BaseModel):
    """Response model for DLQ statistics."""

    scrape: int
    extraction: int


@router.get("/dlq/stats", response_model=DLQStatsResponse)
async def get_dlq_stats(
    dlq_service: DLQService = Depends(get_dlq_service),
) -> DLQStatsResponse:
    """Get DLQ statistics.

    Returns:
        Statistics for scrape and extraction DLQs.
    """
    stats = await dlq_service.get_dlq_stats()
    return DLQStatsResponse(**stats)


@router.get("/dlq/scrape", response_model=list[DLQItemResponse])
async def list_scrape_dlq(
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum items to return"),
    dlq_service: DLQService = Depends(get_dlq_service),
) -> list[DLQItemResponse]:
    """List failed scrape items.

    Args:
        limit: Maximum number of items to return (1-1000).

    Returns:
        List of failed scrape items.
    """
    items = await dlq_service.get_scrape_dlq(limit=limit)
    return [DLQItemResponse(**item.__dict__) for item in items]


@router.get("/dlq/extraction", response_model=list[DLQItemResponse])
async def list_extraction_dlq(
    limit: int = Query(default=100, ge=1, le=1000, description="Maximum items to return"),
    dlq_service: DLQService = Depends(get_dlq_service),
) -> list[DLQItemResponse]:
    """List failed extraction items.

    Args:
        limit: Maximum number of items to return (1-1000).

    Returns:
        List of failed extraction items.
    """
    items = await dlq_service.get_extraction_dlq(limit=limit)
    return [DLQItemResponse(**item.__dict__) for item in items]


@router.post("/dlq/scrape/{item_id}/retry", response_model=DLQItemResponse)
async def retry_scrape_item(
    item_id: str,
    dlq_service: DLQService = Depends(get_dlq_service),
) -> DLQItemResponse:
    """Pop item from DLQ and re-queue for processing.

    Args:
        item_id: ID of the item to retry.

    Returns:
        The popped DLQ item.

    Raises:
        HTTPException: If item not found.
    """
    item = await dlq_service.pop_scrape_item(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Scrape DLQ item {item_id} not found",
        )
    return DLQItemResponse(**item.__dict__)


@router.post("/dlq/extraction/{item_id}/retry", response_model=DLQItemResponse)
async def retry_extraction_item(
    item_id: str,
    dlq_service: DLQService = Depends(get_dlq_service),
) -> DLQItemResponse:
    """Pop item from DLQ and re-queue for processing.

    Args:
        item_id: ID of the item to retry.

    Returns:
        The popped DLQ item.

    Raises:
        HTTPException: If item not found.
    """
    item = await dlq_service.pop_extraction_item(item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Extraction DLQ item {item_id} not found",
        )
    return DLQItemResponse(**item.__dict__)
