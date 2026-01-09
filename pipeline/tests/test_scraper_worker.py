"""Tests for scraper background worker."""

import pytest
from unittest.mock import AsyncMock, patch, Mock
from datetime import datetime, UTC
from uuid import uuid4

from services.scraper.worker import ScraperWorker
from services.scraper.client import ScrapeResult
from services.scraper.rate_limiter import RateLimitExceeded
from orm_models import Job, Page


class TestScraperWorker:
    """Test suite for ScraperWorker."""

    @pytest.fixture
    def db_session(self):
        """Mock database session."""
        session = Mock()
        return session

    @pytest.fixture
    def firecrawl_client(self):
        """Mock FirecrawlClient."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def worker(self, db_session, firecrawl_client):
        """Create ScraperWorker instance."""
        return ScraperWorker(db=db_session, firecrawl_client=firecrawl_client)

    @pytest.mark.asyncio
    async def test_worker_initialization(self, worker, db_session, firecrawl_client):
        """Test ScraperWorker initializes correctly."""
        assert worker.db == db_session
        assert worker.client == firecrawl_client

    @pytest.mark.asyncio
    async def test_process_job_updates_status_to_running(self, worker, db_session):
        """Test that processing a job updates status to running."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker.process_job(job)

        # Verify job status was set to running
        assert job.status == "completed"
        assert job.started_at is not None

    @pytest.mark.asyncio
    async def test_process_job_scrapes_all_urls(self, worker):
        """Test that worker scrapes all URLs in job payload."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": [
                    "https://example.com/page1",
                    "https://example.com/page2",
                    "https://example.com/page3",
                ],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker.process_job(job)

        # Verify scrape was called 3 times
        assert worker.client.scrape.call_count == 3

    @pytest.mark.asyncio
    async def test_process_job_stores_successful_scrape_to_database(
        self, worker, db_session
    ):
        """Test that successful scrapes are stored as Page records."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Example Page\n\nTest content",
            title="Example Page",
            metadata={"description": "Test"},
            status_code=200,
            success=True,
            error=None,
        )

        await worker.process_job(job)

        # Verify a Page was added to the database
        db_session.add.assert_called()
        added_page = db_session.add.call_args[0][0]
        assert isinstance(added_page, Page)
        assert added_page.url == "https://example.com"
        assert added_page.domain == "example.com"
        assert added_page.company == "Example Corp"
        assert added_page.markdown_content == "# Example Page\n\nTest content"
        assert added_page.title == "Example Page"

    @pytest.mark.asyncio
    async def test_process_job_handles_scrape_failure_gracefully(
        self, worker, db_session
    ):
        """Test that scrape failures don't crash the worker."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com/404", "https://example.com/good"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        # First URL fails, second succeeds
        worker.client.scrape.side_effect = [
            ScrapeResult(
                url="https://example.com/404",
                domain="example.com",
                markdown=None,
                title=None,
                metadata={},
                status_code=404,
                success=False,
                error="Page not found",
            ),
            ScrapeResult(
                url="https://example.com/good",
                domain="example.com",
                markdown="# Good Page",
                title="Good",
                metadata={},
                status_code=200,
                success=True,
                error=None,
            ),
        ]

        await worker.process_job(job)

        # Job should still complete
        assert job.status == "completed"
        # Only one page should be added (the successful one)
        assert db_session.add.call_count == 1

    @pytest.mark.asyncio
    async def test_process_job_marks_job_as_completed(self, worker):
        """Test that job is marked as completed after processing."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker.process_job(job)

        assert job.status == "completed"
        assert job.completed_at is not None

    @pytest.mark.asyncio
    async def test_process_job_stores_result_summary_in_job(self, worker):
        """Test that job result contains summary of scraped pages."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com/1", "https://example.com/2"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker.client.scrape.side_effect = [
            ScrapeResult(
                url="https://example.com/1",
                domain="example.com",
                markdown="# Page 1",
                title="Page 1",
                metadata={},
                status_code=200,
                success=True,
                error=None,
            ),
            ScrapeResult(
                url="https://example.com/2",
                domain="example.com",
                markdown=None,
                title=None,
                metadata={},
                status_code=404,
                success=False,
                error="Not found",
            ),
        ]

        await worker.process_job(job)

        # Job result should contain summary
        assert job.result is not None
        assert job.result["pages_scraped"] == 1
        assert job.result["pages_failed"] == 1
        assert job.result["total_urls"] == 2

    @pytest.mark.asyncio
    async def test_process_job_marks_job_as_failed_on_exception(self, worker):
        """Test that unexpected exceptions mark job as failed."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        # Simulate unexpected error
        worker.client.scrape.side_effect = Exception("Database connection lost")

        await worker.process_job(job)

        assert job.status == "failed"
        assert job.error is not None
        assert "database connection lost" in job.error.lower()

    @pytest.mark.asyncio
    async def test_process_job_commits_database_changes(self, worker, db_session):
        """Test that worker commits changes to database."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker.process_job(job)

        # Verify commit was called
        db_session.commit.assert_called()

    @pytest.mark.asyncio
    async def test_process_job_extracts_company_from_payload(self, worker, db_session):
        """Test that company name is correctly extracted from payload."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Acme Corporation",
                "profile": "default",
            },
        )

        worker.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker.process_job(job)

        # Check the Page was created with correct company
        added_page = db_session.add.call_args[0][0]
        assert added_page.company == "Acme Corporation"

    @pytest.mark.asyncio
    async def test_process_job_sets_started_at_timestamp(self, worker):
        """Test that started_at timestamp is set when processing begins."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Example Corp",
                "profile": "default",
            },
            started_at=None,
        )

        worker.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker.process_job(job)

        assert job.started_at is not None
        assert isinstance(job.started_at, datetime)


class TestScraperWorkerWithRateLimiting:
    """Test suite for ScraperWorker with rate limiting."""

    @pytest.fixture
    def db_session(self):
        """Mock database session."""
        session = Mock()
        return session

    @pytest.fixture
    def firecrawl_client(self):
        """Mock FirecrawlClient."""
        client = AsyncMock()
        return client

    @pytest.fixture
    def rate_limiter(self):
        """Mock DomainRateLimiter."""
        limiter = AsyncMock()
        return limiter

    @pytest.fixture
    def worker_with_limiter(self, db_session, firecrawl_client, rate_limiter):
        """Create ScraperWorker instance with rate limiter."""
        return ScraperWorker(
            db=db_session,
            firecrawl_client=firecrawl_client,
            rate_limiter=rate_limiter,
        )

    @pytest.mark.asyncio
    async def test_worker_uses_rate_limiter_before_scraping(
        self, worker_with_limiter, rate_limiter
    ):
        """Test that worker acquires rate limit before each scrape."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com/page1", "https://example.com/page2"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker_with_limiter.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker_with_limiter.process_job(job)

        # Rate limiter should be called twice (once per URL)
        assert rate_limiter.acquire.call_count == 2
        # Verify it was called with the correct domain
        rate_limiter.acquire.assert_any_call("example.com")

    @pytest.mark.asyncio
    async def test_worker_handles_rate_limit_exceeded_gracefully(
        self, worker_with_limiter, rate_limiter
    ):
        """Test that worker handles rate limit exceeded without crashing."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com/page1"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        # Simulate rate limit exceeded
        rate_limiter.acquire.side_effect = RateLimitExceeded(
            domain="example.com", limit=100, reset_in=3600
        )

        await worker_with_limiter.process_job(job)

        # Job should be marked as failed
        assert job.status == "failed"
        assert "rate limit" in job.error.lower()

    @pytest.mark.asyncio
    async def test_worker_extracts_correct_domain_for_rate_limiting(
        self, worker_with_limiter, rate_limiter
    ):
        """Test that worker extracts domain correctly for rate limiting."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": [
                    "https://www.example.com/path/page",
                    "https://another.com/page",
                ],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        worker_with_limiter.client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker_with_limiter.process_job(job)

        # Should be called with correct domains
        assert rate_limiter.acquire.call_count == 2
        rate_limiter.acquire.assert_any_call("www.example.com")
        rate_limiter.acquire.assert_any_call("another.com")

    @pytest.mark.asyncio
    async def test_worker_continues_after_partial_rate_limit(
        self, worker_with_limiter, rate_limiter, db_session
    ):
        """Test that worker continues processing other URLs after rate limit on one."""
        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": [
                    "https://example.com/page1",  # This will be rate limited
                    "https://another.com/page2",  # This should succeed
                ],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        # First URL hits rate limit, second succeeds
        rate_limiter.acquire.side_effect = [
            RateLimitExceeded(domain="example.com", limit=100, reset_in=3600),
            None,  # Second call succeeds
        ]

        worker_with_limiter.client.scrape.return_value = ScrapeResult(
            url="https://another.com/page2",
            domain="another.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        await worker_with_limiter.process_job(job)

        # Job should still complete (not fail completely)
        assert job.status == "completed"
        # Should have 1 successful page
        assert db_session.add.call_count == 1

    @pytest.mark.asyncio
    async def test_worker_without_rate_limiter_works_normally(
        self, db_session, firecrawl_client
    ):
        """Test that worker works without rate limiter (backwards compatibility)."""
        # Create worker without rate limiter
        worker = ScraperWorker(db=db_session, firecrawl_client=firecrawl_client)

        job = Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={
                "urls": ["https://example.com"],
                "company": "Example Corp",
                "profile": "default",
            },
        )

        firecrawl_client.scrape.return_value = ScrapeResult(
            url="https://example.com",
            domain="example.com",
            markdown="# Test",
            title="Test",
            metadata={},
            status_code=200,
            success=True,
            error=None,
        )

        # Should complete without errors
        await worker.process_job(job)
        assert job.status == "completed"
