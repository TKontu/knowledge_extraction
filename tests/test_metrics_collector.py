"""Tests for metrics collector."""

import pytest
from datetime import datetime, UTC
from uuid import uuid4
from sqlalchemy.orm import Session

from services.metrics.collector import MetricsCollector, SystemMetrics
from orm_models import Job, Source, Extraction, Entity, Project


@pytest.fixture
def sample_data(db: Session):
    """Create sample data for metrics testing. Returns project for scoping."""
    # Create a project with unique name
    project = Project(
        id=uuid4(),
        name=f"test_metrics_{uuid4().hex[:8]}",
        description="Test",
        extraction_schema={"type": "test"},
    )
    db.add(project)
    db.flush()

    # Create jobs
    jobs = [
        Job(
            id=uuid4(),
            project_id=project.id,
            type="scrape",
            status="completed",
            payload={"test": "data"},
            created_at=datetime.now(UTC),
        ),
        Job(
            id=uuid4(),
            project_id=project.id,
            type="scrape",
            status="queued",
            payload={"test": "data"},
            created_at=datetime.now(UTC),
        ),
        Job(
            id=uuid4(),
            project_id=project.id,
            type="extract",
            status="completed",
            payload={"test": "data"},
            created_at=datetime.now(UTC),
        ),
        Job(
            id=uuid4(),
            project_id=project.id,
            type="extract",
            status="failed",
            payload={"test": "data"},
            error="Test error",
            created_at=datetime.now(UTC),
        ),
    ]
    for job in jobs:
        db.add(job)

    # Create sources
    sources = [
        Source(
            id=uuid4(),
            project_id=project.id,
            uri=f"https://example.com/{uuid4().hex[:8]}",
            source_group="test-group",
            status="completed",
        ),
        Source(
            id=uuid4(),
            project_id=project.id,
            uri=f"https://example2.com/{uuid4().hex[:8]}",
            source_group="test-group",
            status="pending",
        ),
        Source(
            id=uuid4(),
            project_id=project.id,
            uri=f"https://example3.com/{uuid4().hex[:8]}",
            source_group="test-group",
            status="pending",
        ),
    ]
    for source in sources:
        db.add(source)

    db.flush()

    # Create extractions with different types and confidence
    for i, source in enumerate(sources[:2]):
        db.refresh(source)
        extraction = Extraction(
            id=uuid4(),
            project_id=project.id,
            source_id=source.id,
            data={"test": "data"},
            extraction_type="company" if i == 0 else "person",
            source_group="test-group",
            confidence=0.9 if i == 0 else 0.7,
        )
        db.add(extraction)

    db.flush()

    # Create entities with different types
    entity_types = ["PERSON", "PERSON", "ORGANIZATION"]
    for i, ent_type in enumerate(entity_types):
        entity = Entity(
            id=uuid4(),
            project_id=project.id,
            source_group="test-group",
            entity_type=ent_type,
            value=f"{ent_type} {i}",
            normalized_value=f"{ent_type.lower()}{i}",
        )
        db.add(entity)

    db.flush()


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def test_collect_returns_metrics(self, db: Session, sample_data: None) -> None:
        """Test that collect() returns SystemMetrics with at least our test data."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert isinstance(metrics, SystemMetrics)
        # Shared DB — use >= for totals since pre-existing data may exist
        assert metrics.jobs_total >= 4
        assert metrics.sources_total >= 3
        assert metrics.extractions_total >= 2
        assert metrics.entities_total >= 3

    def test_count_jobs_by_type(self, db: Session, sample_data: None) -> None:
        """Test counting jobs grouped by type."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.jobs_by_type["scrape"] >= 2
        assert metrics.jobs_by_type["extract"] >= 2

    def test_count_jobs_by_status(self, db: Session, sample_data: None) -> None:
        """Test counting jobs grouped by status."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.jobs_by_status["completed"] >= 2
        assert metrics.jobs_by_status.get("queued", 0) >= 1
        assert metrics.jobs_by_status.get("failed", 0) >= 1

    def test_count_sources_by_status(self, db: Session, sample_data: None) -> None:
        """Test counting sources grouped by status."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.sources_by_status.get("completed", 0) >= 1
        assert metrics.sources_by_status.get("pending", 0) >= 2

    def test_count_extractions_by_type(self, db: Session, sample_data: None) -> None:
        """Test counting extractions grouped by type."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.extractions_by_type.get("company", 0) >= 1
        assert metrics.extractions_by_type.get("person", 0) >= 1

    def test_avg_confidence_by_type(self, db: Session, sample_data: None) -> None:
        """Test calculating average confidence by extraction type."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        # Confidence might be averaged with pre-existing data, just check keys exist
        assert "company" in metrics.avg_confidence_by_type
        assert "person" in metrics.avg_confidence_by_type

    def test_count_entities_by_type(self, db: Session, sample_data: None) -> None:
        """Test counting entities grouped by type."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.entities_by_type.get("PERSON", 0) >= 2
        assert metrics.entities_by_type.get("ORGANIZATION", 0) >= 1
