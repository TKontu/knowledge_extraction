"""Project management MCP tools."""

import logging
from typing import Annotated

from mcp.server.fastmcp import Context, FastMCP

from ..client import APIError

logger = logging.getLogger("mcp.tools.projects")


def register_project_tools(mcp: FastMCP) -> None:
    """Register project management tools."""

    @mcp.tool()
    async def create_project(
        name: Annotated[str, "Unique project name (lowercase, hyphens allowed)"],
        template: Annotated[
            str | None,
            "Template name: company_analysis, research_survey, contract_review, book_catalog, drivetrain_company_analysis, drivetrain_company_simple, or default",
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
        - drivetrain_company_analysis: Detailed extraction for industrial drivetrain companies
        - drivetrain_company_simple: Simplified extraction for drivetrain companies
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
                "schema_name": project.get("extraction_schema", {}).get(
                    "name", "unknown"
                ),
                "field_group_count": len(
                    project.get("extraction_schema", {}).get("field_groups", [])
                ),
                "entity_types": [et["name"] for et in project.get("entity_types", [])],
                "created_at": project.get("created_at"),
            }
        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def list_templates(ctx: Context = None) -> dict:
        """List available project templates with descriptions.

        Templates provide pre-configured extraction schemas for common use cases.
        Use these template names with create_project() or get_template_details()
        for more information about what each template extracts.
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            # Use the details=true endpoint to get full template info
            result = await client.list_templates(details=True)

            return {
                "success": True,
                "count": result["count"],
                "templates": [
                    {
                        "name": t["name"],
                        "description": t["description"],
                        "field_group_count": len(t.get("field_groups", [])),
                        "entity_type_count": len(t.get("entity_types", [])),
                    }
                    for t in result["templates"]
                ],
            }
        except APIError as e:
            return {"success": False, "error": e.message}

    @mcp.tool()
    async def get_template_details(
        template_name: Annotated[
            str, "Template name (e.g., 'company_analysis', 'default')"
        ],
        ctx: Context = None,
    ) -> dict:
        """Get detailed information about a specific template.

        Returns the template's field groups (what gets extracted) and entity types
        (what entities are created). Use this to understand what a template does
        before creating a project with it.

        Example:
            get_template_details("company_analysis")
            get_template_details("drivetrain_company_analysis")
        """
        client = ctx.request_context.lifespan_context["client"]

        try:
            template = await client.get_template(template_name)

            # Format field groups for readability
            field_groups = []
            for fg in template.get("field_groups", []):
                field_groups.append(
                    {
                        "name": fg["name"],
                        "description": fg.get("description"),
                        "is_entity_list": fg.get("is_entity_list", False),
                        "fields": [
                            {
                                "name": f["name"],
                                "type": f["field_type"],
                                "required": f.get("required", False),
                                "description": f.get("description"),
                            }
                            for f in fg.get("fields", [])
                        ],
                    }
                )

            return {
                "success": True,
                "name": template["name"],
                "description": template["description"],
                "field_groups": field_groups,
                "entity_types": [
                    {"name": et["name"], "description": et.get("description")}
                    for et in template.get("entity_types", [])
                ],
            }
        except APIError as e:
            return {"success": False, "error": e.message}
