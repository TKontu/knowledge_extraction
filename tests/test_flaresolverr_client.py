"""Tests for FlareSolverr client."""

import pytest
import httpx
from unittest.mock import AsyncMock, Mock

from src.services.proxy.flaresolverr_client import (
    FlareSolverrClient,
    FlareSolverrResponse,
    FlareSolverrError,
)


@pytest.fixture
def mock_http_client():
    """Create a mock httpx.AsyncClient."""
    client = AsyncMock(spec=httpx.AsyncClient)
    return client


@pytest.fixture
def flaresolverr_client(mock_http_client):
    """Create a FlareSolverrClient instance."""
    return FlareSolverrClient(
        base_url="http://flaresolverr:8191",
        max_timeout=60000,
        http_client=mock_http_client,
    )


@pytest.mark.asyncio
async def test_solve_request_success(flaresolverr_client, mock_http_client):
    """Test successful solve_request call."""
    # Mock response from FlareSolverr
    mock_response = Mock()
    mock_response.json.return_value = {
        "status": "ok",
        "solution": {
            "url": "https://www.weg.net/",
            "status": 200,
            "cookies": [{"name": "session", "value": "abc123"}],
            "headers": {"Content-Type": "text/html"},
            "response": "<html>Test content</html>",
            "userAgent": "Mozilla/5.0",
        },
    }
    mock_http_client.post = AsyncMock(return_value=mock_response)

    # Call solve_request
    result = await flaresolverr_client.solve_request("https://www.weg.net/")

    # Verify result
    assert isinstance(result, FlareSolverrResponse)
    assert result.url == "https://www.weg.net/"
    assert result.status == 200
    assert result.cookies == [{"name": "session", "value": "abc123"}]
    assert result.headers == {"Content-Type": "text/html"}
    assert result.html == "<html>Test content</html>"
    assert result.user_agent == "Mozilla/5.0"

    # Verify POST call
    mock_http_client.post.assert_called_once()
    call_args = mock_http_client.post.call_args
    assert call_args[0][0] == "http://flaresolverr:8191/v1"
    assert call_args[1]["json"]["cmd"] == "request.get"
    assert call_args[1]["json"]["url"] == "https://www.weg.net/"
    assert call_args[1]["json"]["maxTimeout"] == 60000


@pytest.mark.asyncio
async def test_solve_request_flaresolverr_error(flaresolverr_client, mock_http_client):
    """Test solve_request raises error when FlareSolverr returns error status."""
    # Mock error response
    mock_response = Mock()
    mock_response.json.return_value = {
        "status": "error",
        "message": "Failed to solve challenge",
    }
    mock_http_client.post = AsyncMock(return_value=mock_response)

    # Verify error is raised
    with pytest.raises(FlareSolverrError) as exc_info:
        await flaresolverr_client.solve_request("https://www.weg.net/")

    assert "error" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_solve_request_timeout(flaresolverr_client, mock_http_client):
    """Test solve_request handles timeout errors."""
    # Mock timeout
    mock_http_client.post = AsyncMock(side_effect=httpx.TimeoutException("Timeout"))

    # Verify error is raised
    with pytest.raises(FlareSolverrError) as exc_info:
        await flaresolverr_client.solve_request("https://www.weg.net/")

    assert "timeout" in str(exc_info.value).lower()


@pytest.mark.asyncio
async def test_solve_request_connection_error(flaresolverr_client, mock_http_client):
    """Test solve_request handles connection errors."""
    # Mock connection error
    mock_http_client.post = AsyncMock(
        side_effect=httpx.ConnectError("Connection failed")
    )

    # Verify error is raised
    with pytest.raises(FlareSolverrError) as exc_info:
        await flaresolverr_client.solve_request("https://www.weg.net/")

    assert "connection" in str(exc_info.value).lower() or "connect" in str(
        exc_info.value
    ).lower()


@pytest.mark.asyncio
async def test_context_manager(mock_http_client):
    """Test FlareSolverrClient can be used as async context manager."""
    async with FlareSolverrClient(
        base_url="http://flaresolverr:8191",
        max_timeout=60000,
        http_client=mock_http_client,
    ) as client:
        assert isinstance(client, FlareSolverrClient)

    # Verify close was called
    mock_http_client.aclose.assert_called_once()
