"""Tests for Pydantic models."""

import pytest
from pydantic import ValidationError

from models import (
    ProjectCreate,
    ReportJobResponse,
    ReportRequest,
    ReportResponse,
    ReportType,
)


class TestReportRequest:
    """Test ReportRequest validation."""

    def test_report_request_validates_source_groups(self):
        """Test that source_groups must have at least one item."""
        # Valid case
        request = ReportRequest(
            type=ReportType.SINGLE,
            source_groups=["company-a"],
        )
        assert request.source_groups == ["company-a"]

        # Invalid case - empty list
        with pytest.raises(ValidationError) as exc_info:
            ReportRequest(
                type=ReportType.SINGLE,
                source_groups=[],
            )
        assert "source_groups" in str(exc_info.value)

    def test_report_request_comparison_needs_multiple(self):
        """Test that comparison reports require at least 2 source_groups."""
        # Valid case - 2 source groups
        request = ReportRequest(
            type=ReportType.COMPARISON,
            source_groups=["company-a", "company-b"],
        )
        assert len(request.source_groups) == 2

        # Invalid case - only 1 source group
        with pytest.raises(ValidationError) as exc_info:
            ReportRequest(
                type=ReportType.COMPARISON,
                source_groups=["company-a"],
            )
        assert "at least 2 source_groups" in str(exc_info.value)

    def test_report_request_single_allows_one_group(self):
        """Test that single report can have just one source_group."""
        request = ReportRequest(
            type=ReportType.SINGLE,
            source_groups=["company-a"],
        )
        assert request.type == ReportType.SINGLE
        assert request.source_groups == ["company-a"]

    def test_report_request_defaults(self):
        """Test default values for optional fields."""
        request = ReportRequest(
            type=ReportType.SINGLE,
            source_groups=["company-a"],
        )
        assert request.entity_types is None
        assert request.categories is None
        assert request.title is None
        assert request.max_extractions == 50

    def test_report_request_max_extractions_constraints(self):
        """Test max_extractions field constraints."""
        # Valid
        request = ReportRequest(
            type=ReportType.SINGLE,
            source_groups=["company-a"],
            max_extractions=100,
        )
        assert request.max_extractions == 100

        # Too low
        with pytest.raises(ValidationError):
            ReportRequest(
                type=ReportType.SINGLE,
                source_groups=["company-a"],
                max_extractions=0,
            )

        # Too high
        with pytest.raises(ValidationError):
            ReportRequest(
                type=ReportType.SINGLE,
                source_groups=["company-a"],
                max_extractions=201,
            )


class TestReportResponse:
    """Test ReportResponse serialization."""

    def test_report_response_serialization(self):
        """Test that ReportResponse serializes correctly."""
        response = ReportResponse(
            id="123e4567-e89b-12d3-a456-426614174000",
            type="single",
            title="Test Report",
            content="# Test Content",
            source_groups=["company-a"],
            extraction_count=25,
            entity_count=10,
            generated_at="2024-01-11T10:00:00Z",
        )

        assert response.id == "123e4567-e89b-12d3-a456-426614174000"
        assert response.type == "single"
        assert response.title == "Test Report"
        assert response.content == "# Test Content"
        assert response.source_groups == ["company-a"]
        assert response.extraction_count == 25
        assert response.entity_count == 10
        assert response.generated_at == "2024-01-11T10:00:00Z"

    def test_report_response_to_dict(self):
        """Test that ReportResponse can be serialized to dict."""
        response = ReportResponse(
            id="123",
            type="comparison",
            title="Comparison Report",
            content="Content",
            source_groups=["a", "b"],
            extraction_count=50,
            entity_count=20,
            generated_at="2024-01-11T10:00:00Z",
        )

        data = response.model_dump()
        assert data["id"] == "123"
        assert data["type"] == "comparison"
        assert data["source_groups"] == ["a", "b"]


class TestReportJobResponse:
    """Test ReportJobResponse."""

    def test_report_job_response_fields(self):
        """Test ReportJobResponse field structure."""
        job_response = ReportJobResponse(
            job_id="job-123",
            status="completed",
            report_id="report-456",
        )

        assert job_response.job_id == "job-123"
        assert job_response.status == "completed"
        assert job_response.report_id == "report-456"

    def test_report_job_response_optional_report_id(self):
        """Test that report_id is optional."""
        job_response = ReportJobResponse(
            job_id="job-123",
            status="pending",
        )

        assert job_response.job_id == "job-123"
        assert job_response.status == "pending"
        assert job_response.report_id is None


class TestReportType:
    """Test ReportType enum."""

    def test_report_type_values(self):
        """Test ReportType enum has correct values."""
        assert ReportType.SINGLE.value == "single"
        assert ReportType.COMPARISON.value == "comparison"


class TestProjectCreate:
    """Test ProjectCreate model with default schema."""

    def test_project_create_without_schema_gets_default(self):
        """None → default schema."""
        project = ProjectCreate(name="test_project")

        assert project.extraction_schema is not None
        assert project.extraction_schema.get("name") == "generic_facts"

    def test_project_create_with_empty_dict_gets_default(self):
        """{} → default schema."""
        project = ProjectCreate(name="test_project", extraction_schema={})

        assert project.extraction_schema is not None
        assert project.extraction_schema.get("name") == "generic_facts"

    def test_project_create_with_schema_keeps_it(self):
        """Custom schema preserved."""
        custom_schema = {
            "name": "custom",
            "field_groups": [
                {
                    "name": "test_group",
                    "description": "Test",
                    "fields": [
                        {
                            "name": "test_field",
                            "field_type": "text",
                            "description": "Test field",
                        }
                    ],
                }
            ],
        }
        project = ProjectCreate(name="test_project", extraction_schema=custom_schema)

        assert project.extraction_schema is not None
        assert project.extraction_schema.get("name") == "custom"
        assert project.extraction_schema != {}

    def test_default_schema_has_expected_name(self):
        """Schema name is 'generic_facts'."""
        project = ProjectCreate(name="test_project")

        assert project.extraction_schema.get("name") == "generic_facts"

    def test_default_schema_has_field_groups(self):
        """Default schema has field_groups."""
        project = ProjectCreate(name="test_project")

        assert "field_groups" in project.extraction_schema
        assert len(project.extraction_schema["field_groups"]) == 3
