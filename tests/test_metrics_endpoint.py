"""Tests for metrics API endpoint."""

import pytest
from fastapi.testclient import TestClient


class TestMetricsEndpoint:
    """Tests for GET /metrics endpoint."""

    def test_metrics_endpoint_returns_text(self, client: TestClient) -> None:
        """Test that metrics endpoint returns text response."""
        response = client.get("/metrics")

        assert response.status_code == 200
        # Should be plain text, not JSON
        assert response.headers["content-type"] == "text/plain; charset=utf-8"

    def test_metrics_endpoint_content_type(self, client: TestClient) -> None:
        """Test that metrics endpoint has correct content type."""
        response = client.get("/metrics")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    def test_metrics_endpoint_valid_prometheus_format(self, client: TestClient) -> None:
        """Test that metrics endpoint returns valid Prometheus format."""
        response = client.get("/metrics")

        assert response.status_code == 200
        text = response.text

        # Check for required HELP lines
        assert "# HELP scristill_jobs_total" in text
        assert "# HELP scristill_sources_total" in text
        assert "# HELP scristill_extractions_total" in text
        assert "# HELP scristill_entities_total" in text

        # Check for required TYPE lines
        assert "# TYPE scristill_jobs_total gauge" in text
        assert "# TYPE scristill_sources_total gauge" in text

        # Check for metric values
        assert "scristill_jobs_total" in text
        assert "scristill_sources_total" in text
        assert "scristill_extractions_total" in text
        assert "scristill_entities_total" in text

    def test_metrics_endpoint_no_auth_required(self, client: TestClient) -> None:
        """Test that metrics endpoint doesn't require authentication."""
        # Make request without API key
        response = client.get("/metrics")

        # Should succeed without auth
        assert response.status_code == 200
