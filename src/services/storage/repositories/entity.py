"""Repository for Entity CRUD operations and entity-extraction linking."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from orm_models import Entity, Extraction, ExtractionEntity


@dataclass
class EntityFilters:
    """Filters for querying entities."""

    project_id: UUID | None = None
    source_group: str | None = None
    entity_type: str | None = None


class EntityRepository:
    """Repository for managing Entity entities and extraction links."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self._session = session

    async def create(
        self,
        project_id: UUID,
        source_group: str,
        entity_type: str,
        value: str,
        normalized_value: str,
        attributes: dict | None = None,
    ) -> Entity:
        """Create a new entity.

        Args:
            project_id: ID of the project
            source_group: Source grouping identifier
            entity_type: Type of entity (company, feature, etc.)
            value: Original entity value
            normalized_value: Normalized value for deduplication
            attributes: Optional attributes dictionary

        Returns:
            Created Entity instance
        """
        entity = Entity(
            project_id=project_id,
            source_group=source_group,
            entity_type=entity_type,
            value=value,
            normalized_value=normalized_value,
            attributes=attributes or {},
        )

        self._session.add(entity)
        self._session.flush()
        return entity

    async def get(self, entity_id: UUID) -> Entity | None:
        """Get entity by ID.

        Args:
            entity_id: Entity UUID

        Returns:
            Entity instance or None if not found
        """
        result = self._session.execute(select(Entity).where(Entity.id == entity_id))
        return result.scalar_one_or_none()

    async def get_or_create(
        self,
        project_id: UUID,
        source_group: str,
        entity_type: str,
        value: str,
        normalized_value: str,
        attributes: dict | None = None,
    ) -> tuple[Entity, bool]:
        """Get existing entity or create new one (deduplication logic).

        Deduplication is scoped by project_id, source_group, entity_type, and normalized_value.
        This prevents duplicates within the same project/source_group/type combination.

        Args:
            project_id: ID of the project
            source_group: Source grouping identifier
            entity_type: Type of entity
            value: Original entity value
            normalized_value: Normalized value for deduplication
            attributes: Optional attributes dictionary

        Returns:
            Tuple of (Entity instance, created flag)
            - created=True if new entity was created
            - created=False if existing entity was returned
        """
        # Try to find existing entity
        result = self._session.execute(
            select(Entity).where(
                and_(
                    Entity.project_id == project_id,
                    Entity.source_group == source_group,
                    Entity.entity_type == entity_type,
                    Entity.normalized_value == normalized_value,
                )
            )
        )
        existing = result.scalar_one_or_none()

        if existing:
            return existing, False

        # Create new entity
        entity = await self.create(
            project_id=project_id,
            source_group=source_group,
            entity_type=entity_type,
            value=value,
            normalized_value=normalized_value,
            attributes=attributes,
        )
        return entity, True

    async def list_by_type(
        self,
        project_id: UUID,
        entity_type: str,
        source_group: str | None = None,
    ) -> list[Entity]:
        """List entities by type, optionally filtered by source_group.

        Args:
            project_id: Project UUID
            entity_type: Type of entity to list
            source_group: Optional source_group filter

        Returns:
            List of Entity instances, sorted by value
        """
        query = select(Entity).where(
            and_(
                Entity.project_id == project_id,
                Entity.entity_type == entity_type,
            )
        )

        if source_group is not None:
            query = query.where(Entity.source_group == source_group)

        # Sort by value for consistent ordering
        query = query.order_by(Entity.value)

        result = self._session.execute(query)
        return list(result.scalars().all())

    async def list(self, filters: EntityFilters) -> list[Entity]:
        """List entities with optional filtering.

        Args:
            filters: EntityFilters instance with filter criteria

        Returns:
            List of Entity instances matching filters, sorted by value
        """
        query = select(Entity)

        # Build filter conditions
        conditions = []
        if filters.project_id is not None:
            conditions.append(Entity.project_id == filters.project_id)
        if filters.source_group is not None:
            conditions.append(Entity.source_group == filters.source_group)
        if filters.entity_type is not None:
            conditions.append(Entity.entity_type == filters.entity_type)

        if conditions:
            query = query.where(and_(*conditions))

        # Sort by value for consistent ordering
        query = query.order_by(Entity.value)

        result = self._session.execute(query)
        return list(result.scalars().all())

    async def link_to_extraction(
        self,
        extraction_id: UUID,
        entity_id: UUID,
        role: str = "mention",
    ) -> tuple[ExtractionEntity, bool]:
        """Create or get existing link between an entity and an extraction.

        This method is idempotent - calling it multiple times with the same
        parameters will return the existing link without causing duplicate
        key violations.

        Args:
            extraction_id: Extraction UUID
            entity_id: Entity UUID
            role: Role of the entity in the extraction (e.g., "mention", "subject", "pricing_detail")

        Returns:
            Tuple of (ExtractionEntity link, created flag)
            - created=True if new link was created
            - created=False if existing link was returned
        """
        # Check for existing link to avoid UniqueViolation on retry
        existing = self._session.execute(
            select(ExtractionEntity).where(
                and_(
                    ExtractionEntity.extraction_id == extraction_id,
                    ExtractionEntity.entity_id == entity_id,
                    ExtractionEntity.role == role,
                )
            )
        ).scalar_one_or_none()

        if existing:
            return existing, False

        # Create new link
        link = ExtractionEntity(
            extraction_id=extraction_id,
            entity_id=entity_id,
            role=role,
        )

        self._session.add(link)
        self._session.flush()
        return link, True

    async def get_entities_for_extraction(self, extraction_id: UUID) -> list[Entity]:
        """Get all entities linked to an extraction.

        Args:
            extraction_id: Extraction UUID

        Returns:
            List of Entity instances linked to the extraction
        """
        # Query with join through ExtractionEntity
        query = (
            select(Entity)
            .join(ExtractionEntity, Entity.id == ExtractionEntity.entity_id)
            .where(ExtractionEntity.extraction_id == extraction_id)
            .order_by(Entity.value)
        )

        result = self._session.execute(query)
        return list(result.scalars().all())

    async def get_extractions_for_entity(self, entity_id: UUID) -> list[Extraction]:
        """Get all extractions linked to an entity.

        Args:
            entity_id: Entity UUID

        Returns:
            List of Extraction instances linked to the entity
        """
        # Query with join through ExtractionEntity
        query = (
            select(Extraction)
            .join(
                ExtractionEntity,
                Extraction.id == ExtractionEntity.extraction_id,
            )
            .where(ExtractionEntity.entity_id == entity_id)
            .order_by(Extraction.created_at.desc())
        )

        result = self._session.execute(query)
        return list(result.scalars().all())
