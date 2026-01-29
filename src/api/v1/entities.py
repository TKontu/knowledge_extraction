"""Entity query API endpoints."""

from collections import Counter
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from database import get_db
from models import (
    EntityListResponse,
    EntityResponse,
    EntityTypeCount,
    EntityTypesResponse,
)
from services.projects.repository import ProjectRepository
from services.storage.repositories.entity import EntityFilters, EntityRepository

router = APIRouter(prefix="/api/v1", tags=["entities"])


@router.get(
    "/projects/{project_id}/entities",
    response_model=EntityListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_entities(
    project_id: UUID,
    entity_type: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> EntityListResponse:
    """List entities for a project with optional filtering and pagination."""
    # Validate project exists
    project_repo = ProjectRepository(db)
    project = project_repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Build filters
    filters = EntityFilters(
        project_id=project_id,
        source_group=source_group,
        entity_type=entity_type,
    )

    # Get entities
    entity_repo = EntityRepository(db)
    all_entities = entity_repo.list(filters)

    # Apply pagination
    total = len(all_entities)
    paginated_entities = all_entities[offset : offset + limit]

    # Convert to response models
    entity_responses = [
        EntityResponse(
            id=str(e.id),
            entity_type=e.entity_type,
            value=e.value,
            normalized_value=e.normalized_value,
            source_group=e.source_group,
            attributes=e.attributes,
            created_at=e.created_at.isoformat(),
        )
        for e in paginated_entities
    ]

    return EntityListResponse(
        entities=entity_responses,
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/projects/{project_id}/entities/types",
    response_model=EntityTypesResponse,
    status_code=status.HTTP_200_OK,
)
async def get_entity_types(
    project_id: UUID,
    source_group: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> EntityTypesResponse:
    """Get count of entities per type for a project."""
    # Validate project exists
    project_repo = ProjectRepository(db)
    project = project_repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )

    # Build filters
    filters = EntityFilters(
        project_id=project_id,
        source_group=source_group,
    )

    # Get entities
    entity_repo = EntityRepository(db)
    entities = entity_repo.list(filters)

    # Count by type
    type_counts = Counter(e.entity_type for e in entities)

    # Convert to response
    type_count_list = [
        EntityTypeCount(entity_type=entity_type, count=count)
        for entity_type, count in type_counts.items()
    ]

    return EntityTypesResponse(
        types=type_count_list,
        total_entities=len(entities),
    )


@router.get(
    "/projects/{project_id}/entities/by-value",
    status_code=status.HTTP_200_OK,
)
async def get_source_groups_by_entity(
    project_id: UUID,
    entity_type: str = Query(..., description="Entity type to search"),
    value: str = Query(..., description="Entity value to match (case-insensitive)"),
    db: Session = Depends(get_db),
) -> dict:
    """Find source_groups that have an entity with the given type and value."""
    # Build filters
    filters = EntityFilters(
        project_id=project_id,
        entity_type=entity_type,
    )

    # Get entities of this type
    entity_repo = EntityRepository(db)
    entities = entity_repo.list(filters)

    # Normalize search value for case-insensitive matching
    search_normalized = value.lower().strip()

    # Filter by normalized value and collect source_groups
    matching = [e for e in entities if e.normalized_value == search_normalized]
    source_groups = list(set(e.source_group for e in matching))

    return {
        "entity_type": entity_type,
        "value": value,
        "source_groups": source_groups,
        "total": len(source_groups),
    }


@router.get(
    "/projects/{project_id}/entities/{entity_id}",
    response_model=EntityResponse,
    status_code=status.HTTP_200_OK,
)
async def get_entity(
    project_id: UUID,
    entity_id: UUID,
    db: Session = Depends(get_db),
) -> EntityResponse:
    """Get a single entity by ID."""
    # Get entity
    entity_repo = EntityRepository(db)
    entity = entity_repo.get(entity_id)

    # Check if entity exists and belongs to project
    if not entity or entity.project_id != project_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Entity {entity_id} not found in project {project_id}",
        )

    return EntityResponse(
        id=str(entity.id),
        entity_type=entity.entity_type,
        value=entity.value,
        normalized_value=entity.normalized_value,
        source_group=entity.source_group,
        attributes=entity.attributes,
        created_at=entity.created_at.isoformat(),
    )
