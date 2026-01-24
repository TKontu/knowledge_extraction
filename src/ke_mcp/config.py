"""MCP Server configuration from environment variables."""

import logging
import sys

from pydantic import ConfigDict, Field
from pydantic_settings import BaseSettings


def configure_logging() -> logging.Logger:
    """Configure logging to stderr only (CRITICAL for STDIO transport).

    WARNING: Never use print() or write to stdout in MCP STDIO servers.
    This will corrupt JSON-RPC messages and break the protocol.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )

    logger = logging.getLogger("mcp")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

    # Suppress httpx info logs (too verbose)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    return logger


class MCPSettings(BaseSettings):
    """MCP Server configuration.

    Environment variables:
        KE_API_BASE_URL: Base URL of the Knowledge Extraction API
        KE_API_KEY: API authentication key
        KE_TIMEOUT_SECONDS: HTTP request timeout (default: 60)
        KE_MAX_RETRIES: Retry attempts for failed requests (default: 3)
        KE_POLL_INTERVAL: Seconds between job status polls (default: 5)
        KE_MAX_POLL_ATTEMPTS: Max polls before timeout (default: 120)
    """

    api_base_url: str = Field(
        default="http://localhost:8000",
        description="Knowledge Extraction API base URL",
    )
    api_key: str = Field(
        default="",
        description="API authentication key (if required)",
    )
    timeout_seconds: int = Field(
        default=60,
        ge=10,
        le=300,
        description="HTTP request timeout in seconds",
    )
    max_retries: int = Field(
        default=3,
        ge=1,
        le=10,
        description="Max retry attempts for failed requests",
    )
    poll_interval: int = Field(
        default=5,
        ge=1,
        le=30,
        description="Seconds between job status polls",
    )
    max_poll_attempts: int = Field(
        default=120,
        ge=10,
        le=600,
        description="Max poll attempts before timeout (120 * 5s = 10 min)",
    )

    model_config = ConfigDict(
        env_prefix="KE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
