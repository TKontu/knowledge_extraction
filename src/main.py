from contextlib import asynccontextmanager
from datetime import datetime, UTC

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from api.v1.extraction import router as extraction_router
from api.v1.scrape import router as scrape_router
from api.v1.projects import router as projects_router
from api.v1.search import router as search_router
from api.v1.entities import router as entities_router
from api.v1.jobs import router as jobs_router
from api.v1.metrics import router as metrics_router
from config import settings
from database import check_database_connection
from middleware.auth import APIKeyMiddleware
from qdrant_connection import check_qdrant_connection
from redis_client import check_redis_connection
from services.scraper.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manage application lifespan events."""
    # Startup: Start the background job scheduler
    await start_scheduler()
    yield
    # Shutdown: Stop the background job scheduler
    await stop_scheduler()


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

# Add authentication middleware
app.add_middleware(APIKeyMiddleware)

# Include API routers
app.include_router(scrape_router)
app.include_router(extraction_router)
app.include_router(projects_router)
app.include_router(search_router)
app.include_router(entities_router)
app.include_router(jobs_router)
app.include_router(metrics_router)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint - returns service status."""
    # Check database connectivity
    db_connected = False
    try:
        db_connected = check_database_connection()
    except Exception:
        db_connected = False

    # Check Redis connectivity
    redis_connected = False
    try:
        redis_connected = check_redis_connection()
    except Exception:
        redis_connected = False

    # Check Qdrant connectivity
    qdrant_connected = False
    try:
        qdrant_connected = check_qdrant_connection()
    except Exception:
        qdrant_connected = False

    return JSONResponse(
        content={
            "status": "ok",
            "service": "scristill-pipeline",
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
    return {
        "service": "Scristill Pipeline API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }
