"""FlareSolverr API client."""

import time
from dataclasses import dataclass

import httpx
import structlog

logger = structlog.get_logger(__name__)


@dataclass
class FlareSolverrResponse:
    """Response from FlareSolverr API."""

    url: str
    status: int
    cookies: list[dict]
    headers: dict[str, str]
    html: str
    user_agent: str


class FlareSolverrError(Exception):
    """Error from FlareSolverr API."""

    pass


class FlareSolverrClient:
    """Client for FlareSolverr API."""

    def __init__(
        self, base_url: str, max_timeout: int, http_client: httpx.AsyncClient
    ) -> None:
        """Initialize FlareSolverr client."""
        self.base_url = base_url
        self.max_timeout = max_timeout
        self.http_client = http_client

    async def solve_request(
        self, url: str, method: str = "GET"
    ) -> FlareSolverrResponse:
        """Solve request using FlareSolverr.

        Args:
            url: Target URL to solve
            method: HTTP method (default: GET)

        Returns:
            FlareSolverrResponse with solved content

        Raises:
            FlareSolverrError: If request fails or FlareSolverr returns error
        """
        start_time = time.time()
        try:
            response = await self.http_client.post(
                f"{self.base_url}/v1",
                json={"cmd": "request.get", "url": url, "maxTimeout": self.max_timeout},
            )
            response_data = response.json()

            if response_data.get("status") != "ok":
                error_msg = response_data.get("message", "Unknown error")
                raise FlareSolverrError(f"FlareSolverr returned error: {error_msg}")

            solution = response_data["solution"]
            duration = time.time() - start_time

            logger.info(
                "flaresolverr_request",
                url=url,
                status=solution["status"],
                duration=duration,
            )

            return FlareSolverrResponse(
                url=solution["url"],
                status=solution["status"],
                cookies=solution["cookies"],
                headers=solution["headers"],
                html=solution["response"],
                user_agent=solution["userAgent"],
            )

        except httpx.TimeoutException as e:
            raise FlareSolverrError(f"FlareSolverr request timed out: {e}") from e
        except httpx.ConnectError as e:
            raise FlareSolverrError(f"FlareSolverr connection failed: {e}") from e

    async def close(self) -> None:
        """Close HTTP client."""
        await self.http_client.aclose()

    async def __aenter__(self):
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit async context manager."""
        await self.close()
