"""Application-wide constants and status enums.

Centralizes bare string literals that were previously scattered across 16+ files.
Using StrEnum allows these to be used anywhere a plain string was used before —
ORM assignments, SQLAlchemy filters, JSON serialization all work unchanged.
"""

from enum import StrEnum


class JobStatus(StrEnum):
    """Status values for Job records."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"


class SourceStatus(StrEnum):
    """Status values for Source records."""

    PENDING = "pending"
    READY = "ready"
    EXTRACTED = "extracted"
    PARTIAL = "partial"  # Extraction completed with some errors
    SKIPPED = "skipped"
    COMPLETED = "completed"
    FAILED = "failed"


class JobType(StrEnum):
    """Job type discriminator values."""

    CRAWL = "crawl"
    SCRAPE = "scrape"
    EXTRACT = "extract"
    CONSOLIDATE = "consolidate"


# LLM retry hint appended to system prompts on retry attempts
LLM_RETRY_HINT = "\n\nIMPORTANT: Be concise. Output valid JSON only."

# Application version — single source of truth
# Overridden at runtime by APP_VERSION env var (set in Dockerfile / CI)
APP_VERSION = "v1.3.1"
