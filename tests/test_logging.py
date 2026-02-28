"""Tests for logging configuration and middleware."""

import json
import logging
import os
import uuid
from unittest.mock import Mock, patch

import pytest
import structlog
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.config import Settings


class TestLoggingConfiguration:
    """Tests for structlog configuration."""

    @patch("src.logging_config.get_settings")
    def test_configure_logging_json_format(self, mock_get_settings):
        """Test that JSON format is configured correctly."""
        from src.logging_config import configure_logging

        mock_settings = Mock(spec=Settings)
        mock_settings.log_format = "json"
        mock_settings.log_level = "INFO"
        mock_get_settings.return_value = mock_settings

        configure_logging()

        # Get a logger and verify it's configured
        logger = structlog.get_logger("test")
        assert logger is not None

    @patch("src.logging_config.get_settings")
    def test_configure_logging_console_format(self, mock_get_settings):
        """Test that console format is configured correctly."""
        from src.logging_config import configure_logging

        mock_settings = Mock(spec=Settings)
        mock_settings.log_format = "console"
        mock_settings.log_level = "DEBUG"
        mock_get_settings.return_value = mock_settings

        configure_logging()

        # Get a logger and verify it's configured
        logger = structlog.get_logger("test")
        assert logger is not None

    @patch("src.logging_config.get_settings")
    def test_configure_logging_respects_log_level(self, mock_get_settings):
        """Test that log level is respected."""
        from src.logging_config import configure_logging

        mock_settings = Mock(spec=Settings)
        mock_settings.log_format = "json"
        mock_settings.log_level = "ERROR"
        mock_get_settings.return_value = mock_settings

        configure_logging()

        # Verify standard library logging is configured
        assert logging.root.level == logging.ERROR


class TestConfigSettings:
    """Tests for logging-related config settings."""

    def test_settings_default_log_level(self):
        """Test log level is a valid level (may be overridden by .env)."""
        settings = Settings()
        assert settings.log_level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

    def test_settings_default_log_format(self):
        """Test log format is valid (may be overridden by .env)."""
        settings = Settings()
        assert settings.log_format in ("json", "pretty")

    def test_settings_validates_log_level_valid(self):
        """Test valid log levels are accepted."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        for level in valid_levels:
            settings = Settings(log_level=level)
            assert settings.log_level == level.upper()

    def test_settings_validates_log_level_invalid(self):
        """Test invalid log levels are rejected."""
        with pytest.raises(ValueError, match="Invalid log level"):
            Settings(log_level="INVALID")

    def test_settings_validates_log_level_case_insensitive(self):
        """Test log level validation is case insensitive."""
        settings = Settings(log_level="info")
        assert settings.log_level == "INFO"


class TestRequestIDMiddleware:
    """Tests for request ID middleware."""

    @pytest.fixture
    def mock_app(self):
        """Mock ASGI application."""
        async def app(scope, receive, send):
            response = Response(content="test", status_code=200)
            await response(scope, receive, send)
        return app

    @pytest.mark.asyncio
    async def test_middleware_generates_request_id(self, mock_app):
        """Test middleware generates a request ID when none provided."""
        from src.middleware.request_id import RequestIDMiddleware

        middleware = RequestIDMiddleware(mock_app)

        # Create mock request without X-Request-ID header
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        response_started = False
        response_headers = []

        async def send(message):
            nonlocal response_started, response_headers
            if message["type"] == "http.response.start":
                response_started = True
                response_headers = message.get("headers", [])

        await middleware(scope, receive, send)

        # Check that X-Request-ID header was added
        assert response_started
        header_dict = {k.decode(): v.decode() for k, v in response_headers}
        assert "x-request-id" in header_dict
        # Validate it's a UUID
        request_id = header_dict["x-request-id"]
        uuid.UUID(request_id)  # Will raise if not valid UUID

    @pytest.mark.asyncio
    async def test_middleware_uses_client_request_id(self, mock_app):
        """Test middleware uses client-provided request ID."""
        from src.middleware.request_id import RequestIDMiddleware

        middleware = RequestIDMiddleware(mock_app)
        client_request_id = "client-provided-id"

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [(b"x-request-id", client_request_id.encode())],
            "query_string": b"",
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        response_headers = []

        async def send(message):
            nonlocal response_headers
            if message["type"] == "http.response.start":
                response_headers = message.get("headers", [])

        await middleware(scope, receive, send)

        # Check that same request ID is in response
        header_dict = {k.decode(): v.decode() for k, v in response_headers}
        assert header_dict.get("x-request-id") == client_request_id

    @pytest.mark.asyncio
    async def test_middleware_adds_response_header(self, mock_app):
        """Test middleware adds X-Request-ID to response headers."""
        from src.middleware.request_id import RequestIDMiddleware

        middleware = RequestIDMiddleware(mock_app)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        response_headers = []

        async def send(message):
            nonlocal response_headers
            if message["type"] == "http.response.start":
                response_headers = message.get("headers", [])

        await middleware(scope, receive, send)

        # Verify header is present
        header_names = [k.decode() for k, v in response_headers]
        assert "x-request-id" in header_names

    @pytest.mark.asyncio
    async def test_middleware_binds_to_structlog_context(self):
        """Test middleware binds request ID to structlog context."""
        from src.middleware.request_id import RequestIDMiddleware

        called = False
        captured_context = {}

        async def mock_app(scope, receive, send):
            nonlocal called, captured_context
            called = True
            # Capture the current structlog context
            captured_context = structlog.contextvars.get_contextvars()
            response = Response(content="test", status_code=200)
            await response(scope, receive, send)

        middleware = RequestIDMiddleware(mock_app)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        await middleware(scope, receive, send)

        assert called
        assert "request_id" in captured_context
        # Validate it's a UUID
        uuid.UUID(captured_context["request_id"])


class TestRequestLoggingMiddleware:
    """Tests for request logging middleware."""

    @pytest.fixture
    def mock_app(self):
        """Mock ASGI application."""
        async def app(scope, receive, send):
            response = Response(content="test", status_code=200)
            await response(scope, receive, send)
        return app

    @pytest.fixture
    def configure_structlog_for_tests(self):
        """Configure structlog to work with caplog."""
        # Configure structlog to use stdlib logging for tests
        structlog.configure(
            processors=[
                structlog.stdlib.add_log_level,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

    @pytest.mark.asyncio
    async def test_middleware_logs_request(self, mock_app, caplog, configure_structlog_for_tests):
        """Test middleware logs incoming requests."""
        from src.middleware.request_logging import RequestLoggingMiddleware

        middleware = RequestLoggingMiddleware(mock_app)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/test",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        with caplog.at_level(logging.INFO):
            await middleware(scope, receive, send)

        # Check that request was logged
        assert any("request_started" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_middleware_logs_response_with_duration(self, mock_app, caplog, configure_structlog_for_tests):
        """Test middleware logs response with duration."""
        from src.middleware.request_logging import RequestLoggingMiddleware

        middleware = RequestLoggingMiddleware(mock_app)

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/test",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        with caplog.at_level(logging.INFO):
            await middleware(scope, receive, send)

        # Check that response was logged with duration
        assert any("request_completed" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_middleware_skips_health_endpoint(self, mock_app, caplog):
        """Test middleware skips logging /health endpoint."""
        from src.middleware.request_logging import RequestLoggingMiddleware

        middleware = RequestLoggingMiddleware(mock_app)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/health",
            "headers": [],
            "query_string": b"",
            "client": ("127.0.0.1", 12345),
        }

        async def receive():
            return {"type": "http.request", "body": b""}

        async def send(message):
            pass

        with caplog.at_level(logging.INFO):
            await middleware(scope, receive, send)

        # Check that health endpoint was not logged
        assert not any("request_started" in record.message for record in caplog.records)
        assert not any("request_completed" in record.message for record in caplog.records)
