"""Metrics collector for system monitoring."""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orm_models import Entity, Extraction, Job, Source


@dataclass
class SystemMetrics:
    """System metrics for Prometheus."""

    jobs_total: int
    jobs_by_type: dict[str, int]
    jobs_by_status: dict[str, int]
    sources_total: int
    sources_by_status: dict[str, int]
    extractions_total: int
    entities_total: int

    # Quality metrics
    extractions_by_type: dict[str, int] = field(default_factory=dict)
    avg_confidence_by_type: dict[str, float] = field(default_factory=dict)
    entities_by_type: dict[str, int] = field(default_factory=dict)


class MetricsCollector:
    """Collects system metrics from database."""

    def __init__(self, db: Session):
        self._db = db

    def collect(self) -> SystemMetrics:
        """Collect all system metrics."""
        return SystemMetrics(
            jobs_total=self._count_total(Job),
            jobs_by_type=self._count_jobs_by_type(),
            jobs_by_status=self._count_jobs_by_status(),
            sources_total=self._count_total(Source),
            sources_by_status=self._count_sources_by_status(),
            extractions_total=self._count_total(Extraction),
            entities_total=self._count_total(Entity),
            extractions_by_type=self._count_extractions_by_type(),
            avg_confidence_by_type=self._avg_confidence_by_type(),
            entities_by_type=self._count_entities_by_type(),
        )

    def _count_total(self, model) -> int:
        """Count total rows for a model."""
        result = self._db.execute(select(func.count(model.id)))
        return result.scalar() or 0

    def _count_jobs_by_type(self) -> dict[str, int]:
        """Count jobs grouped by type."""
        result = self._db.execute(
            select(Job.type, func.count(Job.id)).group_by(Job.type)
        )
        return {row[0]: row[1] for row in result.all()}

    def _count_jobs_by_status(self) -> dict[str, int]:
        """Count jobs grouped by status."""
        result = self._db.execute(
            select(Job.status, func.count(Job.id)).group_by(Job.status)
        )
        return {row[0]: row[1] for row in result.all()}

    def _count_sources_by_status(self) -> dict[str, int]:
        """Count sources grouped by status."""
        result = self._db.execute(
            select(Source.status, func.count(Source.id)).group_by(Source.status)
        )
        return {row[0]: row[1] for row in result.all()}

    def _count_extractions_by_type(self) -> dict[str, int]:
        """Count extractions grouped by type."""
        result = self._db.execute(
            select(Extraction.extraction_type, func.count(Extraction.id)).group_by(
                Extraction.extraction_type
            )
        )
        return {row[0]: row[1] for row in result.all()}

    def _avg_confidence_by_type(self) -> dict[str, float]:
        """Calculate average confidence grouped by extraction type."""
        result = self._db.execute(
            select(
                Extraction.extraction_type, func.avg(Extraction.confidence)
            ).group_by(Extraction.extraction_type)
        )
        return {row[0]: float(row[1]) if row[1] else 0.0 for row in result.all()}

    def _count_entities_by_type(self) -> dict[str, int]:
        """Count entities grouped by type."""
        result = self._db.execute(
            select(Entity.entity_type, func.count(Entity.id)).group_by(
                Entity.entity_type
            )
        )
        return {row[0]: row[1] for row in result.all()}
