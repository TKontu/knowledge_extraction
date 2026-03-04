"""Service container for app-lifetime services used by the scheduler.

Creates, caches, and tears down services that outlive individual jobs.
The JobScheduler receives a ServiceContainer and accesses services
through typed read-only properties.
"""

from uuid import uuid4

import structlog
from openai import AsyncOpenAI

from config import settings
from qdrant_connection import qdrant_client
from redis_client import get_async_redis, redis_client
from services.extraction.embedding_pipeline import ExtractionEmbeddingService
from services.llm.queue import LLMRequestQueue
from services.llm.worker import LLMWorker
from services.scraper.client import FirecrawlClient
from services.scraper.rate_limiter import DomainRateLimiter, RateLimitConfig
from services.scraper.retry import RetryConfig
from services.storage.deduplication import ExtractionDeduplicator
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository

logger = structlog.get_logger(__name__)


class ServiceContainer:
    """Creates, caches, and tears down app-lifetime services."""

    def __init__(self) -> None:
        self._firecrawl_client: FirecrawlClient | None = None
        self._rate_limiter: DomainRateLimiter | None = None
        self._retry_config: RetryConfig | None = None
        self._embedding_service: EmbeddingService | None = None
        self._qdrant_repo: QdrantRepository | None = None
        self._extraction_embedding: ExtractionEmbeddingService | None = None
        self._deduplicator: ExtractionDeduplicator | None = None
        self._async_redis = None
        self._llm_queue: LLMRequestQueue | None = None
        self._llm_worker: LLMWorker | None = None
        self._llm_worker_task = None
        self._started = False

    async def start(self) -> None:
        """Create and initialize all services."""
        self._firecrawl_client = FirecrawlClient(
            base_url=settings.firecrawl_url,
            timeout=settings.scraping.timeout,
        )

        rate_limit_config = RateLimitConfig(
            delay_min=settings.scraping.delay_min,
            delay_max=settings.scraping.delay_max,
            daily_limit=settings.scraping.daily_limit_per_domain,
        )
        self._rate_limiter = DomainRateLimiter(
            redis_client=redis_client,
            config=rate_limit_config,
        )

        self._retry_config = RetryConfig(
            max_retries=settings.scraping.retry_max_attempts,
            base_delay=settings.scraping.retry_base_delay,
            max_delay=settings.scraping.retry_max_delay,
        )

        # Cached stateless services for extraction pipeline
        self._embedding_service = EmbeddingService(
            settings.llm,
            reranker_model=settings.classification.reranker_model,
            max_concurrent=settings.extraction.embedding_max_concurrent,
        )
        self._qdrant_repo = QdrantRepository(
            qdrant_client, embedding_dimension=settings.llm.embedding_dimension
        )
        self._extraction_embedding = ExtractionEmbeddingService(
            self._embedding_service, self._qdrant_repo
        )
        self._deduplicator = ExtractionDeduplicator(
            embedding_service=self._embedding_service,
            qdrant_repo=self._qdrant_repo,
        )

        # LLM request queue and worker
        self._async_redis = await get_async_redis()
        self._llm_queue = LLMRequestQueue(
            redis=self._async_redis,
            stream_key=settings.llm_queue.stream_key,
            max_queue_depth=settings.llm_queue.max_depth,
            backpressure_threshold=settings.llm_queue.backpressure_threshold,
        )
        llm_client = AsyncOpenAI(
            base_url=settings.llm.base_url,
            api_key=settings.llm.api_key,
            timeout=settings.llm.http_timeout,
        )
        self._llm_worker = LLMWorker(
            redis=self._async_redis,
            llm_client=llm_client,
            worker_id=f"llm-worker-{uuid4().hex[:8]}",
            stream_key=settings.llm_queue.stream_key,
            initial_concurrency=settings.llm_queue.worker_concurrency,
            max_concurrency=settings.llm_queue.worker_max_concurrency,
            min_concurrency=settings.llm_queue.worker_min_concurrency,
            model=settings.llm.model,
            max_tokens=settings.llm.max_tokens,
            response_ttl=settings.llm_queue.response_ttl,
            content_limit=settings.extraction_content_limit,
        )
        await self._llm_worker.initialize()
        import asyncio

        self._llm_worker_task = asyncio.create_task(self._llm_worker.start())

        self._started = True
        logger.info("service_container_started")

    async def stop(self) -> None:
        """Tear down services in reverse order."""
        if self._llm_worker:
            await self._llm_worker.stop()
        if self._llm_worker_task:
            await self._llm_worker_task
        if self._firecrawl_client:
            await self._firecrawl_client.close()
        if self._async_redis:
            await self._async_redis.close()
        self._started = False
        logger.info("service_container_stopped")

    def _check_started(self) -> None:
        if not self._started:
            raise RuntimeError("ServiceContainer not started — call start() first")

    @property
    def firecrawl_client(self) -> FirecrawlClient:
        self._check_started()
        return self._firecrawl_client  # type: ignore[return-value]

    @property
    def rate_limiter(self) -> DomainRateLimiter:
        self._check_started()
        return self._rate_limiter  # type: ignore[return-value]

    @property
    def retry_config(self) -> RetryConfig:
        self._check_started()
        return self._retry_config  # type: ignore[return-value]

    @property
    def embedding_service(self) -> EmbeddingService:
        self._check_started()
        return self._embedding_service  # type: ignore[return-value]

    @property
    def extraction_embedding(self) -> ExtractionEmbeddingService:
        self._check_started()
        return self._extraction_embedding  # type: ignore[return-value]

    @property
    def deduplicator(self) -> ExtractionDeduplicator:
        self._check_started()
        return self._deduplicator  # type: ignore[return-value]

    @property
    def llm_queue(self) -> LLMRequestQueue:
        self._check_started()
        return self._llm_queue  # type: ignore[return-value]

    async def __aenter__(self) -> "ServiceContainer":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        await self.stop()
