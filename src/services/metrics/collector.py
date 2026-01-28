"""Metrics collector for system monitoring."""

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from orm_models import Entity, Extraction, Job, Source


@dataclass
class JobDurationStats:
    """Job duration statistics by type."""

    avg_seconds: float
    min_seconds: float
    max_seconds: float
    count: int


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

    # Job duration metrics (completed jobs only)
    job_duration_by_type: dict[str, JobDurationStats] = field(default_factory=dict)

    # Embedding recovery metrics
    orphaned_extractions_total: int = 0


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
            job_duration_by_type=self._job_duration_by_type(),
            orphaned_extractions_total=self._count_orphaned_extractions(),
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

    def _job_duration_by_type(self) -> dict[str, JobDurationStats]:
        """Calculate job duration statistics by type for completed jobs.

        Only includes jobs with both started_at and completed_at set.

        Returns:
            Dictionary mapping job type to duration statistics.
        """
        from sqlalchemy import extract

        # Determine database dialect
        try:
            dialect_name = self._db.bind.dialect.name
        except AttributeError:
            dialect_name = "sqlite"

        # Build duration expression based on dialect
        if dialect_name == "postgresql":
            # PostgreSQL: Use extract epoch
            duration_expr = (
                extract("epoch", Job.completed_at) - extract("epoch", Job.started_at)
            )
        else:
            # SQLite: Use julianday
            duration_expr = (
                (func.julianday(Job.completed_at) - func.julianday(Job.started_at))
                * 86400
            )

        # Query for completed jobs with valid timestamps
        result = self._db.execute(
            select(
                Job.type,
                func.count(Job.id),
                func.avg(duration_expr),
                func.min(duration_expr),
                func.max(duration_expr),
            )
            .where(
                Job.status == "completed",
                Job.started_at.isnot(None),
                Job.completed_at.isnot(None),
            )
            .group_by(Job.type)
        )

        stats = {}
        for row in result.all():
            job_type, count, avg_sec, min_sec, max_sec = row
            if count and count > 0:
                stats[job_type] = JobDurationStats(
                    avg_seconds=float(avg_sec) if avg_sec else 0.0,
                    min_seconds=float(min_sec) if min_sec else 0.0,
                    max_seconds=float(max_sec) if max_sec else 0.0,
                    count=count,
                )
        return stats

    def _count_orphaned_extractions(self) -> int:
        """Count extractions without embeddings (embedding_id IS NULL).

        Returns:
            Number of orphaned extractions.
        """
        result = self._db.execute(
            select(func.count(Extraction.id)).where(Extraction.embedding_id.is_(None))
        )
        return result.scalar() or 0
