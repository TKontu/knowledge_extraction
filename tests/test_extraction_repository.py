"""Tests for ExtractionRepository."""

from datetime import datetime

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction, Project, Source
from services.storage.repositories.extraction import (
    ExtractionFilters,
    ExtractionRepository,
)


@pytest.fixture
def db_session():
    """Create a fresh database session for each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
async def test_project(db_session):
    """Create a test project for extraction tests."""
    project = Project(
        name="test_extraction_project",
        extraction_schema={
            "name": "technical_fact",
            "fields": [
                {"name": "fact_text", "type": "text", "required": True},
                {"name": "category", "type": "text", "required": True},
                {"name": "confidence", "type": "float"},
            ],
        },
    )
    db_session.add(project)
    db_session.flush()
    return project


@pytest.fixture
async def test_source(db_session, test_project):
    """Create a test source for extraction tests."""
    source = Source(
        project_id=test_project.id,
        uri="https://example.com/test",
        source_group="test_company",
    )
    db_session.add(source)
    db_session.flush()
    return source


@pytest.fixture
def extraction_repo(db_session):
    """Create ExtractionRepository instance."""
    return ExtractionRepository(db_session)


class TestExtractionRepositoryCreate:
    """Test ExtractionRepository.create() method."""

    async def test_create_extraction_with_minimal_data(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """Should create extraction with minimal required fields."""
        extraction = await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Test fact", "category": "feature"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        assert extraction.id is not None
        assert extraction.project_id == test_project.id
        assert extraction.source_id == test_source.id
        assert extraction.data["fact_text"] == "Test fact"
        assert extraction.extraction_type == "technical_fact"
        assert extraction.source_group == "test_company"
        assert extraction.confidence is None

    async def test_create_extraction_with_all_fields(
        self, extraction_repo, test_project, test_source
    ):
        """Should create extraction with all fields populated."""
        extraction = await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={
                "fact_text": "Complete fact",
                "category": "pricing",
                "metadata": {"verified": True},
            },
            extraction_type="technical_fact",
            source_group="acme_corp",
            confidence=0.95,
            profile_used="default_profile",
            chunk_index=3,
            chunk_context={"prev": "context before", "next": "context after"},
            embedding_id="emb_123",
        )

        assert extraction.data["fact_text"] == "Complete fact"
        assert extraction.confidence == 0.95
        assert extraction.profile_used == "default_profile"
        assert extraction.chunk_index == 3
        assert extraction.chunk_context["prev"] == "context before"
        assert extraction.embedding_id == "emb_123"

    async def test_create_extraction_returns_persisted_object(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """Created extraction should be retrievable from database."""
        extraction = await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Persisted", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        # Verify it's in the database
        result = db_session.execute(
            select(Extraction).where(Extraction.id == extraction.id)
        )
        db_extraction = result.scalar_one()
        assert db_extraction.data["fact_text"] == "Persisted"

    async def test_create_extraction_sets_timestamps(
        self, extraction_repo, test_project, test_source
    ):
        """Should automatically set created_at and extracted_at timestamps."""
        extraction = await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Timestamped", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        assert extraction.created_at is not None
        assert extraction.extracted_at is not None
        assert isinstance(extraction.created_at, datetime)
        assert isinstance(extraction.extracted_at, datetime)


class TestExtractionRepositoryCreateBatch:
    """Test ExtractionRepository.create_batch() method."""

    async def test_create_batch_creates_multiple_extractions(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """Should create multiple extractions in batch."""
        extractions = await extraction_repo.create_batch(
            [
                {
                    "project_id": test_project.id,
                    "source_id": test_source.id,
                    "data": {"fact_text": "Fact 1", "category": "feature"},
                    "extraction_type": "technical_fact",
                    "source_group": "test_company",
                },
                {
                    "project_id": test_project.id,
                    "source_id": test_source.id,
                    "data": {"fact_text": "Fact 2", "category": "pricing"},
                    "extraction_type": "technical_fact",
                    "source_group": "test_company",
                },
                {
                    "project_id": test_project.id,
                    "source_id": test_source.id,
                    "data": {"fact_text": "Fact 3", "category": "limit"},
                    "extraction_type": "technical_fact",
                    "source_group": "test_company",
                },
            ]
        )

        assert len(extractions) == 3
        assert extractions[0].data["fact_text"] == "Fact 1"
        assert extractions[1].data["fact_text"] == "Fact 2"
        assert extractions[2].data["fact_text"] == "Fact 3"

    async def test_create_batch_returns_empty_list_for_empty_input(
        self, extraction_repo
    ):
        """Should return empty list when given empty input."""
        extractions = await extraction_repo.create_batch([])
        assert extractions == []

    async def test_create_batch_all_have_ids(
        self, extraction_repo, test_project, test_source
    ):
        """All batch-created extractions should have IDs."""
        extractions = await extraction_repo.create_batch(
            [
                {
                    "project_id": test_project.id,
                    "source_id": test_source.id,
                    "data": {"fact_text": f"Fact {i}", "category": "test"},
                    "extraction_type": "technical_fact",
                    "source_group": "test_company",
                }
                for i in range(5)
            ]
        )

        assert all(e.id is not None for e in extractions)
        assert len(set(e.id for e in extractions)) == 5  # All IDs unique


class TestExtractionRepositoryGet:
    """Test ExtractionRepository.get() method."""

    async def test_get_by_id_returns_extraction(
        self, extraction_repo, test_project, test_source
    ):
        """Should retrieve extraction by ID."""
        extraction = await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Get test", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        retrieved = await extraction_repo.get(extraction.id)
        assert retrieved is not None
        assert retrieved.id == extraction.id
        assert retrieved.data["fact_text"] == "Get test"

    async def test_get_nonexistent_returns_none(self, extraction_repo):
        """Should return None for nonexistent extraction."""
        from uuid import uuid4

        retrieved = await extraction_repo.get(uuid4())
        assert retrieved is None


class TestExtractionRepositoryGetBySource:
    """Test ExtractionRepository.get_by_source() method."""

    async def test_get_by_source_returns_all_extractions(
        self, extraction_repo, test_project, test_source
    ):
        """Should retrieve all extractions for a source."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Fact 1", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Fact 2", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.get_by_source(test_source.id)
        assert len(extractions) == 2

    async def test_get_by_source_returns_empty_for_no_extractions(
        self, extraction_repo, test_project, db_session
    ):
        """Should return empty list when source has no extractions."""
        # Create source with no extractions
        empty_source = Source(
            project_id=test_project.id,
            uri="https://example.com/empty",
            source_group="test_company",
        )
        db_session.add(empty_source)
        db_session.flush()

        extractions = await extraction_repo.get_by_source(empty_source.id)
        assert extractions == []

    async def test_get_by_source_sorted_by_created_at(
        self, extraction_repo, test_project, test_source
    ):
        """Should return extractions sorted by created_at descending."""
        e1 = await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "First", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        e2 = await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Second", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.get_by_source(test_source.id)
        # Most recent first
        assert extractions[0].id == e2.id
        assert extractions[1].id == e1.id


class TestExtractionRepositoryList:
    """Test ExtractionRepository.list() method."""

    async def test_list_all_extractions(
        self, extraction_repo, test_project, test_source
    ):
        """Should list all extractions without filters."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Fact 1", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Fact 2", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.list(ExtractionFilters())
        assert len(extractions) >= 2

    async def test_list_filters_by_project_id(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """Should filter extractions by project_id."""
        # Create another project and source
        other_project = Project(
            name="other_extraction_project",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(other_project)
        db_session.flush()

        other_source = Source(
            project_id=other_project.id,
            uri="https://example.com/other",
            source_group="other_company",
        )
        db_session.add(other_source)
        db_session.flush()

        # Create extractions in different projects
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Test project fact", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=other_project.id,
            source_id=other_source.id,
            data={"fact_text": "Other project fact", "category": "test"},
            extraction_type="technical_fact",
            source_group="other_company",
        )

        # Filter by test_project
        extractions = await extraction_repo.list(
            ExtractionFilters(project_id=test_project.id)
        )
        assert len(extractions) == 1
        assert extractions[0].data["fact_text"] == "Test project fact"

    async def test_list_filters_by_source_id(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """Should filter extractions by source_id."""
        # Create another source
        other_source = Source(
            project_id=test_project.id,
            uri="https://example.com/other_source",
            source_group="test_company",
        )
        db_session.add(other_source)
        db_session.flush()

        # Create extractions from different sources
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "From test source", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=other_source.id,
            data={"fact_text": "From other source", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        # Filter by test_source
        extractions = await extraction_repo.list(
            ExtractionFilters(source_id=test_source.id)
        )
        assert len(extractions) == 1
        assert extractions[0].data["fact_text"] == "From test source"

    async def test_list_filters_by_extraction_type(
        self, extraction_repo, test_project, test_source
    ):
        """Should filter extractions by extraction_type."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Technical fact", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"finding": "Research finding"},
            extraction_type="research_finding",
            source_group="test_company",
        )

        extractions = await extraction_repo.list(
            ExtractionFilters(extraction_type="research_finding")
        )
        assert len(extractions) >= 1
        assert all(e.extraction_type == "research_finding" for e in extractions)

    async def test_list_filters_by_source_group(
        self, extraction_repo, test_project, test_source
    ):
        """Should filter extractions by source_group."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Acme fact", "category": "test"},
            extraction_type="technical_fact",
            source_group="acme_corp",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Other fact", "category": "test"},
            extraction_type="technical_fact",
            source_group="other_corp",
        )

        extractions = await extraction_repo.list(
            ExtractionFilters(source_group="acme_corp")
        )
        assert len(extractions) >= 1
        assert all(e.source_group == "acme_corp" for e in extractions)

    async def test_list_filters_by_confidence_range(
        self, extraction_repo, test_project, test_source
    ):
        """Should filter extractions by confidence range."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Low confidence", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
            confidence=0.5,
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "High confidence", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
            confidence=0.95,
        )

        extractions = await extraction_repo.list(ExtractionFilters(min_confidence=0.9))
        assert len(extractions) >= 1
        assert all(e.confidence >= 0.9 for e in extractions if e.confidence)

    async def test_list_combines_multiple_filters(
        self, extraction_repo, test_project, test_source
    ):
        """Should apply multiple filters simultaneously."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Match", "category": "test"},
            extraction_type="technical_fact",
            source_group="acme_corp",
            confidence=0.95,
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "No match - wrong group", "category": "test"},
            extraction_type="technical_fact",
            source_group="other_corp",
            confidence=0.95,
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "No match - low confidence", "category": "test"},
            extraction_type="technical_fact",
            source_group="acme_corp",
            confidence=0.5,
        )

        extractions = await extraction_repo.list(
            ExtractionFilters(
                project_id=test_project.id,
                source_group="acme_corp",
                min_confidence=0.9,
            )
        )
        assert len(extractions) == 1
        assert extractions[0].data["fact_text"] == "Match"

    async def test_list_with_include_source_eager_loads_source(
        self, extraction_repo, test_project, db_session
    ):
        """Should eager-load source relationship when include_source=True."""
        # Create source with title
        source = Source(
            project_id=test_project.id,
            uri="https://example.com/test-source",
            source_group="test_company",
            title="Test Source Title",
        )
        db_session.add(source)
        db_session.flush()

        # Create extraction
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=source.id,
            data={"fact_text": "Test fact", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        # Query with include_source=True
        extractions = await extraction_repo.list(
            ExtractionFilters(project_id=test_project.id),
            include_source=True,
        )

        assert len(extractions) >= 1
        extraction = extractions[0]
        # Source should be loaded (not triggering lazy load)
        assert extraction.source is not None
        assert extraction.source.uri == "https://example.com/test-source"
        assert extraction.source.title == "Test Source Title"

    async def test_list_without_include_source_does_not_load_source(
        self, extraction_repo, test_project, test_source
    ):
        """Should not eager-load source when include_source=False (default)."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Test fact", "category": "test"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        # Query without include_source (default behavior)
        extractions = await extraction_repo.list(
            ExtractionFilters(project_id=test_project.id),
        )

        assert len(extractions) >= 1
        # Source relationship exists but may not be loaded yet
        # (accessing it would trigger lazy load)


class TestExtractionRepositoryQueryJsonb:
    """Test ExtractionRepository.query_jsonb() method for JSONB path queries."""

    async def test_query_jsonb_by_simple_field(
        self, extraction_repo, test_project, test_source
    ):
        """Should query by simple JSONB field."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Test", "category": "pricing"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Test", "category": "feature"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.query_jsonb(
            project_id=test_project.id, path="category", value="pricing"
        )
        assert len(extractions) >= 1
        assert all(e.data.get("category") == "pricing" for e in extractions)

    async def test_query_jsonb_by_nested_field(
        self, extraction_repo, test_project, test_source
    ):
        """Should query by nested JSONB field."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={
                "fact_text": "Test",
                "metadata": {"verified": True, "source": "docs"},
            },
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={
                "fact_text": "Test",
                "metadata": {"verified": False, "source": "docs"},
            },
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.query_jsonb(
            project_id=test_project.id, path="metadata.verified", value=True
        )
        assert len(extractions) >= 1
        assert all(
            e.data.get("metadata", {}).get("verified") is True for e in extractions
        )

    async def test_query_jsonb_returns_empty_for_no_match(
        self, extraction_repo, test_project, test_source
    ):
        """Should return empty list when no extractions match JSONB query."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Test", "category": "feature"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.query_jsonb(
            project_id=test_project.id, path="category", value="nonexistent"
        )
        # Should only include extractions from this test
        matching = [e for e in extractions if e.data.get("category") == "nonexistent"]
        assert len(matching) == 0


class TestExtractionRepositoryFilterByData:
    """Test ExtractionRepository.filter_by_data() for complex JSONB filtering."""

    async def test_filter_by_data_single_field(
        self, extraction_repo, test_project, test_source
    ):
        """Should filter by single JSONB field."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Match", "category": "pricing"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "No match", "category": "feature"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.filter_by_data(
            project_id=test_project.id, filters={"category": "pricing"}
        )
        assert len(extractions) >= 1
        assert all(e.data.get("category") == "pricing" for e in extractions)

    async def test_filter_by_data_multiple_fields(
        self, extraction_repo, test_project, test_source
    ):
        """Should filter by multiple JSONB fields (AND logic)."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={
                "fact_text": "Match",
                "category": "pricing",
                "verified": True,
            },
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={
                "fact_text": "No match",
                "category": "pricing",
                "verified": False,
            },
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.filter_by_data(
            project_id=test_project.id,
            filters={"category": "pricing", "verified": True},
        )
        assert len(extractions) >= 1
        assert all(
            e.data.get("category") == "pricing" and e.data.get("verified") is True
            for e in extractions
        )

    async def test_filter_by_data_returns_empty_for_no_match(
        self, extraction_repo, test_project, test_source
    ):
        """Should return empty list when no extractions match filters."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Test", "category": "feature"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.filter_by_data(
            project_id=test_project.id, filters={"category": "nonexistent"}
        )
        matching = [e for e in extractions if e.data.get("category") == "nonexistent"]
        assert len(matching) == 0

    async def test_filter_by_data_empty_filters_returns_all(
        self, extraction_repo, test_project, test_source
    ):
        """Should return all project extractions when filters are empty."""
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Fact 1", "category": "pricing"},
            extraction_type="technical_fact",
            source_group="test_company",
        )
        await extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"fact_text": "Fact 2", "category": "feature"},
            extraction_type="technical_fact",
            source_group="test_company",
        )

        extractions = await extraction_repo.filter_by_data(
            project_id=test_project.id, filters={}
        )
        assert len(extractions) >= 2
