from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

app = FastAPI(
    title="TechFacts Pipeline API",
    description="Knowledge extraction and report generation pipeline",
    version="0.1.0",
)


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint - returns service status."""
    return JSONResponse(
        content={
            "status": "ok",
            "service": "techfacts-pipeline",
            "timestamp": datetime.utcnow().isoformat(),
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
