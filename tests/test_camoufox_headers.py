"""Tests for Camoufox browser header handling.

TDD: These tests should fail first, then pass after implementation.

ARCHITECTURAL NOTE:
Camoufox handles these headers internally via BrowserForge fingerprints:
- User-Agent (from navigator.userAgent)
- Accept-Language (from locale fingerprint)
- Accept-Encoding (internally)

We do NOT include these in STANDARD_BROWSER_HEADERS to avoid conflicts
with Camoufox's C++-level header injection.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.services.camoufox.models import ScrapeRequest
from src.services.camoufox.scraper import STANDARD_BROWSER_HEADERS, CamoufoxScraper


class TestCamoufoxBrowserHeaders:
    """Test that Camoufox sends standard browser headers."""

    @pytest.fixture
    def scraper(self):
        """Create scraper instance."""
        return CamoufoxScraper()

    @pytest.fixture
    def mock_browser(self):
        """Mock browser instance."""
        browser = MagicMock()
        context = AsyncMock()
        page = AsyncMock()
        response = AsyncMock()

        # Setup mock chain
        browser.new_context = AsyncMock(return_value=context)
        context.new_page = AsyncMock(return_value=page)
        context.route = AsyncMock()
        page.goto = AsyncMock(return_value=response)
        page.content = AsyncMock(return_value="<html><body>Test</body></html>")
        page.close = AsyncMock()
        context.close = AsyncMock()

        response.status = 200
        response.all_headers = AsyncMock(return_value={"content-type": "text/html"})

        return browser, page

    @pytest.mark.asyncio
    async def test_standard_headers_constant_exists(self):
        """Test that STANDARD_BROWSER_HEADERS constant is defined."""
        # This test verifies the constant exists and has expected structure
        assert hasattr(STANDARD_BROWSER_HEADERS, "__getitem__"), (
            "STANDARD_BROWSER_HEADERS should be a dict"
        )

        # Should contain headers that SUPPLEMENT Camoufox's internal handling
        # NOTE: User-Agent, Accept-Language, Accept-Encoding are handled by Camoufox
        expected_headers = [
            "Accept",
            "DNT",
            "Connection",
            "Upgrade-Insecure-Requests",
            "Sec-Fetch-Dest",
            "Sec-Fetch-Mode",
            "Sec-Fetch-Site",
            "Cache-Control",
        ]

        for header in expected_headers:
            assert header in STANDARD_BROWSER_HEADERS, (
                f"Missing expected header: {header}"
            )

        # These should NOT be in our headers (handled by Camoufox internally)
        assert "User-Agent" not in STANDARD_BROWSER_HEADERS, (
            "User-Agent should be handled by Camoufox, not manually set"
        )
        assert "Accept-Language" not in STANDARD_BROWSER_HEADERS, (
            "Accept-Language should be handled by Camoufox, not manually set"
        )
        assert "Accept-Encoding" not in STANDARD_BROWSER_HEADERS, (
            "Accept-Encoding should be handled by Camoufox, not manually set"
        )

    @pytest.mark.asyncio
    async def test_standard_headers_applied_to_all_requests(
        self, scraper, mock_browser
    ):
        """Test that standard browser headers are applied to ALL requests."""
        browser, page = mock_browser
        scraper._browsers = [browser]  # Use browser pool

        # Create request WITHOUT custom headers
        request = ScrapeRequest(
            url="https://example.com",
            timeout=30000,
        )

        await scraper._do_scrape(request, browser)

        # Verify set_extra_http_headers was called
        page.set_extra_http_headers.assert_called_once()

        # Get the headers that were applied
        call_args = page.set_extra_http_headers.call_args
        applied_headers = call_args[0][0]

        # Verify standard headers are present (those we add)
        assert "Accept" in applied_headers
        assert "Sec-Fetch-Dest" in applied_headers
        assert "Sec-Fetch-Mode" in applied_headers

    @pytest.mark.asyncio
    async def test_custom_headers_merged_with_standard(self, scraper, mock_browser):
        """Test that custom headers are merged with standard headers."""
        browser, page = mock_browser
        scraper._browsers = [browser]  # Use browser pool

        # Create request WITH custom headers
        custom_headers = {
            "X-Custom-Header": "custom-value",
            "Authorization": "Bearer token123",
        }

        request = ScrapeRequest(
            url="https://example.com",
            timeout=30000,
            headers=custom_headers,
        )

        await scraper._do_scrape(request, browser)

        # Get the headers that were applied
        call_args = page.set_extra_http_headers.call_args
        applied_headers = call_args[0][0]

        # Should have both standard AND custom headers
        assert "Accept" in applied_headers  # Standard
        assert "X-Custom-Header" in applied_headers  # Custom
        assert applied_headers["X-Custom-Header"] == "custom-value"
        assert applied_headers["Authorization"] == "Bearer token123"

    @pytest.mark.asyncio
    async def test_custom_headers_override_standard(self, scraper, mock_browser):
        """Test that custom headers can override standard ones."""
        browser, page = mock_browser
        scraper._browsers = [browser]  # Use browser pool

        # Create request that overrides a standard header
        custom_headers = {
            "Accept": "application/json",  # Override standard Accept header
        }

        request = ScrapeRequest(
            url="https://example.com",
            timeout=30000,
            headers=custom_headers,
        )

        await scraper._do_scrape(request, browser)

        # Get the headers that were applied
        call_args = page.set_extra_http_headers.call_args
        applied_headers = call_args[0][0]

        # Custom header should override standard
        assert applied_headers["Accept"] == "application/json"

    @pytest.mark.asyncio
    async def test_headers_not_conditional_on_request_headers(
        self, scraper, mock_browser
    ):
        """Test that headers are ALWAYS applied, not conditionally."""
        browser, page = mock_browser
        scraper._browsers = [browser]  # Use browser pool

        # Request WITHOUT custom headers
        request = ScrapeRequest(
            url="https://example.com",
            timeout=30000,
            # No headers field set
        )

        await scraper._do_scrape(request, browser)

        # set_extra_http_headers should STILL be called
        page.set_extra_http_headers.assert_called_once()

        # Should have standard headers even without custom ones
        call_args = page.set_extra_http_headers.call_args
        applied_headers = call_args[0][0]
        assert len(applied_headers) > 0
        assert "Accept" in applied_headers
