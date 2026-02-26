"""Async HTTP client for the Knowledge Extraction API."""

import asyncio
import logging
from typing import Any

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
            headers["X-API-Key"] = self.settings.api_key

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
                    error_data = response.json()
                    detail = error_data.get("detail", "Validation error")
                    raise APIError(f"Validation error: {detail}", 422)
                elif response.status_code >= 500:
                    # Server errors are transient - retry with backoff
                    last_error = APIError(
                        f"Server error: {response.status_code}", response.status_code
                    )
                    logger.warning(
                        f"Server error {response.status_code} (attempt {attempt + 1})"
                    )
                    await asyncio.sleep(2**attempt)
                    continue

                response.raise_for_status()
                return response.json()

            except httpx.TimeoutException as e:
                last_error = e
                logger.warning(f"Request timeout (attempt {attempt + 1})")
                await asyncio.sleep(2**attempt)
            except httpx.HTTPStatusError as e:
                raise APIError(str(e), e.response.status_code) from e
            except APIError:
                raise
            except Exception as e:
                last_error = e
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                await asyncio.sleep(2**attempt)

        raise APIError(
            f"Request failed after {self.settings.max_retries} attempts: {last_error}"
        )

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

    async def list_templates(self, details: bool = False) -> list[str] | dict[str, Any]:
        """List available project templates.

        Args:
            details: If True, return full template details including field groups.

        Returns:
            List of template names (if details=False) or dict with full details.
        """
        params = {"details": details} if details else None
        return await self._request("GET", "/api/v1/projects/templates", params=params)

    async def get_template(self, template_name: str) -> dict[str, Any]:
        """Get detailed information about a specific template.

        Args:
            template_name: Template name (e.g., 'company_analysis', 'default')

        Returns:
            Full template details including field groups and entity types.
        """
        return await self._request("GET", f"/api/v1/projects/templates/{template_name}")

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
        smart_crawl_enabled: bool = True,
        relevance_threshold: float | None = None,
        focus_terms: list[str] | None = None,
    ) -> dict[str, Any]:
        """Start a crawl job.

        Args:
            url: Starting URL to crawl.
            project_id: Project UUID to store sources in.
            company: Company/source group name.
            max_depth: How many levels deep to crawl (1-10).
            limit: Maximum number of pages to crawl.
            prefer_english_only: Filter out non-English pages.
            smart_crawl_enabled: Use Map + Filter + Batch Scrape flow.
            relevance_threshold: Embedding similarity threshold (0.0-1.0).
            focus_terms: Semantic focus terms for URL filtering.
        """
        payload = {
            "url": url,
            "project_id": project_id,
            "company": company,
            "max_depth": max_depth,
            "limit": limit,
            "prefer_english_only": prefer_english_only,
            "auto_extract": False,  # Control extraction separately
            "smart_crawl_enabled": smart_crawl_enabled,
        }

        # Add optional smart crawl parameters
        if relevance_threshold is not None:
            payload["relevance_threshold"] = relevance_threshold
        if focus_terms is not None:
            payload["focus_terms"] = focus_terms

        return await self._request("POST", "/api/v1/crawl", json=payload)

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
        force: bool = False,
    ) -> dict[str, Any]:
        """Start an extraction job.

        Args:
            project_id: Project UUID.
            source_ids: Optional specific source IDs to extract.
            force: If True, re-extract sources even if already extracted.

        Returns:
            Job creation response with job_id.
        """
        return await self._request(
            "POST",
            f"/api/v1/projects/{project_id}/extract",
            json={"source_ids": source_ids, "force": force},
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
    # Source Operations
    # =========================================================================

    async def list_sources(
        self,
        project_id: str,
        source_group: str | None = None,
        status: str | None = None,
        source_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List sources with filters."""
        params = {"limit": limit, "offset": offset}
        if source_group:
            params["source_group"] = source_group
        if status:
            params["status"] = status
        if source_type:
            params["source_type"] = source_type

        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/sources",
            params=params,
        )

    async def get_source_summary(self, project_id: str) -> dict[str, Any]:
        """Get source summary (counts by status and source group)."""
        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/sources/summary",
        )

    # =========================================================================
    # Domain Boilerplate Deduplication
    # =========================================================================

    async def analyze_boilerplate(
        self,
        project_id: str,
        source_groups: list[str] | None = None,
        threshold_pct: float | None = None,
        min_pages: int | None = None,
        min_block_chars: int | None = None,
    ) -> dict[str, Any]:
        """Analyze domains for boilerplate and clean sources.

        Args:
            project_id: Project UUID.
            source_groups: Optional filter by source groups.
            threshold_pct: Boilerplate threshold (default 0.7).
            min_pages: Min pages per domain (default 5).
            min_block_chars: Min block chars (default 50).
        """
        params: dict[str, Any] = {}
        if source_groups:
            params["source_groups"] = source_groups
        if threshold_pct is not None:
            params["threshold_pct"] = threshold_pct
        if min_pages is not None:
            params["min_pages"] = min_pages
        if min_block_chars is not None:
            params["min_block_chars"] = min_block_chars

        return await self._request(
            "POST",
            f"/api/v1/projects/{project_id}/analyze-boilerplate",
            params=params,
        )

    async def get_boilerplate_stats(self, project_id: str) -> dict[str, Any]:
        """Get per-domain boilerplate statistics."""
        return await self._request(
            "GET",
            f"/api/v1/projects/{project_id}/boilerplate-stats",
        )

    # =========================================================================
    # Report Operations
    # =========================================================================

    async def create_report(
        self,
        project_id: str,
        report_type: str,
        source_groups: list[str] | None = None,
        title: str | None = None,
        output_format: str = "md",
        group_by: str = "source",
        include_merge_metadata: bool = False,
        max_extractions: int = 50,
    ) -> dict[str, Any]:
        """Generate a report.

        Args:
            project_id: Project UUID
            report_type: Type of report (single, comparison, table)
            source_groups: Companies to include. If None, includes all.
            title: Custom report title
            output_format: Output format (md, xlsx)
            group_by: Grouping strategy (source, domain)
            include_merge_metadata: Include merge provenance for domain grouping
            max_extractions: Max extractions per source_group (default 50)
        """
        return await self._request(
            "POST",
            f"/api/v1/projects/{project_id}/reports",
            json={
                "type": report_type,
                "source_groups": source_groups,
                "title": title,
                "output_format": output_format,
                "group_by": group_by,
                "include_merge_metadata": include_merge_metadata,
                "max_extractions": max_extractions,
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

    async def cancel_job(self, job_id: str) -> dict[str, Any]:
        """Request cancellation of a queued or running job.

        Args:
            job_id: Job UUID to cancel.

        Returns:
            Cancellation response with job_id, status, message.
        """
        return await self._request("POST", f"/api/v1/jobs/{job_id}/cancel")

    async def cleanup_job(
        self, job_id: str, delete_job: bool = False
    ) -> dict[str, Any]:
        """Delete all artifacts created by a job.

        Args:
            job_id: Job UUID to cleanup.
            delete_job: Also delete the job record itself.

        Returns:
            Cleanup response with deletion counts.
        """
        return await self._request(
            "POST",
            f"/api/v1/jobs/{job_id}/cleanup",
            json={"delete_job": delete_job},
        )

    async def delete_job_record(
        self, job_id: str, cleanup: bool = False
    ) -> dict[str, Any]:
        """Delete a job record from the database.

        Args:
            job_id: Job UUID to delete.
            cleanup: Also delete associated artifacts.

        Returns:
            Deletion response with status.
        """
        return await self._request(
            "DELETE",
            f"/api/v1/jobs/{job_id}",
            params={"cleanup": cleanup},
        )

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
            self.get_crawl_status if job_type == "crawl" else self.get_scrape_status
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
