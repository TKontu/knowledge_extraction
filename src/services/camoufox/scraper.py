"""Camoufox browser management and scraping logic.

This module manages a single Camoufox browser instance with per-request contexts,
matching Firecrawl's Playwright service pattern exactly.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import structlog
from camoufox.async_api import AsyncCamoufox
from playwright.async_api import Browser, BrowserContext, Page, Response

from src.services.camoufox.config import CamoufoxSettings, settings
from src.services.camoufox.models import ScrapeRequest

logger = structlog.get_logger(__name__)


class CamoufoxScraper:
    """Manages a single Camoufox browser with per-request contexts.

    This follows Firecrawl's pattern of creating a new context per request
    and closing it immediately after scraping. No session persistence.
    """

    def __init__(self, config: CamoufoxSettings | None = None) -> None:
        """Initialize scraper with configuration.

        Args:
            config: Optional settings override. Uses global settings if None.
        """
        self.config = config or settings
        self._browser: Browser | None = None
        self._camoufox: AsyncCamoufox | None = None
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_pages)
        self._active_pages = 0
        self._lock = asyncio.Lock()

    @property
    def active_pages(self) -> int:
        """Get current number of active pages."""
        return self._active_pages

    @property
    def max_concurrent_pages(self) -> int:
        """Get maximum concurrent pages allowed."""
        return self.config.max_concurrent_pages

    async def start(self) -> None:
        """Start the Camoufox browser instance."""
        if self._browser is not None:
            logger.warning("browser_already_running")
            return

        logger.info(
            "starting_camoufox_browser",
            headless=self.config.headless,
            max_concurrent_pages=self.config.max_concurrent_pages,
        )

        # Build launch options
        launch_options: dict[str, Any] = {
            "headless": self.config.headless,
        }

        # Add proxy if configured
        if self.config.proxy:
            launch_options["proxy"] = {"server": self.config.proxy}
            logger.info("proxy_configured", proxy=self.config.proxy)

        # Start Camoufox with geoip for realistic fingerprints
        self._camoufox = AsyncCamoufox(geoip=True, **launch_options)
        self._browser = await self._camoufox.start()

        logger.info("camoufox_browser_started")

    async def stop(self) -> None:
        """Stop the Camoufox browser instance gracefully."""
        if self._browser is None:
            return

        logger.info("stopping_camoufox_browser", active_pages=self._active_pages)

        # Wait for active pages to complete (with timeout)
        wait_count = 0
        while self._active_pages > 0 and wait_count < 30:
            await asyncio.sleep(1)
            wait_count += 1

        if self._active_pages > 0:
            logger.warning(
                "forcing_browser_shutdown", remaining_pages=self._active_pages
            )

        try:
            if self._camoufox:
                await self._camoufox.stop()
        except Exception as e:
            logger.error("browser_stop_error", error=str(e))
        finally:
            self._browser = None
            self._camoufox = None

        logger.info("camoufox_browser_stopped")

    @asynccontextmanager
    async def _acquire_page(self):
        """Context manager to acquire and release a page slot."""
        async with self._lock:
            self._active_pages += 1
        try:
            yield
        finally:
            async with self._lock:
                self._active_pages -= 1

    async def scrape(self, request: ScrapeRequest) -> dict[str, Any]:
        """Scrape a URL and return the rendered HTML content.

        Creates a new browser context per request, matching Firecrawl's pattern.
        No session persistence across requests.

        Args:
            request: Scrape request with URL and options.

        Returns:
            Dictionary with content, pageStatusCode, and optional pageError.
            On failure, returns dictionary with error key.
        """
        if self._browser is None:
            return {"error": "Browser not started"}

        async with self._semaphore:
            async with self._acquire_page():
                return await self._do_scrape(request)

    async def _do_scrape(self, request: ScrapeRequest) -> dict[str, Any]:
        """Internal scrape implementation.

        Args:
            request: Scrape request with URL and options.

        Returns:
            Scrape result dictionary.
        """
        context: BrowserContext | None = None
        page: Page | None = None

        log = logger.bind(url=request.url, timeout=request.timeout)
        log.info("scrape_started")

        try:
            # Create new context for this request (matches Firecrawl pattern)
            context_options: dict[str, Any] = {}

            # Apply custom headers if provided
            if request.headers:
                context_options["extra_http_headers"] = request.headers

            # Handle TLS verification
            if request.skip_tls_verification:
                context_options["ignore_https_errors"] = True

            context = await self._browser.new_context(**context_options)
            page = await context.new_page()

            # Navigate to URL
            response: Response | None = await page.goto(
                request.url,
                timeout=request.timeout,
                wait_until="domcontentloaded",
            )

            # Wait for network idle (JavaScript execution)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                # Network idle timeout is not fatal - continue with what we have
                log.debug("network_idle_timeout")

            # Additional wait if specified
            if request.wait_after_load > 0:
                await asyncio.sleep(request.wait_after_load / 1000)

            # Wait for specific selector if provided
            if request.check_selector:
                try:
                    await page.wait_for_selector(
                        request.check_selector,
                        timeout=min(request.timeout, 10000),
                    )
                except Exception as e:
                    log.warning("selector_not_found", selector=request.check_selector)
                    # Continue anyway - selector might not be present on this page

            # Get the rendered content (DOM after JavaScript execution)
            content = await page.content()

            # Determine status code and any error
            status_code = response.status if response else 200
            page_error = None

            if status_code >= 400:
                page_error = f"HTTP {status_code}"
                log.warning("page_error_status", status=status_code)

            log.info(
                "scrape_completed",
                status=status_code,
                content_length=len(content),
            )

            return {
                "content": content,
                "pageStatusCode": status_code,
                "pageError": page_error,
            }

        except Exception as e:
            error_msg = str(e)
            log.error("scrape_failed", error=error_msg)
            return {"error": f"An error occurred while fetching the page: {error_msg}"}

        finally:
            # Always close page and context (matching Firecrawl pattern)
            if page:
                try:
                    await page.close()
                except Exception:
                    pass
            if context:
                try:
                    await context.close()
                except Exception:
                    pass


# Global scraper instance
scraper = CamoufoxScraper()
