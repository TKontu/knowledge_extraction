"""Shared exception classes for the extraction pipeline.

Hierarchy:
    AppError(Exception)               — base, code + is_retryable + details
    ├── TransientError(AppError)      — is_retryable=True
    │   ├── QueueFullError            — queue backpressure
    │   └── RequestTimeoutError       — LLM/queue timeout
    ├── PermanentError(AppError)      — is_retryable=False
    └── LLMExtractionError(AppError)  — ambiguous retryability

Domain-specific exceptions (ScrapeError, FlareSolverrError, TemplateLoadError,
PDFConversionError, RateLimitExceeded) live in their respective modules but
inherit from these base classes.
"""


class AppError(Exception):
    """Base for all application errors."""

    code: str = "INTERNAL_ERROR"
    is_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        is_retryable: bool | None = None,
        details: dict | None = None,
    ) -> None:
        self.message = message
        if code is not None:
            self.code = code
        if is_retryable is not None:
            self.is_retryable = is_retryable
        self.details = details or {}
        super().__init__(message)


class TransientError(AppError):
    """Retryable errors (network, timeout, queue full)."""

    is_retryable = True


class PermanentError(AppError):
    """Non-retryable errors (validation, config, missing resource)."""

    is_retryable = False


class QueueFullError(TransientError):
    """Raised when LLM queue is persistently full and cannot accept new requests."""

    code = "QUEUE_FULL"


class RequestTimeoutError(TransientError):
    """Raised when waiting for LLM result times out."""

    code = "REQUEST_TIMEOUT"


class LLMExtractionError(AppError):
    """Raised when LLM extraction fails.

    Ambiguous retryability: timeout = retryable, bad JSON = not.
    Set is_retryable per-instance at raise site when needed.
    """

    code = "LLM_EXTRACTION_FAILED"
