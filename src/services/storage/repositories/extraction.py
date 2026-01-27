"""Repository for Extraction CRUD operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from orm_models import Extraction


@dataclass
class ExtractionFilters:
    """Filters for querying extractions."""

    project_id: UUID | None = None
    source_id: UUID | None = None
    extraction_type: str | None = None
    source_group: str | None = None
    min_confidence: float | None = None
    max_confidence: float | None = None


class ExtractionRepository:
    """Repository for managing Extraction entities."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self._session = session

    async def create(
        self,
        project_id: UUID,
        source_id: UUID,
        data: dict,
        extraction_type: str,
        source_group: str,
        confidence: float | None = None,
        profile_used: str | None = None,
        chunk_index: int | None = None,
        chunk_context: dict | None = None,
        embedding_id: str | None = None,
    ) -> Extraction:
        """Create a new extraction.

        Args:
            project_id: ID of the project
            source_id: ID of the source
            data: JSONB data validated against project schema
            extraction_type: Type of extraction (technical_fact, etc.)
            source_group: Source grouping identifier
            confidence: Optional confidence score (0.0-1.0)
            profile_used: Optional profile name used for extraction
            chunk_index: Optional chunk index in source document
            chunk_context: Optional context around the chunk
            embedding_id: Optional vector embedding ID

        Returns:
            Created Extraction instance
        """
        extraction = Extraction(
            project_id=project_id,
            source_id=source_id,
            data=data,
            extraction_type=extraction_type,
            source_group=source_group,
            confidence=confidence,
            profile_used=profile_used,
            chunk_index=chunk_index,
            chunk_context=chunk_context or {},
            embedding_id=embedding_id,
        )

        self._session.add(extraction)
        self._session.flush()
        return extraction

    async def create_batch(self, extractions: list[dict]) -> list[Extraction]:
        """Create multiple extractions in batch.

        Args:
            extractions: List of extraction data dictionaries

        Returns:
            List of created Extraction instances
        """
        if not extractions:
            return []

        extraction_objs = []
        for ext_data in extractions:
            extraction = Extraction(
                project_id=ext_data["project_id"],
                source_id=ext_data["source_id"],
                data=ext_data["data"],
                extraction_type=ext_data["extraction_type"],
                source_group=ext_data["source_group"],
                confidence=ext_data.get("confidence"),
                profile_used=ext_data.get("profile_used"),
                chunk_index=ext_data.get("chunk_index"),
                chunk_context=ext_data.get("chunk_context", {}),
                embedding_id=ext_data.get("embedding_id"),
            )
            extraction_objs.append(extraction)

        self._session.add_all(extraction_objs)
        self._session.flush()
        return extraction_objs

    async def get(self, extraction_id: UUID) -> Extraction | None:
        """Get extraction by ID.

        Args:
            extraction_id: Extraction UUID

        Returns:
            Extraction instance or None if not found
        """
        result = self._session.execute(
            select(Extraction).where(Extraction.id == extraction_id)
        )
        return result.scalar_one_or_none()

    async def get_by_source(self, source_id: UUID) -> list[Extraction]:
        """Get all extractions for a source.

        Args:
            source_id: Source UUID

        Returns:
            List of Extraction instances for the source, sorted by created_at desc
        """
        result = self._session.execute(
            select(Extraction)
            .where(Extraction.source_id == source_id)
            .order_by(Extraction.created_at.desc())
        )
        return list(result.scalars().all())

    async def list(
        self,
        filters: ExtractionFilters,
        limit: int | None = None,
        offset: int = 0,
        include_source: bool = False,
    ) -> list[Extraction]:
        """List extractions with optional filtering.

        Args:
            filters: ExtractionFilters instance with filter criteria
            limit: Maximum number of results to return (None for no limit)
            offset: Number of results to skip
            include_source: If True, eager-load source relationship

        Returns:
            List of Extraction instances matching filters, sorted by created_at desc
        """
        from sqlalchemy.orm import joinedload

        query = select(Extraction)

        # Eager-load source if requested
        if include_source:
            query = query.options(joinedload(Extraction.source))

        # Build filter conditions
        conditions = []
        if filters.project_id is not None:
            conditions.append(Extraction.project_id == filters.project_id)
        if filters.source_id is not None:
            conditions.append(Extraction.source_id == filters.source_id)
        if filters.extraction_type is not None:
            conditions.append(Extraction.extraction_type == filters.extraction_type)
        if filters.source_group is not None:
            conditions.append(Extraction.source_group == filters.source_group)
        if filters.min_confidence is not None:
            conditions.append(Extraction.confidence >= filters.min_confidence)
        if filters.max_confidence is not None:
            conditions.append(Extraction.confidence <= filters.max_confidence)

        if conditions:
            query = query.where(and_(*conditions))

        # Sort by created_at descending (most recent first)
        query = query.order_by(Extraction.created_at.desc())

        # Apply pagination
        if offset > 0:
            query = query.offset(offset)
        if limit is not None:
            query = query.limit(limit)

        result = self._session.execute(query)
        return list(result.scalars().all())

    async def query_jsonb(
        self, project_id: UUID, path: str, value: Any
    ) -> list[Extraction]:
        """Query extractions by JSONB path and value.

        Args:
            project_id: Project UUID to scope the query
            path: JSON path (dot notation, e.g., 'category' or 'metadata.verified')
            value: Value to match

        Returns:
            List of Extraction instances matching the JSONB query
        """
        from sqlalchemy import func, text

        # Determine database dialect
        try:
            dialect_name = self._session.bind.dialect.name
        except AttributeError:
            # Fallback if bind is None (some test scenarios)
            dialect_name = "sqlite"

        if dialect_name == "postgresql":
            # PostgreSQL: Use #>> operator for JSONB text extraction
            # Split path: 'category' -> ['category'], 'metadata.verified' -> ['metadata', 'verified']
            path_parts = path.split(".")

            # Create JSONB path array: {category} or {metadata,verified}
            path_array = "{" + ",".join(path_parts) + "}"

            # Use #>> operator which extracts as text
            json_expr = Extraction.data.op("#>>")(text(f"'{path_array}'"))

            # Convert value to string for comparison
            if isinstance(value, bool):
                value_str = "true" if value else "false"
            elif value is None:
                value_str = "null"
            else:
                value_str = str(value)

            query = select(Extraction).where(
                and_(
                    Extraction.project_id == project_id,
                    json_expr == value_str,
                )
            )
        else:
            # SQLite: Use json_extract
            json_path = "$." + path
            json_extract = func.json_extract(Extraction.data, json_path)

            query = select(Extraction).where(
                and_(
                    Extraction.project_id == project_id,
                    json_extract == value,
                )
            )

        query = query.order_by(Extraction.created_at.desc())
        result = self._session.execute(query)
        return list(result.scalars().all())

    async def filter_by_data(self, project_id: UUID, filters: dict) -> list[Extraction]:
        """Filter extractions by multiple JSONB data fields.

        Args:
            project_id: Project UUID to scope the query
            filters: Dictionary of field:value pairs to match in data JSONB

        Returns:
            List of Extraction instances matching all filters
        """
        if not filters:
            # No filters, return all for project
            return await self.list(ExtractionFilters(project_id=project_id))

        from sqlalchemy import func, text

        # Determine database dialect
        try:
            dialect_name = self._session.bind.dialect.name
        except AttributeError:
            dialect_name = "sqlite"

        query = select(Extraction).where(Extraction.project_id == project_id)

        if dialect_name == "postgresql":
            # PostgreSQL: Use #>> operator for each field
            for field, value in filters.items():
                # Single field path: {field}
                path_array = f"{{{field}}}"
                json_expr = Extraction.data.op("#>>")(text(f"'{path_array}'"))

                # Convert value to string for comparison
                if isinstance(value, bool):
                    value_str = "true" if value else "false"
                elif value is None:
                    value_str = "null"
                else:
                    value_str = str(value)

                query = query.where(json_expr == value_str)
        else:
            # SQLite: Use json_extract for each field
            for field, value in filters.items():
                json_path = f"$.{field}"
                json_extract = func.json_extract(Extraction.data, json_path)
                query = query.where(json_extract == value)

        query = query.order_by(Extraction.created_at.desc())
        result = self._session.execute(query)
        return list(result.scalars().all())

    async def update_entities_extracted(
        self, extraction_id: UUID, entities_extracted: bool = True
    ) -> None:
        """Update the entities_extracted flag for an extraction.

        Args:
            extraction_id: Extraction UUID
            entities_extracted: Flag indicating if entities were successfully extracted
        """
        extraction = await self.get(extraction_id)
        if extraction:
            extraction.entities_extracted = entities_extracted
            self._session.flush()
