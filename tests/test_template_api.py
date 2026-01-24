"""Tests for template API endpoints."""

import os

# Set test API key before imports
os.environ.setdefault("API_KEY", "test-api-key-for-pytest-minimum-16-chars")

from fastapi.testclient import TestClient

from main import app

# Create client with auth header
client = TestClient(app)
AUTH_HEADERS = {"X-API-Key": "test-api-key-for-pytest-minimum-16-chars"}


class TestListTemplates:
    """Tests for GET /api/v1/projects/templates."""

    def test_list_templates_names_only(self):
        """Default returns list of template names."""
        response = client.get("/api/v1/projects/templates", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)
        assert "default" in data
        assert "company_analysis" in data

    def test_list_templates_with_details(self):
        """With details=true returns full template info."""
        response = client.get("/api/v1/projects/templates?details=true", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert "templates" in data
        assert "count" in data
        assert data["count"] > 0

        # Check structure of first template
        template = data["templates"][0]
        assert "name" in template
        assert "description" in template
        assert "field_groups" in template
        assert "entity_types" in template

    def test_list_templates_details_false_explicit(self):
        """With details=false returns list of names."""
        response = client.get("/api/v1/projects/templates?details=false", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert isinstance(data, list)


class TestGetTemplateDetails:
    """Tests for GET /api/v1/projects/templates/{name}."""

    def test_get_template_details_company_analysis(self):
        """Returns full details for company_analysis template."""
        response = client.get("/api/v1/projects/templates/company_analysis", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "company_analysis"
        assert "description" in data
        assert len(data["field_groups"]) > 0
        assert len(data["entity_types"]) > 0

        # Check field group structure
        fg = data["field_groups"][0]
        assert "name" in fg
        assert "fields" in fg
        assert len(fg["fields"]) > 0

        # Check field structure
        field = fg["fields"][0]
        assert "name" in field
        assert "field_type" in field

    def test_get_template_details_default(self):
        """Returns details for default template."""
        response = client.get("/api/v1/projects/templates/default", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "default"

    def test_get_template_details_not_found(self):
        """Returns 404 for unknown template."""
        response = client.get("/api/v1/projects/templates/nonexistent_template", headers=AUTH_HEADERS)
        assert response.status_code == 404

        data = response.json()
        assert "not found" in data["detail"].lower()
        assert "Available" in data["detail"]

    def test_get_template_details_research_survey(self):
        """Returns details for research_survey template."""
        response = client.get("/api/v1/projects/templates/research_survey", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "research_survey"
        assert len(data["field_groups"]) > 0

    def test_get_template_details_drivetrain(self):
        """Returns details for drivetrain template with multiple field groups."""
        response = client.get("/api/v1/projects/templates/drivetrain_company_analysis", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        assert data["name"] == "drivetrain_company_analysis"
        # Drivetrain has many field groups
        assert len(data["field_groups"]) >= 5

    def test_get_template_field_details(self):
        """Verify field-level details are returned correctly."""
        response = client.get("/api/v1/projects/templates/company_analysis", headers=AUTH_HEADERS)
        assert response.status_code == 200

        data = response.json()
        # Find a field with enum values
        for fg in data["field_groups"]:
            for field in fg["fields"]:
                if field.get("enum_values"):
                    assert isinstance(field["enum_values"], list)
                    assert len(field["enum_values"]) > 0
                    return
        # If no enum field found, that's okay - just checking structure
