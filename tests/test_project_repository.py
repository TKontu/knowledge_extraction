"""Tests for ProjectRepository."""

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Project
from services.projects.repository import ProjectRepository


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
def project_repo(db_session):
    """Create ProjectRepository instance."""
    return ProjectRepository(db_session)


class TestProjectRepositoryCreate:
    """Test ProjectRepository.create() method."""

    async def test_create_project_with_minimal_data(self, project_repo, db_session):
        """Should create project with minimal required fields."""
        project = await project_repo.create(
            name="test_project",
            extraction_schema={"name": "fact", "fields": []},
        )

        assert project.id is not None
        assert project.name == "test_project"
        assert project.extraction_schema == {"name": "fact", "fields": []}
        assert project.is_active is True
        assert project.is_template is False

    async def test_create_project_with_all_fields(self, project_repo, db_session):
        """Should create project with all fields populated."""
        project = await project_repo.create(
            name="full_project",
            description="A comprehensive project",
            source_config={"type": "pdf", "group_by": "document"},
            extraction_schema={
                "name": "clause",
                "fields": [{"name": "text", "type": "text"}],
            },
            entity_types=[{"name": "party", "description": "Contract party"}],
            prompt_templates={"custom": "Custom prompt"},
            is_template=True,
        )

        assert project.name == "full_project"
        assert project.description == "A comprehensive project"
        assert project.source_config["type"] == "pdf"
        assert len(project.entity_types) == 1
        assert project.is_template is True

    async def test_create_project_returns_persisted_object(
        self, project_repo, db_session
    ):
        """Created project should be retrievable from database."""
        project = await project_repo.create(
            name="persisted",
            extraction_schema={"name": "test", "fields": []},
        )

        # Verify it's in the database
        result = db_session.execute(select(Project).where(Project.id == project.id))
        db_project = result.scalar_one()
        assert db_project.name == "persisted"


class TestProjectRepositoryGet:
    """Test ProjectRepository.get() method."""

    async def test_get_by_id_returns_project(self, project_repo, db_session):
        """Should retrieve project by ID."""
        project = await project_repo.create(
            name="get_test",
            extraction_schema={"name": "test", "fields": []},
        )

        retrieved = await project_repo.get(project.id)
        assert retrieved is not None
        assert retrieved.id == project.id
        assert retrieved.name == "get_test"

    async def test_get_nonexistent_returns_none(self, project_repo):
        """Should return None for nonexistent project."""
        from uuid import uuid4

        retrieved = await project_repo.get(uuid4())
        assert retrieved is None


class TestProjectRepositoryGetByName:
    """Test ProjectRepository.get_by_name() method."""

    async def test_get_by_name_returns_project(self, project_repo, db_session):
        """Should retrieve project by name."""
        await project_repo.create(
            name="named_project",
            extraction_schema={"name": "test", "fields": []},
        )

        retrieved = await project_repo.get_by_name("named_project")
        assert retrieved is not None
        assert retrieved.name == "named_project"

    async def test_get_by_name_nonexistent_returns_none(self, project_repo):
        """Should return None for nonexistent name."""
        retrieved = await project_repo.get_by_name("nonexistent")
        assert retrieved is None


class TestProjectRepositoryListAll:
    """Test ProjectRepository.list_all() method."""

    async def test_list_all_returns_active_projects_only(
        self, project_repo, db_session
    ):
        """Should return only active projects by default."""
        await project_repo.create(
            name="active1",
            extraction_schema={"name": "test", "fields": []},
        )
        await project_repo.create(
            name="active2",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive = await project_repo.create(
            name="inactive",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive.is_active = False
        db_session.flush()

        projects = await project_repo.list_all()
        assert len(projects) == 2
        assert all(p.is_active for p in projects)

    async def test_list_all_with_inactive_returns_all(self, project_repo, db_session):
        """Should return all projects including inactive when specified."""
        await project_repo.create(
            name="active",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive = await project_repo.create(
            name="inactive",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive.is_active = False
        db_session.flush()

        projects = await project_repo.list_all(include_inactive=True)
        assert len(projects) == 2

    async def test_list_all_returns_sorted_by_name(self, project_repo):
        """Should return projects sorted by name."""
        await project_repo.create(
            name="zebra",
            extraction_schema={"name": "test", "fields": []},
        )
        await project_repo.create(
            name="apple",
            extraction_schema={"name": "test", "fields": []},
        )

        projects = await project_repo.list_all()
        assert projects[0].name == "apple"
        assert projects[1].name == "zebra"


class TestProjectRepositoryListTemplates:
    """Test ProjectRepository.list_templates() method."""

    async def test_list_templates_returns_only_templates(self, project_repo):
        """Should return only projects marked as templates."""
        await project_repo.create(
            name="regular",
            extraction_schema={"name": "test", "fields": []},
        )
        await project_repo.create(
            name="template1",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )
        await project_repo.create(
            name="template2",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )

        templates = await project_repo.list_templates()
        assert len(templates) == 2
        assert all(t.is_template for t in templates)

    async def test_list_templates_excludes_inactive(self, project_repo, db_session):
        """Should exclude inactive templates."""
        await project_repo.create(
            name="active_template",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )
        inactive_template = await project_repo.create(
            name="inactive_template",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )
        inactive_template.is_active = False
        db_session.flush()

        templates = await project_repo.list_templates()
        assert len(templates) == 1
        assert templates[0].name == "active_template"


class TestProjectRepositoryUpdate:
    """Test ProjectRepository.update() method."""

    async def test_update_project_fields(self, project_repo):
        """Should update project fields."""
        project = await project_repo.create(
            name="update_test",
            description="Old description",
            extraction_schema={"name": "test", "fields": []},
        )

        updated = await project_repo.update(
            project.id,
            {
                "description": "New description",
                "is_template": True,
            },
        )

        assert updated is not None
        assert updated.description == "New description"
        assert updated.is_template is True

    async def test_update_nonexistent_returns_none(self, project_repo):
        """Should return None when updating nonexistent project."""
        from uuid import uuid4

        result = await project_repo.update(uuid4(), {"description": "test"})
        assert result is None

    async def test_update_ignores_invalid_fields(self, project_repo):
        """Should ignore fields that don't exist on the model."""
        project = await project_repo.create(
            name="safe_update",
            extraction_schema={"name": "test", "fields": []},
        )

        # Should not raise an error
        updated = await project_repo.update(
            project.id,
            {"invalid_field": "value", "description": "Valid"},
        )

        assert updated.description == "Valid"


class TestProjectRepositoryDelete:
    """Test ProjectRepository.delete() method."""

    async def test_delete_sets_inactive(self, project_repo):
        """Should soft delete by setting is_active=False."""
        project = await project_repo.create(
            name="delete_test",
            extraction_schema={"name": "test", "fields": []},
        )

        success = await project_repo.delete(project.id)
        assert success is True

        # Verify it's marked inactive
        retrieved = await project_repo.get(project.id)
        assert retrieved.is_active is False

    async def test_delete_nonexistent_returns_false(self, project_repo):
        """Should return False when deleting nonexistent project."""
        from uuid import uuid4

        success = await project_repo.delete(uuid4())
        assert success is False


class TestProjectRepositoryGetDefaultProject:
    """Test ProjectRepository.get_default_project() method."""

    async def test_get_default_returns_company_analysis(self, project_repo):
        """Should return existing company_analysis project."""
        # Create the default project
        await project_repo.create(
            name="company_analysis",
            description="Default project",
            extraction_schema={
                "name": "technical_fact",
                "fields": [
                    {"name": "fact_text", "type": "text", "required": True},
                    {"name": "category", "type": "enum"},
                ],
            },
        )

        default = await project_repo.get_default_project()
        assert default is not None
        assert default.name == "company_analysis"

    async def test_get_default_creates_if_missing(self, project_repo):
        """Should create company_analysis if it doesn't exist."""
        # Ensure it doesn't exist
        existing = await project_repo.get_by_name("company_analysis")
        assert existing is None

        default = await project_repo.get_default_project()
        assert default is not None
        assert default.name == "company_analysis"
        assert default.extraction_schema["name"] == "technical_fact"
