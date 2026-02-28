"""Tests for ExtractionRepository batch operations."""

import pytest
from uuid import uuid4

from orm_models import Extraction
from services.storage.repositories.extraction import ExtractionRepository


@pytest.fixture
def extraction_repo(db):
    """Create an ExtractionRepository instance."""
    return ExtractionRepository(db)


@pytest.fixture
def test_project(db):
    """Create a test project for FK references."""
    from orm_models import Project

    project = Project(
        name=f"test_extraction_batch_{uuid4().hex[:8]}",
        extraction_schema={"name": "test", "fields": []},
    )
    db.add(project)
    db.flush()
    return project


@pytest.fixture
def test_source(db, test_project):
    """Create a test source for FK references."""
    from orm_models import Source

    source = Source(
        project_id=test_project.id,
        source_type="web",
        uri=f"https://example.com/{uuid4().hex[:8]}",
        source_group="test_group",
        status="completed",
    )
    db.add(source)
    db.flush()
    return source


@pytest.fixture
def test_extraction(db, test_project, test_source):
    """Create a test extraction."""
    extraction = Extraction(
        project_id=test_project.id,
        source_id=test_source.id,
        data={"test": "data"},
        extraction_type="test_type",
        source_group="test_group",
    )
    db.add(extraction)
    db.flush()
    db.refresh(extraction)
    return extraction


@pytest.fixture
def test_extractions(db, test_project, test_source):
    """Create multiple test extractions."""
    extractions = []
    for i in range(3):
        extraction = Extraction(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"test": f"data_{i}"},
            extraction_type="test_type",
            source_group="test_group",
        )
        db.add(extraction)
        extractions.append(extraction)
    db.flush()
    for ext in extractions:
        db.refresh(ext)
    return extractions


class TestUpdateEmbeddingIdsBatch:
    """Tests for update_embedding_ids_batch method."""

    def test_updates_single_extraction(self, extraction_repo, test_extraction, db):
        """Single extraction gets embedding_id set."""
        # Ensure embedding_id is initially None
        assert test_extraction.embedding_id is None

        # Update embedding_id (sync method, no await)
        updated_count = extraction_repo.update_embedding_ids_batch(
            [test_extraction.id]
        )

        # Verify update
        assert updated_count == 1

        # Refresh and check
        db.refresh(test_extraction)
        assert test_extraction.embedding_id == str(test_extraction.id)

    def test_updates_multiple_extractions(
        self, extraction_repo, test_extractions, db
    ):
        """Multiple extractions updated in single query."""
        extraction_ids = [ext.id for ext in test_extractions]

        # Update all
        updated_count = extraction_repo.update_embedding_ids_batch(extraction_ids)

        # Verify count
        assert updated_count == 3

        # Verify each extraction
        for extraction in test_extractions:
            db.refresh(extraction)
            assert extraction.embedding_id == str(extraction.id)

    def test_empty_list_returns_zero(self, extraction_repo):
        """Empty list returns 0 without error."""
        updated_count = extraction_repo.update_embedding_ids_batch([])
        assert updated_count == 0

    def test_nonexistent_ids_ignored(self, extraction_repo):
        """IDs not in database are safely ignored."""
        # Use non-existent UUIDs
        fake_ids = [uuid4(), uuid4()]
        updated_count = extraction_repo.update_embedding_ids_batch(fake_ids)

        # Should return 0 (no rows updated)
        assert updated_count == 0
