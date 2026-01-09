import pytest
from fastapi.testclient import TestClient


class TestScrapeEndpoint:
    """Test POST /api/v1/scrape endpoint."""

    def test_scrape_endpoint_requires_authentication(self, client: TestClient):
        """Scrape endpoint should require API key."""
        response = client.post(
            "/api/v1/scrape",
            json={"urls": ["https://example.com"], "company": "Example Inc"},
        )
        assert response.status_code == 401

    def test_scrape_endpoint_accepts_valid_request(
        self, client: TestClient, valid_api_key: str
    ):
        """Should accept valid scrape request and return 202 Accepted."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com/docs"],
                "company": "Example Inc",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert data["status"] == "queued"

    def test_scrape_endpoint_returns_valid_job_id(
        self, client: TestClient, valid_api_key: str
    ):
        """Job ID should be a valid UUID format."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
            },
        )
        assert response.status_code == 202
        data = response.json()
        job_id = data["job_id"]
        # Basic UUID format check (8-4-4-4-12 hex chars)
        assert isinstance(job_id, str)
        assert len(job_id) == 36
        assert job_id.count("-") == 4

    def test_scrape_endpoint_accepts_optional_profile(
        self, client: TestClient, valid_api_key: str
    ):
        """Should accept optional profile parameter."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "profile": "api_docs",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data

    def test_scrape_endpoint_validates_urls_required(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if urls field is missing."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={"company": "TestCo"},
        )
        assert response.status_code == 422

    def test_scrape_endpoint_validates_company_required(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if company field is missing."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={"urls": ["https://example.com"]},
        )
        assert response.status_code == 422

    def test_scrape_endpoint_validates_urls_is_list(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if urls is not a list."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": "https://example.com",  # String instead of list
                "company": "TestCo",
            },
        )
        assert response.status_code == 422

    def test_scrape_endpoint_validates_urls_not_empty(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if urls list is empty."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": [],
                "company": "TestCo",
            },
        )
        assert response.status_code == 422

    def test_scrape_endpoint_accepts_multiple_urls(
        self, client: TestClient, valid_api_key: str
    ):
        """Should accept multiple URLs in request."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": [
                    "https://example.com/docs",
                    "https://example.com/api",
                    "https://example.com/guides",
                ],
                "company": "TestCo",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "url_count" in data
        assert data["url_count"] == 3

    def test_scrape_endpoint_response_includes_metadata(
        self, client: TestClient, valid_api_key: str
    ):
        """Response should include useful metadata."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "profile": "technical_specs",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "url_count" in data
        assert "company" in data
        assert data["company"] == "TestCo"
        assert data["url_count"] == 1
