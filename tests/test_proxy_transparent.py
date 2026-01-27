"""Tests for transparent proxy functionality in ProxyAdapter."""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import AioHTTPTestCase

from src.services.proxy.flaresolverr_adapter import ProxyAdapter
from src.services.proxy.flaresolverr_client import FlareSolverrResponse


class TestProxyAdapter(AioHTTPTestCase):
    """Test suite for ProxyAdapter transparent proxy functionality."""

    async def get_application(self):
        """Create test application with ProxyAdapter routes."""
        # Create adapter with test configuration
        self.adapter = ProxyAdapter(
            flaresolverr_url="http://test-flaresolverr:8191",
            blocked_domains=["weg.net", "siemens.com"],
            max_timeout=60000,
        )

        # Create test app
        app = web.Application()
        app.router.add_route("*", "/{path:.*}", self.adapter.handle_request)
        return app

    @pytest.mark.asyncio
    async def test_extract_url_explicit_proxy_format(self):
        """Test URL extraction from explicit proxy format (GET /http://example.com/)."""
        # Mock request with explicit proxy format
        request = Mock(spec=web.Request)
        request.path = "/http://example.com/path/to/resource"
        request.headers = {}

        url = self.adapter._extract_url(request)

        assert url == "http://example.com/path/to/resource"

    @pytest.mark.asyncio
    async def test_extract_url_transparent_proxy_format(self):
        """Test URL extraction from transparent proxy format (Host header)."""
        # Mock request with transparent proxy format
        request = Mock(spec=web.Request)
        request.path = "/path/to/resource"
        request.headers = {"Host": "example.com"}

        url = self.adapter._extract_url(request)

        assert url == "http://example.com/path/to/resource"

    @pytest.mark.asyncio
    async def test_extract_url_transparent_proxy_root(self):
        """Test URL extraction for root path in transparent mode."""
        request = Mock(spec=web.Request)
        request.path = "/"
        request.headers = {"Host": "www.example.com:8080"}

        url = self.adapter._extract_url(request)

        assert url == "http://www.example.com:8080/"

    @pytest.mark.asyncio
    async def test_extract_url_https_explicit(self):
        """Test URL extraction with HTTPS in explicit format."""
        request = Mock(spec=web.Request)
        request.path = "/https://secure.example.com/api"
        request.headers = {}

        url = self.adapter._extract_url(request)

        assert url == "https://secure.example.com/api"

    @pytest.mark.asyncio
    async def test_handle_connect_blocked_domain(self):
        """Test CONNECT method blocks HTTPS to blocked domains."""
        # Mock CONNECT request to blocked domain
        request = Mock(spec=web.Request)
        request.method = "CONNECT"
        request.path = "/weg.net:443"

        response = await self.adapter.handle_connect(request)

        assert response.status == 502
        assert "HTTPS not supported" in response.text
        assert "weg.net" in response.text

    @pytest.mark.asyncio
    async def test_handle_connect_blocked_subdomain(self):
        """Test CONNECT method blocks HTTPS to blocked subdomains."""
        request = Mock(spec=web.Request)
        request.method = "CONNECT"
        request.path = "/www.siemens.com:443"

        response = await self.adapter.handle_connect(request)

        assert response.status == 502
        assert "HTTPS not supported" in response.text

    @pytest.mark.asyncio
    async def test_handle_connect_non_blocked_domain(self):
        """Test CONNECT method returns 501 for non-blocked domains (MVP)."""
        request = Mock(spec=web.Request)
        request.method = "CONNECT"
        request.path = "/example.com:443"

        response = await self.adapter.handle_connect(request)

        assert response.status == 501
        assert "Not Implemented" in response.text

    @pytest.mark.asyncio
    async def test_handle_request_routes_connect(self):
        """Test handle_request routes CONNECT to handle_connect."""
        request = Mock(spec=web.Request)
        request.method = "CONNECT"
        request.path = "/blocked.weg.net:443"

        # Patch handle_connect to verify it's called
        with patch.object(
            self.adapter, "handle_connect", new_callable=AsyncMock
        ) as mock_connect:
            mock_connect.return_value = web.Response(text="test", status=502)
            await self.adapter.handle_request(request)

        mock_connect.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_request_blocks_https_to_blocked_domain(self):
        """Test handle_request blocks HTTPS URLs to blocked domains."""
        # Mock request with HTTPS to blocked domain
        request = Mock(spec=web.Request)
        request.method = "GET"
        request.path = "/https://www.weg.net/products"
        request.headers = {}

        response = await self.adapter.handle_request(request)

        assert response.status == 502
        assert "HTTPS not supported" in response.text
        assert "weg.net" in response.text

    @pytest.mark.asyncio
    async def test_handle_request_allows_http_to_blocked_domain(self):
        """Test handle_request allows HTTP to blocked domains via FlareSolverr."""
        # Mock FlareSolverr response
        mock_flaresolverr_response = FlareSolverrResponse(
            url="http://www.weg.net/",
            status=200,
            cookies=[],
            headers={"Content-Type": "text/html"},
            html="<html>Test</html>",
            user_agent="Mozilla/5.0",
        )

        # Patch FlareSolverr client
        with patch.object(
            self.adapter.flaresolverr_client,
            "solve_request",
            new_callable=AsyncMock,
        ) as mock_solve:
            mock_solve.return_value = mock_flaresolverr_response

            # Mock request with HTTP to blocked domain (explicit format)
            request = Mock(spec=web.Request)
            request.method = "GET"
            request.path = "/http://www.weg.net/products"
            request.headers = {}

            response = await self.adapter.handle_request(request)

        assert response.status == 200
        assert "<html>Test</html>" in response.text
        mock_solve.assert_called_once_with("http://www.weg.net/products")

    @pytest.mark.asyncio
    async def test_handle_request_transparent_mode_blocked_domain(self):
        """Test transparent proxy mode with blocked domain."""
        # Mock FlareSolverr response
        mock_flaresolverr_response = FlareSolverrResponse(
            url="http://weg.net/",
            status=200,
            cookies=[],
            headers={},
            html="<html>WEG</html>",
            user_agent="Mozilla/5.0",
        )

        with patch.object(
            self.adapter.flaresolverr_client,
            "solve_request",
            new_callable=AsyncMock,
        ) as mock_solve:
            mock_solve.return_value = mock_flaresolverr_response

            # Transparent proxy request (Host header)
            request = Mock(spec=web.Request)
            request.method = "GET"
            request.path = "/products"
            request.headers = {"Host": "weg.net"}

            response = await self.adapter.handle_request(request)

        assert response.status == 200
        mock_solve.assert_called_once_with("http://weg.net/products")

    @pytest.mark.asyncio
    async def test_handle_request_allows_https_to_non_blocked_domain(self):
        """Test handle_request allows HTTPS to non-blocked domains (direct passthrough)."""
        # Mock httpx.AsyncClient
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value.__aenter__.return_value = mock_client

            # Mock HTTP response
            mock_http_response = Mock()
            mock_http_response.content = b"<html>Non-blocked</html>"
            mock_http_response.status_code = 200
            mock_http_response.headers = {"Content-Type": "text/html"}
            mock_client.get.return_value = mock_http_response

            # Request HTTPS to non-blocked domain
            request = Mock(spec=web.Request)
            request.method = "GET"
            request.path = "/https://example.com/api"
            request.headers = {}

            response = await self.adapter.handle_request(request)

        assert response.status == 200
        assert response.body == b"<html>Non-blocked</html>"

    @pytest.mark.asyncio
    async def test_subdomain_matching(self):
        """Test that subdomains are correctly matched to blocked domains."""
        # Test direct match
        assert self.adapter.should_use_flaresolverr("weg.net") is True

        # Test subdomain match
        assert self.adapter.should_use_flaresolverr("www.weg.net") is True
        assert self.adapter.should_use_flaresolverr("shop.siemens.com") is True

        # Test non-match
        assert self.adapter.should_use_flaresolverr("example.com") is False
        assert self.adapter.should_use_flaresolverr("notweg.net") is False

    @pytest.mark.asyncio
    async def test_domain_extraction_from_url(self):
        """Test should_use_flaresolverr extracts domain from full URL."""
        # Test with HTTP URL
        assert (
            self.adapter.should_use_flaresolverr("http://www.weg.net/products") is True
        )

        # Test with HTTPS URL
        assert self.adapter.should_use_flaresolverr("https://siemens.com/") is True

        # Test with non-blocked URL
        assert self.adapter.should_use_flaresolverr("http://example.com/path") is False
