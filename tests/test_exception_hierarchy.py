"""Tests for exception hierarchy, classification, and FastAPI handler."""

import pytest
from fastapi.testclient import TestClient

from exceptions import (
    AppError,
    LLMExtractionError,
    PermanentError,
    QueueFullError,
    RequestTimeoutError,
    TransientError,
)
from services.projects.template_loader import TemplateLoadError
from services.proxy.flaresolverr_client import FlareSolverrError
from services.reports.pdf import PDFConversionError
from services.scraper.client import ScrapeError
from services.scraper.rate_limiter import RateLimitExceeded

# ---------------------------------------------------------------------------
# Inheritance chain
# ---------------------------------------------------------------------------

class TestInheritanceChain:
    """Verify every custom exception inherits from AppError."""

    @pytest.mark.parametrize(
        "exc_cls",
        [
            TransientError,
            PermanentError,
            QueueFullError,
            RequestTimeoutError,
            LLMExtractionError,
            ScrapeError,
            FlareSolverrError,
            TemplateLoadError,
            PDFConversionError,
            RateLimitExceeded,
        ],
    )
    def test_all_inherit_from_app_error(self, exc_cls):
        assert issubclass(exc_cls, AppError)

    @pytest.mark.parametrize(
        "exc_cls",
        [
            TransientError,
            PermanentError,
            QueueFullError,
            RequestTimeoutError,
            LLMExtractionError,
            ScrapeError,
            FlareSolverrError,
            TemplateLoadError,
            PDFConversionError,
            RateLimitExceeded,
        ],
    )
    def test_all_inherit_from_exception(self, exc_cls):
        assert issubclass(exc_cls, Exception)

    def test_transient_chain(self):
        assert issubclass(QueueFullError, TransientError)
        assert issubclass(RequestTimeoutError, TransientError)
        assert issubclass(FlareSolverrError, TransientError)
        assert issubclass(RateLimitExceeded, TransientError)

    def test_permanent_chain(self):
        assert issubclass(TemplateLoadError, PermanentError)
        assert issubclass(PDFConversionError, PermanentError)

    def test_ambiguous_at_app_error_level(self):
        """LLMExtractionError and ScrapeError are directly under AppError."""
        assert issubclass(LLMExtractionError, AppError)
        assert not issubclass(LLMExtractionError, TransientError)
        assert not issubclass(LLMExtractionError, PermanentError)

        assert issubclass(ScrapeError, AppError)
        assert not issubclass(ScrapeError, TransientError)
        assert not issubclass(ScrapeError, PermanentError)


# ---------------------------------------------------------------------------
# is_retryable classification
# ---------------------------------------------------------------------------

class TestRetryableClassification:
    """Verify is_retryable flag on each exception."""

    @pytest.mark.parametrize(
        "exc_cls,expected",
        [
            (TransientError, True),
            (QueueFullError, True),
            (RequestTimeoutError, True),
            (FlareSolverrError, True),
            (RateLimitExceeded, True),
            (PermanentError, False),
            (TemplateLoadError, False),
            (PDFConversionError, False),
            (AppError, False),
            (LLMExtractionError, False),
            (ScrapeError, False),
        ],
    )
    def test_class_level_retryable(self, exc_cls, expected):
        assert exc_cls.is_retryable is expected

    def test_instance_override_retryable(self):
        """Per-instance is_retryable overrides class default."""
        err = LLMExtractionError("timeout", is_retryable=True)
        assert err.is_retryable is True

        err2 = LLMExtractionError("bad json", is_retryable=False)
        assert err2.is_retryable is False

    def test_transient_instance_retryable(self):
        err = QueueFullError("queue full")
        assert err.is_retryable is True

    def test_permanent_instance_retryable(self):
        err = PDFConversionError("pandoc failed")
        assert err.is_retryable is False


# ---------------------------------------------------------------------------
# code attribute
# ---------------------------------------------------------------------------

class TestCodeAttribute:
    """Verify code attribute set on each class."""

    @pytest.mark.parametrize(
        "exc_cls,expected_code",
        [
            (AppError, "INTERNAL_ERROR"),
            (QueueFullError, "QUEUE_FULL"),
            (RequestTimeoutError, "REQUEST_TIMEOUT"),
            (LLMExtractionError, "LLM_EXTRACTION_FAILED"),
            (ScrapeError, "SCRAPE_FAILED"),
            (FlareSolverrError, "FLARESOLVERR_FAILED"),
            (TemplateLoadError, "TEMPLATE_LOAD_FAILED"),
            (PDFConversionError, "PDF_CONVERSION_FAILED"),
            (RateLimitExceeded, "RATE_LIMIT_EXCEEDED"),
        ],
    )
    def test_code_attribute(self, exc_cls, expected_code):
        assert exc_cls.code == expected_code

    def test_instance_code_override(self):
        err = AppError("custom", code="CUSTOM_CODE")
        assert err.code == "CUSTOM_CODE"


# ---------------------------------------------------------------------------
# Catch categories
# ---------------------------------------------------------------------------

class TestCatchCategories:
    """Verify catching base classes catches all subclasses."""

    def test_catch_transient_catches_all_transient(self):
        transient_exceptions = [
            QueueFullError("q full"),
            RequestTimeoutError("timeout"),
            FlareSolverrError("proxy fail"),
            RateLimitExceeded("example.com", 500, 3600),
        ]
        for exc in transient_exceptions:
            with pytest.raises(TransientError):
                raise exc

    def test_catch_permanent_catches_all_permanent(self):
        permanent_exceptions = [
            TemplateLoadError("test", ["error"]),
            PDFConversionError("pandoc failed"),
        ]
        for exc in permanent_exceptions:
            with pytest.raises(PermanentError):
                raise exc

    def test_catch_app_error_catches_everything(self):
        all_exceptions = [
            QueueFullError("q full"),
            RequestTimeoutError("timeout"),
            FlareSolverrError("proxy fail"),
            RateLimitExceeded("example.com", 500, 3600),
            TemplateLoadError("test", ["error"]),
            PDFConversionError("pandoc failed"),
            LLMExtractionError("extraction fail"),
            ScrapeError("scrape fail"),
        ]
        for exc in all_exceptions:
            with pytest.raises(AppError):
                raise exc

    def test_catch_transient_does_not_catch_permanent(self):
        with pytest.raises(PermanentError):
            try:
                raise TemplateLoadError("test", ["error"])
            except TransientError:
                pytest.fail("TransientError should not catch PermanentError")


# ---------------------------------------------------------------------------
# Custom attributes preserved
# ---------------------------------------------------------------------------

class TestCustomAttributes:
    """Verify domain-specific attributes are preserved after re-parenting."""

    def test_rate_limit_exceeded_attrs(self):
        exc = RateLimitExceeded(domain="example.com", limit=500, reset_in=3600)
        assert exc.domain == "example.com"
        assert exc.limit == 500
        assert exc.reset_in == 3600
        assert "example.com" in str(exc)
        assert exc.is_retryable is True
        assert exc.code == "RATE_LIMIT_EXCEEDED"

    def test_template_load_error_attrs(self):
        exc = TemplateLoadError(template_name="bad_template", errors=["missing field", "bad type"])
        assert exc.template_name == "bad_template"
        assert exc.errors == ["missing field", "bad type"]
        assert "bad_template" in str(exc)
        assert exc.is_retryable is False
        assert exc.code == "TEMPLATE_LOAD_FAILED"

    def test_app_error_message_and_details(self):
        exc = AppError("something broke", details={"key": "value"})
        assert exc.message == "something broke"
        assert exc.details == {"key": "value"}
        assert str(exc) == "something broke"

    def test_app_error_defaults(self):
        exc = AppError("basic error")
        assert exc.code == "INTERNAL_ERROR"
        assert exc.is_retryable is False
        assert exc.details == {}


# ---------------------------------------------------------------------------
# FastAPI exception handler
# ---------------------------------------------------------------------------

class TestFastAPIExceptionHandler:
    """Verify the AppError exception handler returns correct responses."""

    @pytest.fixture
    def handler_app(self):
        """Create a minimal FastAPI app with the exception handler for testing."""
        from fastapi import FastAPI

        test_app = FastAPI()

        @test_app.exception_handler(AppError)
        async def app_error_handler(request, exc: AppError):
            from fastapi.responses import JSONResponse

            if isinstance(exc, TransientError):
                status_code = 503
            elif isinstance(exc, PermanentError):
                status_code = 400
            else:
                status_code = 500
            return JSONResponse(
                status_code=status_code,
                content={"error": {"code": exc.code, "message": exc.message}},
            )

        @test_app.get("/transient")
        async def raise_transient():
            raise QueueFullError("queue is full")

        @test_app.get("/permanent")
        async def raise_permanent():
            raise PDFConversionError("pandoc not found")

        @test_app.get("/ambiguous")
        async def raise_ambiguous():
            raise LLMExtractionError("bad json response")

        @test_app.get("/scrape")
        async def raise_scrape():
            raise ScrapeError("connection refused")

        return test_app

    def test_transient_returns_503(self, handler_app):
        client = TestClient(handler_app, raise_server_exceptions=False)
        resp = client.get("/transient")
        assert resp.status_code == 503
        body = resp.json()
        assert body["error"]["code"] == "QUEUE_FULL"
        assert body["error"]["message"] == "queue is full"

    def test_permanent_returns_400(self, handler_app):
        client = TestClient(handler_app, raise_server_exceptions=False)
        resp = client.get("/permanent")
        assert resp.status_code == 400
        body = resp.json()
        assert body["error"]["code"] == "PDF_CONVERSION_FAILED"
        assert body["error"]["message"] == "pandoc not found"

    def test_ambiguous_returns_500(self, handler_app):
        client = TestClient(handler_app, raise_server_exceptions=False)
        resp = client.get("/ambiguous")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "LLM_EXTRACTION_FAILED"
        assert body["error"]["message"] == "bad json response"

    def test_scrape_error_returns_500(self, handler_app):
        client = TestClient(handler_app, raise_server_exceptions=False)
        resp = client.get("/scrape")
        assert resp.status_code == 500
        body = resp.json()
        assert body["error"]["code"] == "SCRAPE_FAILED"
        assert body["error"]["message"] == "connection refused"
