"""Knowledge extraction MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP

from ..client import APIError

logger = logging.getLogger("mcp.tools.extraction")


def register_extraction_tools(mcp: FastMCP) -> None:
    """Register extraction tools."""

    @mcp.tool()
    async def extract_knowledge(
        project_id: Annotated[str, "Project UUID"],
        source_ids: Annotated[
            list[str] | None,
            "Specific source UUIDs to extract (omit for all pending sources)",
        ] = None,
        force: Annotated[
            bool,
            "If True, re-extract sources even if already extracted (useful for re-running with different template)",
        ] = False,
        ctx: Context = None,
    ) -> dict:
        """Run LLM-based knowledge extraction on sources.

        Processes sources using the project's extraction schema and creates
        structured extractions. This uses the LLM to identify facts, entities,
        and relationships in the content.

        If the project has an extraction_schema (from a template), uses schema-based
        extraction with field groups. Otherwise, uses generic fact extraction.

        If source_ids is omitted, extracts from all sources with 'pending' status.
        Use force=True to re-extract sources that were already extracted.

        This operation may take several minutes depending on the number of sources.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            job = await client.create_extraction(
                project_id=project_id,
                source_ids=source_ids,
                force=force,
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
        min_confidence: Annotated[
            float | None, "Minimum confidence score (0.0-1.0)"
        ] = None,
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
