"""MCP Tools registration."""

from mcp.server.fastmcp import FastMCP

from .acquisition import register_acquisition_tools
from .dedup import register_dedup_tools
from .extraction import register_extraction_tools
from .projects import register_project_tools
from .reports import register_report_tools
from .search import register_search_tools


def register_all_tools(mcp: FastMCP) -> None:
    """Register all MCP tools."""
    register_project_tools(mcp)
    register_acquisition_tools(mcp)
    register_extraction_tools(mcp)
    register_search_tools(mcp)
    register_report_tools(mcp)
    register_dedup_tools(mcp)
