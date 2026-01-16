"""Proxy adapter for routing requests through FlareSolverr."""

import json
from urllib.parse import urlparse

import aiohttp.web
import httpx
import structlog

from .flaresolverr_client import FlareSolverrClient

logger = structlog.get_logger(__name__)


class ProxyAdapter:
    """HTTP proxy adapter that routes requests through FlareSolverr for blocked domains."""

    def __init__(
        self, flaresolverr_url: str, blocked_domains: list[str], max_timeout: int
    ) -> None:
        """Initialize proxy adapter.

        Args:
            flaresolverr_url: URL of FlareSolverr service
            blocked_domains: List of domains requiring FlareSolverr proxy
            max_timeout: Maximum timeout in milliseconds
        """
        self.flaresolverr_url = flaresolverr_url
        self.blocked_domains = {domain.lower() for domain in blocked_domains}
        self.max_timeout = max_timeout

        # Initialize FlareSolverr client
        http_client = httpx.AsyncClient(timeout=max_timeout / 1000)
        self.flaresolverr_client = FlareSolverrClient(
            base_url=flaresolverr_url,
            max_timeout=max_timeout,
            http_client=http_client,
        )

    def should_use_flaresolverr(self, domain: str) -> bool:
        """Check if domain should use FlareSolverr.

        Args:
            domain: Domain or URL to check

        Returns:
            True if domain is in blocked list, False otherwise
        """
        # Extract domain from URL if full URL provided
        if domain.startswith(("http://", "https://")):
            parsed = urlparse(domain)
            domain = parsed.netloc

        # Convert to lowercase
        domain_lower = domain.lower()

        # Check if domain matches or is subdomain of blocked domain
        for blocked_domain in self.blocked_domains:
            if domain_lower == blocked_domain or domain_lower.endswith(
                f".{blocked_domain}"
            ):
                return True

        return False

    async def handle_request(
        self, request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        """Handle incoming proxy request.

        Args:
            request: Incoming aiohttp request

        Returns:
            aiohttp Response with proxied content
        """
        try:
            # Extract target URL from request path (strip leading /)
            url = request.path.lstrip("/")

            # Parse domain from URL
            parsed = urlparse(url)
            domain = parsed.netloc

            if self.should_use_flaresolverr(domain):
                # Route through FlareSolverr
                logger.info("proxy_routing", url=url, method="flaresolverr")
                response = await self.flaresolverr_client.solve_request(url)
                return aiohttp.web.Response(
                    text=response.html, status=response.status, headers=response.headers
                )
            else:
                # Direct passthrough
                logger.info("proxy_routing", url=url, method="direct")
                async with httpx.AsyncClient() as client:
                    http_response = await client.get(url)
                    return aiohttp.web.Response(
                        body=http_response.content,
                        status=http_response.status_code,
                        headers=dict(http_response.headers),
                    )

        except Exception as e:
            logger.error("proxy_error", error=str(e), url=request.path)
            return aiohttp.web.Response(text=str(e), status=500)

    async def health_check(
        self, request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        """Health check endpoint.

        Args:
            request: Incoming aiohttp request

        Returns:
            JSON response with health status
        """
        return aiohttp.web.Response(
            text=json.dumps(
                {
                    "status": "ok",
                    "flaresolverr_url": self.flaresolverr_url,
                    "blocked_domains": list(self.blocked_domains),
                }
            ),
            status=200,
            content_type="application/json",
        )
