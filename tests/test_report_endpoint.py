"""Tests for report API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest
from httpx import AsyncClient, ASGITransport

from main import app
from models import ReportType
from orm_models import Report


@pytest.fixture
def client() -> AsyncClient:
    """Create test client."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def auth_headers(valid_api_key):
    """Return authentication headers with valid API key."""
    return {"X-API-Key": valid_api_key}


@pytest.fixture
def mock_project():
    """Mock project for testing."""
    project = Mock()
    project.id = uuid4()
    project.name = "Test Project"
    return project


@pytest.fixture
def mock_report():
    """Mock report for testing."""
    report = Mock(spec=Report)
    report.id = uuid4()
    report.type = "single"
    report.title = "Test Report"
    report.content = "# Test Content"
    report.source_groups = ["company-a"]
    report.extraction_ids = []
    report.categories = []
    report.created_at = datetime.now(UTC)
    return report


class TestCreateReport:
    """Test POST /api/v1/projects/{project_id}/reports endpoint."""

    @pytest.mark.asyncio
    async def test_create_report_single_type(self, client, mock_project, mock_report, auth_headers):
        """Test creating a single-source report."""
        with patch("api.v1.reports.ProjectRepository") as MockProjectRepo, patch(
            "api.v1.reports.ReportService"
        ) as MockReportService:
            # Setup mocks
            mock_proj_repo = Mock()
            mock_proj_repo.get = AsyncMock(return_value=mock_project)
            MockProjectRepo.return_value = mock_proj_repo

            mock_service = Mock()
            mock_service.generate = AsyncMock(return_value=mock_report)
            MockReportService.return_value = mock_service

            # Make request
            response = await client.post(
                f"/api/v1/projects/{mock_project.id}/reports",
                json={
                    "type": "single",
                    "source_groups": ["company-a"],
                },
                headers=auth_headers,
            )

            # Verify
            assert response.status_code == 201
            data = response.json()
            assert data["type"] == "single"
            assert data["source_groups"] == ["company-a"]

    @pytest.mark.asyncio
    async def test_create_report_comparison_type(self, client, mock_project, mock_report, auth_headers):
        """Test creating a comparison report."""
        mock_report.type = "comparison"
        mock_report.source_groups = ["company-a", "company-b"]

        with patch("api.v1.reports.ProjectRepository") as MockProjectRepo, patch(
            "api.v1.reports.ReportService"
        ) as MockReportService:
            # Setup mocks
            mock_proj_repo = Mock()
            mock_proj_repo.get = AsyncMock(return_value=mock_project)
            MockProjectRepo.return_value = mock_proj_repo

            mock_service = Mock()
            mock_service.generate = AsyncMock(return_value=mock_report)
            MockReportService.return_value = mock_service

            # Make request
            response = await client.post(
                f"/api/v1/projects/{mock_project.id}/reports",
                json={
                    "type": "comparison",
                    "source_groups": ["company-a", "company-b"],
                },
                headers=auth_headers,
            )

            # Verify
            assert response.status_code == 201
            data = response.json()
            assert data["type"] == "comparison"
            assert len(data["source_groups"]) == 2

    @pytest.mark.asyncio
    async def test_create_report_project_not_found(self, client, auth_headers):
        """Test creating report for non-existent project."""
        with patch("api.v1.reports.ProjectRepository") as MockProjectRepo:
            # Setup mocks
            mock_proj_repo = Mock()
            mock_proj_repo.get = AsyncMock(return_value=None)
            MockProjectRepo.return_value = mock_proj_repo

            # Make request
            project_id = uuid4()
            response = await client.post(
                f"/api/v1/projects/{project_id}/reports",
                json={
                    "type": "single",
                    "source_groups": ["company-a"],
                },
                headers=auth_headers,
            )

            # Verify
            assert response.status_code == 404


class TestListReports:
    """Test GET /api/v1/projects/{project_id}/reports endpoint."""

    @pytest.mark.asyncio
    async def test_list_reports_returns_list(self, client, mock_project, mock_report, auth_headers):
        """Test listing reports returns paginated list."""
        with patch("api.v1.reports.ProjectRepository") as MockProjectRepo, patch(
            "api.v1.reports.get_db"
        ) as mock_get_db:
            # Setup mocks
            mock_proj_repo = Mock()
            mock_proj_repo.get = AsyncMock(return_value=mock_project)
            MockProjectRepo.return_value = mock_proj_repo

            mock_db = Mock()
            mock_query = Mock()
            mock_query.filter.return_value = mock_query
            mock_query.order_by.return_value = mock_query
            mock_query.limit.return_value = mock_query
            mock_query.offset.return_value = mock_query
            mock_query.all.return_value = [mock_report]
            mock_query.count.return_value = 1
            mock_db.query.return_value = mock_query
            mock_get_db.return_value = mock_db

            # Make request
            response = await client.get(f"/api/v1/projects/{mock_project.id}/reports", headers=auth_headers)

            # Verify
            assert response.status_code == 200
            data = response.json()
            assert "reports" in data
            assert "total" in data


class TestGetReport:
    """Test GET /api/v1/projects/{project_id}/reports/{report_id} endpoint."""

    @pytest.mark.asyncio
    async def test_get_report_returns_report(self, client, mock_project, mock_report, auth_headers):
        """Test getting specific report."""
        with patch("api.v1.reports.ProjectRepository") as MockProjectRepo, patch(
            "api.v1.reports.get_db"
        ) as mock_get_db:
            # Setup mocks
            mock_proj_repo = Mock()
            mock_proj_repo.get = AsyncMock(return_value=mock_project)
            MockProjectRepo.return_value = mock_proj_repo

            mock_db = Mock()
            mock_query = Mock()
            mock_query.filter.return_value = mock_query
            mock_query.first.return_value = mock_report
            mock_db.query.return_value = mock_query
            mock_get_db.return_value = mock_db

            # Make request
            response = await client.get(
                f"/api/v1/projects/{mock_project.id}/reports/{mock_report.id}",
                headers=auth_headers,
            )

            # Verify
            assert response.status_code == 200
            data = response.json()
            assert data["type"] == "single"
            assert data["title"] == "Test Report"

    @pytest.mark.asyncio
    async def test_get_report_not_found(self, client, mock_project, auth_headers):
        """Test getting non-existent report."""
        with patch("api.v1.reports.ProjectRepository") as MockProjectRepo, patch(
            "api.v1.reports.get_db"
        ) as mock_get_db:
            # Setup mocks
            mock_proj_repo = Mock()
            mock_proj_repo.get = AsyncMock(return_value=mock_project)
            MockProjectRepo.return_value = mock_proj_repo

            mock_db = Mock()
            mock_query = Mock()
            mock_query.filter.return_value = mock_query
            mock_query.first.return_value = None
            mock_db.query.return_value = mock_query
            mock_get_db.return_value = mock_db

            # Make request
            report_id = uuid4()
            response = await client.get(
                f"/api/v1/projects/{mock_project.id}/reports/{report_id}",
                headers=auth_headers,
            )

            # Verify
            assert response.status_code == 404
