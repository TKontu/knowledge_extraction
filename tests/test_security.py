"""Tests for security configuration and middleware."""

import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError


class TestAPIKeyValidation:
    def test_empty_api_key_raises_error(self):
        """Empty API key should fail validation."""
        from config import Settings

        with pytest.raises(ValidationError) as exc_info:
            Settings(api_key="")

        assert "API_KEY" in str(exc_info.value)

    def test_insecure_api_key_raises_error(self):
        """Known insecure API keys should fail."""
        from config import Settings

        insecure_keys = ["dev", "test", "changeme", "dev-key-change-in-production"]

        for key in insecure_keys:
            with pytest.raises(ValidationError):
                Settings(api_key=key)

    def test_short_api_key_raises_error(self):
        """API key shorter than 16 chars should fail."""
        from config import Settings

        with pytest.raises(ValidationError):
            Settings(api_key="short")

    def test_valid_api_key_passes(self):
        """Valid API key should pass."""
        from config import Settings

        settings = Settings(api_key="a" * 32)
        assert settings.api_key == "a" * 32


class TestSecurityHeaders:
    def test_security_headers_added(self, client):
        """All responses should have security headers."""
        response = client.get("/health")

        assert response.headers.get("X-Frame-Options") == "DENY"
        assert response.headers.get("X-Content-Type-Options") == "nosniff"
        assert "X-XSS-Protection" in response.headers


class TestHTTPSRedirect:
    def test_no_redirect_when_disabled(self, client):
        """Should not redirect when HTTPS enforcement disabled."""
        with patch("config.settings.enforce_https", False):
            response = client.get("/health")
            assert response.status_code == 200

    def test_redirect_when_enabled_and_http(self, client):
        """Should redirect HTTP to HTTPS when enforcement enabled."""
        with patch("config.settings.enforce_https", True):
            with patch("config.settings.https_redirect_host", "secure.example.com"):
                response = client.get(
                    "/health",
                    headers={"X-Forwarded-Proto": "http"},
                    follow_redirects=False,
                )
                assert response.status_code == 301
                assert "https://" in response.headers.get("location", "")


class TestSecurityDocumentation:
    def test_security_docs_exist(self):
        """Security documentation should exist."""
        from pathlib import Path

        docs_path = Path(__file__).parent.parent / "docs" / "SECURITY.md"
        assert docs_path.exists(), "docs/SECURITY.md should exist"

    def test_security_docs_has_required_sections(self):
        """Security docs should have key sections."""
        from pathlib import Path

        docs_path = Path(__file__).parent.parent / "docs" / "SECURITY.md"
        content = docs_path.read_text()

        required_sections = [
            "Authentication",
            "API Key",
            "HTTPS",
            "Rate Limiting",
            "Best Practices",
        ]

        for section in required_sections:
            assert section in content, f"Missing section: {section}"
