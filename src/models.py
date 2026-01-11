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


@dataclass
class ExtractionProfile:
    """Configuration profile for fact extraction."""

    name: str
    categories: list[str]
    prompt_focus: str
    depth: str  # "summary", "detailed", or "comprehensive"
    custom_instructions: str | None = None
    is_builtin: bool = False


# Extraction API models


class ExtractRequest(BaseModel):
    """Request body for extraction endpoint."""

    source_ids: list[str] | None = Field(
        default=None,
        description="Optional list of source UUIDs to extract from. If omitted, extracts from all pending sources.",
    )
    profile: str | None = Field(
        default=None,
        description="Optional extraction profile/depth",
    )


class ExtractResponse(BaseModel):
    """Response body for extraction endpoint."""

    job_id: str = Field(
        ...,
        description="Unique job identifier",
    )
    status: str = Field(
        default="queued",
        description="Job status",
    )
    source_count: int = Field(
        ...,
        description="Number of sources to extract from",
    )
    project_id: str = Field(
        ...,
        description="Project identifier",
    )


class ExtractionListResponse(BaseModel):
    """Response body for listing extractions."""

    extractions: list[dict] = Field(
        ...,
        description="List of extraction objects",
    )
    total: int = Field(
        ...,
        description="Total count of extractions matching filters",
    )
    limit: int = Field(
        ...,
        description="Page size",
    )
    offset: int = Field(
        ...,
        description="Pagination offset",
    )


# Project-related Pydantic models for API


class ProjectCreate(BaseModel):
    """Request model for creating a new project."""

    name: str = Field(..., min_length=1, description="Unique project name")
    description: str | None = Field(None, description="Project description")
    source_config: dict = Field(
        default={"type": "web", "group_by": "company"},
        description="Source configuration"
    )
    extraction_schema: dict = Field(..., description="JSONB extraction schema")
    entity_types: list = Field(default=[], description="List of entity type definitions")
    prompt_templates: dict = Field(default={}, description="Custom prompt templates")
    is_template: bool = Field(default=False, description="Whether this is a template")


class ProjectUpdate(BaseModel):
    """Request model for updating an existing project."""

    name: str | None = Field(None, min_length=1, description="Updated project name")
    description: str | None = Field(None, description="Updated description")
    source_config: dict | None = Field(None, description="Updated source configuration")
    extraction_schema: dict | None = Field(None, description="Updated extraction schema")
    entity_types: list | None = Field(None, description="Updated entity types")
    prompt_templates: dict | None = Field(None, description="Updated prompt templates")
    is_active: bool | None = Field(None, description="Updated active status")


class ProjectResponse(BaseModel):
    """Response model for project data."""

    id: UUID = Field(..., description="Project UUID")
    name: str = Field(..., description="Project name")
    description: str | None = Field(None, description="Project description")
    source_config: dict = Field(..., description="Source configuration")
    extraction_schema: dict = Field(..., description="Extraction schema")
    entity_types: list = Field(..., description="Entity types")
    prompt_templates: dict = Field(..., description="Prompt templates")
    is_template: bool = Field(..., description="Is template flag")
    is_active: bool = Field(..., description="Is active flag")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")

    class Config:
        from_attributes = True


class ProjectFromTemplate(BaseModel):
    """Request model for creating a project from a template."""

    template: str = Field(..., description="Template name to clone from")
    name: str = Field(..., min_length=1, description="New project name")
    description: str | None = Field(None, description="Project description")
    customizations: dict = Field(default={}, description="Override specific fields")
