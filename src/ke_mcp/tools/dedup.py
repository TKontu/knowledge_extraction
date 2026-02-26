"""Domain boilerplate deduplication MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP

from ..client import APIError

logger = logging.getLogger("mcp.tools.dedup")


def register_dedup_tools(mcp: FastMCP) -> None:
    """Register domain boilerplate deduplication tools."""

    @mcp.tool()
    async def analyze_boilerplate(
        project_id: Annotated[str, "Project UUID"],
        source_groups: Annotated[
            list[str] | None, "Optional source groups to analyze"
        ] = None,
        threshold_pct: Annotated[
            float | None, "Boilerplate threshold (default 0.7)"
        ] = None,
        min_pages: Annotated[int | None, "Min pages per domain (default 5)"] = None,
        min_block_chars: Annotated[int | None, "Min block chars (default 50)"] = None,
        ctx: Context = None,
    ) -> dict:
        """Analyze domains for boilerplate content and clean sources.

        Scans all pages per domain, identifies repeating blocks (cookie banners,
        navs, footers), stores cleaned versions. Extraction automatically uses
        cleaned content when domain_dedup_enabled=True.

        After crawling, use extract_knowledge() to process the content.

        Example:
            analyze_boilerplate(
                project_id="...",
                source_groups=["Acme Inc"],
                threshold_pct=0.7
            )
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.analyze_boilerplate(
                project_id=project_id,
                source_groups=source_groups,
                threshold_pct=threshold_pct,
                min_pages=min_pages,
                min_block_chars=min_block_chars,
            )
            return {
                "success": True,
                "domains_analyzed": result["domains_analyzed"],
                "domains_with_boilerplate": result["domains_with_boilerplate"],
                "total_pages_cleaned": result["total_pages_cleaned"],
                "total_bytes_removed": result["total_bytes_removed"],
                "domains": result.get("domains", []),
            }
        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def get_boilerplate_stats(
        project_id: Annotated[str, "Project UUID"],
        ctx: Context = None,
    ) -> dict:
        """Get per-domain boilerplate statistics for a project.

        Returns stats from previous analyze_boilerplate() runs including
        pages analyzed, blocks identified, and bytes removed per domain.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            result = await client.get_boilerplate_stats(project_id)
            return {
                "success": True,
                "project_id": result["project_id"],
                "domain_count": result["domain_count"],
                "domains": result.get("domains", []),
            }
        except APIError as e:
            return {"success": False, "error": e.message}
