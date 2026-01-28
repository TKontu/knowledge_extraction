import asyncio
import signal
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.v1.crawl import router as crawl_router
from api.v1.dlq import router as dlq_router
from api.v1.entities import router as entities_router
from api.v1.export import router as export_router
from api.v1.extraction import router as extraction_router
from api.v1.jobs import router as jobs_router
from api.v1.metrics import router as metrics_router
from api.v1.projects import router as projects_router
from api.v1.reports import router as reports_router
from api.v1.scrape import router as scrape_router
from api.v1.search import router as search_router
from api.v1.sources import router as sources_router
from config import settings
from database import check_database_connection
from logging_config import configure_logging
from middleware.auth import APIKeyMiddleware
from middleware.https import HTTPSRedirectMiddleware
from middleware.rate_limit import RateLimitMiddleware
from middleware.request_id import RequestIDMiddleware
from middleware.request_logging import RequestLoggingMiddleware
from middleware.security_headers import SecurityHeadersMiddleware
from qdrant_connection import check_qdrant_connection, qdrant_client
from redis_client import check_redis_connection
from services.projects.template_loader import TemplateLoadError, load_templates
from services.scraper.scheduler import start_scheduler, stop_scheduler
from services.storage.qdrant.repository import QdrantRepository
from shutdown import get_shutdown_manager, shutdown_manager

# Configure logging before creating the app
configure_logging()

logger = structlog.get_logger(__name__)


async def handle_signal(sig: signal.Signals) -> None:
    """Handle shutdown signals."""
    logger.info("signal_received", signal=sig.name)
    await shutdown_manager.initiate_shutdown()


def check_security_config() -> None:
    """Log security configuration status."""
    issues = []

    # Check API key strength
    if len(settings.api_key) < 32:
        issues.append("API key is shorter than recommended (32+ characters)")

    # Check HTTPS enforcement
    if not settings.enforce_https:
        issues.append("HTTPS enforcement is disabled")

    if issues:
        for issue in issues:
            logger.warning("security_config_issue", issue=issue)
    else:
        logger.info("security_config_valid")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    loop = asyncio.get_event_loop()

    # Register signal handlers (skip in test environment if not supported)
    try:
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(
                sig, lambda s=sig: asyncio.create_task(handle_signal(s))
            )
    except NotImplementedError:
        # Signal handlers not supported (e.g., Windows or test environment)
        logger.debug("signal_handlers_not_supported", reason="platform limitation")

    # Startup: Start the background job scheduler
    import os

    app_version = os.getenv("APP_VERSION", "v1.3.1")
    git_commit = os.getenv("GIT_COMMIT", "unknown")
    logger.info(
        "application_startup",
        service="pipeline",
        version=app_version,
        commit=git_commit,
        environment=os.getenv("ENVIRONMENT", "production"),
    )

    # Load templates from YAML files
    try:
        load_templates()
        logger.info("templates_loaded_successfully")
    except TemplateLoadError as e:
        logger.error(
            "template_load_failed",
            template=e.template_name,
            errors=e.errors,
        )
        raise

    # Check security configuration
    check_security_config()

    # Initialize Qdrant collection with retry logic
    qdrant_repo = QdrantRepository(qdrant_client)
    max_retries = 5
    for attempt in range(max_retries):
        try:
            await qdrant_repo.init_collection()
            logger.info(
                "qdrant_collection_initialized",
                collection="extractions",
                attempt=attempt + 1,
            )
            break
        except Exception as e:
            if attempt < max_retries - 1:
                # Exponential backoff: 1s, 2s, 4s, 8s
                wait_time = 2**attempt
                logger.warning(
                    "qdrant_init_retry",
                    attempt=attempt + 1,
                    max_retries=max_retries,
                    wait_seconds=wait_time,
                    error=str(e),
                    error_type=type(e).__name__,
                )
                await asyncio.sleep(wait_time)
            else:
                # Final attempt failed - log warning but don't crash
                logger.warning(
                    "qdrant_init_failed",
                    max_retries=max_retries,
                    error=str(e),
                    error_type=type(e).__name__,
                    detail="Collection will be created on first use if needed",
                )

    await start_scheduler()

    # Register cleanup callbacks
    shutdown_manager.register_cleanup(stop_scheduler)

    yield

    # Shutdown: Stop the background job scheduler
    logger.info("application_shutdown")
    await shutdown_manager.initiate_shutdown()


app = FastAPI(
    title="Scristill Pipeline API",
    description="Knowledge extraction and report generation pipeline",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware (must be added before auth middleware for preflight requests)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

# Add HTTPS redirect middleware (checks setting internally)
app.add_middleware(HTTPSRedirectMiddleware)

# Add security headers middleware
app.add_middleware(SecurityHeadersMiddleware)

# Add authentication middleware
app.add_middleware(APIKeyMiddleware)

# Add rate limiting middleware
app.add_middleware(RateLimitMiddleware)

# Add logging middleware (order matters - request logging before request ID)
app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(RequestIDMiddleware)

# Include API routers
app.include_router(scrape_router)
app.include_router(crawl_router)
app.include_router(extraction_router)
app.include_router(projects_router)
app.include_router(search_router)
app.include_router(entities_router)
app.include_router(reports_router)
app.include_router(jobs_router)
app.include_router(metrics_router)
app.include_router(export_router)
app.include_router(dlq_router)
app.include_router(sources_router)
app.include_router(dlq_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint - returns service status."""
    shutdown = get_shutdown_manager()

    if shutdown.is_shutting_down:
        return JSONResponse(
            status_code=503,
            content={
                "status": "shutting_down",
                "service": "scristill-pipeline",
                "timestamp": datetime.now(UTC).isoformat(),
            },
        )

    # Check database connectivity
    db_connected = False
    try:
        db_connected = check_database_connection()
    except Exception:
        db_connected = False

    if not db_connected:
        logger.warning("health_check_failed", component="database")

    # Check Redis connectivity
    redis_connected = False
    try:
        redis_connected = check_redis_connection()
    except Exception:
        redis_connected = False

    if not redis_connected:
        logger.warning("health_check_failed", component="redis")

    # Check Qdrant connectivity
    qdrant_connected = False
    try:
        qdrant_connected = check_qdrant_connection()
    except Exception:
        qdrant_connected = False

    if not qdrant_connected:
        logger.warning("health_check_failed", component="qdrant")

    import os

    return JSONResponse(
        content={
            "status": "ok",
            "service": "scristill-pipeline",
            "version": os.getenv("APP_VERSION", "v1.3.1"),
            "commit": os.getenv("GIT_COMMIT", "unknown"),
            "timestamp": datetime.now(UTC).isoformat(),
            "log_level": settings.log_level,
            "database": {
                "connected": db_connected,
            },
            "redis": {
                "connected": redis_connected,
            },
            "qdrant": {
                "connected": qdrant_connected,
            },
        }
    )


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint - provides API information."""
    import os

    return {
        "service": "Scristill Pipeline API",
        "version": os.getenv("APP_VERSION", "v1.3.1"),
        "commit": os.getenv("GIT_COMMIT", "unknown"),
        "docs": "/docs",
        "health": "/health",
    }
