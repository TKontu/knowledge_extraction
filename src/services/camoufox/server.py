"""FastAPI server for Camoufox browser service.

This service provides a Firecrawl-compatible /scrape endpoint
using Camoufox for anti-bot protected scraping.
"""

import logging
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse

import structlog
import uvicorn
from fastapi import FastAPI, status
from fastapi.responses import JSONResponse

from src.services.camoufox.config import settings
from src.services.camoufox.models import (
    HealthResponse,
    ScrapeErrorResponse,
    ScrapeRequest,
    ScrapeSuccessResponse,
)
from src.services.camoufox.scraper import scraper


ALLOWED_URL_SCHEMES = {"http", "https"}


def is_valid_url(url: str) -> bool:
    """Validate URL has allowed scheme and netloc.

    Only allows http and https schemes to prevent SSRF attacks
    via file://, ftp://, gopher://, etc.

    Args:
        url: URL string to validate.

    Returns:
        True if URL is valid with allowed scheme, False otherwise.
    """
    try:
        result = urlparse(url)
        return result.scheme in ALLOWED_URL_SCHEMES and bool(result.netloc)
    except Exception:
        return False


def configure_logging() -> None:
    """Configure structlog for the service."""
    is_json = settings.log_format.lower() == "json"
    log_level = getattr(logging, settings.log_level.upper())

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_json:
        # JSON format: use WriteLoggerFactory to avoid duplication
        structlog.configure(
            processors=shared_processors
            + [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
            cache_logger_on_first_use=True,
        )
        # Disable standard library logging to avoid duplication
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=logging.CRITICAL + 1,  # Effectively disable
        )
    else:
        # Console format: use PrintLoggerFactory for colored output
        structlog.configure(
            processors=shared_processors
            + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=log_level,
        )

    logging.root.setLevel(log_level)


configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifecycle - start/stop browser."""
    logger.info(
        "camoufox_service_starting",
        port=settings.port,
        max_concurrent_pages=settings.max_concurrent_pages,
    )

    await scraper.start()

    yield

    logger.info("camoufox_service_stopping")
    await scraper.stop()
    logger.info("camoufox_service_stopped")


app = FastAPI(
    title="Camoufox Browser Service",
    description="Firecrawl-compatible browser service using Camoufox for anti-bot scraping",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        maxConcurrentPages=scraper.max_concurrent_pages,
        activePages=scraper.active_pages,
    )


@app.post(
    "/scrape",
    response_model=ScrapeSuccessResponse,
    responses={
        200: {"model": ScrapeSuccessResponse},
        400: {"model": ScrapeErrorResponse},
        500: {"model": ScrapeErrorResponse},
    },
)
async def scrape_url(request: ScrapeRequest) -> JSONResponse:
    """Scrape a URL and return rendered HTML content.

    This endpoint matches the Firecrawl Playwright service API exactly.
    Returns the DOM after JavaScript execution, not raw HTML source.

    Args:
        request: Scrape request with URL and options.

    Returns:
        JSON response with content and status, or error message.
    """
    # Validate URL (matching Firecrawl's api.ts:234-240)
    if not request.url:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "URL is required"},
        )
    if not is_valid_url(request.url):
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"error": "Invalid URL"},
        )

    log = logger.bind(url=request.url)
    log.info(
        "scrape_request_received",
        headers=request.headers,
        timeout=request.timeout,
        wait_after_load=request.wait_after_load,
        check_selector=request.check_selector,
    )

    result = await scraper.scrape(request)

    if "error" in result:
        log.error("scrape_request_failed", error=result["error"])
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": result["error"]},
        )

    log.info(
        "scrape_request_completed",
        status=result.get("pageStatusCode"),
        content_length=len(result.get("content", "")),
    )

    # Build response - only include optional fields if they have values
    # Firecrawl's Zod schema expects pageError and contentType to be
    # either strings or undefined (not null)
    response_content: dict = {
        "content": result["content"],
        "pageStatusCode": result["pageStatusCode"],
    }
    if result.get("pageError"):
        response_content["pageError"] = result["pageError"]
    if result.get("contentType"):
        response_content["contentType"] = result["contentType"]
    if result.get("discoveredUrls"):
        response_content["discoveredUrls"] = result["discoveredUrls"]

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content=response_content,
    )


def main() -> None:
    """Run the Camoufox service."""
    # Disable uvicorn access logs when using JSON format to avoid duplication
    log_config = None if settings.log_format.lower() == "json" else None

    uvicorn.run(
        "src.services.camoufox.server:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        access_log=False,  # Disable access logs to avoid duplication
        log_config=log_config,
    )


if __name__ == "__main__":
    main()
