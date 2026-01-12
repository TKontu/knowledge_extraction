"""Firecrawl client for web scraping."""

from dataclasses import dataclass
from urllib.parse import urlparse

import httpx


@dataclass
class ScrapeResult:
    """Result from a scrape operation.

    Attributes:
        url: The URL that was scraped.
        domain: Domain extracted from the URL.
        markdown: Markdown content of the page (None if failed).
        title: Page title from metadata (None if not available).
        metadata: Additional metadata from Firecrawl.
        status_code: HTTP status code (None if request failed).
        success: Whether the scrape was successful.
        error: Error message if scrape failed (None if successful).
    """

    url: str
    domain: str
    markdown: str | None
    title: str | None
    metadata: dict
    status_code: int | None
    success: bool
    error: str | None


@dataclass
class CrawlStatus:
    """Status of a crawl operation."""

    status: str  # "scraping", "completed", "failed"
    total: int
    completed: int
    pages: list[dict]  # List of scraped page data
    error: str | None = None


class ScrapeError(Exception):
    """Exception raised when scraping fails."""

    pass


class FirecrawlClient:
    """Client for interacting with Firecrawl API.

    Handles web scraping requests to Firecrawl service and returns
    structured results with markdown content and metadata.

    Args:
        base_url: Base URL of Firecrawl API (e.g., http://localhost:3002).
        timeout: Request timeout in seconds.

    Example:
        async with FirecrawlClient(base_url="http://localhost:3002") as client:
            result = await client.scrape("https://example.com")
            if result.success:
                print(result.markdown)
    """

    def __init__(self, base_url: str, timeout: int = 60) -> None:
        """Initialize FirecrawlClient.

        Args:
            base_url: Base URL of Firecrawl API.
            timeout: Request timeout in seconds (default: 60).
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._http_client = httpx.AsyncClient(timeout=timeout)

    async def scrape(self, url: str) -> ScrapeResult:
        """Scrape a URL and return structured result.

        Args:
            url: The URL to scrape.

        Returns:
            ScrapeResult with markdown content and metadata.

        Example:
            result = await client.scrape("https://example.com")
            if result.success:
                print(f"Title: {result.title}")
                print(f"Content: {result.markdown}")
        """
        domain = self._extract_domain(url)

        try:
            # Make request to Firecrawl API
            response = await self._http_client.post(
                f"{self.base_url}/v1/scrape",
                json={"url": url},
            )

            # Parse response
            data = response.json()

            # Handle successful scrape
            if response.status_code == 200 and data.get("success"):
                scrape_data = data.get("data", {})
                metadata = scrape_data.get("metadata", {})

                return ScrapeResult(
                    url=url,
                    domain=domain,
                    markdown=scrape_data.get("markdown"),
                    title=metadata.get("title"),
                    metadata=metadata,
                    status_code=metadata.get("statusCode", response.status_code),
                    success=True,
                    error=None,
                )

            # Handle error response from Firecrawl
            error_message = data.get("error", "Unknown error")
            return ScrapeResult(
                url=url,
                domain=domain,
                markdown=None,
                title=None,
                metadata={},
                status_code=response.status_code,
                success=False,
                error=error_message,
            )

        except TimeoutError as e:
            return ScrapeResult(
                url=url,
                domain=domain,
                markdown=None,
                title=None,
                metadata={},
                status_code=None,
                success=False,
                error=f"Request timeout: {str(e)}",
            )

        except Exception as e:
            # Handle all other errors (connection errors, etc.)
            return ScrapeResult(
                url=url,
                domain=domain,
                markdown=None,
                title=None,
                metadata={},
                status_code=None,
                success=False,
                error=str(e).lower(),
            )

    async def start_crawl(
        self,
        url: str,
        max_depth: int = 2,
        limit: int = 100,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        allow_backward_links: bool = False,
    ) -> str:
        """Start async crawl job.

        Args:
            url: Starting URL.
            max_depth: How deep to crawl.
            limit: Maximum pages to crawl.
            include_paths: URL patterns to include.
            exclude_paths: URL patterns to exclude.
            allow_backward_links: Allow sibling/parent URLs.

        Returns:
            Firecrawl job ID.
        """
        response = await self._http_client.post(
            f"{self.base_url}/v1/crawl",
            json={
                "url": url,
                "maxDepth": max_depth,
                "limit": limit,
                "includePaths": include_paths or [],
                "excludePaths": exclude_paths or [],
                "allowBackwardLinks": allow_backward_links,
                "scrapeOptions": {"formats": ["markdown"]},
            },
        )
        data = response.json()
        if not data.get("success"):
            raise ScrapeError(data.get("error", "Failed to start crawl"))
        return data["id"]

    async def get_crawl_status(self, crawl_id: str) -> CrawlStatus:
        """Get crawl job status.

        Args:
            crawl_id: Firecrawl job ID.

        Returns:
            CrawlStatus with progress and pages.
        """
        response = await self._http_client.get(
            f"{self.base_url}/v1/crawl/{crawl_id}"
        )
        data = response.json()
        return CrawlStatus(
            status=data.get("status", "unknown"),
            total=data.get("total", 0),
            completed=data.get("completed", 0),
            pages=data.get("data", []),
            error=data.get("error"),
        )

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL.

        Args:
            url: Full URL.

        Returns:
            Domain name (e.g., "example.com").

        Example:
            domain = client._extract_domain("https://www.example.com/path")
            # Returns: "www.example.com"
        """
        parsed = urlparse(url)
        return parsed.netloc

    async def close(self) -> None:
        """Close the HTTP client and cleanup resources.

        Should be called when done using the client, or use
        the client as an async context manager.
        """
        await self._http_client.aclose()

    async def __aenter__(self) -> "FirecrawlClient":
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager and cleanup."""
        await self.close()
