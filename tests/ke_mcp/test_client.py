"""Tests for MCP API client."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from src.ke_mcp.client import APIError, KnowledgeExtractionClient
from src.ke_mcp.config import MCPSettings


@pytest.mark.asyncio
async def test_client_connect_disconnect():
    """Test client lifecycle works."""
    settings = MCPSettings(api_base_url="http://test:8000")
    client = KnowledgeExtractionClient(settings)

    assert client._client is None

    await client.connect()
    assert client._client is not None

    await client.close()
    assert client._client is None


@pytest.mark.asyncio
async def test_client_retry_on_timeout():
    """Test client retries with backoff on timeout."""
    settings = MCPSettings(
        api_base_url="http://test:8000",
        max_retries=3,
    )
    client = KnowledgeExtractionClient(settings)
    await client.connect()

    # Mock timeout on all attempts
    client._client.request = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

    with pytest.raises(APIError) as exc_info:
        await client._request("GET", "/test")

    assert "failed after 3 attempts" in str(exc_info.value.message)
    assert client._client.request.call_count == 3

    await client.close()


@pytest.mark.asyncio
async def test_client_raises_api_error_on_404():
    """Test proper error mapping for 404."""
    settings = MCPSettings(api_base_url="http://test:8000")
    client = KnowledgeExtractionClient(settings)
    await client.connect()

    # Mock 404 response
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_response.json.return_value = {"detail": "Not found"}
    client._client.request = AsyncMock(return_value=mock_response)

    with pytest.raises(APIError) as exc_info:
        await client._request("GET", "/test")

    assert exc_info.value.status_code == 404
    assert "Resource not found" in exc_info.value.message

    await client.close()


@pytest.mark.asyncio
async def test_client_raises_api_error_on_422():
    """Test validation errors are properly mapped."""
    settings = MCPSettings(api_base_url="http://test:8000")
    client = KnowledgeExtractionClient(settings)
    await client.connect()

    # Mock 422 response
    mock_response = MagicMock()
    mock_response.status_code = 422
    mock_response.json.return_value = {"detail": "Invalid input"}
    client._client.request = AsyncMock(return_value=mock_response)

    with pytest.raises(APIError) as exc_info:
        await client._request("GET", "/test")

    assert exc_info.value.status_code == 422
    assert "Validation error" in exc_info.value.message

    await client.close()


@pytest.mark.asyncio
async def test_wait_for_job_returns_on_completion():
    """Test polling works and returns when job completes."""
    settings = MCPSettings(api_base_url="http://test:8000", poll_interval=1)
    client = KnowledgeExtractionClient(settings)
    await client.connect()

    # First call: pending, second call: completed
    responses = [
        {"job_id": "123", "status": "pending"},
        {"job_id": "123", "status": "completed", "pages_total": 10},
    ]

    call_count = 0

    def create_response():
        nonlocal call_count
        mock_response = MagicMock()
        mock_response.status_code = 200
        result = responses[min(call_count, len(responses) - 1)]
        call_count += 1
        mock_response.json.return_value = result
        return mock_response

    client._client.request = AsyncMock(
        side_effect=lambda *args, **kwargs: create_response()
    )

    result = await client.wait_for_job("123", "crawl")

    assert result["status"] == "completed"
    assert result["pages_total"] == 10

    await client.close()


@pytest.mark.asyncio
async def test_wait_for_job_returns_timeout():
    """Test timeout after max attempts."""
    settings = MCPSettings(
        api_base_url="http://test:8000", poll_interval=1, max_poll_attempts=10
    )
    client = KnowledgeExtractionClient(settings)
    await client.connect()

    # Always return pending
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {"job_id": "123", "status": "pending"}
    client._client.request = AsyncMock(return_value=mock_response)

    result = await client.wait_for_job("123", "crawl")

    assert result["status"] == "timeout"
    assert "did not complete" in result["error"]

    await client.close()
