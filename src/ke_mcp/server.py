"""MCP Server entry point for Knowledge Extraction API."""

from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from .client import KnowledgeExtractionClient
from .config import MCPSettings, configure_logging

# Configure logging FIRST (before any other imports that might log)
logger = configure_logging()

# Global client instance (set during lifespan)
_api_client: KnowledgeExtractionClient | None = None


def get_client() -> KnowledgeExtractionClient:
    """Get the API client instance."""
    if _api_client is None:
        raise RuntimeError("API client not initialized")
    return _api_client


@asynccontextmanager
async def lifespan(server: FastMCP):
    """Manage API client lifecycle.

    Initializes the HTTP client on startup and closes it on shutdown.
    The client is available via ctx.request_context.lifespan_context["client"].
    """
    global _api_client

    settings = MCPSettings()
    _api_client = KnowledgeExtractionClient(settings)

    try:
        await _api_client.connect()
        logger.info("MCP server started")
        yield {"client": _api_client, "settings": settings}
    finally:
        await _api_client.close()
        _api_client = None
        logger.info("MCP server stopped")


# Create the FastMCP server instance
mcp = FastMCP(
    name="knowledge-extraction",
    lifespan=lifespan,
)

# Import and register tools, resources, prompts
# ruff: noqa: E402
from .prompts import register_all_prompts
from .resources import register_all_resources
from .tools import register_all_tools

register_all_tools(mcp)
register_all_resources(mcp)
register_all_prompts(mcp)


def main():
    """Entry point for the MCP server."""
    logger.info("Starting Knowledge Extraction MCP Server...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
