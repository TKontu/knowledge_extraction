"""Tests for Entity API endpoints."""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from database import engine, get_db
from main import app
from orm_models import Entity, Project


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
def test_project(db_session):
    """Create a test project."""
    project = Project(
        name="test_entity_project",
        description="Project for entity testing",
        source_config={"type": "web", "group_by": "company"},
        extraction_schema={
            "name": "fact",
            "fields": [{"name": "text", "type": "text", "required": True}],
        },
        entity_types=[
            {"name": "feature", "description": "Product feature"},
            {"name": "plan", "description": "Pricing plan"},
        ],
        is_template=False,
    )
    db_session.add(project)
    db_session.commit()
    db_session.refresh(project)
    return project


@pytest.fixture
def test_entities(db_session, test_project):
    """Create test entities."""
    entities = [
        Entity(
            project_id=test_project.id,
            source_group="acme_corp",
            entity_type="feature",
            value="SSO",
            normalized_value="sso",
            attributes={"confidence": 0.9},
        ),
        Entity(
            project_id=test_project.id,
            source_group="globex_inc",
            entity_type="feature",
            value="sso",
            normalized_value="sso",
            attributes={"confidence": 0.85},
        ),
        Entity(
            project_id=test_project.id,
            source_group="acme_corp",
            entity_type="plan",
            value="Enterprise",
            normalized_value="enterprise",
            attributes={"price": "$99"},
        ),
        Entity(
            project_id=test_project.id,
            source_group="initech",
            entity_type="feature",
            value="API Access",
            normalized_value="api access",
            attributes={},
        ),
    ]
    for entity in entities:
        db_session.add(entity)
    db_session.commit()
    for entity in entities:
        db_session.refresh(entity)
    return entities


# Task 1: Test entity response models
class TestEntityResponseModels:
    """Test entity request/response model serialization."""

    def test_entity_response_serialization(self, test_entities):
        """Should serialize entity to EntityResponse."""
        from models import EntityResponse

        entity = test_entities[0]

        response = EntityResponse(
            id=str(entity.id),
            entity_type=entity.entity_type,
            value=entity.value,
            normalized_value=entity.normalized_value,
            source_group=entity.source_group,
            attributes=entity.attributes,
            created_at=entity.created_at.isoformat(),
        )

        assert response.id == str(entity.id)
        assert response.entity_type == "feature"
        assert response.value == "SSO"

    def test_entity_list_response_with_pagination(self, test_entities):
        """Should include pagination info in EntityListResponse."""
        from models import EntityListResponse, EntityResponse

        entity_responses = [
            EntityResponse(
                id=str(e.id),
                entity_type=e.entity_type,
                value=e.value,
                normalized_value=e.normalized_value,
                source_group=e.source_group,
                attributes=e.attributes,
                created_at=e.created_at.isoformat(),
            )
            for e in test_entities[:2]
        ]

        response = EntityListResponse(
            entities=entity_responses,
            total=4,
            limit=2,
            offset=0,
        )

        assert len(response.entities) == 2
        assert response.total == 4
        assert response.limit == 2
        assert response.offset == 0


# Task 2: Test entity list endpoint
class TestListEntities:
    """Test GET /api/v1/projects/{project_id}/entities endpoint."""

    def test_list_entities_returns_all(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should return all entities for project."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 4
        assert len(data["entities"]) == 4
        assert data["limit"] == 50
        assert data["offset"] == 0

    def test_list_entities_project_not_found(self, client, auth_headers):
        """Should return 404 for missing project."""
        fake_id = uuid4()
        response = client.get(
            f"/api/v1/projects/{fake_id}/entities",
            headers=auth_headers,
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_list_entities_invalid_project_id(self, client, auth_headers):
        """Should return 422 for invalid UUID."""
        response = client.get(
            "/api/v1/projects/invalid-uuid/entities",
            headers=auth_headers,
        )

        assert response.status_code == 422

    def test_list_entities_filter_by_type(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should filter by entity_type."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities?entity_type=feature",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 3
        assert all(e["entity_type"] == "feature" for e in data["entities"])

    def test_list_entities_filter_by_source_group(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should filter by source_group."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities?source_group=acme_corp",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert all(e["source_group"] == "acme_corp" for e in data["entities"])

    def test_list_entities_combined_filters(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should apply both type and source_group filters."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities?entity_type=feature&source_group=acme_corp",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        entity = data["entities"][0]
        assert entity["entity_type"] == "feature"
        assert entity["source_group"] == "acme_corp"

    def test_list_entities_pagination(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should respect limit and offset."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities?limit=2&offset=1",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 4
        assert len(data["entities"]) == 2
        assert data["limit"] == 2
        assert data["offset"] == 1

    def test_list_entities_empty(self, client, test_project, auth_headers):
        """Should return empty list when no entities match."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities?entity_type=nonexistent",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert len(data["entities"]) == 0


# Task 3: Test entity types summary endpoint
class TestGetEntityTypes:
    """Test GET /api/v1/projects/{project_id}/entities/types endpoint."""

    def test_get_entity_types_counts(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should return counts per type."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/types",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_entities"] == 4
        assert len(data["types"]) == 2

        # Check counts
        type_counts = {t["entity_type"]: t["count"] for t in data["types"]}
        assert type_counts["feature"] == 3
        assert type_counts["plan"] == 1

    def test_get_entity_types_project_not_found(self, client, auth_headers):
        """Should return 404 for missing project."""
        fake_id = uuid4()
        response = client.get(
            f"/api/v1/projects/{fake_id}/entities/types",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_get_entity_types_filter_by_source_group(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should filter counts by source_group."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/types?source_group=acme_corp",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_entities"] == 2

        type_counts = {t["entity_type"]: t["count"] for t in data["types"]}
        assert type_counts["feature"] == 1
        assert type_counts["plan"] == 1

    def test_get_entity_types_empty(
        self, client, test_project, auth_headers, db_session
    ):
        """Should return empty when no entities."""
        # Create new project with no entities
        import uuid

        new_project = Project(
            name=f"empty_project_{uuid.uuid4().hex[:8]}",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(new_project)
        db_session.commit()
        db_session.refresh(new_project)

        response = client.get(
            f"/api/v1/projects/{new_project.id}/entities/types",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total_entities"] == 0
        assert len(data["types"]) == 0


# Task 4: Test single entity endpoint
class TestGetEntity:
    """Test GET /api/v1/projects/{project_id}/entities/{entity_id} endpoint."""

    def test_get_entity_returns_entity(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should return entity details."""
        entity = test_entities[0]
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/{entity.id}",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == str(entity.id)
        assert data["entity_type"] == entity.entity_type
        assert data["value"] == entity.value
        assert data["normalized_value"] == entity.normalized_value
        assert data["source_group"] == entity.source_group

    def test_get_entity_not_found(self, client, test_project, auth_headers):
        """Should return 404 for missing entity."""
        fake_id = uuid4()
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/{fake_id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_get_entity_wrong_project(
        self, client, test_entities, db_session, auth_headers
    ):
        """Should return 404 if entity belongs to different project."""
        # Create different project
        other_project = Project(
            name="other_project",
            extraction_schema={"name": "fact", "fields": []},
        )
        db_session.add(other_project)
        db_session.commit()
        db_session.refresh(other_project)

        entity = test_entities[0]
        response = client.get(
            f"/api/v1/projects/{other_project.id}/entities/{entity.id}",
            headers=auth_headers,
        )

        assert response.status_code == 404

    def test_get_entity_invalid_ids(self, client, auth_headers):
        """Should return 422 for invalid UUID format."""
        response = client.get(
            "/api/v1/projects/invalid-uuid/entities/also-invalid",
            headers=auth_headers,
        )

        assert response.status_code == 422


# Task 5: Test source_groups by-value endpoint
class TestGetSourceGroupsByEntity:
    """Test GET /api/v1/projects/{project_id}/entities/by-value endpoint."""

    def test_get_source_groups_by_entity_finds_matches(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should return matching source_groups."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/by-value?entity_type=feature&value=sso",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["entity_type"] == "feature"
        assert data["value"] == "sso"
        assert data["total"] == 2
        assert set(data["source_groups"]) == {"acme_corp", "globex_inc"}

    def test_get_source_groups_by_entity_case_insensitive(
        self, client, test_project, test_entities, auth_headers
    ):
        """Should match case-insensitively (SSO matches sso)."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/by-value?entity_type=feature&value=SSO",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        assert set(data["source_groups"]) == {"acme_corp", "globex_inc"}

    def test_get_source_groups_by_entity_no_matches(
        self, client, test_project, auth_headers
    ):
        """Should return empty list when no matches."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/by-value?entity_type=feature&value=nonexistent",
            headers=auth_headers,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 0
        assert data["source_groups"] == []

    def test_get_source_groups_by_entity_missing_params(
        self, client, test_project, auth_headers
    ):
        """Should return 422 if required params missing."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/entities/by-value",
            headers=auth_headers,
        )

        assert response.status_code == 422
