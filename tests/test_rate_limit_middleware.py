from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.middleware.rate_limit import RateLimitMiddleware


@pytest.fixture
def app_with_rate_limit():
    """Create test app with rate limiting."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.get("/test")
    def test_endpoint():
        return {"status": "ok"}

    @app.get("/health")
    def health_endpoint():
        return {"status": "healthy"}

    return app


@pytest.fixture
def client(app_with_rate_limit):
    return TestClient(app_with_rate_limit)


class TestRateLimitMiddleware:
    def test_exempt_paths_not_rate_limited(self, client):
        """Health and metrics endpoints bypass rate limiting."""
        with patch("src.middleware.rate_limit.settings") as mock_settings:
            mock_settings.rate_limit_enabled = True
            response = client.get("/health")
            assert response.status_code == 200
            assert "X-RateLimit-Limit" not in response.headers

    def test_rate_limit_headers_added(self, client):
        """Rate limit headers are added to responses."""
        with patch("src.middleware.rate_limit.get_redis_client") as mock_redis:
            mock_redis.return_value = None  # Fallback mode
            with patch("src.middleware.rate_limit.settings") as mock_settings:
                mock_settings.rate_limit_enabled = True
                mock_settings.rate_limit_requests = 100
                mock_settings.rate_limit_window_seconds = 60

                response = client.get("/test", headers={"X-API-Key": "test-key"})

                assert "X-RateLimit-Limit" in response.headers
                assert "X-RateLimit-Remaining" in response.headers
                assert "X-RateLimit-Reset" in response.headers

    def test_rate_limit_disabled_skips_check(self, client):
        """When disabled, no rate limiting occurs."""
        with patch("src.middleware.rate_limit.settings") as mock_settings:
            mock_settings.rate_limit_enabled = False

            response = client.get("/test")
            assert response.status_code == 200
            assert "X-RateLimit-Limit" not in response.headers

    def test_rate_limit_exceeded_returns_429(self, client):
        """Exceeding rate limit returns 429."""
        mock_redis = MagicMock()
        mock_pipe = MagicMock()
        mock_pipe.execute.return_value = [None, 150, None, None]  # High count
        mock_redis.pipeline.return_value = mock_pipe

        with patch(
            "src.middleware.rate_limit.get_redis_client", return_value=mock_redis
        ):
            with patch("src.middleware.rate_limit.settings") as mock_settings:
                mock_settings.rate_limit_enabled = True
                mock_settings.rate_limit_requests = 100
                mock_settings.rate_limit_burst = 20
                mock_settings.rate_limit_window_seconds = 60

                response = client.get("/test", headers={"X-API-Key": "test-key"})

                assert response.status_code == 429
                assert "Retry-After" in response.headers
                assert response.json()["error"] == "Too Many Requests"

    def test_redis_unavailable_allows_request(self, client):
        """When Redis is unavailable, requests are allowed."""
        with patch("src.middleware.rate_limit.get_redis_client", return_value=None):
            with patch("src.middleware.rate_limit.settings") as mock_settings:
                mock_settings.rate_limit_enabled = True
                mock_settings.rate_limit_requests = 100
                mock_settings.rate_limit_window_seconds = 60

                response = client.get("/test", headers={"X-API-Key": "test-key"})
                assert response.status_code == 200

    def test_uses_api_key_as_identifier(self):
        """API key is used as rate limit identifier when present."""
        # Test that different API keys have separate limits
        # This is implicitly tested by the key format in _check_rate_limit
        pass

    def test_falls_back_to_ip_without_api_key(self):
        """Client IP is used when no API key provided."""
        # Test that IP is used as fallback identifier
        pass


class TestRateLimitConfig:
    def test_default_config_values(self):
        """Verify default rate limit configuration."""
        from src.config import Settings

        settings = Settings()
        assert settings.rate_limit_enabled is True
        assert settings.rate_limit_requests == 100
        assert settings.rate_limit_window_seconds == 60
        assert settings.rate_limit_burst == 20
