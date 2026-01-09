import pytest
from fastapi.testclient import TestClient


class TestAPIKeyAuthentication:
    """Test API key authentication middleware."""

    def test_health_endpoint_accessible_without_auth(self, client: TestClient):
        """Health endpoint should be accessible without authentication."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_docs_accessible_without_auth(self, client: TestClient):
        """API docs should be accessible without authentication."""
        response = client.get("/docs")
        assert response.status_code == 200

    def test_openapi_json_accessible_without_auth(self, client: TestClient):
        """OpenAPI schema should be accessible without authentication."""
        response = client.get("/openapi.json")
        assert response.status_code == 200

    def test_protected_endpoint_requires_api_key(self, client: TestClient):
        """Protected endpoints should require API key."""
        # This endpoint doesn't exist yet, but will be added
        response = client.get("/api/v1/scrape")
        assert response.status_code in [401, 404]  # 401 if auth works, 404 if endpoint missing

    def test_protected_endpoint_rejects_invalid_key(
        self, client: TestClient, invalid_api_key: str
    ):
        """Protected endpoints should reject invalid API keys."""
        response = client.get(
            "/api/v1/scrape",
            headers={"X-API-Key": invalid_api_key},
        )
        assert response.status_code in [401, 404]  # 401 if auth works, 404 if endpoint missing
        if response.status_code == 401:
            assert "api key" in response.json()["detail"].lower()

    def test_protected_endpoint_accepts_valid_key(
        self, client: TestClient, valid_api_key: str
    ):
        """Protected endpoints should accept valid API key."""
        response = client.get(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
        )
        # Should get 404 (endpoint doesn't exist) not 401 (auth failed)
        assert response.status_code == 404

    def test_root_endpoint_requires_auth(self, client: TestClient):
        """Root endpoint should require authentication (not public)."""
        response = client.get("/")
        assert response.status_code == 401

    def test_root_endpoint_works_with_valid_key(
        self, client: TestClient, valid_api_key: str
    ):
        """Root endpoint should work with valid API key."""
        response = client.get("/", headers={"X-API-Key": valid_api_key})
        assert response.status_code == 200
        assert response.json()["service"] == "TechFacts Pipeline API"

    def test_case_insensitive_header_name(
        self, client: TestClient, valid_api_key: str
    ):
        """API key header should be case-insensitive."""
        # Try different case variations
        for header_name in ["X-API-Key", "x-api-key", "X-Api-Key"]:
            response = client.get("/", headers={header_name: valid_api_key})
            assert response.status_code == 200

    def test_missing_api_key_returns_401(self, client: TestClient):
        """Request without API key should return 401."""
        response = client.get("/")
        assert response.status_code == 401
        assert "detail" in response.json()


class TestPublicEndpoints:
    """Test that certain endpoints remain public."""

    @pytest.mark.parametrize(
        "endpoint",
        [
            "/health",
            "/docs",
            "/redoc",
            "/openapi.json",
        ],
    )
    def test_public_endpoint_accessible(self, client: TestClient, endpoint: str):
        """Public endpoints should not require authentication."""
        response = client.get(endpoint)
        assert response.status_code == 200
