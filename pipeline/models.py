"""Data models for API requests and responses."""

from dataclasses import dataclass
from datetime import datetime, UTC
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class ScrapeRequest(BaseModel):
    """Request body for scrape endpoint."""

    urls: list[str] = Field(
        ...,
        min_length=1,
        description="List of URLs to scrape",
    )
    company: str = Field(
        ...,
        min_length=1,
        description="Company name for the scraped content",
    )
    profile: str | None = Field(
        default=None,
        description="Extraction profile to use (optional)",
    )

    @field_validator("urls")
    @classmethod
    def validate_urls_not_empty(cls, v: list[str]) -> list[str]:
        """Ensure URLs list is not empty."""
        if not v:
            raise ValueError("urls list cannot be empty")
        return v


class ScrapeResponse(BaseModel):
    """Response body for scrape endpoint."""

    job_id: str = Field(
        ...,
        description="Unique job identifier",
    )
    status: str = Field(
        default="queued",
        description="Job status",
    )
    url_count: int = Field(
        ...,
        description="Number of URLs in the job",
    )
    company: str = Field(
        ...,
        description="Company name",
    )
    profile: str | None = Field(
        default=None,
        description="Extraction profile (if specified)",
    )

    @staticmethod
    def create(request: ScrapeRequest) -> "ScrapeResponse":
        """Create a response from a scrape request."""
        return ScrapeResponse(
            job_id=str(uuid4()),
            status="queued",
            url_count=len(request.urls),
            company=request.company,
            profile=request.profile,
        )


class JobStatusResponse(BaseModel):
    """Response body for job status endpoint."""

    job_id: str = Field(
        ...,
        description="Unique job identifier",
    )
    status: str = Field(
        ...,
        description="Job status (queued, running, completed, failed)",
    )
    company: str = Field(
        ...,
        description="Company name",
    )
    url_count: int = Field(
        ...,
        description="Number of URLs in the job",
    )
    profile: str | None = Field(
        default=None,
        description="Extraction profile (if specified)",
    )
    created_at: str = Field(
        ...,
        description="Job creation timestamp",
    )
    urls: list[str] | None = Field(
        default=None,
        description="List of URLs (optional, for completed jobs)",
    )
    error: str | None = Field(
        default=None,
        description="Error message (if failed)",
    )


# LLM-related data models


@dataclass
class DocumentChunk:
    """A chunk of a document for processing."""

    content: str
    chunk_index: int
    total_chunks: int
    header_path: list[str] | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class ExtractedFact:
    """A fact extracted from content by LLM."""

    fact: str
    category: str
    confidence: float
    source_quote: str | None = None
    header_context: str | None = None


@dataclass
class ExtractionResult:
    """Result of extracting facts from a page."""

    page_id: UUID
    facts: list[ExtractedFact]
    chunks_processed: int
    extraction_time_ms: int


# LLM-related data models


@dataclass
class DocumentChunk:
    """A chunk of a document for processing."""

    content: str
    chunk_index: int
    total_chunks: int
    header_path: list[str] | None = None
    start_line: int | None = None
    end_line: int | None = None


@dataclass
class ExtractedFact:
    """A fact extracted from content by LLM."""

    fact: str
    category: str
    confidence: float
    source_quote: str | None = None
    header_context: str | None = None


@dataclass
class ExtractionResult:
    """Result of extracting facts from a page."""

    page_id: UUID
    facts: list[ExtractedFact]
    chunks_processed: int
    extraction_time_ms: int
