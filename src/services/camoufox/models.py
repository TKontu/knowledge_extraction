"""Request/Response models for Camoufox service.

These models match the exact Firecrawl Playwright service API contract.
"""

from pydantic import BaseModel, Field


class ScrapeRequest(BaseModel):
    """Request model matching Firecrawl's Playwright service."""

    url: str = Field(
        ...,
        description="URL to scrape",
    )
    wait_after_load: int = Field(
        default=0,
        alias="wait_after_load",
        description="Milliseconds to wait after page load",
    )
    timeout: int = Field(
        default=15000,
        description="Page load timeout in milliseconds",
    )
    headers: dict[str, str] | None = Field(
        default=None,
        description="Custom headers to send with request",
    )
    check_selector: str | None = Field(
        default=None,
        alias="check_selector",
        description="CSS selector to wait for before returning content",
    )
    skip_tls_verification: bool = Field(
        default=False,
        alias="skip_tls_verification",
        description="Skip TLS certificate verification",
    )
    discover_ajax: bool = Field(
        default=False,
        alias="discover_ajax",
        description="Click interactive elements to discover AJAX URLs",
    )

    model_config = {
        "populate_by_name": True,
    }


class ScrapeSuccessResponse(BaseModel):
    """Successful scrape response matching Firecrawl's format."""

    content: str = Field(
        ...,
        description="HTML content of the page (DOM after JavaScript execution)",
    )
    pageStatusCode: int = Field(
        ...,
        description="HTTP status code of the page response",
    )
    pageError: str | None = Field(
        default=None,
        description="Error message if page returned error status",
    )
    contentType: str | None = Field(
        default=None,
        description="Content-Type header from the response",
    )
    discoveredUrls: list[str] | None = Field(
        default=None,
        description="URLs discovered via AJAX click interception",
    )


class ScrapeErrorResponse(BaseModel):
    """Error response matching Firecrawl's format."""

    error: str = Field(
        ...,
        description="Error message describing what went wrong",
    )


class HealthResponse(BaseModel):
    """Health check response."""

    status: str = Field(
        default="healthy",
        description="Service health status",
    )
    maxConcurrentPages: int = Field(
        ...,
        description="Maximum concurrent pages allowed",
    )
    activePages: int = Field(
        ...,
        description="Currently active pages",
    )
