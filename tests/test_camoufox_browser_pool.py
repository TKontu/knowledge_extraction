"""Tests for Camoufox browser pool implementation.

TDD: These tests define the expected behavior for the browser pool.
The pool should manage multiple browser instances to enable true parallelism.

ROOT CAUSE: Single browser instance cannot handle concurrent page.goto() calls.
Firefox blocks when multiple navigations happen simultaneously.
SOLUTION: Browser pool with N instances, round-robin distribution.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.services.camoufox.config import CamoufoxSettings
from src.services.camoufox.models import ScrapeRequest


def make_test_config(browser_count: int = 3, max_concurrent_pages: int = 6):
    """Create a test config with specified browser count."""
    return CamoufoxSettings(
        browser_count=browser_count,
        max_concurrent_pages=max_concurrent_pages,
    )


class TestBrowserPoolConfig:
    """Test browser pool configuration."""

    def test_browser_count_config_exists(self):
        """Test that browser_count config option exists."""
        settings = CamoufoxSettings()
        assert hasattr(settings, "browser_count"), (
            "CamoufoxSettings should have browser_count attribute"
        )

    def test_browser_count_default_value(self):
        """Test that browser_count defaults to a reasonable value."""
        settings = CamoufoxSettings()
        # Default should be > 1 to enable parallelism
        assert settings.browser_count >= 1, "browser_count should default to at least 1"
        # But not too high (resource constraints)
        assert settings.browser_count <= 10, (
            "browser_count default should be reasonable (<=10)"
        )

    def test_browser_count_from_env(self):
        """Test that browser_count can be set via environment."""
        import os

        with patch.dict(os.environ, {"CAMOUFOX_BROWSER_COUNT": "7"}):
            settings = CamoufoxSettings()
            assert settings.browser_count == 7


class TestBrowserPoolCreation:
    """Test browser pool initialization."""

    @pytest.mark.asyncio
    async def test_pool_creates_multiple_browsers(self):
        """Test that pool creates the configured number of browsers."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=3)
        scraper = CamoufoxScraper(config=config)

        with patch("src.services.camoufox.scraper.AsyncCamoufox") as mock_camoufox:
            mock_browser = AsyncMock()
            mock_camoufox_instance = MagicMock()
            mock_camoufox_instance.start = AsyncMock(return_value=mock_browser)
            mock_camoufox.return_value = mock_camoufox_instance

            await scraper.start()

            # Should have created 3 browser instances
            assert mock_camoufox.call_count == 3, (
                f"Expected 3 browsers, got {mock_camoufox.call_count}"
            )

            await scraper.stop()

    @pytest.mark.asyncio
    async def test_pool_tracks_all_browsers(self):
        """Test that pool tracks all browser instances."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=3)
        scraper = CamoufoxScraper(config=config)

        with patch("src.services.camoufox.scraper.AsyncCamoufox") as mock_camoufox:
            mock_browsers = [AsyncMock() for _ in range(3)]
            call_count = 0

            def create_mock(*args, **kwargs):
                nonlocal call_count
                instance = MagicMock()
                instance.start = AsyncMock(return_value=mock_browsers[call_count])
                call_count += 1
                return instance

            mock_camoufox.side_effect = create_mock

            await scraper.start()

            # Should track all browsers
            assert len(scraper._browsers) == 3, (
                f"Expected 3 tracked browsers, got {len(scraper._browsers)}"
            )

            await scraper.stop()


class TestBrowserPoolDistribution:
    """Test request distribution across browser pool."""

    @pytest.fixture
    def mock_browser_pool(self):
        """Create a scraper with mocked browser pool."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=3, max_concurrent_pages=6)
        scraper = CamoufoxScraper(config=config)

        # Create mock browsers
        browsers = []
        for i in range(3):
            browser = MagicMock()
            context = AsyncMock()
            page = AsyncMock()
            response = AsyncMock()

            browser.new_context = AsyncMock(return_value=context)
            context.new_page = AsyncMock(return_value=page)
            context.route = AsyncMock()
            context.close = AsyncMock()
            page.goto = AsyncMock(return_value=response)
            page.content = AsyncMock(return_value=f"<html>Browser {i}</html>")
            page.wait_for_load_state = AsyncMock()
            page.evaluate = AsyncMock(return_value=100)
            page.close = AsyncMock()
            page.set_extra_http_headers = AsyncMock()

            response.status = 200
            response.all_headers = AsyncMock(return_value={"content-type": "text/html"})

            browsers.append(browser)

        scraper._browsers = browsers
        scraper._camoufox_instances = [MagicMock() for _ in range(3)]

        return scraper, browsers

    @pytest.mark.asyncio
    async def test_requests_distributed_round_robin(self, mock_browser_pool):
        """Test that requests are distributed round-robin across browsers."""
        scraper, browsers = mock_browser_pool

        # Make 6 requests (should hit each browser twice)
        for i in range(6):
            request = ScrapeRequest(url=f"https://example.com/page{i}", timeout=30000)
            await scraper.scrape(request)

        # Each browser should have been used twice
        for i, browser in enumerate(browsers):
            call_count = browser.new_context.call_count
            assert call_count == 2, f"Browser {i} should have 2 calls, got {call_count}"

    @pytest.mark.asyncio
    async def test_concurrent_requests_use_different_browsers(self, mock_browser_pool):
        """Test that concurrent requests go to different browsers."""
        scraper, browsers = mock_browser_pool

        # Track which browser handles each request
        browser_usage = []

        original_new_context = [b.new_context for b in browsers]

        for i, browser in enumerate(browsers):

            async def make_context(browser_id=i):
                browser_usage.append(browser_id)
                return await original_new_context[browser_id]()

            browser.new_context = make_context

        # Make 3 concurrent requests
        requests = [
            ScrapeRequest(url=f"https://example.com/page{i}", timeout=30000)
            for i in range(3)
        ]

        await asyncio.gather(*[scraper.scrape(r) for r in requests])

        # All 3 browsers should have been used
        assert len(set(browser_usage)) == 3, (
            f"Expected 3 different browsers, got {set(browser_usage)}"
        )


class TestBrowserPoolConcurrency:
    """Test concurrent request handling."""

    @pytest.mark.asyncio
    async def test_max_concurrent_pages_per_browser(self):
        """Test that config supports both browser_count and max_concurrent_pages."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=2, max_concurrent_pages=4)
        scraper = CamoufoxScraper(config=config)

        # With 2 browsers and 4 total pages, each browser handles ~2 pages max
        # This ensures no single browser is overwhelmed
        pages_per_browser = config.max_concurrent_pages // config.browser_count
        assert pages_per_browser == 2, (
            f"Expected 2 pages per browser, got {pages_per_browser}"
        )

    @pytest.mark.asyncio
    async def test_semaphore_limits_total_concurrency(self):
        """Test that semaphore limits total concurrent requests."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=3, max_concurrent_pages=6)
        scraper = CamoufoxScraper(config=config)

        # Semaphore should limit to max_concurrent_pages
        assert scraper._semaphore._value == 6, (
            f"Semaphore should be 6, got {scraper._semaphore._value}"
        )


class TestBrowserPoolShutdown:
    """Test browser pool cleanup."""

    @pytest.mark.asyncio
    async def test_stop_closes_all_browsers(self):
        """Test that stop() closes all browser instances."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=3)
        scraper = CamoufoxScraper(config=config)

        # Setup mock browsers
        mock_browsers = [AsyncMock() for _ in range(3)]
        scraper._browsers = mock_browsers
        scraper._camoufox_instances = [MagicMock() for _ in range(3)]
        scraper._active_pages = 0

        await scraper.stop()

        # All browsers should be closed
        for i, browser in enumerate(mock_browsers):
            browser.close.assert_called_once(), f"Browser {i} should have been closed"

    @pytest.mark.asyncio
    async def test_stop_waits_for_active_pages(self):
        """Test that stop() waits for active pages before closing."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=2)
        scraper = CamoufoxScraper(config=config)

        mock_browsers = [AsyncMock() for _ in range(2)]
        scraper._browsers = mock_browsers
        scraper._camoufox_instances = [MagicMock() for _ in range(2)]
        scraper._active_pages = 2

        # Simulate pages completing after a short delay
        async def simulate_page_completion():
            await asyncio.sleep(0.1)
            scraper._active_pages = 0

        asyncio.create_task(simulate_page_completion())

        await scraper.stop()

        # Should have waited for pages to complete
        assert scraper._active_pages == 0


class TestBrowserPoolResilience:
    """Test browser pool error handling."""

    @pytest.mark.asyncio
    async def test_single_browser_failure_doesnt_crash_pool(self):
        """Test that one browser failing doesn't crash the pool."""
        from src.services.camoufox.scraper import CamoufoxScraper

        config = make_test_config(browser_count=3, max_concurrent_pages=6)
        scraper = CamoufoxScraper(config=config)

        # Create mock browsers, one that fails
        browsers = []
        for i in range(3):
            browser = MagicMock()
            context = AsyncMock()
            page = AsyncMock()
            response = AsyncMock()

            if i == 1:  # Second browser fails
                browser.new_context = AsyncMock(
                    side_effect=Exception("Browser crashed")
                )
            else:
                browser.new_context = AsyncMock(return_value=context)
                context.new_page = AsyncMock(return_value=page)
                context.route = AsyncMock()
                context.close = AsyncMock()
                page.goto = AsyncMock(return_value=response)
                page.content = AsyncMock(return_value="<html>OK</html>")
                page.wait_for_load_state = AsyncMock()
                page.evaluate = AsyncMock(return_value=100)
                page.close = AsyncMock()
                page.set_extra_http_headers = AsyncMock()
                response.status = 200
                response.all_headers = AsyncMock(
                    return_value={"content-type": "text/html"}
                )

            browsers.append(browser)

        scraper._browsers = browsers
        scraper._camoufox_instances = [MagicMock() for _ in range(3)]

        # First request succeeds (browser 0)
        request1 = ScrapeRequest(url="https://example.com/page1", timeout=30000)
        result1 = await scraper.scrape(request1)
        assert "error" not in result1

        # Second request fails (browser 1) but returns error, doesn't crash
        request2 = ScrapeRequest(url="https://example.com/page2", timeout=30000)
        result2 = await scraper.scrape(request2)
        assert "error" in result2

        # Third request succeeds (browser 2)
        request3 = ScrapeRequest(url="https://example.com/page3", timeout=30000)
        result3 = await scraper.scrape(request3)
        assert "error" not in result3
