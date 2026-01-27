"""Tests for database connection and health check integration."""

from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient


class TestDatabaseConnection:
    """Test database connection functionality."""

    def test_database_module_exists(self):
        """Database module should be importable."""
        from database import engine, get_db

        assert engine is not None
        assert get_db is not None

    def test_database_can_check_connectivity(self):
        """Database should provide connectivity check function."""
        from database import check_database_connection

        # Should be callable
        assert callable(check_database_connection)

    @patch("database.engine.connect")
    def test_database_connectivity_check_success(self, mock_connect):
        """Database connectivity check should return True when connection succeeds."""
        from database import check_database_connection

        mock_conn = MagicMock()
        mock_connect.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_connect.return_value.__exit__ = MagicMock(return_value=None)

        result = check_database_connection()
        assert result is True
        mock_connect.assert_called_once()

    @patch("database.engine.connect")
    def test_database_connectivity_check_failure(self, mock_connect):
        """Database connectivity check should return False when connection fails."""
        from database import check_database_connection

        mock_connect.side_effect = Exception("Connection failed")

        result = check_database_connection()
        assert result is False


class TestHealthCheckWithDatabase:
    """Test health check endpoint includes database status."""

    @patch("main.check_database_connection")
    def test_health_check_includes_database_status(
        self, mock_db_check, client: TestClient
    ):
        """Health check should include database connection status."""
        mock_db_check.return_value = True

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "database" in data
        assert data["database"]["connected"] is True

    @patch("main.check_database_connection")
    def test_health_check_shows_db_disconnected(
        self, mock_db_check, client: TestClient
    ):
        """Health check should show database disconnected when check fails."""
        mock_db_check.return_value = False

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert "database" in data
        assert data["database"]["connected"] is False

    @patch("main.check_database_connection")
    def test_health_check_still_returns_200_when_db_down(
        self, mock_db_check, client: TestClient
    ):
        """Health check should return 200 even if database is down (graceful degradation)."""
        mock_db_check.return_value = False

        response = client.get("/health")

        # Still returns 200, but shows db as disconnected
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @patch("main.check_database_connection")
    def test_health_check_handles_db_check_exception(
        self, mock_db_check, client: TestClient
    ):
        """Health check should handle exceptions from database check gracefully."""
        mock_db_check.side_effect = Exception("Unexpected error")

        response = client.get("/health")

        # Should still return 200 and show db as disconnected
        assert response.status_code == 200
        data = response.json()
        assert data["database"]["connected"] is False
