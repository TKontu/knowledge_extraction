"""Tests for SourceRepository."""

import pytest
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session
from database import engine
from orm_models import Source, Project
from services.storage.repositories.source import SourceRepository, SourceFilters


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
    """Create a test project for source tests."""
    project = Project(
        name="test_project",
        extraction_schema={"name": "test", "fields": []},
    )
    db_session.add(project)
    db_session.flush()
    return project


@pytest.fixture
def source_repo(db_session):
    """Create SourceRepository instance."""
    return SourceRepository(db_session)


class TestSourceRepositoryCreate:
    """Test SourceRepository.create() method."""

    async def test_create_source_with_minimal_data(
        self, source_repo, test_project, db_session
    ):
        """Should create source with minimal required fields."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/page1",
            source_group="test_company",
        )

        assert source.id is not None
        assert source.project_id == test_project.id
        assert source.uri == "https://example.com/page1"
        assert source.source_group == "test_company"
        assert source.source_type == "web"
        assert source.status == "pending"
        assert source.title is None
        assert source.content is None

    async def test_create_source_with_all_fields(
        self, source_repo, test_project, db_session
    ):
        """Should create source with all fields populated."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/page2",
            source_group="acme_corp",
            source_type="pdf",
            title="Test Document",
            content="Document content",
            raw_content="<html>Raw content</html>",
            meta_data={"author": "John Doe", "pages": 10},
            outbound_links=["https://example.com/link1", "https://example.com/link2"],
            status="completed",
        )

        assert source.uri == "https://example.com/page2"
        assert source.source_type == "pdf"
        assert source.title == "Test Document"
        assert source.content == "Document content"
        assert source.raw_content == "<html>Raw content</html>"
        assert source.meta_data["author"] == "John Doe"
        assert len(source.outbound_links) == 2
        assert source.status == "completed"

    async def test_create_source_returns_persisted_object(
        self, source_repo, test_project, db_session
    ):
        """Created source should be retrievable from database."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/persisted",
            source_group="test_group",
        )

        # Verify it's in the database
        result = db_session.execute(select(Source).where(Source.id == source.id))
        db_source = result.scalar_one()
        assert db_source.uri == "https://example.com/persisted"

    async def test_create_source_sets_created_at(
        self, source_repo, test_project, db_session
    ):
        """Should automatically set created_at timestamp."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/timestamp",
            source_group="test_group",
        )

        assert source.created_at is not None
        assert isinstance(source.created_at, datetime)


class TestSourceRepositoryGet:
    """Test SourceRepository.get() method."""

    async def test_get_by_id_returns_source(
        self, source_repo, test_project, db_session
    ):
        """Should retrieve source by ID."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/get_test",
            source_group="test_group",
        )

        retrieved = await source_repo.get(source.id)
        assert retrieved is not None
        assert retrieved.id == source.id
        assert retrieved.uri == "https://example.com/get_test"

    async def test_get_nonexistent_returns_none(self, source_repo):
        """Should return None for nonexistent source."""
        from uuid import uuid4

        retrieved = await source_repo.get(uuid4())
        assert retrieved is None


class TestSourceRepositoryGetByUri:
    """Test SourceRepository.get_by_uri() method."""

    async def test_get_by_uri_returns_source(
        self, source_repo, test_project, db_session
    ):
        """Should retrieve source by URI within a project."""
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/unique_uri",
            source_group="test_group",
        )

        retrieved = await source_repo.get_by_uri(
            project_id=test_project.id, uri="https://example.com/unique_uri"
        )
        assert retrieved is not None
        assert retrieved.uri == "https://example.com/unique_uri"

    async def test_get_by_uri_nonexistent_returns_none(
        self, source_repo, test_project
    ):
        """Should return None for nonexistent URI."""
        retrieved = await source_repo.get_by_uri(
            project_id=test_project.id, uri="https://example.com/nonexistent"
        )
        assert retrieved is None

    async def test_get_by_uri_is_project_scoped(
        self, source_repo, test_project, db_session
    ):
        """Should only find sources within the specified project."""
        # Create another project
        other_project = Project(
            name="other_project",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(other_project)
        db_session.flush()

        # Create source in test_project
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/shared_uri",
            source_group="test_group",
        )

        # Try to get from other_project (should be None)
        retrieved = await source_repo.get_by_uri(
            project_id=other_project.id, uri="https://example.com/shared_uri"
        )
        assert retrieved is None


class TestSourceRepositoryList:
    """Test SourceRepository.list() method."""

    async def test_list_all_sources(self, source_repo, test_project):
        """Should list all sources without filters."""
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/1",
            source_group="group1",
        )
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/2",
            source_group="group2",
        )

        sources = await source_repo.list(SourceFilters())
        assert len(sources) >= 2

    async def test_list_filters_by_project_id(
        self, source_repo, test_project, db_session
    ):
        """Should filter sources by project_id."""
        # Create another project
        other_project = Project(
            name="other_project",
            extraction_schema={"name": "test", "fields": []},
        )
        db_session.add(other_project)
        db_session.flush()

        # Create sources in different projects
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/project1",
            source_group="group1",
        )
        await source_repo.create(
            project_id=other_project.id,
            uri="https://example.com/project2",
            source_group="group2",
        )

        # Filter by test_project
        sources = await source_repo.list(
            SourceFilters(project_id=test_project.id)
        )
        assert len(sources) == 1
        assert sources[0].uri == "https://example.com/project1"

    async def test_list_filters_by_source_group(self, source_repo, test_project):
        """Should filter sources by source_group."""
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/acme1",
            source_group="acme_corp",
        )
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/acme2",
            source_group="acme_corp",
        )
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/other",
            source_group="other_corp",
        )

        sources = await source_repo.list(
            SourceFilters(source_group="acme_corp")
        )
        assert len(sources) >= 2
        assert all(s.source_group == "acme_corp" for s in sources)

    async def test_list_filters_by_status(self, source_repo, test_project):
        """Should filter sources by status."""
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/pending",
            source_group="test_group",
            status="pending",
        )
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/completed",
            source_group="test_group",
            status="completed",
        )

        sources = await source_repo.list(SourceFilters(status="completed"))
        assert len(sources) >= 1
        assert all(s.status == "completed" for s in sources)

    async def test_list_filters_by_source_type(self, source_repo, test_project):
        """Should filter sources by source_type."""
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/web1",
            source_group="test_group",
            source_type="web",
        )
        await source_repo.create(
            project_id=test_project.id,
            uri="file:///example.pdf",
            source_group="test_group",
            source_type="pdf",
        )

        sources = await source_repo.list(SourceFilters(source_type="pdf"))
        assert len(sources) >= 1
        assert all(s.source_type == "pdf" for s in sources)

    async def test_list_combines_multiple_filters(self, source_repo, test_project):
        """Should apply multiple filters simultaneously."""
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/match",
            source_group="acme_corp",
            status="completed",
        )
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/nomatch1",
            source_group="acme_corp",
            status="pending",
        )
        await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/nomatch2",
            source_group="other_corp",
            status="completed",
        )

        sources = await source_repo.list(
            SourceFilters(
                project_id=test_project.id,
                source_group="acme_corp",
                status="completed",
            )
        )
        assert len(sources) == 1
        assert sources[0].uri == "https://example.com/match"

    async def test_list_returns_sorted_by_created_at(
        self, source_repo, test_project
    ):
        """Should return sources sorted by created_at descending."""
        source1 = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/first",
            source_group="test_group",
        )
        source2 = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/second",
            source_group="test_group",
        )

        sources = await source_repo.list(
            SourceFilters(project_id=test_project.id)
        )
        # Most recent first
        assert sources[0].id == source2.id
        assert sources[1].id == source1.id


class TestSourceRepositoryUpdateStatus:
    """Test SourceRepository.update_status() method."""

    async def test_update_status_changes_status(self, source_repo, test_project):
        """Should update source status."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/status_test",
            source_group="test_group",
            status="pending",
        )

        updated = await source_repo.update_status(source.id, "completed")
        assert updated is not None
        assert updated.status == "completed"

    async def test_update_status_sets_fetched_at(
        self, source_repo, test_project
    ):
        """Should set fetched_at when status changes to completed."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/fetched_test",
            source_group="test_group",
            status="pending",
        )
        assert source.fetched_at is None

        updated = await source_repo.update_status(source.id, "completed")
        assert updated.fetched_at is not None
        assert isinstance(updated.fetched_at, datetime)

    async def test_update_status_nonexistent_returns_none(self, source_repo):
        """Should return None when updating nonexistent source."""
        from uuid import uuid4

        result = await source_repo.update_status(uuid4(), "completed")
        assert result is None


class TestSourceRepositoryUpdateContent:
    """Test SourceRepository.update_content() method."""

    async def test_update_content_changes_fields(self, source_repo, test_project):
        """Should update content and title."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/content_test",
            source_group="test_group",
        )

        updated = await source_repo.update_content(
            source.id,
            content="Updated content",
            title="Updated Title",
        )
        assert updated is not None
        assert updated.content == "Updated content"
        assert updated.title == "Updated Title"

    async def test_update_content_with_optional_fields(
        self, source_repo, test_project
    ):
        """Should update content with all optional fields."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/full_update",
            source_group="test_group",
        )

        updated = await source_repo.update_content(
            source.id,
            content="New content",
            title="New Title",
            raw_content="<html>Raw</html>",
            outbound_links=["https://link1.com", "https://link2.com"],
        )
        assert updated.content == "New content"
        assert updated.title == "New Title"
        assert updated.raw_content == "<html>Raw</html>"
        assert len(updated.outbound_links) == 2

    async def test_update_content_nonexistent_returns_none(self, source_repo):
        """Should return None when updating nonexistent source."""
        from uuid import uuid4

        result = await source_repo.update_content(
            uuid4(), content="test", title="test"
        )
        assert result is None

    async def test_update_content_preserves_other_fields(
        self, source_repo, test_project
    ):
        """Should not modify fields not included in update."""
        source = await source_repo.create(
            project_id=test_project.id,
            uri="https://example.com/preserve_test",
            source_group="original_group",
            status="pending",
        )

        updated = await source_repo.update_content(
            source.id, content="New content", title="New Title"
        )
        # These should remain unchanged
        assert updated.source_group == "original_group"
        assert updated.status == "pending"
        assert updated.uri == "https://example.com/preserve_test"
