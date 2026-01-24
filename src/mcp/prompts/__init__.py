"""MCP Prompts registration."""

from mcp.server.fastmcp import FastMCP

from .workflows import register_workflow_prompts


def register_all_prompts(mcp: FastMCP) -> None:
    """Register all MCP prompts."""
    register_workflow_prompts(mcp)
