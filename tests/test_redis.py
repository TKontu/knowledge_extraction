"""Tests for Redis connection and health check integration."""

from unittest.mock import patch

from fastapi.testclient import TestClient


class TestRedisConnection:
    """Test Redis connection functionality."""

    def test_redis_module_exists(self):
        """Redis module should be importable."""
        from redis_client import get_redis, redis_client

        assert redis_client is not None
        assert get_redis is not None

    def test_redis_can_check_connectivity(self):
        """Redis should provide connectivity check function."""
        from redis_client import check_redis_connection

        # Should be callable
        assert callable(check_redis_connection)

    @patch("redis_client.redis_client.ping")
    def test_redis_connectivity_check_success(self, mock_ping):
        """Redis connectivity check should return True when connection succeeds."""
        from redis_client import check_redis_connection

        mock_ping.return_value = True

        result = check_redis_connection()
        assert result is True
        mock_ping.assert_called_once()

    @patch("redis_client.redis_client.ping")
    def test_redis_connectivity_check_failure(self, mock_ping):
        """Redis connectivity check should return False when connection fails."""
        from redis_client import check_redis_connection

        mock_ping.side_effect = Exception("Connection failed")

        result = check_redis_connection()
        assert result is False


class TestHealthCheckWithRedis:
    """Test health check endpoint includes Redis status."""

    @patch("main.check_redis_connection")
    def test_health_check_includes_redis_status(
        self, mock_redis_check, client: TestClient
    ):
        """Health check should include Redis connection status."""
        mock_redis_check.return_value = True

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "redis" in data
        assert data["redis"]["connected"] is True

    @patch("main.check_redis_connection")
    def test_health_check_shows_redis_disconnected(
        self, mock_redis_check, client: TestClient
    ):
        """Health check should show Redis disconnected when check fails."""
        mock_redis_check.return_value = False

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "redis" in data
        assert data["redis"]["connected"] is False

    @patch("main.check_redis_connection")
    def test_health_check_still_returns_200_when_redis_down(
        self, mock_redis_check, client: TestClient
    ):
        """Health check should return 200 even if Redis is down (graceful degradation)."""
        mock_redis_check.return_value = False

        response = client.get("/health")

        # Still returns 200, but shows redis as disconnected
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @patch("main.check_redis_connection")
    def test_health_check_handles_redis_check_exception(
        self, mock_redis_check, client: TestClient
    ):
        """Health check should handle exceptions from Redis check gracefully."""
        mock_redis_check.side_effect = Exception("Unexpected error")

        response = client.get("/health")

        # Should still return 200 and show redis as disconnected
        assert response.status_code == 200
        data = response.json()
        assert data["redis"]["connected"] is False
