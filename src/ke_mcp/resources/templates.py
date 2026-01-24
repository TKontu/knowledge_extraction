"""Project template MCP resources."""

from mcp.server.fastmcp import FastMCP


def register_template_resources(mcp: FastMCP) -> None:
    """Register template resources."""

    @mcp.resource("templates://overview")
    async def templates_overview() -> str:
        """Overview of available project templates."""
        return """# Knowledge Extraction Templates

## Available Templates

Use the `list_templates()` tool to see all available templates with descriptions.
Use `get_template_details(name)` to get detailed information about a specific template.

## Common Templates

- **company_analysis**: Extract technical facts from company documentation
- **research_survey**: Extract findings from academic papers
- **contract_review**: Extract legal terms from contracts
- **book_catalog**: Extract metadata from books
- **drivetrain_company_analysis**: Detailed extraction for industrial drivetrain companies
- **drivetrain_company_simple**: Simplified extraction for drivetrain companies
- **default**: Generic fact extraction for any content

## Usage

Use `create_project(name, template="template_name")` to create a project from a template.
"""
