"""MCP Resources registration."""

from mcp.server.fastmcp import FastMCP

from .templates import register_template_resources


def register_all_resources(mcp: FastMCP) -> None:
    """Register all MCP resources."""
    register_template_resources(mcp)
