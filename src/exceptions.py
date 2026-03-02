"""Shared exception classes for the extraction pipeline."""


class LLMExtractionError(Exception):
    """Raised when LLM extraction fails."""

    pass


class QueueFullError(Exception):
    """Raised when LLM queue is persistently full and cannot accept new requests."""

    pass
