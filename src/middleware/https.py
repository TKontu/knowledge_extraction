"""HTTPS enforcement middleware."""

from fastapi import Request
from fastapi.responses import RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from config import settings


class HTTPSRedirectMiddleware(BaseHTTPMiddleware):
    """Redirect HTTP requests to HTTPS."""

    async def dispatch(self, request: Request, call_next):
        """Check for HTTPS and redirect if needed."""
        if not settings.enforce_https:
            return await call_next(request)

        # Check if request is already HTTPS
        # X-Forwarded-Proto is set by reverse proxies
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)

        if proto != "https":
            # Build HTTPS URL
            host = settings.https_redirect_host or request.url.netloc
            https_url = request.url.replace(scheme="https", netloc=host)

            return RedirectResponse(
                url=str(https_url),
                status_code=301,
            )

        return await call_next(request)
