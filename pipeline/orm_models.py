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
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
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
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        else:
            return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return value
        else:
            if isinstance(value, uuid.UUID):
                return str(value)
            return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
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

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        primary_key=True,
        default=uuid4
    )
    type: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, default="queued")
    priority: Mapped[int] = mapped_column(Integer, default=0)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )

    def __repr__(self) -> str:
        return f"<Job(id={self.id}, type={self.type}, status={self.status})>"


class Page(Base):
    """Page table for storing scraped web pages."""

    __tablename__ = "pages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        primary_key=True,
        default=uuid4
    )
    url: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    domain: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    company: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    markdown_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )
    status: Mapped[str] = mapped_column(Text, default="completed", index=True)
    meta_data: Mapped[dict] = mapped_column(
        "metadata",
        JSON,
        default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC)
    )

    # Relationship to facts
    facts: Mapped[list["Fact"]] = relationship("Fact", back_populates="page", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Page(id={self.id}, url={self.url}, company={self.company})>"


class Fact(Base):
    """Fact table for storing extracted facts from pages."""

    __tablename__ = "facts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        primary_key=True,
        default=uuid4
    )
    page_id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        ForeignKey("pages.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    fact_text: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, index=True)
    profile_used: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    embedding_id: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )
    meta_data: Mapped[dict] = mapped_column(
        "metadata",
        JSON,
        default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )

    # Relationship to page
    page: Mapped["Page"] = relationship("Page", back_populates="facts")

    def __repr__(self) -> str:
        return f"<Fact(id={self.id}, category={self.category}, confidence={self.confidence})>"


class Profile(Base):
    """Profile table for extraction profiles."""

    __tablename__ = "profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        primary_key=True,
        default=uuid4
    )
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    categories: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False
    )
    prompt_focus: Mapped[str] = mapped_column(Text, nullable=False)
    depth: Mapped[str] = mapped_column(Text, nullable=False)
    custom_instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC)
    )

    def __repr__(self) -> str:
        return f"<Profile(id={self.id}, name={self.name}, depth={self.depth})>"


class Report(Base):
    """Report table for generated reports."""

    __tablename__ = "reports"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID,
        primary_key=True,
        default=uuid4
    )
    type: Mapped[str] = mapped_column(Text, nullable=False, index=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    companies: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False
    )
    categories: Mapped[Optional[list[str]]] = mapped_column(
        JSON,
        nullable=True
    )
    fact_ids: Mapped[Optional[list[str]]] = mapped_column(
        JSON,
        nullable=True
    )
    format: Mapped[str] = mapped_column(Text, default="md")
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        index=True
    )
    meta_data: Mapped[dict] = mapped_column(
        "metadata",
        JSON,
        default=dict
    )

    def __repr__(self) -> str:
        return f"<Report(id={self.id}, type={self.type}, title={self.title})>"


class RateLimit(Base):
    """Rate limit table for tracking domain-specific rate limits."""

    __tablename__ = "rate_limits"

    domain: Mapped[str] = mapped_column(Text, primary_key=True)
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    last_request: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True
    )
    daily_count: Mapped[int] = mapped_column(Integer, default=0)
    daily_reset_at: Mapped[date] = mapped_column(
        Date,
        default=lambda: date.today()
    )

    def __repr__(self) -> str:
        return f"<RateLimit(domain={self.domain}, count={self.request_count})>"
