"""Tests for FirecrawlClient scraper service."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from services.scraper.client import FirecrawlClient, ScrapeResult


class TestFirecrawlClient:
    """Test suite for FirecrawlClient."""

    @pytest.fixture
    def firecrawl_url(self):
        """Firecrawl API URL for testing."""
        return "http://localhost:3002"

    @pytest.fixture
    def client(self, firecrawl_url):
        """Create FirecrawlClient instance."""
        return FirecrawlClient(base_url=firecrawl_url, timeout=30)

    @pytest.mark.asyncio
    async def test_client_initialization(self, client, firecrawl_url):
        """Test FirecrawlClient initializes correctly."""
        assert client.base_url == firecrawl_url
        assert client.timeout == 30
        assert client._http_client is not None

    @pytest.mark.asyncio
    async def test_scrape_success_returns_markdown_content(self, client):
        """Test successful scrape returns markdown content."""
        test_url = "https://example.com"
        mock_response_data = {
            "success": True,
            "data": {
                "markdown": "# Example Page\n\nThis is test content.",
                "metadata": {
                    "title": "Example Page",
                    "description": "Test page",
                    "statusCode": 200,
                },
            },
        }

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            # Create a mock response object
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json = Mock(return_value=mock_response_data)
            mock_post.return_value = mock_response

            result = await client.scrape(test_url)

            assert isinstance(result, ScrapeResult)
            assert result.url == test_url
            assert result.markdown == "# Example Page\n\nThis is test content."
            assert result.title == "Example Page"
            assert result.status_code == 200
            assert result.success is True
            assert result.error is None

    @pytest.mark.asyncio
    async def test_scrape_handles_missing_title_gracefully(self, client):
        """Test scrape handles missing title in metadata."""
        test_url = "https://example.com/notitle"
        mock_response_data = {
            "success": True,
            "data": {
                "markdown": "Content without title",
                "metadata": {
                    "statusCode": 200,
                },
            },
        }

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json = Mock(return_value=mock_response_data)
            mock_post.return_value = mock_response

            result = await client.scrape(test_url)

            assert result.title is None
            assert result.markdown == "Content without title"

    @pytest.mark.asyncio
    async def test_scrape_handles_404_error(self, client):
        """Test scrape handles 404 errors correctly."""
        test_url = "https://example.com/notfound"
        mock_response_data = {
            "success": False,
            "error": "Page not found",
        }

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_response = Mock()
            mock_response.status_code = 404
            mock_response.json = Mock(return_value=mock_response_data)
            mock_post.return_value = mock_response

            result = await client.scrape(test_url)

            assert result.success is False
            assert result.error == "Page not found"
            assert result.status_code == 404
            assert result.markdown is None

    @pytest.mark.asyncio
    async def test_scrape_handles_timeout_error(self, client):
        """Test scrape handles timeout errors with retry."""
        test_url = "https://example.com/slow"

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.side_effect = TimeoutError("Request timed out")

            result = await client.scrape(test_url)

            assert result.success is False
            assert "timeout" in result.error.lower()
            assert result.status_code is None

    @pytest.mark.asyncio
    async def test_scrape_handles_connection_error(self, client):
        """Test scrape handles connection errors."""
        test_url = "https://example.com/unreachable"

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_post.side_effect = Exception("Connection refused")

            result = await client.scrape(test_url)

            assert result.success is False
            assert result.error is not None
            assert "connection refused" in result.error.lower()

    @pytest.mark.asyncio
    async def test_scrape_includes_correct_request_format(self, client):
        """Test scrape sends correct request format to Firecrawl."""
        test_url = "https://example.com/page"
        mock_response_data = {
            "success": True,
            "data": {
                "markdown": "Test",
                "metadata": {"title": "Test", "statusCode": 200},
            },
        }

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json = Mock(return_value=mock_response_data)
            mock_post.return_value = mock_response

            await client.scrape(test_url)

            # Verify the request was made with correct parameters
            mock_post.assert_called_once()
            call_args = mock_post.call_args
            assert call_args[0][0].endswith("/v1/scrape")
            assert call_args[1]["json"]["url"] == test_url

    @pytest.mark.asyncio
    async def test_scrape_respects_timeout_setting(self, firecrawl_url):
        """Test that client respects custom timeout setting."""
        client = FirecrawlClient(base_url=firecrawl_url, timeout=60)
        assert client.timeout == 60

    @pytest.mark.asyncio
    async def test_scrape_extracts_domain_from_url(self, client):
        """Test that scrape correctly extracts domain from URL."""
        test_url = "https://www.example.com/path/to/page?query=param"
        mock_response_data = {
            "success": True,
            "data": {
                "markdown": "Test",
                "metadata": {"title": "Test", "statusCode": 200},
            },
        }

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json = Mock(return_value=mock_response_data)
            mock_post.return_value = mock_response

            result = await client.scrape(test_url)

            assert result.domain == "www.example.com"

    @pytest.mark.asyncio
    async def test_scrape_handles_malformed_response(self, client):
        """Test scrape handles malformed API responses."""
        test_url = "https://example.com"

        with patch.object(
            client._http_client, "post", new_callable=AsyncMock
        ) as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json = Mock(return_value={"unexpected": "format"})
            mock_post.return_value = mock_response

            result = await client.scrape(test_url)

            assert result.success is False
            assert result.error is not None

    @pytest.mark.asyncio
    async def test_client_closes_http_client(self, client):
        """Test that client properly closes HTTP client."""
        with patch.object(
            client._http_client, "aclose", new_callable=AsyncMock
        ) as mock_close:
            await client.close()
            mock_close.assert_called_once()

    @pytest.mark.asyncio
    async def test_client_can_be_used_as_context_manager(self, firecrawl_url):
        """Test FirecrawlClient can be used as async context manager."""
        async with FirecrawlClient(base_url=firecrawl_url, timeout=30) as client:
            assert client._http_client is not None

        # After exiting context, client should be closed
        # We can't easily test this without making another request,
        # but we verify the pattern works


class TestScrapeResult:
    """Test suite for ScrapeResult data class."""

    def test_scrape_result_success_initialization(self):
        """Test ScrapeResult can be initialized with success data."""
        result = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test Page",
            metadata={"statusCode": 200},
            status_code=200,
            success=True,
            error=None,
        )

        assert result.url == "https://example.com"
        assert result.success is True
        assert result.markdown == "# Test"

    def test_scrape_result_failure_initialization(self):
        """Test ScrapeResult can be initialized with failure data."""
        result = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown=None,
            title=None,
            metadata={},
            status_code=404,
            success=False,
            error="Not found",
        )

        assert result.success is False
        assert result.error == "Not found"
        assert result.markdown is None
