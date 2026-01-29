"""Configuration for Camoufox browser service."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class CamoufoxSettings(BaseSettings):
    """Camoufox service configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="CAMOUFOX_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,  # Allow using field name or alias
    )

    # Server
    port: int = Field(
        default=3003,
        description="Service port",
    )
    host: str = Field(
        default="0.0.0.0",
        description="Service host",
    )

    # Browser pool
    browser_count: int = Field(
        default=5,
        description="Number of browser instances in the pool (enables true parallelism)",
    )
    max_concurrent_pages: int = Field(
        default=10,
        alias="pool_size",
        description="Maximum concurrent browser pages across all browsers",
    )

    # Timeouts (in milliseconds to match Firecrawl)
    timeout: int = Field(
        default=180000,
        description="Default page timeout in milliseconds (3 minutes)",
    )
    networkidle_timeout: int = Field(
        default=5000,
        description="Network idle timeout in milliseconds (reduced for faster scraping)",
    )

    # Content stability detection
    content_stability_checks: int = Field(
        default=2,
        description="Number of stability checks before considering content ready",
    )
    content_stability_interval: int = Field(
        default=500,
        description="Interval between content stability checks in milliseconds",
    )

    # AJAX discovery
    ajax_discovery_max_clicks: int = Field(
        default=20,
        description="Maximum interactive elements to click for AJAX content discovery",
    )

    # Proxy
    proxy: str | None = Field(
        default=None,
        description="Optional proxy URL (e.g., http://user:pass@host:port)",
    )

    # Browser options
    headless: bool = Field(
        default=True,
        description="Run browser in headless mode",
    )

    # Logging
    log_level: str = Field(
        default="INFO",
        description="Logging level",
    )
    log_format: str = Field(
        default="json",
        description="Log format (json or pretty)",
    )


# Global settings instance
settings = CamoufoxSettings()
