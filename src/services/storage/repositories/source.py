"""Repository for Source CRUD operations."""

from dataclasses import dataclass
from datetime import datetime, UTC
from typing import List, Optional
from uuid import UUID
from sqlalchemy.orm import Session
from sqlalchemy import select
from orm_models import Source


@dataclass
class SourceFilters:
    """Filters for querying sources."""

    project_id: Optional[UUID] = None
    source_group: Optional[str] = None
    source_type: Optional[str] = None
    status: Optional[str] = None


class SourceRepository:
    """Repository for managing Source entities."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self._session = session

    async def create(
        self,
        project_id: UUID,
        uri: str,
        source_group: str,
        source_type: str = "web",
        title: Optional[str] = None,
        content: Optional[str] = None,
        raw_content: Optional[str] = None,
        meta_data: Optional[dict] = None,
        outbound_links: Optional[list] = None,
        status: str = "pending",
    ) -> Source:
        """Create a new source.

        Args:
            project_id: ID of the project this source belongs to
            uri: URI of the source (URL, file path, etc.)
            source_group: Grouping identifier (company, paper, contract)
            source_type: Type of source (web, pdf, api, etc.)
            title: Optional title of the source
            content: Optional processed content
            raw_content: Optional raw content
            meta_data: Optional metadata dictionary
            outbound_links: Optional list of outbound links
            status: Source status (pending, processing, completed, failed)

        Returns:
            Created Source instance
        """
        source = Source(
            project_id=project_id,
            uri=uri,
            source_group=source_group,
            source_type=source_type,
            title=title,
            content=content,
            raw_content=raw_content,
            meta_data=meta_data or {},
            outbound_links=outbound_links or [],
            status=status,
        )

        self._session.add(source)
        self._session.flush()
        return source

    async def get(self, source_id: UUID) -> Optional[Source]:
        """Get source by ID.

        Args:
            source_id: Source UUID

        Returns:
            Source instance or None if not found
        """
        result = self._session.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one_or_none()

    async def get_by_uri(self, project_id: UUID, uri: str) -> Optional[Source]:
        """Get source by URI within a project.

        Args:
            project_id: Project UUID
            uri: Source URI

        Returns:
            Source instance or None if not found
        """
        result = self._session.execute(
            select(Source).where(
                Source.project_id == project_id,
                Source.uri == uri,
            )
        )
        return result.scalar_one_or_none()

    async def list(self, filters: SourceFilters) -> list[Source]:
        """List sources with optional filtering.

        Args:
            filters: SourceFilters instance with filter criteria

        Returns:
            List of Source instances matching filters, sorted by created_at desc
        """
        query = select(Source)

        # Apply filters
        if filters.project_id is not None:
            query = query.where(Source.project_id == filters.project_id)
        if filters.source_group is not None:
            query = query.where(Source.source_group == filters.source_group)
        if filters.source_type is not None:
            query = query.where(Source.source_type == filters.source_type)
        if filters.status is not None:
            query = query.where(Source.status == filters.status)

        # Sort by created_at descending (most recent first)
        query = query.order_by(Source.created_at.desc())

        result = self._session.execute(query)
        return list(result.scalars().all())

    async def update_status(self, source_id: UUID, status: str) -> Optional[Source]:
        """Update source status.

        Args:
            source_id: Source UUID
            status: New status value

        Returns:
            Updated Source instance or None if not found
        """
        source = await self.get(source_id)
        if source is None:
            return None

        source.status = status

        # Set fetched_at when completed
        if status == "completed" and source.fetched_at is None:
            source.fetched_at = datetime.now(UTC)

        self._session.flush()
        return source

    async def get_by_project_and_status(
        self, project_id: UUID, status: str
    ) -> List[Source]:
        """Get sources by project ID and status.

        Args:
            project_id: Project UUID
            status: Source status (pending, processing, completed, failed)

        Returns:
            List of Source instances matching the criteria
        """
        result = self._session.execute(
            select(Source)
            .where(Source.project_id == project_id, Source.status == status)
            .order_by(Source.created_at.asc())
        )
        return list(result.scalars().all())

    async def update_content(
        self,
        source_id: UUID,
        content: str,
        title: str,
        raw_content: Optional[str] = None,
        outbound_links: Optional[list] = None,
    ) -> Optional[Source]:
        """Update source content fields.

        Args:
            source_id: Source UUID
            content: Processed content
            title: Source title
            raw_content: Optional raw content
            outbound_links: Optional list of outbound links

        Returns:
            Updated Source instance or None if not found
        """
        source = await self.get(source_id)
        if source is None:
            return None

        source.content = content
        source.title = title

        if raw_content is not None:
            source.raw_content = raw_content
        if outbound_links is not None:
            source.outbound_links = outbound_links

        self._session.flush()
        return source
