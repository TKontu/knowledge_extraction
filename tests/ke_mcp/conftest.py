"""Test fixtures for MCP server tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.ke_mcp.client import KnowledgeExtractionClient
from src.ke_mcp.config import MCPSettings


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    return MCPSettings(
        api_base_url="http://test-api:8000",
        api_key="test-key",
        timeout_seconds=30,
        max_retries=2,
        poll_interval=1,
        max_poll_attempts=10,
    )


@pytest.fixture
def mock_client(mock_settings):
    """Create mock API client."""
    client = KnowledgeExtractionClient(mock_settings)
    client._client = AsyncMock()
    return client


@pytest.fixture
def mock_context(mock_client):
    """Create mock MCP context."""
    context = MagicMock()
    context.request_context.lifespan_context = {
        "client": mock_client,
        "settings": mock_client.settings,
    }
    return context
