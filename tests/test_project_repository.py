"""Tests for ProjectRepository."""

import pytest
from datetime import datetime
from sqlalchemy import create_engine, select
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

    def test_create_project_with_minimal_data(self, project_repo, db_session):
        """Should create project with minimal required fields."""
        project = project_repo.create(
            name="test_project",
            extraction_schema={"name": "fact", "fields": []},
        )

        assert project.id is not None
        assert project.name == "test_project"
        assert project.extraction_schema == {"name": "fact", "fields": []}
        assert project.is_active is True
        assert project.is_template is False

    def test_create_project_with_all_fields(self, project_repo, db_session):
        """Should create project with all fields populated."""
        project = project_repo.create(
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

    def test_create_project_returns_persisted_object(
        self, project_repo, db_session
    ):
        """Created project should be retrievable from database."""
        project = project_repo.create(
            name="persisted",
            extraction_schema={"name": "test", "fields": []},
        )

        # Verify it's in the database
        result = db_session.execute(
            select(Project).where(Project.id == project.id)
        )
        db_project = result.scalar_one()
        assert db_project.name == "persisted"


class TestProjectRepositoryGet:
    """Test ProjectRepository.get() method."""

    def test_get_by_id_returns_project(self, project_repo, db_session):
        """Should retrieve project by ID."""
        project = project_repo.create(
            name="get_test",
            extraction_schema={"name": "test", "fields": []},
        )

        retrieved = project_repo.get(project.id)
        assert retrieved is not None
        assert retrieved.id == project.id
        assert retrieved.name == "get_test"

    def test_get_nonexistent_returns_none(self, project_repo):
        """Should return None for nonexistent project."""
        from uuid import uuid4

        retrieved = project_repo.get(uuid4())
        assert retrieved is None


class TestProjectRepositoryGetByName:
    """Test ProjectRepository.get_by_name() method."""

    def test_get_by_name_returns_project(self, project_repo, db_session):
        """Should retrieve project by name."""
        project_repo.create(
            name="named_project",
            extraction_schema={"name": "test", "fields": []},
        )

        retrieved = project_repo.get_by_name("named_project")
        assert retrieved is not None
        assert retrieved.name == "named_project"

    def test_get_by_name_nonexistent_returns_none(self, project_repo):
        """Should return None for nonexistent name."""
        retrieved = project_repo.get_by_name("nonexistent")
        assert retrieved is None


class TestProjectRepositoryListAll:
    """Test ProjectRepository.list_all() method."""

    def test_list_all_returns_active_projects_only(
        self, project_repo, db_session
    ):
        """Should return only active projects by default."""
        # Count pre-existing active projects
        pre_existing = len(project_repo.list_all())

        project_repo.create(
            name="active1",
            extraction_schema={"name": "test", "fields": []},
        )
        project_repo.create(
            name="active2",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive = project_repo.create(
            name="inactive",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive.is_active = False
        db_session.flush()

        projects = project_repo.list_all()
        assert len(projects) == pre_existing + 2
        assert all(p.is_active for p in projects)

    def test_list_all_with_inactive_returns_all(self, project_repo, db_session):
        """Should return all projects including inactive when specified."""
        # Count pre-existing projects (active + inactive)
        pre_existing = len(project_repo.list_all(include_inactive=True))

        project_repo.create(
            name="active",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive = project_repo.create(
            name="inactive",
            extraction_schema={"name": "test", "fields": []},
        )
        inactive.is_active = False
        db_session.flush()

        projects = project_repo.list_all(include_inactive=True)
        assert len(projects) == pre_existing + 2

    def test_list_all_returns_sorted_by_name(self, project_repo):
        """Should return projects sorted by name."""
        project_repo.create(
            name="zebra",
            extraction_schema={"name": "test", "fields": []},
        )
        project_repo.create(
            name="apple",
            extraction_schema={"name": "test", "fields": []},
        )

        projects = project_repo.list_all()
        names = [p.name for p in projects]
        # Verify our test projects are present and in sorted order
        assert "apple" in names
        assert "zebra" in names
        apple_idx = names.index("apple")
        zebra_idx = names.index("zebra")
        assert apple_idx < zebra_idx


class TestProjectRepositoryListTemplates:
    """Test ProjectRepository.list_templates() method."""

    def test_list_templates_returns_only_templates(self, project_repo):
        """Should return only projects marked as templates."""
        pre_existing = len(project_repo.list_templates())

        project_repo.create(
            name="regular",
            extraction_schema={"name": "test", "fields": []},
        )
        project_repo.create(
            name="template1",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )
        project_repo.create(
            name="template2",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )

        templates = project_repo.list_templates()
        assert len(templates) == pre_existing + 2
        assert all(t.is_template for t in templates)

    def test_list_templates_excludes_inactive(self, project_repo, db_session):
        """Should exclude inactive templates."""
        pre_existing = len(project_repo.list_templates())

        project_repo.create(
            name="active_template",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )
        inactive_template = project_repo.create(
            name="inactive_template",
            extraction_schema={"name": "test", "fields": []},
            is_template=True,
        )
        inactive_template.is_active = False
        db_session.flush()

        templates = project_repo.list_templates()
        assert len(templates) == pre_existing + 1
        template_names = [t.name for t in templates]
        assert "active_template" in template_names


class TestProjectRepositoryUpdate:
    """Test ProjectRepository.update() method."""

    def test_update_project_fields(self, project_repo):
        """Should update project fields."""
        project = project_repo.create(
            name="update_test",
            description="Old description",
            extraction_schema={"name": "test", "fields": []},
        )

        updated = project_repo.update(
            project.id,
            {
                "description": "New description",
                "is_template": True,
            },
        )

        assert updated is not None
        assert updated.description == "New description"
        assert updated.is_template is True

    def test_update_nonexistent_returns_none(self, project_repo):
        """Should return None when updating nonexistent project."""
        from uuid import uuid4

        result = project_repo.update(uuid4(), {"description": "test"})
        assert result is None

    def test_update_ignores_invalid_fields(self, project_repo):
        """Should ignore fields that don't exist on the model."""
        project = project_repo.create(
            name="safe_update",
            extraction_schema={"name": "test", "fields": []},
        )

        # Should not raise an error
        updated = project_repo.update(
            project.id,
            {"invalid_field": "value", "description": "Valid"},
        )

        assert updated.description == "Valid"


class TestProjectRepositoryDelete:
    """Test ProjectRepository.delete() method."""

    def test_delete_sets_inactive(self, project_repo):
        """Should soft delete by setting is_active=False."""
        project = project_repo.create(
            name="delete_test",
            extraction_schema={"name": "test", "fields": []},
        )

        success = project_repo.delete(project.id)
        assert success is True

        # Verify it's marked inactive
        retrieved = project_repo.get(project.id)
        assert retrieved.is_active is False

    def test_delete_nonexistent_returns_false(self, project_repo):
        """Should return False when deleting nonexistent project."""
        from uuid import uuid4

        success = project_repo.delete(uuid4())
        assert success is False


class TestProjectRepositoryGetDefaultProject:
    """Test ProjectRepository.get_default_project() method."""

    def test_get_default_returns_company_analysis(self, project_repo):
        """Should return company_analysis project (existing or newly created)."""
        default = project_repo.get_default_project()
        assert default is not None
        assert default.name == "company_analysis"
        assert default.extraction_schema["name"] == "technical_fact"

    def test_get_default_is_idempotent(self, project_repo):
        """Calling get_default_project twice returns the same project."""
        first = project_repo.get_default_project()
        second = project_repo.get_default_project()
        assert first.id == second.id
        assert first.name == "company_analysis"
