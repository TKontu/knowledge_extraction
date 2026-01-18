"""Logging configuration for the application."""

import logging
import sys

import structlog

from src.config import Settings, settings


def get_settings() -> Settings:
    """Get the global settings instance."""
    return settings


def configure_logging() -> None:
    """Configure structlog for the application."""
    settings_obj = get_settings()

    # Determine if we're in development or production
    is_json = settings_obj.log_format.lower() == "json"
    log_level = getattr(logging, settings_obj.log_level.upper())

    # Shared processors
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if is_json:
        # Production: JSON output with WriteLoggerFactory to avoid duplication
        structlog.configure(
            processors=shared_processors
            + [
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.WriteLoggerFactory(file=sys.stdout),
            cache_logger_on_first_use=True,
        )
        # Disable standard library logging to avoid duplication
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=logging.CRITICAL + 1,  # Effectively disable
        )
    else:
        # Development: colored console output
        structlog.configure(
            processors=shared_processors
            + [
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(log_level),
            context_class=dict,
            logger_factory=structlog.PrintLoggerFactory(),
            cache_logger_on_first_use=True,
        )
        logging.basicConfig(
            format="%(message)s",
            stream=sys.stdout,
            level=log_level,
        )

    # Explicitly set root logger level
    logging.root.setLevel(log_level)
