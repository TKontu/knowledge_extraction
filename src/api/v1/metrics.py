"""Metrics API endpoint."""

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from database import get_db
from services.metrics.collector import MetricsCollector
from services.metrics.prometheus import format_prometheus

router = APIRouter(tags=["metrics"])


@router.get("/metrics", response_class=PlainTextResponse)
def get_metrics(db: Session = Depends(get_db)) -> str:
    """Get Prometheus-format metrics.

    Returns system metrics in Prometheus text exposition format.
    This endpoint is unauthenticated to allow Prometheus scraping.

    Returns:
        Metrics in Prometheus text exposition format.
    """
    collector = MetricsCollector(db)
    metrics = collector.collect()
    return format_prometheus(metrics)
