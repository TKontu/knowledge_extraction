"""Tests for HTTP error handling in crawl worker.

TDD: These tests should fail first, then pass after implementation.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from orm_models import Job
from services.scraper.crawl_worker import CrawlWorker


class TestHttpErrorFiltering:
    """Test that HTTP errors (400+) are filtered before storing sources."""

    @pytest.fixture
    def mock_db(self):
        """Mock database session."""
        return MagicMock()

    @pytest.fixture
    def mock_firecrawl_client(self):
        """Mock Firecrawl client."""
        return MagicMock()

    @pytest.fixture
    def crawl_worker(self, mock_db, mock_firecrawl_client):
        """Create crawl worker instance."""
        worker = CrawlWorker(db=mock_db, firecrawl_client=mock_firecrawl_client)
        worker.source_repo = AsyncMock()
        # Mock get_by_uri to return None (no duplicates)
        worker.source_repo.get_by_uri = AsyncMock(return_value=None)
        return worker

    @pytest.fixture
    def test_job(self, mock_db):
        """Create test job."""
        job = Job(
            id=uuid4(),
            type="crawl",
            status="running",
            payload={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
                "max_depth": 2,
                "limit": 10,
            },
        )
        return job

    @pytest.mark.asyncio
    async def test_filters_400_error_pages(self, crawl_worker, test_job):
        """Test that pages with HTTP 400 are not stored."""
        pages = [
            {
                "markdown": "Error page content",
                "metadata": {
                    "url": "https://example.com/page1",
                    "sourceURL": "https://example.com/page1",
                    "statusCode": 400,  # Bad Request
                    "title": "Bad Request",
                },
            },
            {
                "markdown": "Valid page content",
                "metadata": {
                    "url": "https://example.com/page2",
                    "sourceURL": "https://example.com/page2",
                    "statusCode": 200,  # OK
                    "title": "Valid Page",
                },
            },
        ]

        sources_created = await crawl_worker._store_pages(test_job, pages)

        # Should only create 1 source (skip the 400 error)
        assert sources_created == 1
        # Verify source_repo.create called only once (for 200 OK page)
        assert crawl_worker.source_repo.create.call_count == 1

    @pytest.mark.asyncio
    async def test_filters_404_error_pages(self, crawl_worker, test_job):
        """Test that pages with HTTP 404 are not stored."""
        pages = [
            {
                "markdown": "Not found page",
                "metadata": {
                    "url": "https://example.com/missing",
                    "statusCode": 404,
                },
            }
        ]

        sources_created = await crawl_worker._store_pages(test_job, pages)

        assert sources_created == 0
        crawl_worker.source_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_filters_500_error_pages(self, crawl_worker, test_job):
        """Test that pages with HTTP 500 are not stored."""
        pages = [
            {
                "markdown": "Internal server error",
                "metadata": {
                    "url": "https://example.com/error",
                    "statusCode": 500,
                },
            }
        ]

        sources_created = await crawl_worker._store_pages(test_job, pages)

        assert sources_created == 0
        crawl_worker.source_repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_stores_200_success_pages(self, crawl_worker, test_job):
        """Test that pages with HTTP 200 ARE stored."""
        pages = [
            {
                "markdown": "Valid content",
                "metadata": {
                    "url": "https://example.com/page",
                    "statusCode": 200,
                },
            }
        ]

        sources_created = await crawl_worker._store_pages(test_job, pages)

        assert sources_created == 1
        crawl_worker.source_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_201_created_pages(self, crawl_worker, test_job):
        """Test that pages with HTTP 201 ARE stored."""
        pages = [
            {
                "markdown": "Created content",
                "metadata": {
                    "url": "https://example.com/page",
                    "statusCode": 201,
                },
            }
        ]

        sources_created = await crawl_worker._store_pages(test_job, pages)

        assert sources_created == 1

    @pytest.mark.asyncio
    async def test_filters_403_forbidden_pages(self, crawl_worker, test_job):
        """Test that pages with HTTP 403 are not stored."""
        pages = [
            {
                "markdown": "Access denied",
                "metadata": {
                    "url": "https://example.com/forbidden",
                    "statusCode": 403,
                },
            }
        ]

        sources_created = await crawl_worker._store_pages(test_job, pages)

        assert sources_created == 0

    @pytest.mark.asyncio
    async def test_handles_missing_status_code(self, crawl_worker, test_job):
        """Test that pages without statusCode are still processed."""
        pages = [
            {
                "markdown": "Content without status",
                "metadata": {
                    "url": "https://example.com/page",
                    # No statusCode field
                },
            }
        ]

        # Should not crash, should store the page
        sources_created = await crawl_worker._store_pages(test_job, pages)

        assert sources_created == 1
        crawl_worker.source_repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_logs_filtered_error_pages(self, crawl_worker, test_job):
        """Test that filtered error pages are logged."""
        pages = [
            {
                "markdown": "Error page",
                "metadata": {
                    "url": "https://example.com/error",
                    "statusCode": 400,
                },
            }
        ]

        with patch("services.scraper.crawl_worker.logger") as mock_logger:
            await crawl_worker._store_pages(test_job, pages)

            # Should log a warning about skipped page
            mock_logger.warning.assert_called()
            call_args = mock_logger.warning.call_args
            assert "page_http_error_skipped" in str(call_args) or "400" in str(call_args)

    @pytest.mark.asyncio
    async def test_mixed_status_codes_filtering(self, crawl_worker, test_job):
        """Test filtering with mixed status codes."""
        pages = [
            {"markdown": "Page 1", "metadata": {"url": "https://example.com/1", "statusCode": 200}},
            {"markdown": "Page 2", "metadata": {"url": "https://example.com/2", "statusCode": 404}},
            {"markdown": "Page 3", "metadata": {"url": "https://example.com/3", "statusCode": 200}},
            {"markdown": "Page 4", "metadata": {"url": "https://example.com/4", "statusCode": 500}},
            {"markdown": "Page 5", "metadata": {"url": "https://example.com/5", "statusCode": 201}},
        ]

        sources_created = await crawl_worker._store_pages(test_job, pages)

        # Should store: 200, 200, 201 = 3 sources
        assert sources_created == 3
        assert crawl_worker.source_repo.create.call_count == 3

    @pytest.mark.asyncio
    async def test_stores_http_status_in_metadata(self, crawl_worker, test_job):
        """Test that HTTP status is stored in source metadata."""
        pages = [
            {
                "markdown": "Valid content",
                "metadata": {
                    "url": "https://example.com/page",
                    "statusCode": 200,
                    "title": "Test Page",
                },
            }
        ]

        await crawl_worker._store_pages(test_job, pages)

        # Check what was passed to create()
        call_args = crawl_worker.source_repo.create.call_args
        meta_data = call_args.kwargs["meta_data"]

        # Should include http_status
        assert "http_status" in meta_data
        assert meta_data["http_status"] == 200
