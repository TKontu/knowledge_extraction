"""SQLAlchemy ORM models for database tables."""

from datetime import datetime, UTC, date
from uuid import uuid4

from sqlalchemy import (
    String,
    Text,
    Integer,
    Float,
    Boolean,
    DateTime,
    Date,
    ARRAY,
    JSON,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import TypeDecorator, CHAR
from typing import Optional
import uuid


class UUID(TypeDecorator):
    """Platform-independent UUID type.

    Uses PostgreSQL's UUID type, otherwise uses CHAR(36), storing as stringified hex values.
    """

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return value
        else:
            if isinstance(value, uuid.UUID):
                return str(value)
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == "postgresql":
            return value
        else:
            if isinstance(value, uuid.UUID):
                return value
            return uuid.UUID(value)


class Base(DeclarativeBase):
    """Base class for all ORM models."""

    pass


class Job(Base):
    """Job table for tracking scrape, extraction, and report jobs."""

    __tablename__ = "jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID, ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    def __repr__(self) -> str:
        return f"<Job(id={self.id}, type={self.type}, status={self.status})>"


class Page(Base):
    """Page table for storing scraped web pages."""

    __tablename__ = "pages"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    company: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    status: Mapped[str] = mapped_column(Text, default="completed", index=True)
    meta_data: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationship to facts
    facts: Mapped[list["Fact"]] = relationship(
        "Fact", back_populates="page", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Page(id={self.id}, url={self.url}, company={self.company})>"


class Fact(Base):
    """Fact table for storing extracted facts from pages."""

    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("pages.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    profile_used: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    embedding_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    meta_data: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationship to page
    page: Mapped["Page"] = relationship("Page", back_populates="facts")

    def __repr__(self) -> str:
        return f"<Fact(id={self.id}, category={self.category}, confidence={self.confidence})>"


class Profile(Base):
    """Profile table for extraction profiles."""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    prompt_focus: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[str] = mapped_column(Text, nullable=False)
    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    def __repr__(self) -> str:
        return f"<Profile(id={self.id}, name={self.name}, depth={self.depth})>"


class Report(Base):
    """Report table for generated reports."""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    project_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=True
    )
    type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_groups: Mapped[list] = mapped_column(JSON, default=list)
    categories: Mapped[list] = mapped_column(JSON, default=list)
    extraction_ids: Mapped[list] = mapped_column(JSON, default=list)
    format: Mapped[str] = mapped_column(Text, default="md")
    meta_data: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<Report(id={self.id}, type={self.type}, title={self.title})>"


class RateLimit(Base):
    """Rate limit table for tracking domain-specific rate limits."""

    __tablename__ = "rate_limits"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    last_request: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    daily_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_reset_at: Mapped[date] = mapped_column(Date, default=lambda: date.today())

    def __repr__(self) -> str:
        return f"<RateLimit(domain={self.domain}, count={self.request_count})>"


# ===================
# Generalized Schema Models
# ===================


class Project(Base):
    """Project table for multi-domain extraction configurations."""

    __tablename__ = "projects"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Configuration stored as JSONB (uses JSON for cross-DB compatibility)
    source_config: Mapped[dict] = mapped_column(
        JSON, nullable=False, default=lambda: {"type": "web", "group_by": "company"}
    )
    extraction_schema: Mapped[dict] = mapped_column(JSON, nullable=False)
    entity_types: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    prompt_templates: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)

    # Settings
    is_template: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    sources: Mapped[list["Source"]] = relationship(
        "Source", back_populates="project", cascade="all, delete-orphan"
    )
    extractions: Mapped[list["Extraction"]] = relationship(
        "Extraction", back_populates="project", cascade="all, delete-orphan"
    )
    entities: Mapped[list["Entity"]] = relationship(
        "Entity", back_populates="project", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name={self.name})>"


class Source(Base):
    """Source table for generalized document sources."""

    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )

    source_type: Mapped[str] = mapped_column(Text, nullable=False, default="web")
    uri: Mapped[str] = mapped_column(Text, nullable=False)
    source_group: Mapped[str] = mapped_column(Text, nullable=False)

    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    meta_data: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    outbound_links: Mapped[list] = mapped_column(JSON, default=list)

    status: Mapped[str] = mapped_column(Text, default="pending")
    fetched_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="sources")
    extractions: Mapped[list["Extraction"]] = relationship(
        "Extraction", back_populates="source", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return (
            f"<Source(id={self.id}, uri={self.uri}, source_group={self.source_group})>"
        )


class Extraction(Base):
    """Extraction table for generalized extracted data."""

    __tablename__ = "extractions"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )

    # Dynamic data validated against project schema
    data: Mapped[dict] = mapped_column(JSON, nullable=False)

    # Denormalized for indexing/queries
    extraction_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_group: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Provenance
    profile_used: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chunk_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    chunk_context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    # Vector reference
    embedding_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="extractions")
    source: Mapped["Source"] = relationship("Source", back_populates="extractions")
    entity_links: Mapped[list["ExtractionEntity"]] = relationship(
        "ExtractionEntity", back_populates="extraction", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Extraction(id={self.id}, type={self.extraction_type}, confidence={self.confidence})>"


class Entity(Base):
    """Entity table for project-scoped entity recognition."""

    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_group: Mapped[str] = mapped_column(Text, nullable=False)

    entity_type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    normalized_value: Mapped[str] = mapped_column(Text, nullable=False)
    attributes: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="entities")
    extraction_links: Mapped[list["ExtractionEntity"]] = relationship(
        "ExtractionEntity", back_populates="entity", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Entity(id={self.id}, type={self.entity_type}, value={self.value})>"


class ExtractionEntity(Base):
    """Junction table linking extractions to entities."""

    __tablename__ = "extraction_entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("extractions.id", ondelete="CASCADE"), nullable=False
    )
    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(Text, default="mention")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    # Relationships
    extraction: Mapped["Extraction"] = relationship(
        "Extraction", back_populates="entity_links"
    )
    entity: Mapped["Entity"] = relationship("Entity", back_populates="extraction_links")

    def __repr__(self) -> str:
        return f"<ExtractionEntity(extraction_id={self.extraction_id}, entity_id={self.entity_id}, role={self.role})>"
