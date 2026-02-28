"""Tests for report API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from orm_models import Project, Report


@pytest.fixture
def test_project(db):
    """Create a real project in the transactional DB."""
    project = Project(
        name=f"test_report_project_{uuid4().hex[:8]}",
        extraction_schema={"name": "test", "fields": []},
    )
    db.add(project)
    db.flush()
    db.refresh(project)
    return project


@pytest.fixture
def test_report(db, test_project):
    """Create a real report in the transactional DB."""
    report = Report(
        project_id=test_project.id,
        type="single",
        title="Test Report",
        content="# Test Content",
        source_groups=["company-a"],
        extraction_ids=["ext-1", "ext-2"],
        categories=[],
        meta_data={"entity_count": 5},
    )
    db.add(report)
    db.flush()
    db.refresh(report)
    return report


class TestCreateReport:
    """Test POST /api/v1/projects/{project_id}/reports endpoint."""

    @patch("api.v1.reports.LLMClient")
    @patch("api.v1.reports.ReportService")
    def test_create_report_single_type(
        self, MockReportService, MockLLMClient, client: TestClient, valid_api_key: str, test_project, db
    ):
        """Test creating a single-source report."""
        # Mock LLMClient as async context manager
        mock_llm = AsyncMock()
        MockLLMClient.return_value.__aenter__ = AsyncMock(return_value=mock_llm)
        MockLLMClient.return_value.__aexit__ = AsyncMock(return_value=False)

        # Mock ReportService.generate
        mock_report = Mock(spec=Report)
        mock_report.id = uuid4()
        mock_report.type = "single"
        mock_report.title = "Test Report"
        mock_report.content = "# Test Content"
        mock_report.source_groups = ["company-a"]
        mock_report.extraction_ids = ["ext-1", "ext-2"]
        mock_report.meta_data = {"entity_count": 5}
        mock_report.created_at = datetime.now(UTC)

        mock_service = Mock()
        mock_service.generate = AsyncMock(return_value=mock_report)
        MockReportService.return_value = mock_service

        response = client.post(
            f"/api/v1/projects/{test_project.id}/reports",
            json={"type": "single", "source_groups": ["company-a"]},
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "single"
        assert data["source_groups"] == ["company-a"]

    @patch("api.v1.reports.LLMClient")
    @patch("api.v1.reports.ReportService")
    def test_create_report_comparison_type(
        self, MockReportService, MockLLMClient, client: TestClient, valid_api_key: str, test_project, db
    ):
        """Test creating a comparison report."""
        mock_llm = AsyncMock()
        MockLLMClient.return_value.__aenter__ = AsyncMock(return_value=mock_llm)
        MockLLMClient.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_report = Mock(spec=Report)
        mock_report.id = uuid4()
        mock_report.type = "comparison"
        mock_report.title = "Comparison Report"
        mock_report.content = "# Comparison"
        mock_report.source_groups = ["company-a", "company-b"]
        mock_report.extraction_ids = ["ext-1"]
        mock_report.meta_data = {"entity_count": 3}
        mock_report.created_at = datetime.now(UTC)

        mock_service = Mock()
        mock_service.generate = AsyncMock(return_value=mock_report)
        MockReportService.return_value = mock_service

        response = client.post(
            f"/api/v1/projects/{test_project.id}/reports",
            json={"type": "comparison", "source_groups": ["company-a", "company-b"]},
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["type"] == "comparison"
        assert len(data["source_groups"]) == 2

    def test_create_report_project_not_found(self, client: TestClient, valid_api_key: str):
        """Test creating report for non-existent project."""
        project_id = uuid4()
        response = client.post(
            f"/api/v1/projects/{project_id}/reports",
            json={"type": "single", "source_groups": ["company-a"]},
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 404


class TestListReports:
    """Test GET /api/v1/projects/{project_id}/reports endpoint."""

    def test_list_reports_returns_list(
        self, client: TestClient, valid_api_key: str, test_project, test_report, db
    ):
        """Test listing reports returns paginated list."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/reports",
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert "reports" in data
        assert "total" in data
        assert data["total"] >= 1


class TestGetReport:
    """Test GET /api/v1/projects/{project_id}/reports/{report_id} endpoint."""

    def test_get_report_returns_report(
        self, client: TestClient, valid_api_key: str, test_project, test_report, db
    ):
        """Test getting specific report."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/reports/{test_report.id}",
            headers={"X-API-Key": valid_api_key},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "single"
        assert data["title"] == "Test Report"

    def test_get_report_not_found(
        self, client: TestClient, valid_api_key: str, test_project, db
    ):
        """Test getting non-existent report."""
        report_id = uuid4()
        response = client.get(
            f"/api/v1/projects/{test_project.id}/reports/{report_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 404
