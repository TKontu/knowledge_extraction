"""Integration tests for MCP server (requires running API)."""

import os

import pytest

# Skip if no API available
pytestmark = pytest.mark.skipif(
    not os.environ.get("KE_API_BASE_URL"),
    reason="KE_API_BASE_URL not set - skipping integration tests",
)


class TestMCPServerIntegration:
    """Integration tests requiring a running API."""

    @pytest.fixture
    async def client(self):
        """Create real client connected to API."""
        from src.mcp.client import KnowledgeExtractionClient
        from src.mcp.config import MCPSettings

        settings = MCPSettings()
        client = KnowledgeExtractionClient(settings)
        await client.connect()
        yield client
        await client.close()

    @pytest.mark.asyncio
    async def test_list_projects(self, client):
        """Test listing projects against real API."""
        projects = await client.list_projects()
        assert isinstance(projects, list)

    @pytest.mark.asyncio
    async def test_list_templates(self, client):
        """Test listing templates against real API."""
        templates = await client.list_templates()
        assert isinstance(templates, list)
        assert len(templates) > 0
