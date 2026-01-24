"""Search and entity query MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP

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
            list[str] | None, "Filter by specific companies/source groups"
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
        entity_type: Annotated[
            str | None, "Filter by entity type (e.g., 'plan', 'feature')"
        ] = None,
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
