"""Tests for typed subsystem config facades."""

import dataclasses

import pytest

from config import (
    ClassificationConfig,
    CrawlConfig,
    DatabaseConfig,
    ExtractionConfig,
    LLMConfig,
    LLMQueueConfig,
    ObservabilityConfig,
    ProxyConfig,
    SchedulerConfig,
    ScrapingConfig,
    Settings,
)


@pytest.fixture
def s(monkeypatch: pytest.MonkeyPatch) -> Settings:
    """Settings with a valid API key (env vars cleared so defaults apply)."""
    monkeypatch.setenv("API_KEY", "test-key-at-least-16-chars")
    return Settings()


# ---------------------------------------------------------------------------
# Type & isinstance checks
# ---------------------------------------------------------------------------


ALL_FACADES = [
    ("database", DatabaseConfig),
    ("llm", LLMConfig),
    ("llm_queue", LLMQueueConfig),
    ("extraction", ExtractionConfig),
    ("classification", ClassificationConfig),
    ("scraping", ScrapingConfig),
    ("crawl", CrawlConfig),
    ("proxy", ProxyConfig),
    ("scheduler", SchedulerConfig),
    ("observability", ObservabilityConfig),
]


@pytest.mark.parametrize("prop,cls", ALL_FACADES)
def test_facade_isinstance(s: Settings, prop: str, cls: type) -> None:
    facade = getattr(s, prop)
    assert isinstance(facade, cls)


@pytest.mark.parametrize("prop,cls", ALL_FACADES)
def test_facade_is_frozen_dataclass(s: Settings, prop: str, cls: type) -> None:
    assert dataclasses.is_dataclass(cls)
    assert cls.__dataclass_params__.frozen  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Immutability
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prop,cls", ALL_FACADES)
def test_facade_immutable(s: Settings, prop: str, cls: type) -> None:
    facade = getattr(s, prop)
    first_field = dataclasses.fields(facade)[0].name
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(facade, first_field, "MUTATED")


# ---------------------------------------------------------------------------
# Round-trip: flat field → facade field
# ---------------------------------------------------------------------------


class TestDatabaseConfig:
    def test_roundtrip(self, s: Settings) -> None:
        db = s.database
        assert db.url == s.database_url
        assert db.pool_size == s.db_pool_size
        assert db.max_overflow == s.db_max_overflow
        assert db.pool_timeout == s.db_pool_timeout


class TestLLMConfig:
    def test_roundtrip(self, s: Settings) -> None:
        llm = s.llm
        assert llm.base_url == s.openai_base_url
        assert llm.embedding_base_url == s.openai_embedding_base_url
        assert llm.api_key == s.openai_api_key
        assert llm.model == s.llm_model
        assert llm.embedding_model == s.rag_embedding_model
        assert llm.embedding_dimension == s.embedding_dimension
        assert llm.http_timeout == s.llm_http_timeout
        assert llm.max_tokens == s.llm_max_tokens
        assert llm.max_retries == s.llm_max_retries
        assert llm.retry_backoff_min == s.llm_retry_backoff_min
        assert llm.retry_backoff_max == s.llm_retry_backoff_max
        assert llm.base_temperature == s.llm_base_temperature
        assert llm.retry_temperature_increment == s.llm_retry_temperature_increment


class TestLLMQueueConfig:
    def test_roundtrip(self, s: Settings) -> None:
        q = s.llm_queue
        assert q.enabled == s.llm_queue_enabled
        assert q.stream_key == s.llm_queue_stream_key
        assert q.max_depth == s.llm_queue_max_depth
        assert q.backpressure_threshold == s.llm_queue_backpressure_threshold
        assert q.response_ttl == s.llm_response_ttl
        assert q.request_timeout == s.llm_request_timeout
        assert q.worker_concurrency == s.llm_worker_concurrency
        assert q.worker_max_concurrency == s.llm_worker_max_concurrency
        assert q.worker_min_concurrency == s.llm_worker_min_concurrency


class TestExtractionConfig:
    def test_roundtrip(self, s: Settings) -> None:
        ex = s.extraction
        assert ex.content_limit == s.extraction_content_limit
        assert ex.chunk_max_tokens == s.extraction_chunk_max_tokens
        assert ex.chunk_overlap_tokens == s.extraction_chunk_overlap_tokens
        assert ex.max_concurrent_chunks == s.extraction_max_concurrent_chunks
        assert ex.max_concurrent_sources == s.extraction_max_concurrent_sources
        assert ex.extraction_batch_size == s.extraction_batch_size
        assert ex.source_quoting_enabled == s.extraction_source_quoting_enabled
        assert ex.conflict_detection_enabled == s.extraction_conflict_detection_enabled
        assert ex.validation_enabled == s.extraction_validation_enabled
        assert ex.validation_min_confidence == s.extraction_validation_min_confidence
        assert ex.embedding_max_concurrent == s.embedding_max_concurrent
        assert ex.schema_embedding_enabled == s.schema_extraction_embedding_enabled
        assert ex.domain_dedup_enabled == s.domain_dedup_enabled
        assert ex.domain_dedup_threshold_pct == s.domain_dedup_threshold_pct
        assert ex.domain_dedup_min_pages == s.domain_dedup_min_pages
        assert ex.domain_dedup_min_block_chars == s.domain_dedup_min_block_chars


class TestClassificationConfig:
    def test_roundtrip(self, s: Settings) -> None:
        cl = s.classification
        assert cl.enabled == s.classification_enabled
        assert cl.skip_enabled == s.classification_skip_enabled
        assert cl.smart_enabled == s.smart_classification_enabled
        assert cl.reranker_model == s.reranker_model
        assert cl.embedding_high_threshold == s.classification_embedding_high_threshold
        assert cl.embedding_low_threshold == s.classification_embedding_low_threshold
        assert cl.reranker_threshold == s.classification_reranker_threshold
        assert cl.cache_ttl == s.classification_cache_ttl
        assert cl.use_default_skip_patterns == s.classification_use_default_skip_patterns
        assert cl.classifier_content_limit == s.classification_content_limit


class TestScrapingConfig:
    def test_roundtrip(self, s: Settings) -> None:
        sc = s.scraping
        assert sc.delay_min == s.scrape_delay_min
        assert sc.delay_max == s.scrape_delay_max
        assert sc.max_concurrent_per_domain == s.scrape_max_concurrent_per_domain
        assert sc.daily_limit_per_domain == s.scrape_daily_limit_per_domain
        assert sc.max_retries == s.scrape_max_retries
        assert sc.timeout == s.scrape_timeout
        assert sc.retry_max_attempts == s.scrape_retry_max_attempts
        assert sc.retry_base_delay == s.scrape_retry_base_delay
        assert sc.retry_max_delay == s.scrape_retry_max_delay
        assert sc.camoufox_networkidle_timeout == s.camoufox_networkidle_timeout
        assert sc.camoufox_content_stability_checks == s.camoufox_content_stability_checks
        assert sc.camoufox_content_stability_interval == s.camoufox_content_stability_interval


class TestCrawlConfig:
    def test_roundtrip(self, s: Settings) -> None:
        cr = s.crawl
        assert cr.delay_ms == s.crawl_delay_ms
        assert cr.max_concurrency == s.crawl_max_concurrency
        assert cr.max_concurrent_crawls == s.max_concurrent_crawls
        assert cr.poll_interval == s.crawl_poll_interval
        assert cr.smart_relevance_threshold == s.smart_crawl_default_relevance_threshold
        assert cr.smart_map_limit == s.smart_crawl_map_limit
        assert cr.smart_batch_max_concurrency == s.smart_crawl_batch_max_concurrency
        assert cr.language_filtering_enabled == s.language_filtering_enabled
        assert cr.language_detection_confidence == s.language_detection_confidence_threshold
        assert cr.language_detection_timeout == s.language_detection_timeout_seconds
        assert cr.excluded_language_codes == s.excluded_language_codes


class TestProxyConfig:
    def test_roundtrip(self, s: Settings) -> None:
        pr = s.proxy
        assert pr.enabled == s.proxy_adapter_enabled
        assert pr.port == s.proxy_adapter_port
        assert pr.flaresolverr_url == s.flaresolverr_url
        assert pr.flaresolverr_max_timeout == s.flaresolverr_max_timeout
        assert pr.flaresolverr_blocked_domains == s.flaresolverr_blocked_domains_list


class TestSchedulerConfig:
    def test_roundtrip(self, s: Settings) -> None:
        sch = s.scheduler
        assert sch.cleanup_stale_on_startup == s.scheduler_cleanup_stale_on_startup
        assert sch.startup_stagger_seconds == s.scheduler_startup_stagger_seconds
        assert sch.stale_threshold_scrape == s.job_stale_threshold_scrape
        assert sch.stale_threshold_extract == s.job_stale_threshold_extract
        assert sch.stale_threshold_crawl == s.job_stale_threshold_crawl


class TestObservabilityConfig:
    def test_roundtrip(self, s: Settings) -> None:
        obs = s.observability
        assert obs.log_level == s.log_level
        assert obs.log_format == s.log_format
        assert obs.metrics_enabled == s.enable_metrics
        assert obs.alerting_enabled == s.alerting_enabled
        assert obs.alert_webhook_url == s.alert_webhook_url
        assert obs.alert_webhook_format == s.alert_webhook_format


# ---------------------------------------------------------------------------
# Default value spot-checks
# ---------------------------------------------------------------------------


class TestDefaults:
    def test_database_defaults(self, s: Settings) -> None:
        db = s.database
        assert db.pool_size == 5
        assert db.max_overflow == 10
        assert db.pool_timeout == 30

    def test_llm_defaults(self, s: Settings) -> None:
        llm = s.llm
        assert llm.model == s.llm_model
        assert llm.base_temperature == s.llm_base_temperature
        assert llm.max_retries == s.llm_max_retries

    def test_extraction_defaults(self, s: Settings) -> None:
        ex = s.extraction
        assert ex.content_limit == 20000
        assert ex.chunk_max_tokens == 5000
        assert ex.domain_dedup_enabled is True

    def test_scheduler_defaults(self, s: Settings) -> None:
        sch = s.scheduler
        assert sch.cleanup_stale_on_startup is True
        assert sch.startup_stagger_seconds == 1.0
        assert sch.stale_threshold_scrape == 300

    def test_observability_defaults(self, s: Settings) -> None:
        obs = s.observability
        assert obs.log_level == s.log_level
        assert obs.metrics_enabled == s.enable_metrics


# ---------------------------------------------------------------------------
# Validator results reflected in facades
# ---------------------------------------------------------------------------


class TestValidatorReflection:
    def test_classification_thresholds_reflected(self, s: Settings) -> None:
        cl = s.classification
        assert cl.embedding_high_threshold > cl.embedding_low_threshold

    def test_excluded_language_codes_is_list(self, s: Settings) -> None:
        cr = s.crawl
        assert isinstance(cr.excluded_language_codes, list)
        assert "de" in cr.excluded_language_codes

    def test_blocked_domains_is_list(self, s: Settings) -> None:
        pr = s.proxy
        assert isinstance(pr.flaresolverr_blocked_domains, list)
        assert "weg.net" in pr.flaresolverr_blocked_domains
