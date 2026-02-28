import csv
import io
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from database import get_db
from main import app


@pytest.fixture
def mock_entities():
    """Create mock entity objects."""
    entity = MagicMock()
    entity.id = uuid4()
    entity.entity_type = "feature"
    entity.value = "SSO Support"
    entity.normalized_value = "sso_support"
    entity.source_group = "company_a"
    entity.attributes = {"priority": "high"}
    entity.created_at = None
    return [entity]


@pytest.fixture
def mock_extractions():
    """Create mock extraction objects."""
    extraction = MagicMock()
    extraction.id = uuid4()
    extraction.source_id = uuid4()
    extraction.extraction_type = "feature"
    extraction.data = {"fact_text": "Supports SSO"}
    extraction.source_group = "company_a"
    extraction.confidence = 0.95
    extraction.profile_used = "general"
    extraction.created_at = None
    return [extraction]


@pytest.fixture
def mock_db_session(mock_entities, mock_extractions):
    """Create a mock database session."""
    mock_session = MagicMock()
    mock_query = MagicMock()
    mock_query.filter.return_value = mock_query
    mock_query.all.return_value = mock_entities
    mock_session.query.return_value = mock_query
    return mock_session


@pytest.fixture
def export_client(mock_db_session, valid_api_key):
    """Create test client with mocked database."""
    def override_get_db():
        yield mock_db_session

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


class TestExportEntities:
    def test_export_entities_csv(self, export_client, mock_entities, valid_api_key):
        """Export entities as CSV."""
        project_id = uuid4()

        response = export_client.get(
            f"/api/v1/projects/{project_id}/export/entities?format=csv",
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert "attachment" in response.headers["content-disposition"]

        # Parse CSV
        reader = csv.reader(io.StringIO(response.text))
        rows = list(reader)
        assert rows[0][0] == "id"  # Header
        assert len(rows) == 2  # Header + 1 data row

    def test_export_entities_json(self, export_client, mock_entities, valid_api_key):
        """Export entities as JSON."""
        project_id = uuid4()

        response = export_client.get(
            f"/api/v1/projects/{project_id}/export/entities?format=json",
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 200
        assert "application/json" in response.headers["content-type"]

        data = response.json()
        assert data["count"] == 1
        assert "entities" in data
        assert data["entities"][0]["entity_type"] == "feature"

    def test_export_entities_filter_by_type(self, export_client, mock_entities, valid_api_key):
        """Filter entities by type."""
        project_id = uuid4()

        response = export_client.get(
            f"/api/v1/projects/{project_id}/export/entities?entity_type=feature",
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 200


class TestExportExtractions:
    def test_export_extractions_csv(self, valid_api_key):
        """Export extractions as CSV."""
        project_id = uuid4()

        # Create specific mock for this test
        mock_session = MagicMock()
        mock_query = MagicMock()
        extraction = MagicMock()
        extraction.id = uuid4()
        extraction.source_id = uuid4()
        extraction.extraction_type = "feature"
        extraction.data = {"fact_text": "Supports SSO"}
        extraction.source_group = "company_a"
        extraction.confidence = 0.95
        extraction.profile_used = "general"
        extraction.created_at = None

        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [extraction]
        mock_session.query.return_value = mock_query

        def override_get_db():
            yield mock_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        response = client.get(
            f"/api/v1/projects/{project_id}/export/extractions?format=csv",
            headers={"X-API-Key": valid_api_key},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        assert response.headers["content-type"] == "text/csv; charset=utf-8"

    def test_export_extractions_json(self, valid_api_key):
        """Export extractions as JSON."""
        project_id = uuid4()

        # Create specific mock for this test
        mock_session = MagicMock()
        mock_query = MagicMock()
        extraction = MagicMock()
        extraction.id = uuid4()
        extraction.source_id = uuid4()
        extraction.extraction_type = "feature"
        extraction.data = {"fact_text": "Supports SSO"}
        extraction.source_group = "company_a"
        extraction.confidence = 0.95
        extraction.profile_used = "general"
        extraction.created_at = None

        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [extraction]
        mock_session.query.return_value = mock_query

        def override_get_db():
            yield mock_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        response = client.get(
            f"/api/v1/projects/{project_id}/export/extractions?format=json",
            headers={"X-API-Key": valid_api_key},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 1
        assert "extractions" in data

    def test_export_extractions_filter_by_confidence(self, valid_api_key):
        """Filter extractions by minimum confidence."""
        project_id = uuid4()

        # Create specific mock for this test
        mock_session = MagicMock()
        mock_query = MagicMock()
        extraction = MagicMock()
        extraction.id = uuid4()
        extraction.source_id = uuid4()
        extraction.extraction_type = "feature"
        extraction.data = {"fact_text": "Supports SSO"}
        extraction.source_group = "company_a"
        extraction.confidence = 0.95
        extraction.profile_used = "general"
        extraction.created_at = None

        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = [extraction]
        mock_session.query.return_value = mock_query

        def override_get_db():
            yield mock_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        response = client.get(
            f"/api/v1/projects/{project_id}/export/extractions?min_confidence=0.8",
            headers={"X-API-Key": valid_api_key},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200


class TestExportEmpty:
    def test_export_empty_entities(self, valid_api_key):
        """Export with no entities returns empty result."""
        project_id = uuid4()

        # Create specific mock for this test
        mock_session = MagicMock()
        mock_query = MagicMock()
        mock_query.filter.return_value = mock_query
        mock_query.all.return_value = []
        mock_session.query.return_value = mock_query

        def override_get_db():
            yield mock_session

        app.dependency_overrides[get_db] = override_get_db
        client = TestClient(app)

        response = client.get(
            f"/api/v1/projects/{project_id}/export/entities?format=json",
            headers={"X-API-Key": valid_api_key},
        )

        app.dependency_overrides.clear()

        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["entities"] == []
