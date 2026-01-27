"""Tests for metrics collector."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from src.orm_models import Entity, Extraction, Job, Project, Source
from src.services.metrics.collector import MetricsCollector, SystemMetrics


@pytest.fixture
def sample_data(db: Session) -> None:
    """Create sample data for metrics testing."""
    # Create a project first
    project = Project(
        id=uuid4(),
        name="Test Project",
        description="Test",
        extraction_schema={"type": "test"},
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    # Create jobs
    jobs = [
        Job(
            id=uuid4(),
            type="scrape",
            status="completed",
            payload={"test": "data"},
            created_at=datetime.now(UTC),
        ),
        Job(
            id=uuid4(),
            type="scrape",
            status="queued",
            payload={"test": "data"},
            created_at=datetime.now(UTC),
        ),
        Job(
            id=uuid4(),
            type="extract",
            status="completed",
            payload={"test": "data"},
            created_at=datetime.now(UTC),
        ),
        Job(
            id=uuid4(),
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
            uri="https://example.com",
            source_group="test-group",
            status="completed",
        ),
        Source(
            id=uuid4(),
            project_id=project.id,
            uri="https://example2.com",
            source_group="test-group",
            status="pending",
        ),
        Source(
            id=uuid4(),
            project_id=project.id,
            uri="https://example3.com",
            source_group="test-group",
            status="pending",
        ),
    ]
    for source in sources:
        db.add(source)

    db.commit()

    # Create extractions
    for source in sources[:2]:
        db.refresh(source)
        extraction = Extraction(
            id=uuid4(),
            project_id=project.id,
            source_id=source.id,
            data={"test": "data"},
            extraction_type="test",
            source_group="test-group",
        )
        db.add(extraction)

    db.commit()

    # Create entities
    for i in range(3):
        entity = Entity(
            id=uuid4(),
            project_id=project.id,
            source_group="test-group",
            entity_type="PERSON",
            value=f"Person {i}",
            normalized_value=f"person{i}",
        )
        db.add(entity)

    db.commit()


class TestMetricsCollector:
    """Tests for MetricsCollector class."""

    def test_collect_returns_metrics(self, db: Session, sample_data: None) -> None:
        """Test that collect() returns SystemMetrics."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert isinstance(metrics, SystemMetrics)
        assert metrics.jobs_total == 4
        assert metrics.sources_total == 3
        assert metrics.extractions_total == 2
        assert metrics.entities_total == 3

    def test_count_jobs_by_type(self, db: Session, sample_data: None) -> None:
        """Test counting jobs grouped by type."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.jobs_by_type["scrape"] == 2
        assert metrics.jobs_by_type["extract"] == 2

    def test_count_jobs_by_status(self, db: Session, sample_data: None) -> None:
        """Test counting jobs grouped by status."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.jobs_by_status["completed"] == 2
        assert metrics.jobs_by_status["queued"] == 1
        assert metrics.jobs_by_status["failed"] == 1

    def test_count_sources_by_status(self, db: Session, sample_data: None) -> None:
        """Test counting sources grouped by status."""
        collector = MetricsCollector(db)
        metrics = collector.collect()

        assert metrics.sources_by_status["completed"] == 1
        assert metrics.sources_by_status["pending"] == 2
