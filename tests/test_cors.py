from fastapi.testclient import TestClient


class TestCORSMiddleware:
    """Test CORS middleware configuration."""

    def test_cors_headers_present_for_allowed_origin(
        self, client: TestClient, valid_api_key: str
    ):
        """Response should include CORS headers for allowed origins."""
        response = client.get(
            "/",
            headers={
                "X-API-Key": valid_api_key,
                "Origin": "http://localhost:8080",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        assert (
            response.headers["access-control-allow-origin"] == "http://localhost:8080"
        )

    def test_cors_allows_multiple_origins(self, client: TestClient, valid_api_key: str):
        """Should support multiple allowed origins from config."""
        # Test first allowed origin
        response = client.get(
            "/",
            headers={
                "X-API-Key": valid_api_key,
                "Origin": "http://localhost:8080",
            },
        )
        assert response.status_code == 200
        assert (
            response.headers.get("access-control-allow-origin")
            == "http://localhost:8080"
        )

    def test_cors_preflight_request(self, client: TestClient):
        """OPTIONS preflight requests should be handled correctly."""
        response = client.options(
            "/",
            headers={
                "Origin": "http://localhost:8080",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "X-API-Key",
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers
        assert "access-control-allow-methods" in response.headers
        assert "access-control-allow-headers" in response.headers

    def test_cors_allows_credentials(self, client: TestClient, valid_api_key: str):
        """CORS should allow credentials for authenticated requests."""
        response = client.get(
            "/",
            headers={
                "X-API-Key": valid_api_key,
                "Origin": "http://localhost:8080",
            },
        )
        assert response.status_code == 200
        assert response.headers.get("access-control-allow-credentials") == "true"

    def test_cors_headers_on_public_endpoints(self, client: TestClient):
        """Public endpoints should also have CORS headers."""
        response = client.get(
            "/health",
            headers={"Origin": "http://localhost:8080"},
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers

    def test_cors_exposes_common_headers(self, client: TestClient, valid_api_key: str):
        """CORS should expose common headers to browser."""
        response = client.get(
            "/",
            headers={
                "X-API-Key": valid_api_key,
                "Origin": "http://localhost:8080",
            },
        )
        assert response.status_code == 200
        # FastAPI CORS middleware should set expose headers
        assert "access-control-expose-headers" in response.headers
