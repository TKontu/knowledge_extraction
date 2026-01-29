"""Reusable FastAPI dependencies for API endpoints."""

from uuid import UUID

from fastapi import Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from orm_models import Project
from qdrant_connection import qdrant_client
from redis_client import get_async_redis
from services.dlq.service import DLQService
from services.projects.repository import ProjectRepository
from services.storage.qdrant.repository import QdrantRepository


async def get_project_or_404(
    project_id: UUID,
    db: Session = Depends(get_db),
) -> Project:
    """Validate that a project exists and return it.

    Args:
        project_id: UUID of the project to validate.
        db: Database session.

    Returns:
        The Project instance if it exists.

    Raises:
        HTTPException: 404 if the project does not exist.
    """
    project_repo = ProjectRepository(db)
    project = await project_repo.get(project_id)
    if not project:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Project {project_id} not found",
        )
    return project


async def get_dlq_service() -> DLQService:
    """Get DLQ service instance.

    Returns:
        DLQService instance with async Redis connection.
    """
    redis = await get_async_redis()
    return DLQService(redis)


def get_qdrant_repository() -> QdrantRepository:
    """Get QdrantRepository instance.

    Returns:
        QdrantRepository instance with the global Qdrant client.
    """
    return QdrantRepository(qdrant_client)
