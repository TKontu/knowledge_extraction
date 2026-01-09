from datetime import datetime, UTC

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from config import settings
from middleware.auth import APIKeyMiddleware

app = FastAPI(
    title="TechFacts Pipeline API",
    description="Knowledge extraction and report generation pipeline",
    version="0.1.0",
)

# Add authentication middleware
app.add_middleware(APIKeyMiddleware)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint - returns service status."""
    return JSONResponse(
        content={
            "status": "ok",
            "service": "techfacts-pipeline",
            "timestamp": datetime.now(UTC).isoformat(),
            "log_level": settings.log_level,
        }
    )


@app.get("/")
async def root() -> dict[str, str]:
    """Root endpoint - provides API information."""
    return {
        "service": "TechFacts Pipeline API",
        "version": "0.1.0",
        "docs": "/docs",
        "health": "/health",
    }
