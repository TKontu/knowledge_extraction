"""Tests for proxy adapter."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web

from src.services.proxy.flaresolverr_adapter import ProxyAdapter
from src.services.proxy.flaresolverr_client import FlareSolverrResponse


class TestProxyAdapterUnit:
    """Unit tests for ProxyAdapter."""

    def test_should_use_flaresolverr_blocked_domain(self):
        """Test that blocked domain returns True."""
        adapter = ProxyAdapter(
            flaresolverr_url="http://flaresolverr:8191",
            blocked_domains=["weg.net", "siemens.com"],
            max_timeout=60000,
        )
        assert adapter.should_use_flaresolverr("weg.net") is True

    def test_should_use_flaresolverr_non_blocked_domain(self):
        """Test that non-blocked domain returns False."""
        adapter = ProxyAdapter(
            flaresolverr_url="http://flaresolverr:8191",
            blocked_domains=["weg.net", "siemens.com"],
            max_timeout=60000,
        )
        assert adapter.should_use_flaresolverr("example.com") is False

    def test_should_use_flaresolverr_case_insensitive(self):
        """Test that domain check is case insensitive."""
        adapter = ProxyAdapter(
            flaresolverr_url="http://flaresolverr:8191",
            blocked_domains=["weg.net"],
            max_timeout=60000,
        )
        assert adapter.should_use_flaresolverr("WEG.NET") is True
        assert adapter.should_use_flaresolverr("Weg.Net") is True

    @pytest.mark.asyncio
    async def test_handle_request_flaresolverr_routing(self):
        """Test that blocked domain routes through FlareSolverr."""
        adapter = ProxyAdapter(
            flaresolverr_url="http://flaresolverr:8191",
            blocked_domains=["weg.net"],
            max_timeout=60000,
        )

        # Mock FlareSolverr client
        mock_response = FlareSolverrResponse(
            url="https://www.weg.net/",
            status=200,
            cookies=[],
            headers={"Content-Type": "text/html"},
            html="<html>Test content</html>",
            user_agent="Mozilla/5.0",
        )
        adapter.flaresolverr_client.solve_request = AsyncMock(
            return_value=mock_response
        )

        # Create mock request
        mock_request = Mock(spec=web.Request)
        mock_request.path = "/https://www.weg.net/"

        # Handle request
        response = await adapter.handle_request(mock_request)

        # Verify response
        assert response.status == 200
        assert response.text == "<html>Test content</html>"
        adapter.flaresolverr_client.solve_request.assert_called_once_with(
            "https://www.weg.net/"
        )

    @pytest.mark.asyncio
    async def test_handle_request_direct_passthrough(self):
        """Test that non-blocked domain uses direct passthrough."""
        adapter = ProxyAdapter(
            flaresolverr_url="http://flaresolverr:8191",
            blocked_domains=["weg.net"],
            max_timeout=60000,
        )

        # Mock httpx client
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = Mock()
            mock_response.content = b"<html>Direct content</html>"
            mock_response.status_code = 200
            mock_response.headers = {"Content-Type": "text/html"}
            mock_client.get.return_value = mock_response
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Create mock request
            mock_request = Mock(spec=web.Request)
            mock_request.path = "/https://example.com/"

            # Handle request
            response = await adapter.handle_request(mock_request)

            # Verify response
            assert response.status == 200
            assert response.body == b"<html>Direct content</html>"
            mock_client.get.assert_called_once_with("https://example.com/")

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test health check endpoint."""
        adapter = ProxyAdapter(
            flaresolverr_url="http://flaresolverr:8191",
            blocked_domains=["weg.net", "siemens.com"],
            max_timeout=60000,
        )

        mock_request = Mock(spec=web.Request)
        response = await adapter.health_check(mock_request)

        # Parse JSON response
        import json

        body = json.loads(response.text)

        assert response.status == 200
        assert body["status"] == "ok"
        assert body["flaresolverr_url"] == "http://flaresolverr:8191"
        assert set(body["blocked_domains"]) == {"weg.net", "siemens.com"}
