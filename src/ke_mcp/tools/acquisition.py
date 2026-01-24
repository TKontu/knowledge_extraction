"""Web crawling and scraping MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP

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
            bool, "Wait for crawl to finish (may take several minutes)"
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
