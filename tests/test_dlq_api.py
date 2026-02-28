"""Tests for DLQ API endpoints."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from main import app
from services.dlq.service import DLQItem


@pytest.fixture
def auth_headers(valid_api_key):
    """Return authentication headers with valid API key."""
    return {"X-API-Key": valid_api_key}


@pytest.fixture
def mock_dlq_service():
    """Create a mock DLQService."""
    service = AsyncMock()
    service.get_dlq_stats = AsyncMock()
    service.get_scrape_dlq = AsyncMock()
    service.get_extraction_dlq = AsyncMock()
    service.pop_scrape_item = AsyncMock()
    service.pop_extraction_item = AsyncMock()
    return service


@pytest.fixture(autouse=True)
def override_dlq_service(mock_dlq_service):
    """Override the DLQ service dependency for all tests."""
    from api.dependencies import get_dlq_service

    async def override():
        return mock_dlq_service

    app.dependency_overrides[get_dlq_service] = override
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def dlq_client():
    """Create test client for DLQ tests."""
    return TestClient(app)


class TestDLQAPI:
    """Test DLQ API endpoints."""

    def test_get_dlq_stats(self, dlq_client, mock_dlq_service, auth_headers):
        """Test GET /api/v1/dlq/stats."""
        mock_dlq_service.get_dlq_stats.return_value = {"scrape": 5, "extraction": 3}

        response = dlq_client.get("/api/v1/dlq/stats", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert data["scrape"] == 5
        assert data["extraction"] == 3

    def test_list_scrape_dlq(self, dlq_client, mock_dlq_service, auth_headers):
        """Test GET /api/v1/dlq/scrape."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        dlq_item = DLQItem(
            id=item_id,
            source_id=source_id,
            job_id=None,
            error="Connection timeout",
            failed_at=failed_at,
            retry_count=0,
            dlq_type="scrape",
        )

        mock_dlq_service.get_scrape_dlq.return_value = [dlq_item]

        response = dlq_client.get("/api/v1/dlq/scrape", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == item_id
        assert data[0]["source_id"] == source_id
        assert data[0]["error"] == "Connection timeout"
        assert data[0]["dlq_type"] == "scrape"

    def test_list_scrape_dlq_with_limit(
        self, dlq_client, mock_dlq_service, auth_headers
    ):
        """Test GET /api/v1/dlq/scrape with limit parameter."""
        mock_dlq_service.get_scrape_dlq.return_value = []

        response = dlq_client.get("/api/v1/dlq/scrape?limit=50", headers=auth_headers)

        assert response.status_code == 200
        # Verify the limit was passed to the service
        mock_dlq_service.get_scrape_dlq.assert_called_once_with(limit=50)

    def test_list_extraction_dlq(self, dlq_client, mock_dlq_service, auth_headers):
        """Test GET /api/v1/dlq/extraction."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        job_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        dlq_item = DLQItem(
            id=item_id,
            source_id=source_id,
            job_id=job_id,
            error="LLM timeout",
            failed_at=failed_at,
            retry_count=2,
            dlq_type="extraction",
        )

        mock_dlq_service.get_extraction_dlq.return_value = [dlq_item]

        response = dlq_client.get("/api/v1/dlq/extraction", headers=auth_headers)

        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["id"] == item_id
        assert data[0]["job_id"] == job_id
        assert data[0]["retry_count"] == 2
        assert data[0]["dlq_type"] == "extraction"

    def test_retry_scrape_item(self, dlq_client, mock_dlq_service, auth_headers):
        """Test POST /api/v1/dlq/scrape/{item_id}/retry."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        dlq_item = DLQItem(
            id=item_id,
            source_id=source_id,
            job_id=None,
            error="Timeout",
            failed_at=failed_at,
            retry_count=0,
            dlq_type="scrape",
        )

        mock_dlq_service.pop_scrape_item.return_value = dlq_item

        response = dlq_client.post(
            f"/api/v1/dlq/scrape/{item_id}/retry", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == item_id
        assert data["source_id"] == source_id
        mock_dlq_service.pop_scrape_item.assert_called_once_with(item_id)

    def test_retry_scrape_item_not_found(
        self, dlq_client, mock_dlq_service, auth_headers
    ):
        """Test POST /api/v1/dlq/scrape/{item_id}/retry with non-existent item."""
        item_id = str(uuid4())
        mock_dlq_service.pop_scrape_item.return_value = None

        response = dlq_client.post(
            f"/api/v1/dlq/scrape/{item_id}/retry", headers=auth_headers
        )

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()

    def test_retry_extraction_item(self, dlq_client, mock_dlq_service, auth_headers):
        """Test POST /api/v1/dlq/extraction/{item_id}/retry."""
        item_id = str(uuid4())
        source_id = str(uuid4())
        failed_at = datetime.now(UTC).isoformat()

        dlq_item = DLQItem(
            id=item_id,
            source_id=source_id,
            job_id=None,
            error="Parse failed",
            failed_at=failed_at,
            retry_count=1,
            dlq_type="extraction",
        )

        mock_dlq_service.pop_extraction_item.return_value = dlq_item

        response = dlq_client.post(
            f"/api/v1/dlq/extraction/{item_id}/retry", headers=auth_headers
        )

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == item_id
        assert data["retry_count"] == 1
        mock_dlq_service.pop_extraction_item.assert_called_once_with(item_id)

    def test_retry_extraction_item_not_found(
        self, dlq_client, mock_dlq_service, auth_headers
    ):
        """Test POST /api/v1/dlq/extraction/{item_id}/retry with non-existent item."""
        item_id = str(uuid4())
        mock_dlq_service.pop_extraction_item.return_value = None

        response = dlq_client.post(
            f"/api/v1/dlq/extraction/{item_id}/retry", headers=auth_headers
        )

        assert response.status_code == 404
