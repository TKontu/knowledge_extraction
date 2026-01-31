"""Tests for FirecrawlClient map and batch_scrape methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.scraper.client import (
    BatchScrapeResult,
    FirecrawlClient,
    MapResult,
    ScrapeError,
)


@pytest.fixture
def mock_http_client():
    """Create mock HTTP client."""
    client = AsyncMock()
    return client


@pytest.fixture
def firecrawl_client(mock_http_client):
    """Create FirecrawlClient with mocked HTTP client."""
    client = FirecrawlClient(base_url="http://localhost:3002")
    client._http_client = mock_http_client
    return client


class TestFirecrawlClientMap:
    """Tests for FirecrawlClient.map() method."""

    @pytest.mark.asyncio
    async def test_map_success_with_string_urls(self, firecrawl_client, mock_http_client):
        """Test successful map with URLs as strings."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "links": [
                "https://example.com/products",
                "https://example.com/pricing",
                "https://example.com/about",
            ],
        }
        mock_http_client.post.return_value = mock_response

        result = await firecrawl_client.map("https://example.com")

        assert result.success is True
        assert result.total == 3
        assert len(result.urls) == 3
        assert result.urls[0]["url"] == "https://example.com/products"
        assert result.urls[0]["title"] is None  # String URLs have no metadata
        assert result.error is None

    @pytest.mark.asyncio
    async def test_map_success_with_metadata(self, firecrawl_client, mock_http_client):
        """Test successful map with URLs containing metadata."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "links": [
                {
                    "url": "https://example.com/products",
                    "title": "Products Page",
                    "description": "Browse our products",
                },
                {
                    "url": "https://example.com/pricing",
                    "title": "Pricing",
                    "description": "View pricing plans",
                },
            ],
        }
        mock_http_client.post.return_value = mock_response

        result = await firecrawl_client.map("https://example.com")

        assert result.success is True
        assert result.total == 2
        assert result.urls[0]["url"] == "https://example.com/products"
        assert result.urls[0]["title"] == "Products Page"
        assert result.urls[0]["description"] == "Browse our products"

    @pytest.mark.asyncio
    async def test_map_with_search_parameter(self, firecrawl_client, mock_http_client):
        """Test map with search parameter."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "links": ["https://example.com/products/widget"],
        }
        mock_http_client.post.return_value = mock_response

        result = await firecrawl_client.map(
            "https://example.com",
            search="widget specifications",
        )

        # Verify search was passed in request
        call_args = mock_http_client.post.call_args
        request_body = call_args[1]["json"]
        assert request_body["search"] == "widget specifications"
        assert result.success is True

    @pytest.mark.asyncio
    async def test_map_with_all_options(self, firecrawl_client, mock_http_client):
        """Test map with all options specified."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "links": [],
        }
        mock_http_client.post.return_value = mock_response

        await firecrawl_client.map(
            url="https://example.com",
            search="products",
            limit=1000,
            include_subdomains=True,
            ignore_query_parameters=False,
        )

        call_args = mock_http_client.post.call_args
        request_body = call_args[1]["json"]

        assert request_body["url"] == "https://example.com"
        assert request_body["search"] == "products"
        assert request_body["limit"] == 1000
        assert request_body["includeSubdomains"] is True
        # Note: ignore_query_parameters is only added when True

    @pytest.mark.asyncio
    async def test_map_failure_api_error(self, firecrawl_client, mock_http_client):
        """Test map returns error on API failure."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.json.return_value = {
            "success": False,
            "error": "Internal server error",
        }
        mock_http_client.post.return_value = mock_response

        result = await firecrawl_client.map("https://example.com")

        assert result.success is False
        assert result.error == "Internal server error"
        assert result.urls == []
        assert result.total == 0

    @pytest.mark.asyncio
    async def test_map_failure_network_error(self, firecrawl_client, mock_http_client):
        """Test map handles network errors."""
        mock_http_client.post.side_effect = Exception("Connection refused")

        result = await firecrawl_client.map("https://example.com")

        assert result.success is False
        assert "Connection refused" in result.error
        assert result.urls == []

    @pytest.mark.asyncio
    async def test_map_empty_result(self, firecrawl_client, mock_http_client):
        """Test map with no URLs found."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "success": True,
            "links": [],
        }
        mock_http_client.post.return_value = mock_response

        result = await firecrawl_client.map("https://example.com")

        assert result.success is True
        assert result.total == 0
        assert result.urls == []


class TestFirecrawlClientBatchScrape:
    """Tests for FirecrawlClient batch scrape methods."""

    @pytest.mark.asyncio
    async def test_start_batch_scrape_success(self, firecrawl_client, mock_http_client):
        """Test successful batch scrape start."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "id": "batch-job-123",
        }
        mock_http_client.post.return_value = mock_response

        job_id = await firecrawl_client.start_batch_scrape(
            urls=["https://example.com/a", "https://example.com/b"],
        )

        assert job_id == "batch-job-123"

        # Verify request
        call_args = mock_http_client.post.call_args
        assert "/v1/batch/scrape" in call_args[0][0]
        request_body = call_args[1]["json"]
        assert request_body["urls"] == ["https://example.com/a", "https://example.com/b"]
        assert request_body["formats"] == ["markdown"]

    @pytest.mark.asyncio
    async def test_start_batch_scrape_with_options(self, firecrawl_client, mock_http_client):
        """Test batch scrape with all options."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            "id": "batch-job-456",
        }
        mock_http_client.post.return_value = mock_response

        await firecrawl_client.start_batch_scrape(
            urls=["https://example.com/page"],
            formats=["markdown", "html"],
            max_concurrency=5,
        )

        call_args = mock_http_client.post.call_args
        request_body = call_args[1]["json"]
        assert request_body["formats"] == ["markdown", "html"]
        assert request_body["maxConcurrency"] == 5

    @pytest.mark.asyncio
    async def test_start_batch_scrape_empty_urls(self, firecrawl_client):
        """Test batch scrape with empty URLs raises error."""
        with pytest.raises(ScrapeError, match="No URLs provided"):
            await firecrawl_client.start_batch_scrape(urls=[])

    @pytest.mark.asyncio
    async def test_start_batch_scrape_failure(self, firecrawl_client, mock_http_client):
        """Test batch scrape start failure."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": False,
            "error": "Rate limit exceeded",
        }
        mock_http_client.post.return_value = mock_response

        with pytest.raises(ScrapeError, match="Rate limit exceeded"):
            await firecrawl_client.start_batch_scrape(
                urls=["https://example.com/page"],
            )

    @pytest.mark.asyncio
    async def test_start_batch_scrape_no_job_id(self, firecrawl_client, mock_http_client):
        """Test batch scrape with missing job ID raises error."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "success": True,
            # Missing "id" field
        }
        mock_http_client.post.return_value = mock_response

        with pytest.raises(ScrapeError, match="No job ID"):
            await firecrawl_client.start_batch_scrape(
                urls=["https://example.com/page"],
            )

    @pytest.mark.asyncio
    async def test_get_batch_scrape_status_scraping(self, firecrawl_client, mock_http_client):
        """Test getting batch scrape status while scraping."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "scraping",
            "total": 10,
            "completed": 3,
            "data": [],
        }
        mock_http_client.get.return_value = mock_response

        result = await firecrawl_client.get_batch_scrape_status("batch-job-123")

        assert result.status == "scraping"
        assert result.total == 10
        assert result.completed == 3
        assert result.pages == []

    @pytest.mark.asyncio
    async def test_get_batch_scrape_status_completed(self, firecrawl_client, mock_http_client):
        """Test getting batch scrape status when completed."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "completed",
            "total": 2,
            "completed": 2,
            "data": [
                {
                    "markdown": "# Page 1\n\nContent",
                    "metadata": {"url": "https://example.com/a", "title": "Page 1"},
                },
                {
                    "markdown": "# Page 2\n\nMore content",
                    "metadata": {"url": "https://example.com/b", "title": "Page 2"},
                },
            ],
            "next": None,
        }
        mock_http_client.get.return_value = mock_response

        result = await firecrawl_client.get_batch_scrape_status("batch-job-123")

        assert result.status == "completed"
        assert result.total == 2
        assert result.completed == 2
        assert len(result.pages) == 2
        assert result.pages[0]["markdown"] == "# Page 1\n\nContent"

    @pytest.mark.asyncio
    async def test_get_batch_scrape_status_with_pagination(self, firecrawl_client, mock_http_client):
        """Test batch scrape status handles pagination."""
        # First response with pagination
        first_response = MagicMock()
        first_response.json.return_value = {
            "status": "completed",
            "total": 4,
            "completed": 4,
            "data": [
                {"markdown": "Page 1", "metadata": {"url": "https://example.com/1"}},
                {"markdown": "Page 2", "metadata": {"url": "https://example.com/2"}},
            ],
            "next": "https://api.firecrawl.dev/v1/batch/scrape/123?cursor=abc",
        }

        # Second response (final page)
        second_response = MagicMock()
        second_response.json.return_value = {
            "status": "completed",
            "total": 4,
            "completed": 4,
            "data": [
                {"markdown": "Page 3", "metadata": {"url": "https://example.com/3"}},
                {"markdown": "Page 4", "metadata": {"url": "https://example.com/4"}},
            ],
            "next": None,
        }

        mock_http_client.get.side_effect = [first_response, second_response]

        result = await firecrawl_client.get_batch_scrape_status("batch-job-123")

        assert result.status == "completed"
        assert len(result.pages) == 4
        assert mock_http_client.get.call_count == 2

    @pytest.mark.asyncio
    async def test_get_batch_scrape_status_failed(self, firecrawl_client, mock_http_client):
        """Test batch scrape status when failed."""
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "status": "failed",
            "total": 5,
            "completed": 2,
            "data": [],
            "error": "Some pages failed to scrape",
        }
        mock_http_client.get.return_value = mock_response

        result = await firecrawl_client.get_batch_scrape_status("batch-job-123")

        assert result.status == "failed"
        assert result.error == "Some pages failed to scrape"

    @pytest.mark.asyncio
    async def test_get_batch_scrape_status_network_error(self, firecrawl_client, mock_http_client):
        """Test batch scrape status handles network errors."""
        mock_http_client.get.side_effect = Exception("Connection timeout")

        result = await firecrawl_client.get_batch_scrape_status("batch-job-123")

        assert result.status == "error"
        assert "Connection timeout" in result.error


class TestMapResultDataclass:
    """Tests for MapResult dataclass."""

    def test_map_result_success(self):
        """Test MapResult creation for success case."""
        result = MapResult(
            urls=[
                {"url": "https://example.com/a", "title": "A", "description": "Page A"},
            ],
            total=1,
            success=True,
            error=None,
        )

        assert result.success is True
        assert result.total == 1
        assert len(result.urls) == 1
        assert result.error is None

    def test_map_result_failure(self):
        """Test MapResult creation for failure case."""
        result = MapResult(
            urls=[],
            total=0,
            success=False,
            error="API error",
        )

        assert result.success is False
        assert result.total == 0
        assert result.urls == []
        assert result.error == "API error"


class TestBatchScrapeResultDataclass:
    """Tests for BatchScrapeResult dataclass."""

    def test_batch_scrape_result_scraping(self):
        """Test BatchScrapeResult during scraping."""
        result = BatchScrapeResult(
            status="scraping",
            total=10,
            completed=5,
            pages=[],
        )

        assert result.status == "scraping"
        assert result.total == 10
        assert result.completed == 5
        assert result.pages == []
        assert result.error is None

    def test_batch_scrape_result_completed(self):
        """Test BatchScrapeResult when completed."""
        pages = [
            {"markdown": "Content", "metadata": {"url": "https://example.com"}},
        ]
        result = BatchScrapeResult(
            status="completed",
            total=1,
            completed=1,
            pages=pages,
        )

        assert result.status == "completed"
        assert len(result.pages) == 1

    def test_batch_scrape_result_failed(self):
        """Test BatchScrapeResult when failed."""
        result = BatchScrapeResult(
            status="failed",
            total=5,
            completed=3,
            pages=[],
            error="Timeout on remaining pages",
        )

        assert result.status == "failed"
        assert result.error == "Timeout on remaining pages"
