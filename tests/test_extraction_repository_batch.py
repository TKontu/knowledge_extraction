"""Tests for ExtractionRepository batch operations."""

import pytest
from uuid import uuid4

from src.orm_models import Extraction
from src.services.storage.repositories.extraction import ExtractionRepository


@pytest.fixture
def extraction_repo(db):
    """Create an ExtractionRepository instance."""
    return ExtractionRepository(db)


@pytest.fixture
def test_extraction(db):
    """Create a test extraction."""
    extraction = Extraction(
        project_id=uuid4(),
        source_id=uuid4(),
        data={"test": "data"},
        extraction_type="test_type",
        source_group="test_group",
    )
    db.add(extraction)
    db.commit()
    db.refresh(extraction)
    return extraction


@pytest.fixture
def test_extractions(db):
    """Create multiple test extractions."""
    project_id = uuid4()
    source_id = uuid4()
    extractions = []
    for i in range(3):
        extraction = Extraction(
            project_id=project_id,
            source_id=source_id,
            data={"test": f"data_{i}"},
            extraction_type="test_type",
            source_group="test_group",
        )
        db.add(extraction)
        extractions.append(extraction)
    db.commit()
    for ext in extractions:
        db.refresh(ext)
    return extractions


class TestUpdateEmbeddingIdsBatch:
    """Tests for update_embedding_ids_batch method."""

    @pytest.mark.asyncio
    async def test_updates_single_extraction(self, extraction_repo, test_extraction, db):
        """Single extraction gets embedding_id set."""
        # Ensure embedding_id is initially None
        assert test_extraction.embedding_id is None

        # Update embedding_id
        updated_count = await extraction_repo.update_embedding_ids_batch(
            [test_extraction.id]
        )

        # Verify update
        assert updated_count == 1

        # Refresh and check
        db.refresh(test_extraction)
        assert test_extraction.embedding_id == str(test_extraction.id)

    @pytest.mark.asyncio
    async def test_updates_multiple_extractions(
        self, extraction_repo, test_extractions, db
    ):
        """Multiple extractions updated in single query."""
        extraction_ids = [ext.id for ext in test_extractions]

        # Update all
        updated_count = await extraction_repo.update_embedding_ids_batch(extraction_ids)

        # Verify count
        assert updated_count == 3

        # Verify each extraction
        for extraction in test_extractions:
            db.refresh(extraction)
            assert extraction.embedding_id == str(extraction.id)

    @pytest.mark.asyncio
    async def test_empty_list_returns_zero(self, extraction_repo):
        """Empty list returns 0 without error."""
        updated_count = await extraction_repo.update_embedding_ids_batch([])
        assert updated_count == 0

    @pytest.mark.asyncio
    async def test_nonexistent_ids_ignored(self, extraction_repo):
        """IDs not in database are safely ignored."""
        # Use non-existent UUIDs
        fake_ids = [uuid4(), uuid4()]
        updated_count = await extraction_repo.update_embedding_ids_batch(fake_ids)

        # Should return 0 (no rows updated)
        assert updated_count == 0
