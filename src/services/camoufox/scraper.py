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

# HTTP error messages matching Firecrawl's get_error.ts
HTTP_ERROR_MESSAGES = {
    400: "Bad Request",
    401: "Unauthorized",
    402: "Payment Required",
    403: "Forbidden",
    404: "Not Found",
    405: "Method Not Allowed",
    406: "Not Acceptable",
    407: "Proxy Authentication Required",
    408: "Request Timeout",
    409: "Conflict",
    410: "Gone",
    411: "Length Required",
    412: "Precondition Failed",
    413: "Payload Too Large",
    414: "URI Too Long",
    415: "Unsupported Media Type",
    416: "Range Not Satisfiable",
    417: "Expectation Failed",
    418: "I'm a Teapot",
    421: "Misdirected Request",
    422: "Unprocessable Entity",
    423: "Locked",
    424: "Failed Dependency",
    425: "Too Early",
    426: "Upgrade Required",
    428: "Precondition Required",
    429: "Too Many Requests",
    431: "Request Header Fields Too Large",
    451: "Unavailable For Legal Reasons",
    500: "Internal Server Error",
    501: "Not Implemented",
    502: "Bad Gateway",
    503: "Service Unavailable",
    504: "Gateway Timeout",
    505: "HTTP Version Not Supported",
    506: "Variant Also Negotiates",
    507: "Insufficient Storage",
    508: "Loop Detected",
    510: "Not Extended",
    511: "Network Authentication Required",
}

# Ad-serving domains to block (matching Firecrawl's api.ts)
AD_SERVING_DOMAINS = [
    "doubleclick.net",
    "adservice.google.com",
    "googlesyndication.com",
    "googletagservices.com",
    "googletagmanager.com",
    "google-analytics.com",
    "adsystem.com",
    "adservice.com",
    "adnxs.com",
    "ads-twitter.com",
    "facebook.net",
    "fbcdn.net",
    "amazon-adsystem.com",
]

# Standard browser headers to send with all requests
# These supplement Camoufox's built-in header handling
#
# IMPORTANT: Do NOT include headers that Camoufox handles internally:
# - User-Agent (set from navigator.userAgent fingerprint)
# - Accept-Language (set from locale fingerprint)
# - Accept-Encoding (set internally)
#
# Adding these would conflict with Camoufox's C++-level header injection.
# See: https://camoufox.com - HTTP Headers section
STANDARD_BROWSER_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}


def get_error_message(status_code: int | None) -> str | None:
    """Get human-readable error message for HTTP status code.

    Args:
        status_code: HTTP status code or None.

    Returns:
        Error message string or None if no error.
    """
    if status_code is None:
        return "No response received"
    if status_code < 300:
        return None
    return HTTP_ERROR_MESSAGES.get(status_code, "Unknown Error")


class CamoufoxScraper:
    """Manages a pool of Camoufox browsers with per-request contexts.

    Uses multiple browser instances to enable true parallelism, since a single
    Firefox browser cannot handle concurrent page.goto() calls efficiently.

    This follows Firecrawl's pattern of creating a new context per request
    and closing it immediately after scraping. No session persistence.
    """

    def __init__(self, config: CamoufoxSettings | None = None) -> None:
        """Initialize scraper with configuration.

        Args:
            config: Optional settings override. Uses global settings if None.
        """
        self.config = config or settings
        self._browsers: list[Browser] = []
        self._camoufox_instances: list[AsyncCamoufox] = []
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent_pages)
        self._active_pages = 0
        self._lock = asyncio.Lock()
        self._browser_index = 0  # For round-robin selection
        self._restarting_browsers: set[int] = set()  # Track browsers being restarted

    @property
    def active_pages(self) -> int:
        """Get current number of active pages."""
        return self._active_pages

    @property
    def max_concurrent_pages(self) -> int:
        """Get maximum concurrent pages allowed."""
        return self.config.max_concurrent_pages

    def _get_next_browser(self) -> tuple[Browser, int]:
        """Get the next connected browser in round-robin order.

        Checks browser connectivity and skips dead browsers to prevent
        cascade failures when a browser dies from a timeout. Schedules
        background restarts for any dead browsers found.

        Returns:
            Tuple of (Browser instance, browser index).

        Raises:
            RuntimeError: If no browsers are available or all are disconnected.
        """
        if not self._browsers:
            raise RuntimeError("No browsers available in pool")

        # Track dead browsers to restart after finding a live one
        dead_browser_indices: list[int] = []

        # Try each browser once, starting from current index
        start_index = self._browser_index
        for _ in range(len(self._browsers)):
            browser = self._browsers[self._browser_index]
            current_index = self._browser_index
            self._browser_index = (self._browser_index + 1) % len(self._browsers)

            if browser.is_connected():
                # Schedule background restarts for any dead browsers we found
                for dead_idx in dead_browser_indices:
                    self._schedule_browser_restart(dead_idx)
                return browser, current_index
            else:
                logger.warning(
                    "browser_disconnected_skipping",
                    browser_index=current_index,
                    checked_from=start_index,
                )
                dead_browser_indices.append(current_index)

        # All browsers are dead
        raise RuntimeError("All browsers in pool are disconnected")

    def _schedule_browser_restart(self, index: int) -> None:
        """Schedule a background restart for a dead browser.

        Only schedules if restart is not already in progress for this index.

        Args:
            index: Index of the browser to restart.
        """
        if index in self._restarting_browsers:
            logger.debug("browser_restart_already_scheduled", browser_index=index)
            return

        logger.info("scheduling_browser_restart", browser_index=index)
        task = asyncio.create_task(self._restart_browser(index))
        task.add_done_callback(self._handle_restart_task_result)

    async def start(self) -> None:
        """Start the Camoufox browser pool."""
        if self._browsers:
            logger.warning("browser_pool_already_running")
            return

        browser_count = self.config.browser_count
        logger.info(
            "starting_camoufox_browser_pool",
            browser_count=browser_count,
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

        # Start multiple Camoufox browsers
        for i in range(browser_count):
            try:
                camoufox = AsyncCamoufox(geoip=True, **launch_options)
                browser = await camoufox.start()
                self._camoufox_instances.append(camoufox)
                self._browsers.append(browser)
                logger.info("browser_started", browser_index=i)
            except Exception as e:
                logger.error("browser_start_failed", browser_index=i, error=str(e))
                # Continue starting other browsers - partial pool is better than none
                continue

        if not self._browsers:
            raise RuntimeError("Failed to start any Camoufox browsers")

        logger.info(
            "camoufox_browser_pool_started",
            started_count=len(self._browsers),
            requested_count=browser_count,
        )

    async def stop(self) -> None:
        """Stop all Camoufox browser instances gracefully."""
        if not self._browsers:
            return

        logger.info(
            "stopping_camoufox_browser_pool",
            browser_count=len(self._browsers),
            active_pages=self._active_pages,
        )

        # Wait for active pages to complete (with timeout)
        wait_count = 0
        while self._active_pages > 0 and wait_count < 30:
            await asyncio.sleep(1)
            wait_count += 1

        if self._active_pages > 0:
            logger.warning(
                "forcing_browser_pool_shutdown", remaining_pages=self._active_pages
            )

        # Cleanup AsyncCamoufox context managers
        # __aexit__ handles browser.close() internally, so we don't call it separately
        for i, camoufox in enumerate(self._camoufox_instances):
            try:
                await camoufox.__aexit__(None, None, None)
                logger.debug("camoufox_context_cleaned", browser_index=i)
            except Exception as e:
                logger.error("camoufox_cleanup_error", browser_index=i, error=str(e))

        self._browsers = []
        self._camoufox_instances = []
        self._browser_index = 0

        logger.info("camoufox_browser_pool_stopped")

    async def _restart_browser(self, index: int) -> Browser | None:
        """Restart a dead browser at the given index.

        Uses a tracking set to prevent concurrent restart attempts on the
        same browser index.

        Args:
            index: Index of the browser to restart in the pool.

        Returns:
            New Browser instance, or None if restart failed or already in progress.
        """
        # Check if already restarting (prevent concurrent restarts)
        if index in self._restarting_browsers:
            logger.debug("browser_restart_already_in_progress", browser_index=index)
            return None

        self._restarting_browsers.add(index)
        try:
            logger.info("restarting_browser", browser_index=index)

            # Clean up old camoufox instance (with timeout to prevent hanging)
            old_camoufox = self._camoufox_instances[index]
            try:
                await asyncio.wait_for(
                    old_camoufox.__aexit__(None, None, None),
                    timeout=30.0,
                )
            except asyncio.TimeoutError:
                logger.warning(
                    "old_browser_cleanup_timeout", browser_index=index, timeout=30.0
                )
            except Exception as e:
                logger.debug(
                    "old_browser_cleanup_error", browser_index=index, error=str(e)
                )

            # Build launch options (same as start())
            launch_options: dict[str, Any] = {
                "headless": self.config.headless,
            }
            if self.config.proxy:
                launch_options["proxy"] = {"server": self.config.proxy}

            # Create new browser
            try:
                camoufox = AsyncCamoufox(geoip=True, **launch_options)
                browser = await camoufox.start()
                self._camoufox_instances[index] = camoufox
                self._browsers[index] = browser
                logger.info("browser_restarted", browser_index=index)
                return browser
            except Exception as e:
                logger.error("browser_restart_failed", browser_index=index, error=str(e))
                return None
        finally:
            self._restarting_browsers.discard(index)

    def _handle_restart_task_result(self, task: asyncio.Task) -> None:
        """Handle completion of background browser restart task.

        Logs any unexpected exceptions that weren't caught inside _restart_browser.
        """
        try:
            # This will re-raise any exception from the task
            task.result()
        except asyncio.CancelledError:
            logger.debug("browser_restart_task_cancelled")
        except Exception as e:
            # This shouldn't happen since _restart_browser catches exceptions,
            # but log it just in case
            logger.error("browser_restart_task_unexpected_error", error=str(e))

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

    async def _discover_ajax_urls(self, page: Page, log) -> list[str]:
        """Discover AJAX URLs by clicking interactive elements.

        Finds clickable elements (tabs, buttons with data attributes),
        clicks each one, and captures the network requests made.

        Args:
            page: Playwright page object.
            log: Bound logger instance.

        Returns:
            List of discovered AJAX URLs.
        """
        discovered_urls: set[str] = set()
        base_url = page.url

        # Set up request interception to capture AJAX calls
        async def capture_request(request):
            url = request.url
            resource_type = request.resource_type
            # Capture XHR, fetch, and document requests that aren't the base page
            # Also capture requests with common AJAX query patterns
            if (
                url != base_url
                and not any(domain in url for domain in AD_SERVING_DOMAINS)
                and (
                    resource_type in ("xhr", "fetch", "document")
                    or "ajax" in url.lower()
                    or "api" in url.lower()
                )
            ):
                # Skip common non-AJAX resources
                if not any(url.endswith(ext) for ext in (
                    ".css", ".js", ".png", ".jpg", ".gif", ".svg", ".woff", ".woff2"
                )):
                    discovered_urls.add(url)

        page.on("request", capture_request)

        try:
            # Find clickable elements that likely trigger AJAX
            # Look for: tabs, buttons with data-* attributes, links with # or javascript:
            clickable_selectors = [
                # Tab-like elements (common AJAX patterns)
                "[data-toggle='tab']",
                "[role='tab']",
                ".nav-tabs a",
                ".tab-link",
                ".year-link",  # Oscar films page pattern
                # Elements with year/filter data attributes
                "[data-year]",
                "[data-filter]",
                "[data-id]",
                # Links that don't navigate (hash links, javascript:)
                "a[href='#']",  # Exact hash links (common AJAX trigger)
                "a[href^='#']",  # Hash links with anchors
                "a[href^='javascript:']",
                # Buttons that aren't submit buttons
                "button:not([type='submit'])",
                # Clickable divs/spans with cursor pointer (via class names)
                ".clickable",
                "[onclick]",
            ]

            # Combine selectors
            selector = ", ".join(clickable_selectors)

            # Get all matching elements
            elements = await page.query_selector_all(selector)
            log.info("ajax_discovery_elements_found", count=len(elements))

            # Click each element and wait for network activity
            max_clicks = self.config.ajax_discovery_max_clicks
            for i, element in enumerate(elements[:max_clicks]):
                try:
                    # Check if element is visible and clickable
                    is_visible = await element.is_visible()
                    if not is_visible:
                        continue

                    # Get element info for logging
                    tag = await element.evaluate("el => el.tagName")
                    text = await element.evaluate(
                        "el => el.textContent?.trim()?.substring(0, 30) || ''"
                    )
                    elem_id = await element.evaluate("el => el.id || ''")
                    elem_class = await element.evaluate("el => el.className || ''")
                    href = await element.evaluate(
                        "el => el.getAttribute('href') || ''"
                    )

                    # Skip navigation links that would leave the page
                    if href and href not in ("#", "javascript:void(0)", "javascript:;", ""):
                        # Skip absolute URLs and paths that would navigate away
                        if (
                            href.startswith("/")
                            or href.startswith("http")
                            or href.startswith("mailto:")
                            or href.startswith("tel:")
                        ):
                            log.debug(
                                "ajax_discovery_skipping_nav",
                                index=i,
                                href=href,
                            )
                            continue

                    log.info(
                        "ajax_discovery_clicking",
                        index=i,
                        tag=tag,
                        text=text,
                        id=elem_id,
                        class_name=elem_class,
                    )

                    # Click and wait for network activity
                    await element.click()
                    # Wait for AJAX - some sites have intentional delays up to 2-3 seconds
                    await page.wait_for_timeout(3000)

                    # Try to wait for network idle (JS-heavy sites may take longer)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass  # Timeout is fine - continue anyway

                except Exception as e:
                    log.debug("ajax_discovery_click_failed", index=i, error=str(e))
                    continue

            log.info("ajax_discovery_complete", urls_found=len(discovered_urls))

        except Exception as e:
            log.warning("ajax_discovery_error", error=str(e))

        finally:
            # Remove the request listener
            page.remove_listener("request", capture_request)

        return list(discovered_urls)

    async def _wait_for_content_ready(
        self, page: Page, timeout_ms: int, log
    ) -> None:
        """Wait for content to be ready using a tiered approach.

        Strategy:
        1. Wait for DOM to load (fast, reliable baseline)
        2. Try networkidle with short timeout (avoid long waits)
        3. Fall back to content stability check if networkidle times out

        This prevents the 60-second timeout issue where pages complete
        quickly but we wait unnecessarily for full network quiescence.

        Args:
            page: Playwright page object.
            timeout_ms: Network idle timeout in milliseconds.
            log: Bound logger instance.
        """
        try:
            # Step 1: Ensure DOM is loaded (fast, reliable)
            await page.wait_for_load_state("domcontentloaded", timeout=10000)
            log.debug("dom_content_loaded")

            # Step 2: Try networkidle with SHORT timeout (5s default)
            try:
                await page.wait_for_load_state("networkidle", timeout=timeout_ms)
                log.debug("network_idle_achieved")
            except Exception:
                # Network never went idle - that's acceptable
                # Many modern sites with persistent connections never reach true idle
                log.debug("network_idle_timeout_acceptable")

                # Step 3: Check if content is stable (stopped changing)
                await self._wait_for_content_stability(page, log)

        except Exception as e:
            log.warning("page_load_incomplete", error=str(e))
            # Continue anyway - we may have partial content

    async def _wait_for_content_stability(self, page: Page, log) -> None:
        """Wait until page content stops changing.

        Checks if the page HTML length remains stable across multiple
        checks, indicating JavaScript execution has completed.

        Args:
            page: Playwright page object.
            log: Bound logger instance.
        """
        checks = self.config.content_stability_checks
        interval_ms = self.config.content_stability_interval

        last_length = 0
        stable_count = 0

        # Max iterations = checks * 2 to avoid infinite loops
        for _ in range(checks * 2):
            try:
                current_length = await page.evaluate("document.body.innerHTML.length")

                if current_length == last_length:
                    stable_count += 1
                    if stable_count >= checks:
                        log.debug("content_stable", checks=stable_count)
                        return  # Content is stable
                else:
                    stable_count = 0
                    last_length = current_length

                await asyncio.sleep(interval_ms / 1000)

            except Exception as e:
                log.debug("content_stability_check_failed", error=str(e))
                return  # Continue with what we have

        log.debug("content_stability_max_iterations_reached")

    async def _inline_iframes_in_dom(self, page: Page, log) -> int:
        """Replace iframe elements with their content directly in the DOM.

        Uses JavaScript evaluation to access iframe contentDocument and
        replace each iframe with a div containing its content.

        Args:
            page: Playwright page object.
            log: Bound logger instance.

        Returns:
            Number of iframes processed.
        """
        try:
            processed = await page.evaluate("""
                () => {
                    let count = 0;
                    const iframes = document.querySelectorAll('iframe');
                    iframes.forEach(iframe => {
                        try {
                            const doc = iframe.contentDocument ||
                                       (iframe.contentWindow ? iframe.contentWindow.document : null);
                            if (doc && doc.body) {
                                const div = document.createElement('div');
                                div.setAttribute('data-iframe-src', iframe.src || '');
                                div.setAttribute('data-original-tag', 'iframe');
                                div.innerHTML = doc.body.innerHTML;
                                iframe.parentNode.replaceChild(div, iframe);
                                count++;
                            }
                        } catch (e) {
                            // Cross-origin iframe - cannot access content
                            // Leave iframe as-is
                        }
                    });
                    return count;
                }
            """)
            if processed > 0:
                log.debug("iframes_inlined", count=processed)
            return processed
        except Exception as e:
            log.warning("iframe_inline_error", error=str(e))
            return 0

    async def scrape(self, request: ScrapeRequest) -> dict[str, Any]:
        """Scrape a URL and return the rendered HTML content.

        Creates a new browser context per request on a browser from the pool,
        using round-robin selection for even distribution. Automatically skips
        dead browsers and attempts to restart them if all are disconnected.

        Args:
            request: Scrape request with URL and options.

        Returns:
            Dictionary with content, pageStatusCode, and optional pageError.
            On failure, returns dictionary with error key.
        """
        if not self._browsers:
            return {"error": "Browser pool not started"}

        async with self._semaphore:
            # Select a connected browser (round-robin with health check)
            try:
                browser, browser_idx = self._get_next_browser()
            except RuntimeError as e:
                # All browsers are disconnected - try to restart one
                logger.warning("all_browsers_disconnected_attempting_restart")
                browser = await self._restart_browser(0)
                if browser is None:
                    return {"error": str(e)}
                browser_idx = 0

            async with self._acquire_page():
                result = await self._do_scrape(request, browser)

                # If scrape failed due to browser death, try to restart it
                # for future requests (don't retry this request to avoid delays)
                if (
                    "error" in result
                    and "browser has been closed" in result["error"].lower()
                ):
                    logger.warning(
                        "browser_died_during_scrape",
                        browser_index=browser_idx,
                        url=request.url,
                    )
                    # Schedule restart in background (don't block this request)
                    task = asyncio.create_task(self._restart_browser(browser_idx))
                    task.add_done_callback(self._handle_restart_task_result)

                return result

    async def _do_scrape(
        self, request: ScrapeRequest, browser: Browser
    ) -> dict[str, Any]:
        """Internal scrape implementation.

        Args:
            request: Scrape request with URL and options.
            browser: Browser instance from the pool to use.

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

            # Handle TLS verification
            if request.skip_tls_verification:
                context_options["ignore_https_errors"] = True

            context = await browser.new_context(**context_options)

            # Set up ad-blocking route (matching Firecrawl's api.ts)
            async def block_ads(route):
                url = route.request.url
                if any(domain in url for domain in AD_SERVING_DOMAINS):
                    await route.abort()
                else:
                    await route.continue_()

            await context.route("**/*", block_ads)

            page = await context.new_page()

            # Merge standard browser headers with custom headers
            # Standard headers make Camoufox appear as real Firefox
            headers_to_apply = {**STANDARD_BROWSER_HEADERS}
            if request.headers:
                # Filter out headers that Camoufox handles internally
                # These would override the browser fingerprint and break anti-bot evasion
                protected_headers = {"user-agent", "accept-language", "accept-encoding"}
                filtered_headers = {
                    k: v for k, v in request.headers.items()
                    if k.lower() not in protected_headers
                }
                if filtered_headers != request.headers:
                    log.info(
                        "filtered_protected_headers",
                        original_count=len(request.headers),
                        filtered_count=len(filtered_headers),
                    )
                headers_to_apply.update(filtered_headers)

            # Always apply headers (not conditional)
            await page.set_extra_http_headers(headers_to_apply)

            # Navigate to URL (wait_until="load" matches Firecrawl)
            response: Response | None = await page.goto(
                request.url,
                timeout=request.timeout,
                wait_until="load",
            )

            # Use smart waiting strategy (DOM + networkidle + content stability)
            await self._wait_for_content_ready(
                page, self.config.networkidle_timeout, log
            )

            # Additional wait if specified
            if request.wait_after_load > 0:
                await asyncio.sleep(request.wait_after_load / 1000)

            # Discover AJAX URLs if requested (before getting content)
            discovered_urls: list[str] = []
            if request.discover_ajax:
                discovered_urls = await self._discover_ajax_urls(page, log)

            # Wait for specific selector if provided (Firecrawl throws on failure)
            if request.check_selector:
                try:
                    await page.wait_for_selector(
                        request.check_selector,
                        timeout=min(request.timeout, 10000),
                    )
                except Exception:
                    log.warning("selector_not_found", selector=request.check_selector)
                    return {"error": "Required selector not found"}

            # Extract content-type header from response
            content_type: str | None = None
            if response:
                headers = await response.all_headers()
                content_type = next(
                    (v for k, v in headers.items() if k.lower() == "content-type"),
                    None,
                )

            # For JSON/plain-text, return raw body instead of DOM
            if content_type and (
                "application/json" in content_type.lower()
                or "text/plain" in content_type.lower()
            ):
                body = await response.body()
                # Parse charset from content-type header
                charset = "utf-8"
                if content_type:
                    for part in content_type.split(";"):
                        if part.strip().lower().startswith("charset="):
                            charset = part.split("=", 1)[1].strip().strip("\"'")
                            break
                try:
                    content = body.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    content = body.decode("utf-8", errors="replace")
            else:
                # Inline iframe contents into the DOM before extraction
                await self._inline_iframes_in_dom(page, log)

                # Get the rendered content (DOM after JavaScript execution)
                content = await page.content()

            # Determine status code and any error (matching Firecrawl's get_error.ts)
            status_code = response.status if response else None
            page_error = get_error_message(status_code)

            # Use 0 if no response received (not 200, which falsely indicates success)
            if status_code is None:
                status_code = 0

            if page_error:
                log.warning("page_error_status", status=status_code, error=page_error)

            log.info(
                "scrape_completed",
                status=status_code,
                content_length=len(content),
                content_type=content_type,
                discovered_urls=len(discovered_urls) if discovered_urls else 0,
            )

            result = {
                "content": content,
                "pageStatusCode": status_code,
                "pageError": page_error,
                "contentType": content_type,
            }

            # Include discovered URLs if any were found
            if discovered_urls:
                result["discoveredUrls"] = discovered_urls

            return result

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
