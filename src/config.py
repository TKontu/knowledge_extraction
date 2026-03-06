from dataclasses import dataclass

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Typed subsystem facades (frozen dataclasses)
# ---------------------------------------------------------------------------
# These provide grouped access to settings (e.g. settings.llm.model) while
# keeping all flat fields unchanged (settings.llm_model still works).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DatabaseConfig:
    url: str
    pool_size: int
    max_overflow: int
    pool_timeout: int


@dataclass(frozen=True, slots=True)
class LLMConfig:
    base_url: str
    embedding_base_url: str
    api_key: str
    model: str
    embedding_model: str
    embedding_dimension: int
    http_timeout: int
    max_tokens: int
    max_retries: int
    retry_backoff_min: int
    retry_backoff_max: int
    base_temperature: float
    retry_temperature_increment: float


@dataclass(frozen=True, slots=True)
class LLMQueueConfig:
    enabled: bool
    stream_key: str
    max_depth: int
    backpressure_threshold: int
    response_ttl: int
    request_timeout: int
    worker_concurrency: int
    worker_max_concurrency: int
    worker_min_concurrency: int


@dataclass(frozen=True, slots=True)
class ExtractionConfig:
    content_limit: int
    chunk_max_tokens: int
    chunk_overlap_tokens: int
    max_concurrent_chunks: int
    max_concurrent_sources: int
    extraction_batch_size: int
    source_quoting_enabled: bool
    conflict_detection_enabled: bool
    validation_enabled: bool
    validation_min_confidence: float
    embedding_max_concurrent: int
    schema_embedding_enabled: bool
    domain_dedup_enabled: bool
    domain_dedup_threshold_pct: float
    domain_dedup_min_pages: int
    domain_dedup_min_block_chars: int
    source_grounding_min_ratio: float


@dataclass(frozen=True, slots=True)
class ClassificationConfig:
    enabled: bool
    skip_enabled: bool
    smart_enabled: bool
    reranker_model: str
    embedding_high_threshold: float
    embedding_low_threshold: float
    reranker_threshold: float
    cache_ttl: int
    use_default_skip_patterns: bool
    classifier_content_limit: int


@dataclass(frozen=True, slots=True)
class ScrapingConfig:
    delay_min: int
    delay_max: int
    max_concurrent_per_domain: int
    daily_limit_per_domain: int
    max_retries: int
    timeout: int
    retry_max_attempts: int
    retry_base_delay: float
    retry_max_delay: float
    camoufox_networkidle_timeout: int
    camoufox_content_stability_checks: int
    camoufox_content_stability_interval: int


@dataclass(frozen=True, slots=True)
class CrawlConfig:
    delay_ms: int
    max_concurrency: int
    max_concurrent_crawls: int
    poll_interval: int
    smart_relevance_threshold: float
    smart_map_limit: int
    smart_batch_max_concurrency: int
    language_filtering_enabled: bool
    language_detection_confidence: float
    language_detection_timeout: float
    excluded_language_codes: list[str]


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    enabled: bool
    port: int
    flaresolverr_url: str
    flaresolverr_max_timeout: int
    flaresolverr_blocked_domains: list[str]


@dataclass(frozen=True, slots=True)
class SchedulerConfig:
    cleanup_stale_on_startup: bool
    startup_stagger_seconds: float
    stale_threshold_scrape: int
    stale_threshold_extract: int
    stale_threshold_crawl: int


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    log_level: str
    log_format: str
    metrics_enabled: bool
    alerting_enabled: bool
    alert_webhook_url: str | None
    alert_webhook_format: str


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

    # Database pool settings
    db_pool_size: int = Field(
        default=5,
        description="Database connection pool size",
    )
    db_max_overflow: int = Field(
        default=10,
        description="Max overflow connections",
    )
    db_pool_timeout: int = Field(
        default=30,
        description="Pool connection timeout in seconds",
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
        default="Qwen3-30B-A3B-Instruct-4bit",
        description="LLM model name for extraction",
    )
    rag_embedding_model: str = Field(
        default="bge-m3",
        description="Embedding model name",
    )
    embedding_dimension: int = Field(
        default=1024,
        ge=1,
        le=8192,
        description="Embedding vector dimension (1024 for bge-m3)",
    )

    # LLM Timeouts
    llm_http_timeout: int = Field(
        default=120,
        description="HTTP timeout for LLM requests in seconds (reduced to detect stuck models)",
    )
    llm_max_tokens: int = Field(
        default=8192,
        description="Maximum tokens for LLM response (prevents endless generation)",
    )
    llm_max_retries: int = Field(
        default=3,
        description="Maximum retries for LLM requests",
    )
    llm_retry_backoff_min: int = Field(
        default=2,
        description="Minimum backoff time in seconds",
    )
    llm_retry_backoff_max: int = Field(
        default=30,
        description="Maximum backoff time in seconds",
    )
    llm_base_temperature: float = Field(
        default=0.1,
        description="Base temperature for LLM requests",
    )
    llm_retry_temperature_increment: float = Field(
        default=0.05,
        description="Temperature increase per retry attempt to vary outputs",
    )

    # Extraction Concurrency
    extraction_max_concurrent_chunks: int = Field(
        default=80,
        description="Max concurrent chunk extractions for optimal vLLM KV cache utilization",
    )
    extraction_max_concurrent_sources: int = Field(
        default=20,
        description="Max concurrent source extractions in pipeline",
    )
    extraction_batch_size: int = Field(
        default=20,
        ge=1,
        le=200,
        description="Number of sources per chunk in schema extraction pipeline",
    )

    # Embedding Concurrency
    embedding_max_concurrent: int = Field(
        default=50,
        description="Max concurrent embedding/rerank requests to embedding server",
    )

    # Schema extraction embedding (enables search_knowledge for schema pipeline)
    schema_extraction_embedding_enabled: bool = Field(
        default=True,
        description="Generate embeddings for schema pipeline extractions (enables semantic search)",
    )

    # LLM Worker Queue Settings
    llm_worker_concurrency: int = Field(
        default=10,
        description="Initial concurrency for LLM worker (requests in flight)",
    )
    llm_worker_max_concurrency: int = Field(
        default=50,
        description="Maximum concurrency for LLM worker",
    )
    llm_worker_min_concurrency: int = Field(
        default=5,
        description="Minimum concurrency for LLM worker",
    )
    llm_request_timeout: int = Field(
        default=300,
        description="Timeout for LLM requests in seconds",
    )
    llm_queue_enabled: bool = Field(
        default=False,
        description="Enable Redis-based LLM request queue for batching and adaptive concurrency",
    )
    llm_queue_stream_key: str = Field(
        default="llm:requests",
        description="Redis stream key for LLM request queue",
    )
    llm_queue_max_depth: int = Field(
        default=1000,
        ge=10,
        le=10000,
        description="Maximum queue depth before rejecting requests",
    )
    llm_queue_backpressure_threshold: int = Field(
        default=500,
        ge=5,
        le=5000,
        description="Queue depth that triggers backpressure signaling",
    )
    llm_response_ttl: int = Field(
        default=300,
        ge=60,
        le=3600,
        description="TTL in seconds for LLM response storage in Redis (must be >= llm_request_timeout)",
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
        default=180,
        description="Scrape timeout in seconds (3 minutes for anti-bot protected sites)",
    )

    # Crawl Rate Limiting Configuration
    crawl_delay_ms: int = Field(
        default=2000,
        description="Delay between crawl requests in milliseconds (respectful crawling)",
    )
    crawl_max_concurrency: int = Field(
        default=2,
        description="Max concurrent requests during crawl (per domain rate limiting)",
    )
    max_concurrent_crawls: int = Field(
        default=6,
        description="Max parallel crawl jobs (different domains) - adjust based on available resources",
    )
    crawl_poll_interval: int = Field(
        default=10,
        description="Interval in seconds between polling Firecrawl for crawl job status updates",
    )

    # Smart Crawl Settings
    smart_crawl_default_relevance_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Default embedding similarity threshold for URL relevance filtering",
    )
    smart_crawl_map_limit: int = Field(
        default=5000,
        ge=1,
        le=50000,
        description="Maximum URLs to return from Firecrawl Map endpoint",
    )
    smart_crawl_batch_max_concurrency: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Max concurrent requests for batch scraping in smart crawl",
    )

    # Smart Merge Settings (for domain-level report aggregation)
    smart_merge_max_candidates: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum URL candidates to include per column when merging by domain",
    )
    smart_merge_min_confidence: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Minimum extraction confidence to include in merge candidates",
    )

    # Camoufox Timeout Strategy
    camoufox_networkidle_timeout: int = Field(
        default=5000,
        description="Network idle timeout in milliseconds (reduced for faster scraping)",
    )
    camoufox_content_stability_checks: int = Field(
        default=2,
        description="Number of stability checks before considering content ready",
    )
    camoufox_content_stability_interval: int = Field(
        default=500,
        description="Interval between content stability checks in milliseconds",
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

    # Language Filtering Configuration
    language_filtering_enabled: bool = Field(
        default=True,
        description="Enable language-based content filtering",
    )
    language_detection_confidence_threshold: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold for language detection (0.0-1.0)",
    )
    language_detection_timeout_seconds: float = Field(
        default=5.0,
        description="Timeout for language detection per page in seconds",
    )
    excluded_language_codes: str | list[str] = Field(
        default="de,fi,fr,es,it,nl,pt,pl,ru,sv,no,da",
        description="Comma-separated list of ISO 639-1 language codes to exclude",
    )

    @field_validator("excluded_language_codes", mode="after")
    @classmethod
    def parse_excluded_languages(cls, v: str | list[str]) -> list[str]:
        """Parse comma-separated string into list."""
        if isinstance(v, str):
            return [code.strip().lower() for code in v.split(",") if code.strip()]
        return [code.lower() for code in v]

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

    # Alerting
    alerting_enabled: bool = Field(
        default=True,
        description="Enable alerting system (always logs, webhook optional)",
    )
    alert_webhook_url: str | None = Field(
        default=None,
        description="Webhook URL for alerts (Slack, Discord, or generic endpoint)",
    )
    alert_webhook_format: str = Field(
        default="json",
        description="Webhook payload format: 'json' (generic) or 'slack' (Slack-formatted)",
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

    # Job recovery settings
    job_stale_threshold_scrape: int = Field(
        default=300,
        description="Scrape job stale threshold in seconds (default: 5 minutes)",
    )
    job_stale_threshold_extract: int = Field(
        default=900,
        description="Extract job stale threshold in seconds (default: 15 minutes)",
    )
    job_stale_threshold_crawl: int = Field(
        default=1800,
        description="Crawl job stale threshold in seconds (default: 30 minutes)",
    )

    # Scheduler Startup Resilience
    scheduler_cleanup_stale_on_startup: bool = Field(
        default=True,
        description="Mark running/cancelling jobs as failed on startup",
    )
    scheduler_startup_stagger_seconds: float = Field(
        default=1.0,
        ge=0.0,
        le=10.0,
        description="Delay between starting each worker loop on startup",
    )

    # Domain Boilerplate Deduplication
    domain_dedup_enabled: bool = Field(
        default=True,
        description="Use cleaned_content (domain-deduped) for extraction when available",
    )
    domain_dedup_threshold_pct: float = Field(
        default=0.7,
        ge=0.1,
        le=1.0,
        description="Fraction of pages a block must appear in to be boilerplate",
    )
    domain_dedup_min_pages: int = Field(
        default=5,
        ge=2,
        le=100,
        description="Minimum pages per domain before boilerplate analysis runs",
    )
    domain_dedup_min_block_chars: int = Field(
        default=50,
        ge=10,
        le=500,
        description="Minimum characters for a content block to be considered",
    )

    # Source Grounding (quote-in-content verification)
    source_grounding_min_ratio: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Minimum ratio of source-grounded quotes before re-extraction is triggered. "
        "0.5 means retry if fewer than half the quotes exist in the source content.",
    )

    # Grounding Verification
    grounding_llm_verify_enabled: bool = Field(
        default=True,
        description="Enable LLM verification for unresolved grounding scores",
    )
    grounding_llm_verify_model: str = Field(
        default="",
        description="Model for LLM grounding verification (empty = use LLM_MODEL)",
    )

    # Page Classification (extraction optimization)
    classification_enabled: bool = Field(
        default=True,
        description="Enable page classification to filter field groups before extraction",
    )
    classification_skip_enabled: bool = Field(
        default=True,
        description="Enable skipping pages classified as irrelevant (careers, news, etc.)",
    )

    # Smart Classification (embedding + reranker)
    smart_classification_enabled: bool = Field(
        default=True,
        description="Enable embedding-based smart classification (requires embedding server)",
    )
    reranker_model: str = Field(
        default="bge-reranker-v2-m3",
        description="Reranker model name for relevance scoring",
    )
    classification_embedding_high_threshold: float = Field(
        default=0.75,
        ge=0.0,
        le=1.0,
        description="Embedding similarity threshold for high confidence (use matched groups)",
    )
    classification_embedding_low_threshold: float = Field(
        default=0.4,
        ge=0.0,
        le=1.0,
        description="Below this threshold, use all groups (conservative)",
    )
    classification_reranker_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Reranker score threshold for including a field group",
    )
    classification_cache_ttl: int = Field(
        default=86400,
        ge=0,
        description="TTL for field group embedding cache in seconds (24 hours)",
    )
    classification_use_default_skip_patterns: bool = Field(
        default=True,
        description="When True, use DEFAULT_SKIP_PATTERNS if template has no classification_config. "
        "When False, smart classification uses no skip patterns (context-agnostic).",
    )
    classification_content_limit: int = Field(
        default=6000,
        ge=1000,
        le=30000,
        description="Max characters of content for classifier embedding/reranking",
    )

    # Extraction Pipeline Reliability
    extraction_content_limit: int = Field(
        default=20000,
        ge=1000,
        le=100000,
        description="Max characters of source content sent to LLM per extraction call",
    )
    extraction_chunk_max_tokens: int = Field(
        default=5000,
        ge=500,
        le=16000,
        description="Max tokens per chunk for chunked extraction",
    )
    extraction_chunk_overlap_tokens: int = Field(
        default=200,
        ge=0,
        le=1000,
        description="Overlap between chunks in tokens (0=disabled)",
    )
    extraction_source_quoting_enabled: bool = Field(
        default=True,
        description="Ask LLM for source quotes per field",
    )
    extraction_conflict_detection_enabled: bool = Field(
        default=True,
        description="Record merge conflicts between chunks",
    )
    extraction_validation_enabled: bool = Field(
        default=True,
        description="Validate extracted types against field definitions",
    )
    extraction_validation_min_confidence: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="Suppress all fields below this confidence",
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

    @model_validator(mode="after")
    def validate_classification_thresholds(self) -> "Settings":
        """Validate classification thresholds are logically consistent."""
        high = self.classification_embedding_high_threshold
        low = self.classification_embedding_low_threshold
        if high <= low:
            raise ValueError(
                f"classification_embedding_high_threshold ({high}) must be greater than "
                f"classification_embedding_low_threshold ({low})"
            )
        return self

    @model_validator(mode="after")
    def validate_chunk_config(self) -> "Settings":
        """Validate chunk overlap is less than chunk max tokens."""
        if self.extraction_chunk_overlap_tokens >= self.extraction_chunk_max_tokens:
            raise ValueError(
                f"extraction_chunk_overlap_tokens ({self.extraction_chunk_overlap_tokens}) "
                f"must be less than extraction_chunk_max_tokens "
                f"({self.extraction_chunk_max_tokens})"
            )
        return self

    @property
    def allowed_origins_list(self) -> list[str]:
        """Parse comma-separated origins into list."""
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @property
    def flaresolverr_blocked_domains_list(self) -> list[str]:
        """Get blocked domains as list."""
        if isinstance(self.flaresolverr_blocked_domains, list):
            return self.flaresolverr_blocked_domains
        return [
            d.strip() for d in self.flaresolverr_blocked_domains.split(",") if d.strip()
        ]

    # -------------------------------------------------------------------
    # Typed subsystem facades (cached on first access)
    # -------------------------------------------------------------------

    def _get_facade(self, key: str, factory):
        """Return cached facade or create and cache it."""
        if not hasattr(self, "_facade_cache"):
            object.__setattr__(self, "_facade_cache", {})
        cache = object.__getattribute__(self, "_facade_cache")
        if key not in cache:
            cache[key] = factory()
        return cache[key]

    @property
    def database(self) -> DatabaseConfig:
        return self._get_facade(
            "database",
            lambda: DatabaseConfig(
                url=self.database_url,
                pool_size=self.db_pool_size,
                max_overflow=self.db_max_overflow,
                pool_timeout=self.db_pool_timeout,
            ),
        )

    @property
    def llm(self) -> LLMConfig:
        return self._get_facade(
            "llm",
            lambda: LLMConfig(
                base_url=self.openai_base_url,
                embedding_base_url=self.openai_embedding_base_url,
                api_key=self.openai_api_key,
                model=self.llm_model,
                embedding_model=self.rag_embedding_model,
                embedding_dimension=self.embedding_dimension,
                http_timeout=self.llm_http_timeout,
                max_tokens=self.llm_max_tokens,
                max_retries=self.llm_max_retries,
                retry_backoff_min=self.llm_retry_backoff_min,
                retry_backoff_max=self.llm_retry_backoff_max,
                base_temperature=self.llm_base_temperature,
                retry_temperature_increment=self.llm_retry_temperature_increment,
            ),
        )

    @property
    def llm_queue(self) -> LLMQueueConfig:
        return self._get_facade(
            "llm_queue",
            lambda: LLMQueueConfig(
                enabled=self.llm_queue_enabled,
                stream_key=self.llm_queue_stream_key,
                max_depth=self.llm_queue_max_depth,
                backpressure_threshold=self.llm_queue_backpressure_threshold,
                response_ttl=self.llm_response_ttl,
                request_timeout=self.llm_request_timeout,
                worker_concurrency=self.llm_worker_concurrency,
                worker_max_concurrency=self.llm_worker_max_concurrency,
                worker_min_concurrency=self.llm_worker_min_concurrency,
            ),
        )

    @property
    def extraction(self) -> ExtractionConfig:
        return self._get_facade(
            "extraction",
            lambda: ExtractionConfig(
                content_limit=self.extraction_content_limit,
                chunk_max_tokens=self.extraction_chunk_max_tokens,
                chunk_overlap_tokens=self.extraction_chunk_overlap_tokens,
                max_concurrent_chunks=self.extraction_max_concurrent_chunks,
                max_concurrent_sources=self.extraction_max_concurrent_sources,
                extraction_batch_size=self.extraction_batch_size,
                source_quoting_enabled=self.extraction_source_quoting_enabled,
                conflict_detection_enabled=self.extraction_conflict_detection_enabled,
                validation_enabled=self.extraction_validation_enabled,
                validation_min_confidence=self.extraction_validation_min_confidence,
                embedding_max_concurrent=self.embedding_max_concurrent,
                schema_embedding_enabled=self.schema_extraction_embedding_enabled,
                domain_dedup_enabled=self.domain_dedup_enabled,
                domain_dedup_threshold_pct=self.domain_dedup_threshold_pct,
                domain_dedup_min_pages=self.domain_dedup_min_pages,
                domain_dedup_min_block_chars=self.domain_dedup_min_block_chars,
                source_grounding_min_ratio=self.source_grounding_min_ratio,
            ),
        )

    @property
    def classification(self) -> ClassificationConfig:
        return self._get_facade(
            "classification",
            lambda: ClassificationConfig(
                enabled=self.classification_enabled,
                skip_enabled=self.classification_skip_enabled,
                smart_enabled=self.smart_classification_enabled,
                reranker_model=self.reranker_model,
                embedding_high_threshold=self.classification_embedding_high_threshold,
                embedding_low_threshold=self.classification_embedding_low_threshold,
                reranker_threshold=self.classification_reranker_threshold,
                cache_ttl=self.classification_cache_ttl,
                use_default_skip_patterns=self.classification_use_default_skip_patterns,
                classifier_content_limit=self.classification_content_limit,
            ),
        )

    @property
    def scraping(self) -> ScrapingConfig:
        return self._get_facade(
            "scraping",
            lambda: ScrapingConfig(
                delay_min=self.scrape_delay_min,
                delay_max=self.scrape_delay_max,
                max_concurrent_per_domain=self.scrape_max_concurrent_per_domain,
                daily_limit_per_domain=self.scrape_daily_limit_per_domain,
                max_retries=self.scrape_max_retries,
                timeout=self.scrape_timeout,
                retry_max_attempts=self.scrape_retry_max_attempts,
                retry_base_delay=self.scrape_retry_base_delay,
                retry_max_delay=self.scrape_retry_max_delay,
                camoufox_networkidle_timeout=self.camoufox_networkidle_timeout,
                camoufox_content_stability_checks=self.camoufox_content_stability_checks,
                camoufox_content_stability_interval=self.camoufox_content_stability_interval,
            ),
        )

    @property
    def crawl(self) -> CrawlConfig:
        return self._get_facade(
            "crawl",
            lambda: CrawlConfig(
                delay_ms=self.crawl_delay_ms,
                max_concurrency=self.crawl_max_concurrency,
                max_concurrent_crawls=self.max_concurrent_crawls,
                poll_interval=self.crawl_poll_interval,
                smart_relevance_threshold=self.smart_crawl_default_relevance_threshold,
                smart_map_limit=self.smart_crawl_map_limit,
                smart_batch_max_concurrency=self.smart_crawl_batch_max_concurrency,
                language_filtering_enabled=self.language_filtering_enabled,
                language_detection_confidence=self.language_detection_confidence_threshold,
                language_detection_timeout=self.language_detection_timeout_seconds,
                excluded_language_codes=self.excluded_language_codes
                if isinstance(self.excluded_language_codes, list)
                else [
                    c.strip()
                    for c in self.excluded_language_codes.split(",")
                    if c.strip()
                ],
            ),
        )

    @property
    def proxy(self) -> ProxyConfig:
        return self._get_facade(
            "proxy",
            lambda: ProxyConfig(
                enabled=self.proxy_adapter_enabled,
                port=self.proxy_adapter_port,
                flaresolverr_url=self.flaresolverr_url,
                flaresolverr_max_timeout=self.flaresolverr_max_timeout,
                flaresolverr_blocked_domains=self.flaresolverr_blocked_domains
                if isinstance(self.flaresolverr_blocked_domains, list)
                else [
                    d.strip()
                    for d in self.flaresolverr_blocked_domains.split(",")
                    if d.strip()
                ],
            ),
        )

    @property
    def scheduler(self) -> SchedulerConfig:
        return self._get_facade(
            "scheduler",
            lambda: SchedulerConfig(
                cleanup_stale_on_startup=self.scheduler_cleanup_stale_on_startup,
                startup_stagger_seconds=self.scheduler_startup_stagger_seconds,
                stale_threshold_scrape=self.job_stale_threshold_scrape,
                stale_threshold_extract=self.job_stale_threshold_extract,
                stale_threshold_crawl=self.job_stale_threshold_crawl,
            ),
        )

    @property
    def observability(self) -> ObservabilityConfig:
        return self._get_facade(
            "observability",
            lambda: ObservabilityConfig(
                log_level=self.log_level,
                log_format=self.log_format,
                metrics_enabled=self.enable_metrics,
                alerting_enabled=self.alerting_enabled,
                alert_webhook_url=self.alert_webhook_url,
                alert_webhook_format=self.alert_webhook_format,
            ),
        )


__all__ = [
    "Settings",
    "settings",
    "DatabaseConfig",
    "LLMConfig",
    "LLMQueueConfig",
    "ExtractionConfig",
    "ClassificationConfig",
    "ScrapingConfig",
    "CrawlConfig",
    "ProxyConfig",
    "SchedulerConfig",
    "ObservabilityConfig",
]

# Global settings instance
settings = Settings()
