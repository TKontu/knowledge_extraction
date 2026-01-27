"""Repository for Source CRUD operations."""

from __future__ import annotations

import builtins
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orm_models import Source


@dataclass
class SourceFilters:
    """Filters for querying sources."""

    project_id: UUID | None = None
    source_group: str | None = None
    source_type: str | None = None
    status: str | None = None


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
        title: str | None = None,
        content: str | None = None,
        raw_content: str | None = None,
        meta_data: dict | None = None,
        outbound_links: list | None = None,
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

    async def get(self, source_id: UUID) -> Source | None:
        """Get source by ID.

        Args:
            source_id: Source UUID

        Returns:
            Source instance or None if not found
        """
        result = self._session.execute(select(Source).where(Source.id == source_id))
        return result.scalar_one_or_none()

    async def get_by_uri(self, project_id: UUID, uri: str) -> Source | None:
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

    async def update_status(self, source_id: UUID, status: str) -> Source | None:
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
    ) -> builtins.list[Source]:
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
        raw_content: str | None = None,
        outbound_links: list | None = None,
    ) -> Source | None:
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

    async def upsert(
        self,
        project_id: UUID,
        uri: str,
        source_group: str,
        source_type: str = "web",
        title: str | None = None,
        content: str | None = None,
        raw_content: str | None = None,
        meta_data: dict | None = None,
        outbound_links: list | None = None,
        status: str = "pending",
        created_by_job_id: UUID | None = None,
    ) -> tuple[Source, bool]:
        """Insert or update source based on (project_id, uri) unique constraint.

        Uses PostgreSQL's ON CONFLICT DO UPDATE to handle race conditions
        when concurrent crawlers process the same URL.

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
            created_by_job_id: Optional ID of the job that created this source

        Returns:
            Tuple of (Source instance, created) where created is True if new,
            False if existing record was updated.
        """
        values = {
            "project_id": project_id,
            "uri": uri,
            "source_group": source_group,
            "source_type": source_type,
            "title": title,
            "content": content,
            "raw_content": raw_content,
            "meta_data": meta_data or {},
            "outbound_links": outbound_links or [],
            "status": status,
            "created_by_job_id": created_by_job_id,
        }

        # PostgreSQL INSERT ... ON CONFLICT DO UPDATE
        stmt = pg_insert(Source).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_sources_project_uri",
            set_={
                Source.title: stmt.excluded.title,
                Source.content: stmt.excluded.content,
                Source.raw_content: stmt.excluded.raw_content,
                Source.meta_data: stmt.excluded.metadata,  # Fixed: use db column name
                Source.outbound_links: stmt.excluded.outbound_links,
                # Don't update status on conflict - keep existing status
            },
        ).returning(Source.id)

        result = self._session.execute(stmt)
        source_id = result.scalar_one()

        # Get the source to return
        source = await self.get(source_id)

        # Check if it was a create or update by checking created_at
        # If the source was just created, its created_at will be very recent
        created = (datetime.now(UTC) - source.created_at).total_seconds() < 1

        self._session.flush()
        return source, created
