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

    def _extract_url(self, request: aiohttp.web.Request) -> str:
        """Extract target URL from request (supports both proxy formats).

        Handles two proxy request formats:
        1. Explicit proxy: GET /http://example.com/path
        2. Transparent proxy: GET /path with Host: example.com header

        Args:
            request: Incoming aiohttp request

        Returns:
            Full URL (http://... or https://...)
        """
        path = request.path.lstrip("/")

        # Explicit proxy format: path contains full URL
        if path.startswith(("http://", "https://")):
            return path

        # Transparent proxy format: reconstruct from Host header
        host = request.headers.get("Host", "")
        if host:
            # Default to http for transparent mode (Playwright uses http)
            scheme = "http"
            return f"{scheme}://{host}{request.path}"

        # Fallback: treat path as URL (for compatibility)
        return path if path.startswith("http") else f"http://{path}"

    async def handle_connect(
        self, request: aiohttp.web.Request
    ) -> aiohttp.web.Response:
        """Handle HTTPS CONNECT request (transparent proxy mode).

        For transparent proxy, browsers send CONNECT for HTTPS tunneling.
        Since FlareSolverr can't act as CONNECT tunnel, we block these
        for blocked domains and return 501 for others (MVP approach).

        Args:
            request: Incoming CONNECT request

        Returns:
            502 Bad Gateway for blocked domains, 501 Not Implemented for others
        """
        # CONNECT target is in path: "example.com:443"
        target = request.path.lstrip("/")
        host = target.split(":")[0]

        if self.should_use_flaresolverr(host):
            # Blocked domain attempting HTTPS - not supported
            logger.warning(
                "connect_blocked",
                host=host,
                reason="HTTPS to blocked domains not supported by FlareSolverr",
            )
            return aiohttp.web.Response(
                text=f"502 Bad Gateway: HTTPS not supported for {host}. Use HTTP URL instead.",
                status=502,
            )

        # Non-blocked domain HTTPS tunneling not implemented (MVP)
        logger.info("connect_not_implemented", host=host)
        return aiohttp.web.Response(
            text="501 Not Implemented: CONNECT tunneling not supported in MVP",
            status=501,
        )

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
            # DEBUG: Log raw request details
            logger.debug(
                "proxy_request_received",
                method=request.method,
                path=request.path,
                host_header=request.headers.get("Host", ""),
                headers=dict(request.headers),
            )

            # Handle CONNECT method (HTTPS tunneling)
            if request.method == "CONNECT":
                return await self.handle_connect(request)

            # Extract target URL (supports both proxy formats)
            url = self._extract_url(request)
            logger.debug("proxy_url_extracted", url=url, path=request.path)

            # Parse domain and scheme from URL
            parsed = urlparse(url)
            domain = parsed.netloc
            scheme = parsed.scheme

            # Block HTTPS to blocked domains (FlareSolverr limitation)
            if scheme == "https" and self.should_use_flaresolverr(domain):
                logger.warning(
                    "https_blocked",
                    url=url,
                    reason="FlareSolverr cannot proxy HTTPS",
                )
                return aiohttp.web.Response(
                    text=f"502 Bad Gateway: HTTPS not supported for {domain}. Use HTTP URL instead.",
                    status=502,
                )

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
                async with httpx.AsyncClient(timeout=30.0) as client:
                    logger.debug("direct_request_start", url=url)
                    http_response = await client.get(url)
                    logger.debug(
                        "direct_request_complete",
                        url=url,
                        status=http_response.status_code,
                        content_length=len(http_response.content),
                        headers=dict(http_response.headers),
                    )

                    # Filter out problematic headers
                    response_headers = {}
                    skip_headers = {
                        "content-encoding",  # Let aiohttp handle encoding
                        "content-length",  # aiohttp will recalculate
                        "transfer-encoding",  # aiohttp will set this
                        "connection",  # Proxy will manage connections
                    }
                    for key, value in http_response.headers.items():
                        if key.lower() not in skip_headers:
                            response_headers[key] = value

                    logger.debug("direct_response_sending", headers=response_headers)
                    return aiohttp.web.Response(
                        body=http_response.content,
                        status=http_response.status_code,
                        headers=response_headers,
                    )

        except Exception as e:
            logger.error("proxy_error", error=str(e), url=request.path)
            return aiohttp.web.Response(text=str(e), status=500)

    async def health_check(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
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
