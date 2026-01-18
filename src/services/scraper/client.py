"""Firecrawl client for web scraping."""

from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx
import structlog

logger = structlog.get_logger(__name__)

# Default user agent for crawling (customizable)
DEFAULT_USER_AGENT = "ResearchBot/1.0 (AI Research Assistant)"

# Known AI bot user-agents that sites may allow in llms.txt
AI_BOT_PATTERNS = [
    "GPTBot",
    "ClaudeBot",
    "PerplexityBot",
    "Google-Extended",
    "Anthropic",
    "OpenAI",
]


@dataclass
class LlmsTxtResult:
    """Result from checking a site's llms.txt file.

    Attributes:
        exists: Whether llms.txt was found.
        allows_ai_agents: Whether the site explicitly allows AI agents.
        allowed_agents: List of specifically allowed AI agent names.
        description: Site description from llms.txt if present.
        raw_content: Raw content of llms.txt file.
    """

    exists: bool
    allows_ai_agents: bool
    allowed_agents: list[str] = field(default_factory=list)
    description: str | None = None
    raw_content: str | None = None


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
        self._http_client = httpx.AsyncClient(
            timeout=timeout,
            limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
        )

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

    async def check_llms_txt(self, url: str) -> LlmsTxtResult:
        """Check if a site has llms.txt and allows AI agents.

        Args:
            url: Any URL on the target site.

        Returns:
            LlmsTxtResult with parsing results.
        """
        parsed = urlparse(url)
        base_url = f"{parsed.scheme}://{parsed.netloc}"
        llms_txt_url = f"{base_url}/llms.txt"

        try:
            response = await self._http_client.get(llms_txt_url, timeout=10)

            if response.status_code != 200:
                return LlmsTxtResult(exists=False, allows_ai_agents=False)

            content = response.text
            allowed_agents = []
            description = None
            allows_ai = False

            # Parse llms.txt content
            for line in content.split("\n"):
                line = line.strip()

                # Check for User-Agent allow directives
                if line.lower().startswith("user-agent:"):
                    agent = line.split(":", 1)[1].strip()
                    # Check if it's an AI bot
                    for pattern in AI_BOT_PATTERNS:
                        if pattern.lower() in agent.lower():
                            allowed_agents.append(agent)
                            allows_ai = True

                # Check for Allow: / directive (general allow)
                if line.lower().startswith("allow:") and "/" in line:
                    allows_ai = True

                # Extract description
                if line.lower().startswith("description:"):
                    description = line.split(":", 1)[1].strip()

            logger.info(
                "llms_txt_checked",
                url=llms_txt_url,
                exists=True,
                allows_ai=allows_ai,
                allowed_agents=allowed_agents,
            )

            return LlmsTxtResult(
                exists=True,
                allows_ai_agents=allows_ai,
                allowed_agents=allowed_agents,
                description=description,
                raw_content=content,
            )

        except Exception as e:
            logger.debug("llms_txt_fetch_failed", url=llms_txt_url, error=str(e))
            return LlmsTxtResult(exists=False, allows_ai_agents=False)

    async def start_crawl(
        self,
        url: str,
        max_depth: int = 2,
        limit: int = 100,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        allow_backward_links: bool = False,
        ignore_robots_txt: bool = False,
        user_agent: str | None = None,
        check_llms_txt_override: bool = True,
        scrape_timeout: int = 60000,
        delay_ms: int | None = None,
        max_concurrency: int | None = None,
    ) -> str:
        """Start async crawl job with rate limiting.

        Args:
            url: Starting URL.
            max_depth: How deep to crawl from the starting URL (relative depth).
            limit: Maximum pages to crawl.
            include_paths: URL patterns to include.
            exclude_paths: URL patterns to exclude.
            allow_backward_links: Allow sibling/parent URLs.
            ignore_robots_txt: If True, ignore robots.txt restrictions.
            user_agent: Custom user agent string (defaults to ResearchBot).
            check_llms_txt_override: If True, check llms.txt and override
                robots.txt if AI agents are allowed.
            scrape_timeout: Playwright page load timeout in milliseconds.
                Default 60000 (60s) to allow for FlareSolverr anti-bot bypass.
            delay_ms: Delay between requests in milliseconds (respectful crawling).
            max_concurrency: Max concurrent requests (rate limiting).

        Returns:
            Firecrawl job ID.
        """
        # Calculate the starting URL's depth and adjust maxDepth for Firecrawl
        # Firecrawl uses absolute depth from domain root, but we want relative depth
        parsed_url = urlparse(url)
        url_path = parsed_url.path.strip("/")
        starting_depth = len(url_path.split("/")) if url_path else 0
        absolute_max_depth = starting_depth + max_depth

        # Check llms.txt to see if we should override robots.txt
        if check_llms_txt_override and not ignore_robots_txt:
            llms_result = await self.check_llms_txt(url)
            if llms_result.allows_ai_agents:
                logger.info(
                    "llms_txt_override",
                    url=url,
                    reason="Site allows AI agents via llms.txt",
                    allowed_agents=llms_result.allowed_agents,
                )
                ignore_robots_txt = True

        # Build scrape options with custom user agent and timeout
        scrape_options: dict = {
            "formats": ["markdown"],
            "timeout": scrape_timeout,  # Playwright page load timeout (ms)
        }
        if user_agent or not ignore_robots_txt:
            # Use custom user agent
            scrape_options["headers"] = {
                "User-Agent": user_agent or DEFAULT_USER_AGENT
            }

        # Build crawl request with rate limiting options
        crawl_request = {
            "url": url,
            "maxDepth": absolute_max_depth,
            "limit": limit,
            "includePaths": include_paths or [],
            "excludePaths": exclude_paths or [],
            "allowBackwardLinks": allow_backward_links,
            "ignoreRobotsTxt": ignore_robots_txt,
            "scrapeOptions": scrape_options,
        }

        # Add rate limiting parameters if specified
        if delay_ms is not None:
            crawl_request["delay"] = delay_ms
        if max_concurrency is not None:
            crawl_request["maxConcurrency"] = max_concurrency

        response = await self._http_client.post(
            f"{self.base_url}/v1/crawl",
            json=crawl_request,
        )
        try:
            data = response.json()
        except Exception as e:
            logger.error(
                "firecrawl_invalid_response",
                endpoint="start_crawl",
                status_code=response.status_code,
                error=str(e),
            )
            raise ScrapeError(f"Invalid JSON response from Firecrawl: {e}") from e
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
        try:
            data = response.json()
        except Exception as e:
            logger.error(
                "firecrawl_invalid_response",
                endpoint="get_crawl_status",
                crawl_id=crawl_id,
                status_code=response.status_code,
                error=str(e),
            )
            return CrawlStatus(
                status="error",
                total=0,
                completed=0,
                pages=[],
                error=f"Invalid JSON response from Firecrawl: {e}",
            )
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
