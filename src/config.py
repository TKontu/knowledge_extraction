from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application configuration loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Security
    api_key: str = Field(
        description="API key for authentication (required - no default)",
    )
    allowed_origins: str = Field(
        default="http://localhost:8080",
        description="Comma-separated CORS origins",
    )

    # Security - HTTPS
    enforce_https: bool = Field(
        default=False,
        description="Redirect HTTP to HTTPS (enable in production)",
    )
    https_redirect_host: str | None = Field(
        default=None,
        description="Host to redirect to for HTTPS (optional)",
    )

    # Database
    database_url: str = Field(
        default="postgresql://scristill:scristill@localhost:5432/scristill",
        description="PostgreSQL connection URL",
    )

    # Redis
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="Redis connection URL",
    )

    # Qdrant
    qdrant_url: str = Field(
        default="http://localhost:6333",
        description="Qdrant vector database URL",
    )

    # Firecrawl
    firecrawl_url: str = Field(
        default="http://localhost:3002",
        description="Firecrawl API URL",
    )

    # LLM Configuration
    openai_base_url: str = Field(
        default="http://192.168.0.247:9003/v1",
        description="OpenAI-compatible API base URL",
    )
    openai_embedding_base_url: str = Field(
        default="http://192.168.0.136:9003/v1",
        description="Embedding model API base URL",
    )
    openai_api_key: str = Field(
        default="ollama",
        description="API key for LLM gateway",
    )
    llm_model: str = Field(
        default="gemma3-12b-awq",
        description="LLM model name for extraction",
    )
    rag_embedding_model: str = Field(
        default="bge-large-en",
        description="Embedding model name",
    )

    # LLM Timeouts
    llm_http_timeout: int = Field(
        default=900,
        description="HTTP timeout for LLM requests in seconds",
    )
    llm_max_retries: int = Field(
        default=5,
        description="Maximum retries for LLM requests",
    )
    llm_retry_backoff_min: int = Field(
        default=2,
        description="Minimum backoff time in seconds",
    )
    llm_retry_backoff_max: int = Field(
        default=60,
        description="Maximum backoff time in seconds",
    )

    # Extraction Concurrency
    extraction_max_concurrent_chunks: int = Field(
        default=25,
        description="Max concurrent chunk extractions to avoid KV cache exhaustion",
    )

    # Scraping Configuration
    scrape_delay_min: int = Field(
        default=2,
        description="Minimum delay between scrapes in seconds",
    )
    scrape_delay_max: int = Field(
        default=5,
        description="Maximum delay between scrapes in seconds",
    )
    scrape_max_concurrent_per_domain: int = Field(
        default=2,
        description="Max concurrent scrapes per domain",
    )
    scrape_daily_limit_per_domain: int = Field(
        default=500,
        description="Daily scrape limit per domain",
    )
    scrape_max_retries: int = Field(
        default=3,
        description="Maximum scrape retries",
    )
    scrape_timeout: int = Field(
        default=60,
        description="Scrape timeout in seconds",
    )

    # Scraper Retry Configuration
    scrape_retry_max_attempts: int = Field(
        default=3,
        description="Maximum retry attempts for failed scrapes",
    )
    scrape_retry_base_delay: float = Field(
        default=2.0,
        description="Base delay between retries in seconds",
    )
    scrape_retry_max_delay: float = Field(
        default=60.0,
        description="Maximum delay between retries in seconds",
    )

    # FlareSolverr Proxy Adapter
    proxy_adapter_enabled: bool = Field(
        default=True,
        description="Enable proxy adapter service",
    )
    proxy_adapter_port: int = Field(
        default=8192,
        description="Port for proxy adapter service",
    )
    flaresolverr_url: str = Field(
        default="http://flaresolverr:8191",
        description="FlareSolverr service URL",
    )
    flaresolverr_max_timeout: int = Field(
        default=60000,
        description="FlareSolverr timeout in milliseconds",
    )
    flaresolverr_blocked_domains: str | list[str] = Field(
        default="weg.net,siemens.com,wattdrive.com",
        description="Domains requiring FlareSolverr proxy (comma-separated or list)",
    )

    # Logging & Monitoring
    log_level: str = Field(
        default="INFO",
        description="Logging level (DEBUG, INFO, WARNING, ERROR)",
    )
    log_format: str = Field(
        default="json",
        description="Log format (json or pretty)",
    )
    enable_metrics: bool = Field(
        default=True,
        description="Enable Prometheus metrics",
    )

    # Rate limiting
    rate_limit_enabled: bool = Field(
        default=True,
        description="Enable rate limiting",
    )
    rate_limit_requests: int = Field(
        default=100,
        description="Requests per window",
    )
    rate_limit_window_seconds: int = Field(
        default=60,
        description="Window size in seconds",
    )
    rate_limit_burst: int = Field(
        default=20,
        description="Burst allowance above limit",
    )

    # PDF Export
    pdf_enabled: bool = Field(
        default=True,
        description="Enable PDF export (requires Pandoc)",
    )
    pandoc_path: str = Field(
        default="pandoc",
        description="Path to Pandoc executable",
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate API key is set and not a known insecure value."""
        insecure_values = {
            "dev-key-change-in-production",
            "changeme",
            "test",
            "dev",
            "development",
        }
        if not v:
            raise ValueError("API_KEY environment variable must be set")
        if v.lower() in insecure_values:
            raise ValueError(
                f"Insecure API key '{v}'. Please set a strong API_KEY in production."
            )
        if len(v) < 16:
            raise ValueError("API key must be at least 16 characters")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is valid."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        if v.upper() not in valid_levels:
            raise ValueError(f"Invalid log level. Must be one of: {valid_levels}")
        return v.upper()

    @field_validator("flaresolverr_blocked_domains", mode="after")
    @classmethod
    def parse_blocked_domains(cls, v):
        """Parse comma-separated string into list."""
        if isinstance(v, str):
            # Split by comma and strip whitespace
            return [domain.strip() for domain in v.split(",") if domain.strip()]
        return v

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse comma-separated origins into list."""
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @property
    def flaresolverr_blocked_domains_list(self) -> list[str]:
        """Get blocked domains as list."""
        if isinstance(self.flaresolverr_blocked_domains, list):
            return self.flaresolverr_blocked_domains
        return [d.strip() for d in self.flaresolverr_blocked_domains.split(",") if d.strip()]


# Global settings instance
settings = Settings()
