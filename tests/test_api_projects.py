"""Tests for Project CRUD API endpoints."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database import engine, get_db
from main import app
from orm_models import Extraction, Project


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
def client(db_session):
    """Create test client with database session."""

    def override_get_db():
        try:
            yield db_session
        finally:
            pass  # Don't close, let the db_session fixture handle it

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers(valid_api_key):
    """Return authentication headers with valid API key."""
    return {"X-API-Key": valid_api_key}


@pytest.fixture
def valid_api_key():
    """Return valid API key from config."""
    from config import settings

    return settings.api_key


@pytest.fixture
def sample_project_data():
    """Valid project creation payload."""
    return {
        "name": "test_project",
        "description": "Test project description",
        "source_config": {"type": "web", "group_by": "company"},
        "extraction_schema": {
            "name": "technical_fact",
            "fields": [
                {
                    "name": "fact_text",
                    "type": "text",
                    "required": True,
                    "description": "The extracted fact",
                },
                {
                    "name": "category",
                    "type": "enum",
                    "required": True,
                    "values": ["specs", "api", "security"],
                },
            ],
        },
        "entity_types": [{"name": "feature", "description": "Product feature"}],
        "is_template": False,
    }


@pytest.fixture
def created_project(db_session):
    """Pre-created project for GET/PUT/DELETE tests."""
    project = Project(
        name="existing_project",
        description="Existing project description",
        source_config={"type": "web", "group_by": "company"},
        extraction_schema={
            "name": "fact",
            "fields": [{"name": "text", "type": "text", "required": True}],
        },
        entity_types=[],
        is_template=False,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


# RED PHASE: First failing test for POST /projects
class TestCreateProject:
    """Test POST /api/v1/projects endpoint."""

    def test_create_with_valid_data(
        self, client, sample_project_data, db_session, auth_headers
    ):
        """Should create project and return 201 with project data."""
        response = client.post(
            "/api/v1/projects", json=sample_project_data, headers=auth_headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == sample_project_data["name"]
        assert data["description"] == sample_project_data["description"]
        assert "id" in data
        assert data["is_active"] is True

    def test_create_duplicate_name_returns_409(
        self, client, created_project, auth_headers
    ):
        """Should return 409 when creating project with duplicate name."""
        duplicate_data = {
            "name": created_project.name,  # Use existing project name
            "extraction_schema": {
                "name": "fact",
                "fields": [{"name": "text", "type": "text", "required": True}],
            },
        }
        response = client.post(
            "/api/v1/projects", json=duplicate_data, headers=auth_headers
        )

        assert response.status_code == 409
        assert "already exists" in response.json()["detail"].lower()

    def test_create_with_minimal_fields(self, client, db_session, auth_headers):
        """Should create project with only required fields."""
        minimal_data = {
            "name": "minimal_project",
            "extraction_schema": {
                "name": "fact",
                "fields": [{"name": "text", "type": "text", "required": True}],
            },
        }
        response = client.post(
            "/api/v1/projects", json=minimal_data, headers=auth_headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "minimal_project"
        assert data["description"] is None
        assert data["source_config"] == {"type": "web", "group_by": "company"}
        assert data["entity_types"] == []


class TestListProjects:
    """Test GET /api/v1/projects endpoint."""

    def test_list_returns_all_active_projects(
        self, client, created_project, auth_headers
    ):
        """Should return list of all active projects."""
        response = client.get("/api/v1/projects", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        assert any(p["id"] == str(created_project.id) for p in data)

    def test_list_excludes_inactive_by_default(self, client, db_session, auth_headers):
        """Should exclude inactive projects by default."""
        # Create inactive project
        inactive = Project(
            name="inactive_proj",
            extraction_schema={"name": "fact", "fields": []},
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()

        response = client.get("/api/v1/projects", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert not any(p["name"] == "inactive_proj" for p in data)

    def test_list_includes_inactive_with_param(self, client, db_session, auth_headers):
        """Should include inactive projects when requested."""
        inactive = Project(
            name="inactive_proj2",
            extraction_schema={"name": "fact", "fields": []},
            is_active=False,
        )
        db_session.add(inactive)
        db_session.commit()

        response = client.get(
            "/api/v1/projects?include_inactive=true", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert any(p["name"] == "inactive_proj2" for p in data)


class TestGetProject:
    """Test GET /api/v1/projects/{project_id} endpoint."""

    def test_get_existing_project_returns_200(
        self, client, created_project, auth_headers
    ):
        """Should return project data for existing project."""
        response = client.get(
            f"/api/v1/projects/{created_project.id}", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(created_project.id)
        assert data["name"] == created_project.name

    def test_get_nonexistent_project_returns_404(self, client, auth_headers):
        """Should return 404 for non-existent project."""
        fake_id = uuid4()
        response = client.get(f"/api/v1/projects/{fake_id}", headers=auth_headers)

        assert response.status_code == 404


class TestUpdateProject:
    """Test PUT /api/v1/projects/{project_id} endpoint."""

    def test_update_description_only(self, client, created_project, auth_headers):
        """Should update only specified fields."""
        update_data = {"description": "Updated description"}
        response = client.put(
            f"/api/v1/projects/{created_project.id}",
            json=update_data,
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["description"] == "Updated description"
        assert data["name"] == created_project.name  # Unchanged

    def test_update_with_extractions_adds_warning(
        self, client, created_project, db_session, auth_headers
    ):
        """Should add warning header when project has extractions."""
        # Create an extraction for the project
        from orm_models import Source

        source = Source(
            project_id=created_project.id,
            source_type="web",
            uri="https://example.com",
            source_group="test",
        )
        db_session.add(source)
        db_session.flush()

        extraction = Extraction(
            project_id=created_project.id,
            source_id=source.id,
            data={"test": "data"},
            extraction_type="fact",
            source_group="test",
        )
        db_session.add(extraction)
        db_session.commit()

        # Update schema
        update_data = {
            "extraction_schema": {
                "name": "new_schema",
                "fields": [{"name": "new_field", "type": "text", "required": True}],
            }
        }
        response = client.put(
            f"/api/v1/projects/{created_project.id}",
            json=update_data,
            headers=auth_headers,
        )

        assert response.status_code == 200
        assert "X-Extraction-Warning" in response.headers
        assert "extraction" in response.headers["X-Extraction-Warning"].lower()

    def test_update_nonexistent_project_returns_404(self, client, auth_headers):
        """Should return 404 when updating non-existent project."""
        fake_id = uuid4()
        response = client.put(
            f"/api/v1/projects/{fake_id}",
            json={"description": "test"},
            headers=auth_headers,
        )

        assert response.status_code == 404


class TestDeleteProject:
    """Test DELETE /api/v1/projects/{project_id} endpoint."""

    def test_delete_sets_is_active_false(
        self, client, created_project, db_session, auth_headers
    ):
        """Should soft delete by setting is_active to False."""
        response = client.delete(
            f"/api/v1/projects/{created_project.id}", headers=auth_headers
        )

        assert response.status_code == 204

        # Verify soft delete
        db_session.refresh(created_project)
        assert created_project.is_active is False

    def test_delete_nonexistent_returns_404(self, client, auth_headers):
        """Should return 404 when deleting non-existent project."""
        fake_id = uuid4()
        response = client.delete(f"/api/v1/projects/{fake_id}", headers=auth_headers)

        assert response.status_code == 404


class TestListTemplates:
    """Test GET /api/v1/projects/templates endpoint."""

    def test_list_templates_returns_template_names(self, client, auth_headers):
        """Should return list of available template names."""
        response = client.get("/api/v1/projects/templates", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert isinstance(data, list)
        assert "company_analysis" in data


class TestCreateFromTemplate:
    """Test POST /api/v1/projects/from-template endpoint."""

    def test_create_from_company_analysis_template(
        self, client, db_session, auth_headers
    ):
        """Should create project from company_analysis template."""
        request_data = {
            "template": "company_analysis",
            "name": "my_company_project",
            "description": "My custom project",
        }
        response = client.post(
            "/api/v1/projects/from-template", json=request_data, headers=auth_headers
        )

        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "my_company_project"
        assert data["description"] == "My custom project"
        assert data["is_template"] is False
        assert "extraction_schema" in data

    def test_create_from_nonexistent_template_returns_404(self, client, auth_headers):
        """Should return 404 for non-existent template."""
        request_data = {
            "template": "nonexistent_template",
            "name": "test_proj",
        }
        response = client.post(
            "/api/v1/projects/from-template", json=request_data, headers=auth_headers
        )

        assert response.status_code == 404
        assert "template" in response.json()["detail"].lower()
