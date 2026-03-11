"""Report generation MCP tools."""

import logging
from typing import Annotated, Literal

from mcp.server.fastmcp import Context, FastMCP

from ..client import APIError

logger = logging.getLogger("mcp.tools.reports")


def register_report_tools(mcp: FastMCP) -> None:
    """Register report tools."""

    @mcp.tool()
    async def create_report(
        project_id: Annotated[str, "Project UUID"],
        report_type: Annotated[
            Literal["single", "comparison", "table"],
            "Report type: single (one company), comparison (multiple), table (structured data)",
        ],
        source_groups: Annotated[
            list[str] | None,
            "Companies/source groups to include. If None or empty, includes ALL source groups in the project.",
        ] = None,
        title: Annotated[str | None, "Custom report title"] = None,
        output_format: Annotated[
            Literal["md", "xlsx"], "Output format: md (markdown) or xlsx (Excel)"
        ] = "md",
        group_by: Annotated[
            Literal["source", "domain", "consolidated"],
            "Grouping: 'source' (one row per URL), 'domain' (LLM smart merge per domain), or 'consolidated' (pre-computed consolidated data)",
        ] = "source",
        include_merge_metadata: Annotated[
            bool,
            "Include merge provenance (sources_used, confidence per column) when group_by='domain'",
        ] = False,
        max_extractions: Annotated[
            int,
            "Max extractions per source_group to include (default 50, max 200). Increase for complete data.",
        ] = 50,
        layout: Annotated[
            Literal["multi_sheet", "single_sheet"],
            "Layout for consolidated reports: 'multi_sheet' (separate entity tabs) or 'single_sheet' (denormalized flat)",
        ] = "multi_sheet",
        entity_focus: Annotated[
            str | None,
            "Entity group for single_sheet denormalization. None=company-only with counts, 'all'=superset, or specific group name.",
        ] = None,
        include_provenance: Annotated[
            bool,
            "Include provenance columns (source_count, avg_agreement, grounded_pct) in consolidated reports",
        ] = False,
        ctx: Context = None,
    ) -> dict:
        """Generate an analysis report from extracted knowledge.

        Report types:
        - single: Summarize findings for one company with LLM synthesis
        - comparison: Compare findings across multiple companies
        - table: Tabular format with all field groups flattened into columns

        Grouping (table reports only):
        - source: One row per URL, all field group extractions consolidated (default)
        - domain: One row per domain, LLM smart merge synthesizes values from all URLs
        - consolidated: Pre-computed consolidated data with multi-sheet or single-sheet layout

        Example:
            # Consolidated multi-sheet Excel report
            create_report(
                project_id="...",
                report_type="table",
                group_by="consolidated",
                output_format="xlsx"
            )

            # Single-sheet with entity denormalization
            create_report(
                project_id="...",
                report_type="table",
                group_by="consolidated",
                layout="single_sheet",
                entity_focus="products_gearbox",
                output_format="xlsx"
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
                group_by=group_by,
                include_merge_metadata=include_merge_metadata,
                max_extractions=max_extractions,
                layout=layout,
                entity_focus=entity_focus,
                include_provenance=include_provenance,
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
