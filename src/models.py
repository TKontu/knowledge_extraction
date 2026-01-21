"""Data models for API requests and responses."""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ScrapeRequest(BaseModel):
    """Request body for scrape endpoint."""

    urls: list[str] = Field(
        ...,
        min_length=1,
        description="List of URLs to scrape",
    )
    project_id: UUID = Field(
        ...,
        description="Project ID to associate scraped sources with",
    )
    company: str = Field(
        ...,
        min_length=1,
        description="Company name for the scraped content (used as source_group)",
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
    project_id: str = Field(
        ...,
        description="Project ID for the scraped sources",
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


class CrawlRequest(BaseModel):
    """Request body for crawl endpoint."""

    url: str = Field(..., description="Starting URL to crawl from")
    project_id: UUID = Field(..., description="Project ID for sources")
    company: str = Field(..., min_length=1, description="Source group name")
    max_depth: int = Field(default=2, ge=1, le=10, description="Crawl depth")
    limit: int = Field(default=100, ge=1, le=1000, description="Max pages")
    include_paths: list[str] | None = Field(
        default=None, description="URL patterns to include"
    )
    exclude_paths: list[str] | None = Field(
        default=None, description="URL patterns to exclude"
    )
    allow_backward_links: bool = Field(
        default=False, description="Allow parent/sibling URLs"
    )
    auto_extract: bool = Field(default=True, description="Auto-trigger extraction")
    profile: str | None = Field(default=None, description="Extraction profile")
    prefer_english_only: bool = Field(
        default=True,
        description="Filter non-English pages (URL patterns + content detection)",
    )
    language_detection_enabled: bool = Field(
        default=True,
        description="Enable content-based language detection (post-crawl)",
    )
    allowed_languages: list[str] | None = Field(
        default=None,
        description="ISO 639-1 codes of allowed languages (default: ['en'])",
    )


class CrawlResponse(BaseModel):
    """Response body for crawl endpoint."""

    job_id: str
    status: str = "queued"
    url: str
    max_depth: int
    limit: int
    project_id: str
    company: str


class CrawlStatusResponse(BaseModel):
    """Response for crawl job status."""

    job_id: str
    status: str  # queued, running, completed, failed
    url: str
    pages_total: int | None = None
    pages_completed: int | None = None
    sources_created: int | None = None
    error: str | None = None
    created_at: str
    completed_at: str | None = None


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
        description="Source configuration",
    )
    extraction_schema: dict = Field(..., description="JSONB extraction schema")
    entity_types: list = Field(
        default=[], description="List of entity type definitions"
    )
    prompt_templates: dict = Field(default={}, description="Custom prompt templates")
    is_template: bool = Field(default=False, description="Whether this is a template")


class ProjectUpdate(BaseModel):
    """Request model for updating an existing project."""

    name: str | None = Field(None, min_length=1, description="Updated project name")
    description: str | None = Field(None, description="Updated description")
    source_config: dict | None = Field(None, description="Updated source configuration")
    extraction_schema: dict | None = Field(
        None, description="Updated extraction schema"
    )
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

    model_config = ConfigDict(from_attributes=True)


class ProjectFromTemplate(BaseModel):
    """Request model for creating a project from a template."""

    template: str = Field(..., description="Template name to clone from")
    name: str = Field(..., min_length=1, description="New project name")
    description: str | None = Field(None, description="Project description")
    customizations: dict = Field(default={}, description="Override specific fields")


# Search API models


class SearchRequest(BaseModel):
    """Request body for search endpoint."""

    query: str = Field(..., min_length=1, max_length=1000, description="Search query")
    limit: int = Field(default=10, ge=1, le=100, description="Max results")
    source_groups: list[str] | None = Field(
        default=None, description="Filter by source groups"
    )
    filters: dict[str, Any] | None = Field(default=None, description="JSONB filters")


class SearchResultItem(BaseModel):
    """Single search result."""

    extraction_id: str
    score: float
    data: dict[str, Any]
    source_group: str
    source_uri: str
    confidence: float | None


class SearchResponse(BaseModel):
    """Response for search endpoint."""

    results: list[SearchResultItem]
    query: str
    total: int


# Entity API models


class EntityResponse(BaseModel):
    """Single entity in response."""

    id: str = Field(..., description="Entity UUID")
    entity_type: str = Field(..., description="Entity type")
    value: str = Field(..., description="Original entity value")
    normalized_value: str = Field(..., description="Normalized value for matching")
    source_group: str = Field(..., description="Source group identifier")
    attributes: dict[str, Any] = Field(..., description="Entity attributes")
    created_at: str = Field(..., description="Creation timestamp")


class EntityListResponse(BaseModel):
    """Response for entity list endpoint."""

    entities: list[EntityResponse] = Field(..., description="List of entities")
    total: int = Field(..., description="Total count of entities")
    limit: int = Field(..., description="Page size")
    offset: int = Field(..., description="Pagination offset")


class EntityTypeCount(BaseModel):
    """Count of entities per type."""

    entity_type: str = Field(..., description="Entity type name")
    count: int = Field(..., description="Number of entities of this type")


class EntityTypesResponse(BaseModel):
    """Response for entity types summary."""

    types: list[EntityTypeCount] = Field(..., description="List of type counts")
    total_entities: int = Field(..., description="Total number of entities")


# Report API models


class ReportType(str, Enum):
    """Types of reports that can be generated."""

    SINGLE = "single"
    COMPARISON = "comparison"
    TABLE = "table"
    SCHEMA_TABLE = "schema_table"


class ReportRequest(BaseModel):
    """Request to generate a report."""

    type: ReportType
    source_groups: list[str] = Field(
        ..., min_length=1, description="Source groups to include"
    )
    entity_types: list[str] | None = Field(
        default=None, description="Entity types for comparison tables"
    )
    categories: list[str] | None = Field(
        default=None, description="Filter by extraction categories"
    )
    title: str | None = Field(default=None, description="Custom report title")
    max_extractions: int = Field(
        default=50, ge=1, le=200, description="Max extractions per source_group"
    )
    columns: list[str] | None = Field(
        default=None,
        description="Specific field names to include as columns (None = all fields)",
    )
    output_format: Literal["md", "xlsx"] = Field(
        default="md", description="Output format for TABLE reports"
    )

    @field_validator("source_groups")
    @classmethod
    def validate_comparison_needs_multiple(cls, v, info):
        """Validate comparison reports require at least 2 source_groups."""
        if info.data.get("type") == ReportType.COMPARISON and len(v) < 2:
            raise ValueError("Comparison reports require at least 2 source_groups")
        return v


class ReportResponse(BaseModel):
    """Response with generated report."""

    id: str
    type: str
    title: str
    content: str  # Markdown content
    source_groups: list[str]
    extraction_count: int
    entity_count: int
    generated_at: str


class ReportJobResponse(BaseModel):
    """Response when report job is created."""

    job_id: str
    status: str
    report_id: str | None = None


# Job API models


class JobSummary(BaseModel):
    """Summary of a job for list views."""

    id: str
    type: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None


class JobListResponse(BaseModel):
    """Response for job list endpoint."""

    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int


class JobDetailResponse(BaseModel):
    """Detailed job information."""

    id: str
    type: str
    status: str
    payload: dict
    result: dict | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
