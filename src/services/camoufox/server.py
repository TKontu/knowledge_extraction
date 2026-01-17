"""FastAPI server for Camoufox browser service.

This service provides a Firecrawl-compatible /scrape endpoint
using Camoufox for anti-bot protected scraping.
"""

import logging
import sys
from contextlib import asynccontextmanager

import structlog
import uvicorn
from fastapi import FastAPI, HTTPException, status
from fastapi.responses import JSONResponse

from src.services.camoufox.config import settings
from src.services.camoufox.models import (
    HealthResponse,
    ScrapeErrorResponse,
    ScrapeRequest,
    ScrapeSuccessResponse,
)
from src.services.camoufox.scraper import scraper


def configure_logging() -> None:
    """Configure structlog for the service."""
    is_json = settings.log_format.lower() == "json"

    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_json:
        structlog.configure(
            processors=shared_processors
            + [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, settings.log_level.upper())
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        structlog.configure(
            processors=shared_processors
            + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(
                getattr(logging, settings.log_level.upper())
            ),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )

    log_level = getattr(logging, settings.log_level.upper())
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
    log = logger.bind(url=request.url)
    log.info("scrape_request_received")

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

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "content": result["content"],
            "pageStatusCode": result["pageStatusCode"],
            "pageError": result.get("pageError"),
        },
    )


def main() -> None:
    """Run the Camoufox service."""
    uvicorn.run(
        "src.services.camoufox.server:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
    )


if __name__ == "__main__":
    main()
