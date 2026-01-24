# TODO: MCP Server for Knowledge Extraction API

**Agent:** agent-mcp-server
**Branch:** `feat/mcp-server`
**Priority:** High

## Context

The Knowledge Extraction Orchestrator exposes a REST API (FastAPI) for:
- Creating and managing projects with extraction schemas
- Crawling websites and scraping URLs
- Extracting structured knowledge using LLMs
- Semantic search across extractions
- Generating comparison reports

We need an **MCP (Model Context Protocol) server** that wraps this API, enabling AI assistants (Claude Desktop, Claude Code, etc.) to interact with the system directly via MCP tools.

**Key References:**
- MCP Python SDK: https://github.com/modelcontextprotocol/python-sdk
- FastMCP: https://github.com/jlowin/fastmcp
- MCP Docs: https://modelcontextprotocol.io/docs/develop/build-server

**Architecture Decision:** HTTP-wrapper pattern (MCP Server → HTTP → FastAPI API)
- Reuses existing validation and business logic
- API can be used independently
- Easier to test and maintain

**Transport:** STDIO (for Claude Desktop/Claude Code integration)

## Objective

Implement a production-ready MCP server that exposes the Knowledge Extraction API as MCP tools, resources, and prompts, following official MCP patterns and best practices.

## Tasks

### Phase 1: Foundation

#### Task 1.1: Create MCP Package Structure

Create the following directory structure:

```
src/mcp/
├── __init__.py
├── server.py           # FastMCP server entry point
├── config.py           # Pydantic settings
├── client.py           # Async HTTP client for API
├── tools/
│   ├── __init__.py
│   ├── projects.py
│   ├── acquisition.py  # scrape + crawl
│   ├── extraction.py
│   ├── search.py
│   └── reports.py
├── resources/
│   ├── __init__.py
│   └── templates.py
└── prompts/
    ├── __init__.py
    └── workflows.py
```

**File:** `src/mcp/__init__.py`
```python
"""MCP Server for Knowledge Extraction API."""

__version__ = "1.0.0"
```

---

#### Task 1.2: Create Configuration Module

**File:** `src/mcp/config.py` (NEW)

```python
"""MCP Server configuration from environment variables."""

import logging
import sys
from pydantic_settings import BaseSettings
from pydantic import Field


def configure_logging() -> logging.Logger:
    """Configure logging to stderr only (CRITICAL for STDIO transport).

    WARNING: Never use print() or write to stdout in MCP STDIO servers.
    This will corrupt JSON-RPC messages and break the protocol.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    logger = logging.getLogger("mcp")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    # Suppress httpx info logs (too verbose)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return logger


class MCPSettings(BaseSettings):
    """MCP Server configuration.

    Environment variables:
        KE_API_BASE_URL: Base URL of the Knowledge Extraction API
        KE_API_KEY: API authentication key
        KE_TIMEOUT_SECONDS: HTTP request timeout (default: 60)
        KE_MAX_RETRIES: Retry attempts for failed requests (default: 3)
        KE_POLL_INTERVAL: Seconds between job status polls (default: 5)
        KE_MAX_POLL_ATTEMPTS: Max polls before timeout (default: 120)
    """

    api_base_url: str = Field(
        default="http://localhost:8000",
        description="Knowledge Extraction API base URL",
    )
    api_key: str = Field(
        default="",
        description="API authentication key (if required)",
    )
    timeout_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="HTTP request timeout in seconds",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max retry attempts for failed requests",
    )
    poll_interval: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Seconds between job status polls",
    )
    max_poll_attempts: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Max poll attempts before timeout (120 * 5s = 10 min)",
    )

    class Config:
        env_prefix = "KE_"
        env_file = ".env"
        env_file_encoding = "utf-8"
```

---

#### Task 1.3: Create Async HTTP Client

**File:** `src/mcp/client.py` (NEW)

```python
"""Async HTTP client for the Knowledge Extraction API."""

import asyncio
import logging
from typing import Any
from uuid import UUID

import httpx

from .config import MCPSettings

logger = logging.getLogger("mcp.client")


class APIError(Exception):
    """Raised when API request fails."""

    def __init__(self, message: str, status_code: int | None = None):
        self.message = message
        self.status_code = status_code
        super().__init__(message)


class KnowledgeExtractionClient:
    """Async HTTP client for the Knowledge Extraction API.

    Handles authentication, retries, and error mapping.
    """

    def __init__(self, settings: MCPSettings):
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    async def connect(self) -> None:
        """Initialize the HTTP client."""
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"

        self._client = httpx.AsyncClient(
            base_url=self.settings.api_base_url,
            headers=headers,
            timeout=self.settings.timeout_seconds,
        )
        logger.info(
            "API client connected",
            extra={"base_url": self.settings.api_base_url},
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("API client disconnected")

    async def _request(
        self,
        method: str,
        path: str,
        json: dict | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Make an API request with retry logic."""
        if not self._client:
            raise APIError("Client not connected")

        last_error: Exception | None = None

        for attempt in range(self.settings.max_retries):
            try:
                response = await self._client.request(
                    method=method,
                    url=path,
                    json=json,
                    params=params,
                )

                if response.status_code == 404:
                    raise APIError("Resource not found", 404)
                elif response.status_code == 409:
                    raise APIError("Resource already exists", 409)
                elif response.status_code == 422:
                    detail = response.json().get("detail", "Validation error")
                    raise APIError(f"Validation error: {detail}", 422)
                elif response.status_code >= 500:
                    raise APIError(f"Server error: {response.status_code}", response.status_code)

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"Request timeout (attempt {attempt + 1})")
                await asyncio.sleep(2 ** attempt)  # Exponential backoff
            except httpx.HTTPStatusError as e:
                raise APIError(str(e), e.response.status_code) from e
            except APIError:
                raise
            except Exception as e:
                last_error = e
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(2 ** attempt)

        raise APIError(f"Request failed after {self.settings.max_retries} attempts: {last_error}")

    # =========================================================================
    # Project Operations
    # =========================================================================

    async def create_project(
        self,
        name: str,
        description: str | None = None,
        template: str | None = None,
    ) -> dict[str, Any]:
        """Create a new project."""
        if template:
            return await self._request(
                "POST",
                "/api/v1/projects/from-template",
                json={
                    "template": template,
                    "name": name,
                    "description": description,
                },
            )
        return await self._request(
            "POST",
            "/api/v1/projects",
            json={
                "name": name,
                "description": description,
            },
        )

    async def list_projects(self, include_inactive: bool = False) -> list[dict]:
        """List all projects."""
        return await self._request(
            "GET",
            "/api/v1/projects",
            params={"include_inactive": include_inactive},
        )

    async def get_project(self, project_id: str) -> dict[str, Any]:
        """Get project by ID."""
        return await self._request("GET", f"/api/v1/projects/{project_id}")

    async def list_templates(self) -> list[str]:
        """List available project templates."""
        return await self._request("GET", "/api/v1/projects/templates")

    # =========================================================================
    # Crawl/Scrape Operations
    # =========================================================================

    async def create_crawl(
        self,
        url: str,
        project_id: str,
        company: str,
        max_depth: int = 2,
        limit: int = 100,
        prefer_english_only: bool = True,
    ) -> dict[str, Any]:
        """Start a crawl job."""
        return await self._request(
            "POST",
            "/api/v1/crawl",
            json={
                "url": url,
                "project_id": project_id,
                "company": company,
                "max_depth": max_depth,
                "limit": limit,
                "prefer_english_only": prefer_english_only,
                "auto_extract": False,  # Control extraction separately
            },
        )

    async def get_crawl_status(self, job_id: str) -> dict[str, Any]:
        """Get crawl job status."""
        return await self._request("GET", f"/api/v1/crawl/{job_id}")

    async def create_scrape(
        self,
        urls: list[str],
        project_id: str,
        company: str,
    ) -> dict[str, Any]:
        """Start a scrape job."""
        return await self._request(
            "POST",
            "/api/v1/scrape",
            json={
                "urls": urls,
                "project_id": project_id,
                "company": company,
            },
        )

    async def get_scrape_status(self, job_id: str) -> dict[str, Any]:
        """Get scrape job status."""
        return await self._request("GET", f"/api/v1/scrape/{job_id}")

    # =========================================================================
    # Extraction Operations
    # =========================================================================

    async def create_extraction(
        self,
        project_id: str,
        source_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start an extraction job."""
        return await self._request(
            "POST",
            f"/api/v1/projects/{project_id}/extract",
            json={"source_ids": source_ids},
        )

    async def list_extractions(
        self,
        project_id: str,
        source_group: str | None = None,
        extraction_type: str | None = None,
        min_confidence: float | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List extractions with filters."""
        params = {"limit": limit, "offset": offset}
        if source_group:
            params["source_group"] = source_group
        if extraction_type:
            params["extraction_type"] = extraction_type
        if min_confidence is not None:
            params["min_confidence"] = min_confidence

        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/extractions",
            params=params,
        )

    # =========================================================================
    # Search Operations
    # =========================================================================

    async def search(
        self,
        project_id: str,
        query: str,
        limit: int = 10,
        source_groups: list[str] | None = None,
    ) -> dict[str, Any]:
        """Semantic search in extractions."""
        return await self._request(
            "POST",
            f"/api/v1/projects/{project_id}/search",
            json={
                "query": query,
                "limit": limit,
                "source_groups": source_groups,
            },
        )

    async def list_entities(
        self,
        project_id: str,
        entity_type: str | None = None,
        source_group: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List entities with filters."""
        params = {"limit": limit, "offset": offset}
        if entity_type:
            params["entity_type"] = entity_type
        if source_group:
            params["source_group"] = source_group

        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/entities",
            params=params,
        )

    async def get_entity_types(self, project_id: str) -> dict[str, Any]:
        """Get entity type counts."""
        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/entities/types",
        )

    # =========================================================================
    # Report Operations
    # =========================================================================

    async def create_report(
        self,
        project_id: str,
        report_type: str,
        source_groups: list[str],
        title: str | None = None,
        output_format: str = "md",
    ) -> dict[str, Any]:
        """Generate a report."""
        return await self._request(
            "POST",
            f"/api/v1/projects/{project_id}/reports",
            json={
                "type": report_type,
                "source_groups": source_groups,
                "title": title,
                "output_format": output_format,
            },
        )

    async def list_reports(
        self,
        project_id: str,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List reports for a project."""
        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/reports",
            params={"limit": limit, "offset": offset},
        )

    async def get_report(self, project_id: str, report_id: str) -> dict[str, Any]:
        """Get a specific report."""
        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/reports/{report_id}",
        )

    # =========================================================================
    # Job Operations
    # =========================================================================

    async def list_jobs(
        self,
        job_type: str | None = None,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List jobs with filters."""
        params = {"limit": limit, "offset": offset}
        if job_type:
            params["type"] = job_type
        if status:
            params["status"] = status

        return await self._request("GET", "/api/v1/jobs", params=params)

    async def get_job(self, job_id: str) -> dict[str, Any]:
        """Get job details."""
        return await self._request("GET", f"/api/v1/jobs/{job_id}")

    # =========================================================================
    # Polling Helpers
    # =========================================================================

    async def wait_for_job(
        self,
        job_id: str,
        job_type: str,  # "crawl" or "scrape"
    ) -> dict[str, Any]:
        """Poll until job completes or fails.

        Returns final job status.
        """
        get_status = (
            self.get_crawl_status if job_type == "crawl"
            else self.get_scrape_status
        )

        for _ in range(self.settings.max_poll_attempts):
            status = await get_status(job_id)

            if status.get("status") in ("completed", "failed"):
                return status

            await asyncio.sleep(self.settings.poll_interval)

        return {
            "job_id": job_id,
            "status": "timeout",
            "error": f"Job did not complete within {self.settings.max_poll_attempts * self.settings.poll_interval}s",
        }
```

**Tests:** `tests/mcp/test_client.py`
- `test_client_connect_disconnect` - lifecycle works
- `test_client_retry_on_timeout` - retries with backoff
- `test_client_raises_api_error_on_404` - proper error mapping
- `test_client_raises_api_error_on_422` - validation errors
- `test_wait_for_job_returns_on_completion` - polling works
- `test_wait_for_job_returns_timeout` - timeout after max attempts

---

### Phase 2: MCP Server Core

#### Task 2.1: Create MCP Server Entry Point

**File:** `src/mcp/server.py` (NEW)

```python
"""MCP Server entry point for Knowledge Extraction API."""

import asyncio
import logging
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .config import MCPSettings, configure_logging
from .client import KnowledgeExtractionClient

# Configure logging FIRST (before any other imports that might log)
logger = configure_logging()

# Global client instance (set during lifespan)
_api_client: KnowledgeExtractionClient | None = None


def get_client() -> KnowledgeExtractionClient:
    """Get the API client instance."""
    if _api_client is None:
        raise RuntimeError("API client not initialized")
    return _api_client


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Manage API client lifecycle.

    Initializes the HTTP client on startup and closes it on shutdown.
    The client is available via ctx.request_context.lifespan_context["client"].
    """
    global _api_client

    settings = MCPSettings()
    _api_client = KnowledgeExtractionClient(settings)

    try:
        await _api_client.connect()
        logger.info("MCP server started")
        yield {"client": _api_client, "settings": settings}
    finally:
        await _api_client.close()
        _api_client = None
        logger.info("MCP server stopped")


# Create the FastMCP server instance
mcp = FastMCP(
    name="knowledge-extraction",
    version="1.0.0",
    lifespan=lifespan,
)

# Import and register tools, resources, prompts
from .tools import register_all_tools
from .resources import register_all_resources
from .prompts import register_all_prompts

register_all_tools(mcp)
register_all_resources(mcp)
register_all_prompts(mcp)


def main():
    """Entry point for the MCP server."""
    logger.info("Starting Knowledge Extraction MCP Server...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
```

---

#### Task 2.2: Create Tools Registration Module

**File:** `src/mcp/tools/__init__.py` (NEW)

```python
"""MCP Tools registration."""

from mcp.server.fastmcp import FastMCP

from .projects import register_project_tools
from .acquisition import register_acquisition_tools
from .extraction import register_extraction_tools
from .search import register_search_tools
from .reports import register_report_tools


def register_all_tools(mcp: FastMCP) -> None:
    """Register all MCP tools."""
    register_project_tools(mcp)
    register_acquisition_tools(mcp)
    register_extraction_tools(mcp)
    register_search_tools(mcp)
    register_report_tools(mcp)
```

---

### Phase 3: Tool Implementations

#### Task 3.1: Project Management Tools

**File:** `src/mcp/tools/projects.py` (NEW)

```python
"""Project management MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP, Context

from ..client import APIError

logger = logging.getLogger("mcp.tools.projects")


def register_project_tools(mcp: FastMCP) -> None:
    """Register project management tools."""

    @mcp.tool()
    async def create_project(
        name: Annotated[str, "Unique project name (lowercase, hyphens allowed)"],
        template: Annotated[
            str | None,
            "Template name: company_analysis, research_survey, contract_review, book_catalog, or default"
        ] = None,
        description: Annotated[str | None, "Project description"] = None,
        ctx: Context = None,
    ) -> dict:
        """Create a new knowledge extraction project.

        Projects define extraction schemas and entity types for processing documents.
        Use a template for common use cases or omit for the default generic template.

        Templates:
        - company_analysis: Extract technical facts from company documentation
        - research_survey: Extract findings from academic papers
        - contract_review: Extract legal terms from contracts
        - book_catalog: Extract metadata from books
        - default: Generic fact extraction for any content
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.create_project(
                name=name,
                description=description,
                template=template,
            )
            return {
                "success": True,
                "project_id": result["id"],
                "name": result["name"],
                "template": template or "default",
                "message": f"Project '{name}' created successfully.",
            }
        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def list_projects(
        include_inactive: Annotated[bool, "Include soft-deleted projects"] = False,
        ctx: Context = None,
    ) -> dict:
        """List all knowledge extraction projects.

        Returns project names and IDs for use with other tools.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            projects = await client.list_projects(include_inactive)
            return {
                "success": True,
                "count": len(projects),
                "projects": [
                    {
                        "id": p["id"],
                        "name": p["name"],
                        "is_active": p.get("is_active", True),
                    }
                    for p in projects
                ],
            }
        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def get_project(
        project_id: Annotated[str, "Project UUID"],
        ctx: Context = None,
    ) -> dict:
        """Get detailed information about a project.

        Returns the project's extraction schema, entity types, and configuration.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            project = await client.get_project(project_id)
            return {
                "success": True,
                "id": project["id"],
                "name": project["name"],
                "description": project.get("description"),
                "is_active": project.get("is_active", True),
                "schema_name": project.get("extraction_schema", {}).get("name", "unknown"),
                "field_group_count": len(
                    project.get("extraction_schema", {}).get("field_groups", [])
                ),
                "entity_types": [
                    et["name"] for et in project.get("entity_types", [])
                ],
                "created_at": project.get("created_at"),
            }
        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def list_templates(ctx: Context = None) -> dict:
        """List available project templates.

        Templates provide pre-configured extraction schemas for common use cases.
        Use these template names with create_project().
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            templates = await client.list_templates()

            # Add descriptions for known templates
            template_info = {
                "company_analysis": "Extract technical facts from company documentation",
                "research_survey": "Extract findings from academic papers",
                "contract_review": "Extract legal terms from contracts",
                "book_catalog": "Extract metadata from books",
                "default": "Generic fact extraction for any content",
            }

            return {
                "success": True,
                "templates": [
                    {
                        "name": t,
                        "description": template_info.get(t, "Custom template"),
                    }
                    for t in templates
                ],
            }
        except APIError as e:
            return {"success": False, "error": e.message}
```

**Tests:** `tests/mcp/test_tools_projects.py`
- `test_create_project_success` - creates project with template
- `test_create_project_conflict` - handles duplicate name
- `test_list_projects_empty` - returns empty list
- `test_list_projects_with_data` - returns project info
- `test_get_project_success` - returns full details
- `test_get_project_not_found` - handles 404

---

#### Task 3.2: Acquisition Tools (Crawl/Scrape)

**File:** `src/mcp/tools/acquisition.py` (NEW)

```python
"""Web crawling and scraping MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP, Context

from ..client import APIError

logger = logging.getLogger("mcp.tools.acquisition")


def register_acquisition_tools(mcp: FastMCP) -> None:
    """Register crawl and scrape tools."""

    @mcp.tool()
    async def crawl_website(
        url: Annotated[str, "Starting URL to crawl (e.g., https://example.com/docs)"],
        project_id: Annotated[str, "Project UUID to store sources in"],
        company: Annotated[str, "Company/source group name for grouping results"],
        max_depth: Annotated[int, "How many levels deep to crawl (1-10)"] = 2,
        limit: Annotated[int, "Maximum number of pages to crawl (1-1000)"] = 100,
        prefer_english_only: Annotated[bool, "Filter out non-English pages"] = True,
        wait_for_completion: Annotated[
            bool,
            "Wait for crawl to finish (may take several minutes)"
        ] = True,
        ctx: Context = None,
    ) -> dict:
        """Crawl a website to discover and fetch pages.

        Starts from the given URL and follows links up to max_depth levels.
        Discovered pages are stored as sources in the specified project.

        After crawling, use extract_knowledge() to process the content.

        Example:
            crawl_website(
                url="https://acme.com/docs",
                project_id="...",
                company="Acme Inc",
                max_depth=3,
                limit=200
            )
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            # Start the crawl job
            job = await client.create_crawl(
                url=url,
                project_id=project_id,
                company=company,
                max_depth=max_depth,
                limit=limit,
                prefer_english_only=prefer_english_only,
            )

            job_id = job["job_id"]
            logger.info(f"Crawl job started: {job_id}")

            if not wait_for_completion:
                return {
                    "success": True,
                    "job_id": job_id,
                    "status": "queued",
                    "message": f"Crawl job started. Use get_job_status('{job_id}') to check progress.",
                }

            # Wait for completion
            result = await client.wait_for_job(job_id, "crawl")

            if result.get("status") == "completed":
                return {
                    "success": True,
                    "job_id": job_id,
                    "status": "completed",
                    "pages_discovered": result.get("pages_total", 0),
                    "sources_created": result.get("sources_created", 0),
                    "message": "Crawl complete. Run extract_knowledge() to process the content.",
                }
            elif result.get("status") == "failed":
                return {
                    "success": False,
                    "job_id": job_id,
                    "status": "failed",
                    "error": result.get("error", "Unknown error"),
                }
            else:
                return {
                    "success": True,
                    "job_id": job_id,
                    "status": "timeout",
                    "message": "Crawl is still running. Use get_job_status() to check later.",
                }

        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def scrape_urls(
        urls: Annotated[list[str], "List of URLs to scrape"],
        project_id: Annotated[str, "Project UUID to store sources in"],
        company: Annotated[str, "Company/source group name"],
        wait_for_completion: Annotated[bool, "Wait for scrape to finish"] = True,
        ctx: Context = None,
    ) -> dict:
        """Scrape specific URLs and store them as sources.

        Unlike crawl_website(), this only fetches the specified URLs without
        following links. Useful when you have a list of known documentation pages.

        Example:
            scrape_urls(
                urls=["https://acme.com/api/reference", "https://acme.com/pricing"],
                project_id="...",
                company="Acme Inc"
            )
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            job = await client.create_scrape(
                urls=urls,
                project_id=project_id,
                company=company,
            )

            job_id = job["job_id"]
            logger.info(f"Scrape job started: {job_id}")

            if not wait_for_completion:
                return {
                    "success": True,
                    "job_id": job_id,
                    "status": "queued",
                    "url_count": len(urls),
                    "message": f"Scrape job started. Use get_job_status('{job_id}') to check progress.",
                }

            # Wait for completion
            result = await client.wait_for_job(job_id, "scrape")

            if result.get("status") == "completed":
                return {
                    "success": True,
                    "job_id": job_id,
                    "status": "completed",
                    "url_count": len(urls),
                    "message": "Scrape complete. Run extract_knowledge() to process the content.",
                }
            elif result.get("status") == "failed":
                return {
                    "success": False,
                    "job_id": job_id,
                    "status": "failed",
                    "error": result.get("error", "Unknown error"),
                }
            else:
                return {
                    "success": True,
                    "job_id": job_id,
                    "status": "timeout",
                    "message": "Scrape is still running. Use get_job_status() to check later.",
                }

        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def get_job_status(
        job_id: Annotated[str, "Job UUID from crawl_website or scrape_urls"],
        ctx: Context = None,
    ) -> dict:
        """Check the status of a crawl or scrape job.

        Returns current status and progress information.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            job = await client.get_job(job_id)

            result = {
                "success": True,
                "job_id": job_id,
                "type": job.get("type"),
                "status": job.get("status"),
                "created_at": job.get("created_at"),
            }

            if job.get("status") == "completed":
                result["completed_at"] = job.get("completed_at")
                if job.get("result"):
                    result["result"] = job["result"]
            elif job.get("status") == "failed":
                result["error"] = job.get("error")

            return result

        except APIError as e:
            return {"success": False, "error": e.message}
```

**Tests:** `tests/mcp/test_tools_acquisition.py`
- `test_crawl_website_starts_job` - returns job_id
- `test_crawl_website_waits_for_completion` - polls until done
- `test_scrape_urls_validates_urls` - handles empty list
- `test_get_job_status_returns_details` - shows progress

---

#### Task 3.3: Extraction Tools

**File:** `src/mcp/tools/extraction.py` (NEW)

```python
"""Knowledge extraction MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP, Context

from ..client import APIError

logger = logging.getLogger("mcp.tools.extraction")


def register_extraction_tools(mcp: FastMCP) -> None:
    """Register extraction tools."""

    @mcp.tool()
    async def extract_knowledge(
        project_id: Annotated[str, "Project UUID"],
        source_ids: Annotated[
            list[str] | None,
            "Specific source UUIDs to extract (omit for all pending sources)"
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """Run LLM-based knowledge extraction on sources.

        Processes sources using the project's extraction schema and creates
        structured extractions. This uses the LLM to identify facts, entities,
        and relationships in the content.

        If source_ids is omitted, extracts from all sources with 'pending' status.

        This operation may take several minutes depending on the number of sources.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            job = await client.create_extraction(
                project_id=project_id,
                source_ids=source_ids,
            )

            return {
                "success": True,
                "job_id": job["job_id"],
                "status": job["status"],
                "source_count": job["source_count"],
                "message": f"Extraction started for {job['source_count']} sources. "
                          f"Use get_job_status('{job['job_id']}') to check progress.",
            }

        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def list_extractions(
        project_id: Annotated[str, "Project UUID"],
        source_group: Annotated[str | None, "Filter by company/source group"] = None,
        extraction_type: Annotated[str | None, "Filter by extraction type"] = None,
        min_confidence: Annotated[float | None, "Minimum confidence score (0.0-1.0)"] = None,
        limit: Annotated[int, "Maximum results to return"] = 20,
        ctx: Context = None,
    ) -> dict:
        """List extracted knowledge from a project.

        Returns structured extractions with their data, confidence scores,
        and source information. Use filters to narrow down results.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.list_extractions(
                project_id=project_id,
                source_group=source_group,
                extraction_type=extraction_type,
                min_confidence=min_confidence,
                limit=limit,
            )

            return {
                "success": True,
                "total": result["total"],
                "showing": len(result["extractions"]),
                "extractions": [
                    {
                        "id": e["id"],
                        "type": e.get("extraction_type"),
                        "source_group": e.get("source_group"),
                        "confidence": e.get("confidence"),
                        "data": e.get("data"),
                    }
                    for e in result["extractions"]
                ],
            }

        except APIError as e:
            return {"success": False, "error": e.message}
```

**Tests:** `tests/mcp/test_tools_extraction.py`
- `test_extract_knowledge_starts_job` - returns job info
- `test_list_extractions_with_filters` - applies filters correctly

---

#### Task 3.4: Search Tools

**File:** `src/mcp/tools/search.py` (NEW)

```python
"""Search and entity query MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import FastMCP, Context

from ..client import APIError

logger = logging.getLogger("mcp.tools.search")


def register_search_tools(mcp: FastMCP) -> None:
    """Register search tools."""

    @mcp.tool()
    async def search_knowledge(
        project_id: Annotated[str, "Project UUID"],
        query: Annotated[str, "Natural language search query"],
        limit: Annotated[int, "Maximum results to return"] = 10,
        source_groups: Annotated[
            list[str] | None,
            "Filter by specific companies/source groups"
        ] = None,
        ctx: Context = None,
    ) -> dict:
        """Search extracted knowledge using semantic similarity.

        Uses vector embeddings to find extractions that match the query
        meaning, not just keywords. Great for finding related facts across
        multiple sources.

        Example:
            search_knowledge(
                project_id="...",
                query="pricing tiers and limits",
                source_groups=["Acme Inc", "Competitor Corp"]
            )
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.search(
                project_id=project_id,
                query=query,
                limit=limit,
                source_groups=source_groups,
            )

            return {
                "success": True,
                "query": query,
                "total": result["total"],
                "results": [
                    {
                        "score": r["score"],
                        "source_group": r["source_group"],
                        "source_uri": r["source_uri"],
                        "confidence": r.get("confidence"),
                        "data": r["data"],
                    }
                    for r in result["results"]
                ],
            }

        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def list_entities(
        project_id: Annotated[str, "Project UUID"],
        entity_type: Annotated[str | None, "Filter by entity type (e.g., 'plan', 'feature')"] = None,
        source_group: Annotated[str | None, "Filter by company/source group"] = None,
        limit: Annotated[int, "Maximum results to return"] = 50,
        ctx: Context = None,
    ) -> dict:
        """List normalized entities extracted from a project.

        Entities are deduplicated and normalized values like product names,
        features, pricing tiers, etc. that were identified during extraction.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.list_entities(
                project_id=project_id,
                entity_type=entity_type,
                source_group=source_group,
                limit=limit,
            )

            return {
                "success": True,
                "total": result["total"],
                "showing": len(result["entities"]),
                "entities": [
                    {
                        "id": e["id"],
                        "type": e["entity_type"],
                        "value": e["value"],
                        "source_group": e["source_group"],
                    }
                    for e in result["entities"]
                ],
            }

        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def get_entity_summary(
        project_id: Annotated[str, "Project UUID"],
        ctx: Context = None,
    ) -> dict:
        """Get a summary of entity types and counts in a project.

        Useful for understanding what kinds of entities were extracted
        and their distribution across the project.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.get_entity_types(project_id)

            return {
                "success": True,
                "total_entities": result["total_entities"],
                "types": [
                    {"type": t["entity_type"], "count": t["count"]}
                    for t in result["types"]
                ],
            }

        except APIError as e:
            return {"success": False, "error": e.message}
```

**Tests:** `tests/mcp/test_tools_search.py`
- `test_search_knowledge_returns_results` - semantic search works
- `test_list_entities_with_type_filter` - filters apply
- `test_get_entity_summary_returns_counts` - aggregation works

---

#### Task 3.5: Report Tools

**File:** `src/mcp/tools/reports.py` (NEW)

```python
"""Report generation MCP tools."""

import logging
from typing import Annotated, Literal

from mcp.server.fastmcp import FastMCP, Context

from ..client import APIError

logger = logging.getLogger("mcp.tools.reports")


def register_report_tools(mcp: FastMCP) -> None:
    """Register report tools."""

    @mcp.tool()
    async def create_report(
        project_id: Annotated[str, "Project UUID"],
        report_type: Annotated[
            Literal["single", "comparison", "table", "schema_table"],
            "Report type: single (one company), comparison (multiple), table, schema_table"
        ],
        source_groups: Annotated[
            list[str],
            "Companies/source groups to include in the report"
        ],
        title: Annotated[str | None, "Custom report title"] = None,
        output_format: Annotated[
            Literal["md", "xlsx"],
            "Output format: md (markdown) or xlsx (Excel)"
        ] = "md",
        ctx: Context = None,
    ) -> dict:
        """Generate an analysis report from extracted knowledge.

        Report types:
        - single: Summarize findings for one company
        - comparison: Compare findings across multiple companies
        - table: Tabular format of extracted data
        - schema_table: Structured table following extraction schema

        Example:
            create_report(
                project_id="...",
                report_type="comparison",
                source_groups=["Acme Inc", "Competitor Corp"],
                title="Pricing Comparison"
            )
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.create_report(
                project_id=project_id,
                report_type=report_type,
                source_groups=source_groups,
                title=title,
                output_format=output_format,
            )

            return {
                "success": True,
                "report_id": result["id"],
                "title": result["title"],
                "type": result["type"],
                "extraction_count": result["extraction_count"],
                "content_preview": result["content"][:500] + "..."
                    if len(result.get("content", "")) > 500
                    else result.get("content", ""),
                "message": f"Report generated. Use get_report('{result['id']}') for full content.",
            }

        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def list_reports(
        project_id: Annotated[str, "Project UUID"],
        limit: Annotated[int, "Maximum reports to return"] = 10,
        ctx: Context = None,
    ) -> dict:
        """List generated reports for a project."""
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.list_reports(project_id=project_id, limit=limit)

            return {
                "success": True,
                "total": result["total"],
                "reports": [
                    {
                        "id": r["id"],
                        "type": r["type"],
                        "title": r.get("title"),
                        "source_groups": r.get("source_groups", []),
                        "created_at": r["created_at"],
                    }
                    for r in result["reports"]
                ],
            }

        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def get_report(
        project_id: Annotated[str, "Project UUID"],
        report_id: Annotated[str, "Report UUID"],
        ctx: Context = None,
    ) -> dict:
        """Get the full content of a generated report."""
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.get_report(project_id=project_id, report_id=report_id)

            return {
                "success": True,
                "report_id": result["id"],
                "title": result["title"],
                "type": result["type"],
                "source_groups": result["source_groups"],
                "content": result["content"],
                "extraction_count": result["extraction_count"],
                "generated_at": result["generated_at"],
            }

        except APIError as e:
            return {"success": False, "error": e.message}
```

**Tests:** `tests/mcp/test_tools_reports.py`
- `test_create_report_single` - single company report
- `test_create_report_comparison` - multi-company comparison
- `test_list_reports_returns_summaries` - list works
- `test_get_report_returns_full_content` - full content retrieved

---

### Phase 4: Resources and Prompts

#### Task 4.1: Create Resources Registration

**File:** `src/mcp/resources/__init__.py` (NEW)

```python
"""MCP Resources registration."""

from mcp.server.fastmcp import FastMCP

from .templates import register_template_resources


def register_all_resources(mcp: FastMCP) -> None:
    """Register all MCP resources."""
    register_template_resources(mcp)
```

---

#### Task 4.2: Template Resources

**File:** `src/mcp/resources/templates.py` (NEW)

```python
"""Project template MCP resources."""

from mcp.server.fastmcp import FastMCP


def register_template_resources(mcp: FastMCP) -> None:
    """Register template resources."""

    @mcp.resource("templates://overview")
    async def templates_overview() -> str:
        """Overview of available project templates."""
        return """# Knowledge Extraction Templates

## Available Templates

### company_analysis (Recommended for documentation)
Extract technical facts from company documentation.
- **Categories**: specs, api, security, pricing, features, integration
- **Entity types**: plan, feature, limit, certification, pricing
- **Best for**: Product docs, API references, pricing pages

### research_survey
Extract findings from academic papers and research.
- **Categories**: methodology, result, conclusion, limitation, future_work
- **Entity types**: author, institution, method, metric, dataset
- **Best for**: Academic papers, research reports, white papers

### contract_review
Extract legal terms from contracts and agreements.
- **Categories**: obligation, right, condition, definition, termination
- **Entity types**: party, date, amount, jurisdiction
- **Best for**: Legal documents, terms of service, agreements

### book_catalog
Extract metadata and summaries from books.
- **Categories**: author, title, genre, plot, character
- **Entity types**: author, character, setting, publication_date
- **Best for**: Book catalogs, library content

### default
Generic fact extraction for any content type.
- **Categories**: general, technical, financial, operational, historical
- **Entity types**: entity, fact
- **Best for**: General content when no specific template fits

## Usage

Use the `create_project` tool with the `template` parameter:

```
create_project(
    name="my-analysis",
    template="company_analysis",
    description="Analyzing competitor documentation"
)
```
"""
```

---

#### Task 4.3: Create Prompts Registration

**File:** `src/mcp/prompts/__init__.py` (NEW)

```python
"""MCP Prompts registration."""

from mcp.server.fastmcp import FastMCP

from .workflows import register_workflow_prompts


def register_all_prompts(mcp: FastMCP) -> None:
    """Register all MCP prompts."""
    register_workflow_prompts(mcp)
```

---

#### Task 4.4: Workflow Prompts

**File:** `src/mcp/prompts/workflows.py` (NEW)

```python
"""Workflow prompt templates."""

from mcp.server.fastmcp import FastMCP


def register_workflow_prompts(mcp: FastMCP) -> None:
    """Register workflow prompts."""

    @mcp.prompt()
    def analyze_company_docs(
        company_name: str,
        documentation_url: str,
        focus_areas: str = "all technical facts",
    ) -> str:
        """Complete workflow to extract knowledge from company documentation."""
        return f"""# Analyze {company_name} Documentation

Follow these steps to extract structured knowledge from {company_name}'s documentation.

## Step 1: Create Project
```
create_project(
    name="{company_name.lower().replace(' ', '-')}-analysis",
    template="company_analysis",
    description="Analysis of {company_name} documentation"
)
```

## Step 2: Crawl Documentation
```
crawl_website(
    url="{documentation_url}",
    project_id="<project_id from step 1>",
    company="{company_name}",
    max_depth=3,
    limit=200,
    wait_for_completion=True
)
```

## Step 3: Extract Knowledge
```
extract_knowledge(
    project_id="<project_id>"
)
```
Then wait for extraction to complete using `get_job_status()`.

## Step 4: Search and Explore
```
search_knowledge(
    project_id="<project_id>",
    query="{focus_areas}"
)

get_entity_summary(project_id="<project_id>")
```

## Step 5: Generate Report
```
create_report(
    project_id="<project_id>",
    report_type="single",
    source_groups=["{company_name}"],
    title="{company_name} Analysis Report"
)
```

## Expected Results
- Structured extractions of technical facts
- Normalized entities (features, pricing tiers, limits)
- Searchable knowledge base
- Summary report in markdown format
"""

    @mcp.prompt()
    def compare_competitors(
        company_names: str,
        focus_area: str = "features and pricing",
    ) -> str:
        """Workflow to compare multiple companies."""
        companies = [c.strip() for c in company_names.split(",")]

        return f"""# Compare: {' vs '.join(companies)}

This workflow compares {len(companies)} companies on {focus_area}.

## Prerequisites
Each company must already have extracted data in a project.
If not, run the `analyze_company_docs` workflow for each company first.

## Step 1: Search Across Companies
For each focus area, search across all companies:
```
search_knowledge(
    project_id="<project_id>",
    query="{focus_area}",
    source_groups={companies}
)
```

## Step 2: Compare Entities
```
list_entities(
    project_id="<project_id>",
    entity_type="feature"  # or "pricing", "plan", etc.
)
```

## Step 3: Generate Comparison Report
```
create_report(
    project_id="<project_id>",
    report_type="comparison",
    source_groups={companies},
    title="{' vs '.join(companies)} - {focus_area.title()}"
)
```

## Expected Results
- Side-by-side comparison of {focus_area}
- Differences and similarities highlighted
- Structured comparison table
"""
```

---

### Phase 5: Packaging and Testing

#### Task 5.1: Add Dependencies to Requirements

**File:** `requirements.txt` (MODIFY - add these lines)

```
# MCP Server
mcp>=1.2.0
pydantic-settings>=2.0.0
```

---

#### Task 5.2: Create MCP Server Entry Script

**File:** `src/mcp/__main__.py` (NEW)

```python
"""Entry point for running the MCP server as a module.

Usage:
    python -m src.mcp
"""

from .server import main

if __name__ == "__main__":
    main()
```

---

#### Task 5.3: Create Test Fixtures

**File:** `tests/mcp/conftest.py` (NEW)

```python
"""Test fixtures for MCP server tests."""

import pytest
from unittest.mock import AsyncMock, MagicMock

from src.mcp.config import MCPSettings
from src.mcp.client import KnowledgeExtractionClient


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    return MCPSettings(
        api_base_url="http://test-api:8000",
        api_key="test-key",
        timeout_seconds=30,
        max_retries=2,
        poll_interval=1,
        max_poll_attempts=5,
    )


@pytest.fixture
def mock_client(mock_settings):
    """Create mock API client."""
    client = KnowledgeExtractionClient(mock_settings)
    client._client = AsyncMock()
    return client


@pytest.fixture
def mock_context(mock_client):
    """Create mock MCP context."""
    context = MagicMock()
    context.request_context.lifespan_context = {
        "client": mock_client,
        "settings": mock_client.settings,
    }
    return context
```

---

#### Task 5.4: Create Integration Test

**File:** `tests/mcp/test_integration.py` (NEW)

```python
"""Integration tests for MCP server (requires running API)."""

import os
import pytest

# Skip if no API available
pytestmark = pytest.mark.skipif(
    not os.environ.get("KE_API_BASE_URL"),
    reason="KE_API_BASE_URL not set - skipping integration tests",
)


class TestMCPServerIntegration:
    """Integration tests requiring a running API."""

    @pytest.fixture
    async def client(self):
        """Create real client connected to API."""
        from src.mcp.config import MCPSettings
        from src.mcp.client import KnowledgeExtractionClient

        settings = MCPSettings()
        client = KnowledgeExtractionClient(settings)
        await client.connect()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_list_projects(self, client):
        """Test listing projects against real API."""
        projects = await client.list_projects()
        assert isinstance(projects, list)

    @pytest.mark.asyncio
    async def test_list_templates(self, client):
        """Test listing templates against real API."""
        templates = await client.list_templates()
        assert isinstance(templates, list)
        assert len(templates) > 0
```

---

## Constraints

- Do NOT modify any existing files in `src/api/`, `src/services/`, or `src/models.py`
- Do NOT use `print()` or write to stdout anywhere in MCP code (breaks STDIO transport)
- Do NOT add external dependencies beyond `mcp` and `pydantic-settings`
- All logs MUST go to stderr via the logging module
- Keep backward compatibility with existing API - MCP server is an additional interface
- Do NOT run full test suite - only run MCP-specific tests

## Test Scope

**ONLY run these tests:**

```bash
# Unit tests
pytest tests/mcp/test_client.py -v
pytest tests/mcp/test_tools_projects.py -v
pytest tests/mcp/test_tools_acquisition.py -v
pytest tests/mcp/test_tools_extraction.py -v
pytest tests/mcp/test_tools_search.py -v
pytest tests/mcp/test_tools_reports.py -v

# All MCP tests
pytest tests/mcp/ -v

# Integration tests (only if API running)
KE_API_BASE_URL=http://localhost:8000 pytest tests/mcp/test_integration.py -v
```

## Lint Scope

**ONLY lint these files:**

```bash
ruff check src/mcp/ tests/mcp/
ruff format src/mcp/ tests/mcp/
```

## Verification

Before creating PR, verify:

1. `python -m src.mcp` starts without errors (then Ctrl+C)
2. `pytest tests/mcp/ -v` - All unit tests pass
3. `ruff check src/mcp/` - No lint errors
4. `ruff format --check src/mcp/` - Formatting correct

## Manual Testing with MCP Inspector

After implementation, test with MCP Inspector:

```bash
# Install MCP Inspector
pip install mcp-inspector

# Run inspector (starts web UI)
mcp-inspector python -m src.mcp

# Open browser to http://localhost:5173
# Test each tool with sample inputs
```

## Claude Desktop Configuration

For testing with Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "knowledge-extraction": {
      "command": "python",
      "args": ["-m", "src.mcp"],
      "cwd": "/path/to/knowledge_extraction-orchestrator",
      "env": {
        "KE_API_BASE_URL": "http://localhost:8000",
        "KE_API_KEY": "your-api-key"
      }
    }
  }
}
```

## Definition of Done

- [ ] MCP package structure created (`src/mcp/`)
- [ ] Configuration module with pydantic-settings
- [ ] Async HTTP client with retry logic
- [ ] FastMCP server with lifespan management
- [ ] Project tools: create_project, list_projects, get_project, list_templates
- [ ] Acquisition tools: crawl_website, scrape_urls, get_job_status
- [ ] Extraction tools: extract_knowledge, list_extractions
- [ ] Search tools: search_knowledge, list_entities, get_entity_summary
- [ ] Report tools: create_report, list_reports, get_report
- [ ] Template resources registered
- [ ] Workflow prompts registered
- [ ] All unit tests pass
- [ ] No lint errors
- [ ] Server starts successfully with `python -m src.mcp`
- [ ] PR created with title: `feat: MCP server for Knowledge Extraction API`

## PR Description Template

```markdown
## Summary

- Add MCP (Model Context Protocol) server to expose Knowledge Extraction API to AI assistants
- Implement 14 MCP tools across 5 categories (projects, acquisition, extraction, search, reports)
- Add template resources and workflow prompts for guided usage
- Support STDIO transport for Claude Desktop/Claude Code integration

## Tools Implemented

| Category | Tools |
|----------|-------|
| Projects | create_project, list_projects, get_project, list_templates |
| Acquisition | crawl_website, scrape_urls, get_job_status |
| Extraction | extract_knowledge, list_extractions |
| Search | search_knowledge, list_entities, get_entity_summary |
| Reports | create_report, list_reports, get_report |

## Test Plan

- [x] Unit tests for API client
- [x] Unit tests for all tool implementations
- [x] Integration tests (manual - requires running API)
- [x] MCP Inspector manual testing
- [x] Lint and format checks pass

## Configuration

Set environment variables:
- `KE_API_BASE_URL`: API base URL (default: http://localhost:8000)
- `KE_API_KEY`: API authentication key (optional)

## Usage

```bash
# Run MCP server
python -m src.mcp

# Or configure in Claude Desktop
```
```
