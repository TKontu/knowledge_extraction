"""Test error handling improvements for worker classes.

Tests that exceptions are properly formatted with:
- Error type in job.error
- exc_info in logs
- error_type field in log context
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from orm_models import Job
from services.extraction.worker import ExtractionWorker
from services.scraper.crawl_worker import CrawlWorker
from services.scraper.worker import ScraperWorker


class TestCrawlWorkerErrorHandling:
    """Test CrawlWorker error formatting."""

    @pytest.fixture
    def db_session(self):
        """Mock database session."""
        session = MagicMock()
        session.commit = MagicMock()
        session.rollback = MagicMock()
        return session

    @pytest.fixture
    def firecrawl_client(self):
        """Mock Firecrawl client."""
        return AsyncMock()

    @pytest.fixture
    def crawl_worker(self, db_session, firecrawl_client):
        """Create CrawlWorker instance."""
        return CrawlWorker(db=db_session, firecrawl_client=firecrawl_client)

    @pytest.fixture
    def crawl_job(self):
        """Create a test crawl job."""
        return Job(
            id=uuid4(),
            type="crawl",
            status="running",
            payload={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "test_company",
                "max_depth": 2,
                "limit": 10,
                "firecrawl_job_id": "test-firecrawl-id",
            },
            started_at=datetime.now(UTC),
        )

    @pytest.mark.asyncio
    async def test_exception_includes_error_type_in_job_error(
        self, crawl_worker, crawl_job, firecrawl_client
    ):
        """Test that job.error includes the error type."""
        # Arrange: Firecrawl client raises AttributeError
        firecrawl_client.get_crawl_status.side_effect = AttributeError("meta_data")

        # Act: Process job (will fail)
        await crawl_worker.process_job(crawl_job)

        # Assert: job.error should include error type
        assert crawl_job.status == "failed"
        assert "AttributeError" in crawl_job.error, (
            f"Expected error to include 'AttributeError', got: {crawl_job.error}"
        )
        assert "meta_data" in crawl_job.error

    @pytest.mark.asyncio
    async def test_exception_logs_with_exc_info(
        self, crawl_worker, crawl_job, firecrawl_client
    ):
        """Test that exceptions are logged with exc_info=True."""
        # Arrange: Firecrawl client raises ValueError
        firecrawl_client.get_crawl_status.side_effect = ValueError("Invalid response")

        # Act: Process job with log capture
        with patch("services.scraper.crawl_worker.logger") as mock_logger:
            await crawl_worker.process_job(crawl_job)

            # Assert: logger.error was called with exc_info=True
            mock_logger.error.assert_called_once()
            call_kwargs = mock_logger.error.call_args[1]
            assert call_kwargs.get("exc_info") is True, (
                "Expected exc_info=True in logger.error call"
            )

    @pytest.mark.asyncio
    async def test_exception_logs_include_error_type_field(
        self, crawl_worker, crawl_job, firecrawl_client
    ):
        """Test that logs include error_type field."""
        # Arrange: Firecrawl client raises TypeError
        firecrawl_client.get_crawl_status.side_effect = TypeError("Bad type")

        # Act: Process job with log capture
        with patch("services.scraper.crawl_worker.logger") as mock_logger:
            await crawl_worker.process_job(crawl_job)

            # Assert: logger.error includes error_type
            call_kwargs = mock_logger.error.call_args[1]
            assert "error_type" in call_kwargs, (
                "Expected error_type field in log context"
            )
            assert call_kwargs["error_type"] == "TypeError"


class TestScraperWorkerErrorHandling:
    """Test ScraperWorker error formatting."""

    @pytest.fixture
    def db_session(self):
        """Mock database session."""
        session = MagicMock()
        session.commit = MagicMock()
        session.rollback = MagicMock()
        return session

    @pytest.fixture
    def firecrawl_client(self):
        """Mock Firecrawl client."""
        return AsyncMock()

    @pytest.fixture
    def scraper_worker(self, db_session, firecrawl_client):
        """Create ScraperWorker instance."""
        return ScraperWorker(db=db_session, firecrawl_client=firecrawl_client)

    @pytest.fixture
    def scrape_job(self):
        """Create a test scrape job."""
        return Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com/page1"],
                "project_id": str(uuid4()),
                "company": "test_company",
            },
        )

    @pytest.mark.asyncio
    async def test_exception_includes_error_type_in_job_error(
        self, scraper_worker, scrape_job, firecrawl_client
    ):
        """Test that job.error includes the error type."""
        # Arrange: Firecrawl client raises ConnectionError
        firecrawl_client.scrape_urls.side_effect = ConnectionError("Network failed")

        # Act: Process job
        await scraper_worker.process_job(scrape_job)

        # Assert: job.error should include error type
        assert scrape_job.status == "failed"
        assert "ConnectionError" in scrape_job.error, (
            f"Expected error to include 'ConnectionError', got: {scrape_job.error}"
        )
        assert "Network failed" in scrape_job.error

    @pytest.mark.asyncio
    async def test_exception_logs_with_exc_info(
        self, scraper_worker, scrape_job, firecrawl_client
    ):
        """Test that exceptions are logged with exc_info=True."""
        # Arrange
        firecrawl_client.scrape_urls.side_effect = RuntimeError("Unexpected error")

        # Act: Process job with log capture
        with patch("services.scraper.worker.logger") as mock_logger:
            await scraper_worker.process_job(scrape_job)

            # Assert
            mock_logger.error.assert_called_once()
            call_kwargs = mock_logger.error.call_args[1]
            assert call_kwargs.get("exc_info") is True


class TestExtractionWorkerErrorHandling:
    """Test ExtractionWorker error formatting."""

    @pytest.fixture
    def db_session(self):
        """Mock database session."""
        session = MagicMock()
        session.commit = MagicMock()
        session.rollback = MagicMock()
        return session

    @pytest.fixture
    def pipeline_service(self):
        """Mock pipeline service."""
        return AsyncMock()

    @pytest.fixture
    def extraction_worker(self, db_session, pipeline_service):
        """Create ExtractionWorker instance."""
        return ExtractionWorker(db=db_session, pipeline_service=pipeline_service)

    @pytest.fixture
    def extract_job(self):
        """Create a test extraction job."""
        return Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={
                "project_id": str(uuid4()),
                "source_ids": None,
            },
        )

    @pytest.mark.asyncio
    async def test_exception_includes_error_type_in_job_error(
        self, extraction_worker, extract_job, pipeline_service
    ):
        """Test that job.error includes the error type."""
        # Arrange: Pipeline service raises KeyError
        pipeline_service.extract_from_sources.side_effect = KeyError("missing_key")

        # Act: Process job
        await extraction_worker.process_job(extract_job)

        # Assert: job.error should include error type
        assert extract_job.status == "failed"
        assert "KeyError" in extract_job.error, (
            f"Expected error to include 'KeyError', got: {extract_job.error}"
        )
        assert "missing_key" in extract_job.error

    @pytest.mark.asyncio
    async def test_exception_logs_with_exc_info(
        self, extraction_worker, extract_job, pipeline_service
    ):
        """Test that exceptions are logged with exc_info=True."""
        # Arrange
        pipeline_service.extract_from_sources.side_effect = ValueError("Invalid data")

        # Act: Process job with log capture
        with patch("services.extraction.worker.logger") as mock_logger:
            await extraction_worker.process_job(extract_job)

            # Assert
            mock_logger.error.assert_called_once()
            call_kwargs = mock_logger.error.call_args[1]
            assert call_kwargs.get("exc_info") is True

    @pytest.mark.asyncio
    async def test_exception_logs_include_error_type_field(
        self, extraction_worker, extract_job, pipeline_service
    ):
        """Test that logs include error_type field."""
        # Arrange
        pipeline_service.extract_from_sources.side_effect = OSError("File not found")

        # Act: Process job with log capture
        with patch("services.extraction.worker.logger") as mock_logger:
            await extraction_worker.process_job(extract_job)

            # Assert
            call_kwargs = mock_logger.error.call_args[1]
            assert "error_type" in call_kwargs
            assert call_kwargs["error_type"] == "OSError"


class TestErrorMessageFormats:
    """Test various error message format scenarios."""

    def test_simple_error_message_format(self):
        """Test formatting a simple error."""
        try:
            raise ValueError("Invalid input")
        except Exception as e:
            formatted = f"{type(e).__name__}: {str(e)}"
            assert formatted == "ValueError: Invalid input"

    def test_error_with_no_message_format(self):
        """Test formatting error with no message."""
        try:
            raise RuntimeError()
        except Exception as e:
            formatted = f"{type(e).__name__}: {str(e)}"
            # Should still include error type even if message is empty
            assert "RuntimeError" in formatted

    def test_nested_exception_format(self):
        """Test formatting nested exceptions."""
        try:
            try:
                raise KeyError("inner")
            except KeyError as inner:
                raise ValueError("outer") from inner
        except Exception as e:
            formatted = f"{type(e).__name__}: {str(e)}"
            assert formatted == "ValueError: outer"
            # Cause should be preserved in __cause__
            assert e.__cause__ is not None
            assert type(e.__cause__).__name__ == "KeyError"
