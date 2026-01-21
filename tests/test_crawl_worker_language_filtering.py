"""Integration tests for language filtering in CrawlWorker."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from config import settings
from orm_models import Job
from services.filtering.language import LanguageResult
from services.scraper.crawl_worker import CrawlWorker


@pytest.fixture
def mock_db() -> MagicMock:
    """Create mock database session."""
    db = MagicMock()
    db.commit = MagicMock()
    return db


@pytest.fixture
def mock_firecrawl_client() -> MagicMock:
    """Create mock Firecrawl client."""
    return MagicMock()


@pytest.fixture
def crawl_worker(mock_db: MagicMock, mock_firecrawl_client: MagicMock) -> CrawlWorker:
    """Create CrawlWorker instance with mocks."""
    return CrawlWorker(db=mock_db, firecrawl_client=mock_firecrawl_client)


@pytest.fixture
def sample_job() -> Job:
    """Create sample crawl job with language filtering enabled."""
    return Job(
        id=uuid4(),
        type="crawl",
        status="queued",
        payload={
            "url": "https://example.com",
            "project_id": str(uuid4()),
            "company": "TestCompany",
            "max_depth": 2,
            "limit": 100,
            "language_detection_enabled": True,
            "allowed_languages": ["en"],
        },
    )


@pytest.fixture
def sample_pages() -> list[dict]:
    """Create sample crawl pages."""
    return [
        {
            "markdown": "This is an English page about our products.",
            "metadata": {
                "url": "https://example.com/en/products",
                "sourceURL": "https://example.com/en/products",
                "title": "Products",
                "statusCode": 200,
            },
        },
        {
            "markdown": "Dies ist eine deutsche Seite über unsere Produkte.",
            "metadata": {
                "url": "https://example.com/de/produkte",
                "sourceURL": "https://example.com/de/produkte",
                "title": "Produkte",
                "statusCode": 200,
            },
        },
    ]


class TestCrawlWorkerLanguageFiltering:
    """Integration tests for language filtering in CrawlWorker."""

    @pytest.mark.asyncio
    async def test_filters_german_content(
        self, crawl_worker: CrawlWorker, sample_job: Job, sample_pages: list[dict]
    ) -> None:
        """Test that German content is filtered out."""
        # Mock the source repository
        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        # Store pages
        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True
            mock_settings.language_detection_confidence_threshold = 0.7
            mock_settings.language_detection_timeout_seconds = 5.0

            sources_created = await crawl_worker._store_pages(sample_job, sample_pages)

        # Should only store 1 page (English), not the German one
        assert crawl_worker.source_repo.upsert.call_count == 1

        # Verify English page was stored
        call_args = crawl_worker.source_repo.upsert.call_args
        assert "example.com/en/products" in call_args.kwargs["uri"]

    @pytest.mark.asyncio
    async def test_stores_english_content(
        self, crawl_worker: CrawlWorker, sample_job: Job
    ) -> None:
        """Test that English content is stored."""
        english_page = {
            "markdown": "This is an English page with lots of content.",
            "metadata": {
                "url": "https://example.com/about",
                "sourceURL": "https://example.com/about",
                "title": "About Us",
                "statusCode": 200,
            },
        }

        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True
            mock_settings.language_detection_confidence_threshold = 0.7
            mock_settings.language_detection_timeout_seconds = 5.0

            sources_created = await crawl_worker._store_pages(sample_job, [english_page])

        # Should store the English page
        assert sources_created == 1
        assert crawl_worker.source_repo.upsert.call_count == 1

    @pytest.mark.asyncio
    async def test_handles_detection_timeout(
        self, crawl_worker: CrawlWorker, sample_job: Job
    ) -> None:
        """Test that detection timeout doesn't block crawl."""
        page = {
            "markdown": "Some content",
            "metadata": {
                "url": "https://example.com/page",
                "sourceURL": "https://example.com/page",
                "title": "Page",
                "statusCode": 200,
            },
        }

        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        # Mock language service to timeout
        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True
            mock_settings.language_detection_confidence_threshold = 0.7
            mock_settings.language_detection_timeout_seconds = 0.001  # Very short timeout

            with patch("services.filtering.language.get_language_service") as mock_service:
                mock_lang_service = MagicMock()
                mock_lang_service.detect = AsyncMock(
                    side_effect=asyncio.TimeoutError("Timeout")
                )
                mock_service.return_value = mock_lang_service

                sources_created = await crawl_worker._store_pages(sample_job, [page])

        # Should still store the page despite timeout
        assert sources_created == 1

    @pytest.mark.asyncio
    async def test_stores_language_metadata(
        self, crawl_worker: CrawlWorker, sample_job: Job
    ) -> None:
        """Test that language detection results are stored in metadata."""
        english_page = {
            "markdown": "This is an English page",
            "metadata": {
                "url": "https://example.com/en/page",
                "sourceURL": "https://example.com/en/page",
                "title": "Page",
                "statusCode": 200,
            },
        }

        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True
            mock_settings.language_detection_confidence_threshold = 0.7
            mock_settings.language_detection_timeout_seconds = 5.0

            await crawl_worker._store_pages(sample_job, [english_page])

        # Verify metadata includes language detection results
        call_args = crawl_worker.source_repo.upsert.call_args
        metadata = call_args.kwargs["meta_data"]

        # Should have detected language from URL
        assert "detected_language" in metadata
        assert metadata["detected_language"] == "en"

    @pytest.mark.asyncio
    async def test_skips_filtering_when_disabled(
        self, crawl_worker: CrawlWorker, sample_job: Job, sample_pages: list[dict]
    ) -> None:
        """Test that filtering is skipped when disabled."""
        # Disable language detection
        sample_job.payload["language_detection_enabled"] = False

        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True

            sources_created = await crawl_worker._store_pages(sample_job, sample_pages)

        # Should store both pages when filtering is disabled
        assert sources_created == 2
        assert crawl_worker.source_repo.upsert.call_count == 2

    @pytest.mark.asyncio
    async def test_handles_detection_error_gracefully(
        self, crawl_worker: CrawlWorker, sample_job: Job
    ) -> None:
        """Test that detection errors don't break crawl."""
        page = {
            "markdown": "Some content",
            "metadata": {
                "url": "https://example.com/page",
                "sourceURL": "https://example.com/page",
                "title": "Page",
                "statusCode": 200,
            },
        }

        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        # Mock language service to raise exception
        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True
            mock_settings.language_detection_confidence_threshold = 0.7
            mock_settings.language_detection_timeout_seconds = 5.0

            with patch("services.filtering.language.get_language_service") as mock_service:
                mock_lang_service = MagicMock()
                mock_lang_service.detect = AsyncMock(side_effect=Exception("Detection error"))
                mock_service.return_value = mock_lang_service

                sources_created = await crawl_worker._store_pages(sample_job, [page])

        # Should still store the page despite error
        assert sources_created == 1

    @pytest.mark.asyncio
    async def test_filters_by_url_heuristics(
        self, crawl_worker: CrawlWorker, sample_job: Job
    ) -> None:
        """Test that URL heuristics are used for fast filtering."""
        # Page with German URL but English content
        page = {
            "markdown": "This is actually English content",
            "metadata": {
                "url": "https://example.com/de/page",
                "sourceURL": "https://example.com/de/page",
                "title": "Page",
                "statusCode": 200,
            },
        }

        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True
            mock_settings.language_detection_confidence_threshold = 0.7
            mock_settings.language_detection_timeout_seconds = 5.0

            sources_created = await crawl_worker._store_pages(sample_job, [page])

        # Should filter based on URL (fast path) even though content is English
        assert sources_created == 0

    @pytest.mark.asyncio
    async def test_allows_multiple_languages(
        self, crawl_worker: CrawlWorker, sample_job: Job, sample_pages: list[dict]
    ) -> None:
        """Test allowing multiple languages."""
        # Allow both English and German
        sample_job.payload["allowed_languages"] = ["en", "de"]

        crawl_worker.source_repo.upsert = AsyncMock(return_value=(MagicMock(), True))

        with patch("services.scraper.crawl_worker.settings") as mock_settings:
            mock_settings.language_filtering_enabled = True
            mock_settings.language_detection_confidence_threshold = 0.7
            mock_settings.language_detection_timeout_seconds = 5.0

            sources_created = await crawl_worker._store_pages(sample_job, sample_pages)

        # Should store both pages
        assert sources_created == 2
        assert crawl_worker.source_repo.upsert.call_count == 2
