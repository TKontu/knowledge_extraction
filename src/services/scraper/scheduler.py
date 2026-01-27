"""Background task scheduler for processing scrape jobs."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import structlog
from openai import AsyncOpenAI
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

from config import settings
from database import SessionLocal
from orm_models import Job
from qdrant_connection import qdrant_client
from redis_client import get_async_redis, redis_client
from services.extraction.extractor import ExtractionOrchestrator
from services.extraction.pipeline import ExtractionPipelineService
from services.extraction.profiles import ProfileRepository
from services.extraction.worker import ExtractionWorker
from services.knowledge.extractor import EntityExtractor
from services.llm.client import LLMClient
from services.llm.queue import LLMRequestQueue
from services.llm.worker import LLMWorker
from services.projects.repository import ProjectRepository
from services.scraper.client import FirecrawlClient
from services.scraper.crawl_worker import CrawlWorker
from services.scraper.rate_limiter import DomainRateLimiter, RateLimitConfig
from services.scraper.retry import RetryConfig
from services.scraper.worker import ScraperWorker
from services.storage.deduplication import ExtractionDeduplicator
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository
from services.storage.repositories.entity import EntityRepository
from services.storage.repositories.extraction import ExtractionRepository
from services.storage.repositories.source import SourceRepository
from shutdown import get_shutdown_manager


class JobScheduler:
    """Background scheduler for processing queued jobs.

    Periodically checks for queued scrape jobs and processes them
    using the ScraperWorker.

    Args:
        poll_interval: Seconds between database polls for new jobs.

    Example:
        scheduler = JobScheduler(poll_interval=5)
        await scheduler.start()  # Runs until stopped
        await scheduler.stop()   # Graceful shutdown
    """

    def __init__(self, poll_interval: int = 5) -> None:
        """Initialize JobScheduler.

        Args:
            poll_interval: Seconds to wait between polling for jobs (default: 5).
        """
        self.poll_interval = poll_interval
        self._running = False
        self._scrape_task: asyncio.Task | None = None
        self._extract_task: asyncio.Task | None = None
        self._crawl_tasks: list[asyncio.Task] = []
        self._firecrawl_client: FirecrawlClient | None = None
        self._rate_limiter: DomainRateLimiter | None = None
        self._retry_config: RetryConfig | None = None
        self._llm_queue: LLMRequestQueue | None = None
        self._llm_worker: LLMWorker | None = None
        self._llm_worker_task: asyncio.Task | None = None
        self._async_redis = None  # Async Redis client for LLM queue

    async def start(self) -> None:
        """Start the background scheduler.

        Begins polling for queued jobs and processing them.
        """
        self._running = True
        self._firecrawl_client = FirecrawlClient(
            base_url=settings.firecrawl_url,
            timeout=settings.scrape_timeout,
        )
        # Initialize rate limiter
        rate_limit_config = RateLimitConfig(
            delay_min=settings.scrape_delay_min,
            delay_max=settings.scrape_delay_max,
            daily_limit=settings.scrape_daily_limit_per_domain,
        )
        self._rate_limiter = DomainRateLimiter(
            redis_client=redis_client,
            config=rate_limit_config,
        )
        # Initialize retry config
        self._retry_config = RetryConfig(
            max_retries=settings.scrape_retry_max_attempts,
            base_delay=settings.scrape_retry_base_delay,
            max_delay=settings.scrape_retry_max_delay,
        )

        # Initialize LLM request queue and worker with async Redis
        self._async_redis = await get_async_redis()
        self._llm_queue = LLMRequestQueue(
            redis=self._async_redis,
            stream_key="llm:requests",
            max_queue_depth=1000,
            backpressure_threshold=500,
        )
        llm_client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout=settings.llm_http_timeout,
        )
        self._llm_worker = LLMWorker(
            redis=self._async_redis,
            llm_client=llm_client,
            worker_id=f"llm-worker-{uuid4().hex[:8]}",
            stream_key="llm:requests",
            initial_concurrency=settings.llm_worker_concurrency,
            max_concurrency=settings.llm_worker_max_concurrency,
            min_concurrency=settings.llm_worker_min_concurrency,
            model=settings.llm_model,
        )
        await self._llm_worker.initialize()
        self._llm_worker_task = asyncio.create_task(self._llm_worker.start())

        # Start scrape, crawl, and extract workers concurrently
        self._scrape_task = asyncio.create_task(self._run_scrape_worker())

        # Spawn multiple crawl workers for multi-domain parallelism
        num_crawl_workers = settings.max_concurrent_crawls
        self._crawl_tasks = [
            asyncio.create_task(self._run_single_crawl_worker(worker_id=i))
            for i in range(num_crawl_workers)
        ]

        self._extract_task = asyncio.create_task(self._run_extract_worker())

    async def stop(self) -> None:
        """Stop the background scheduler gracefully.

        Waits for current job to finish, then shuts down.
        """
        self._running = False
        if self._scrape_task:
            await self._scrape_task
        if self._crawl_tasks:
            await asyncio.gather(*self._crawl_tasks, return_exceptions=True)
        if self._extract_task:
            await self._extract_task
        if self._llm_worker:
            await self._llm_worker.stop()
        if self._llm_worker_task:
            await self._llm_worker_task
        if self._firecrawl_client:
            await self._firecrawl_client.close()
        if self._async_redis:
            await self._async_redis.close()

    async def _run_scrape_worker(self) -> None:
        """Main loop for processing scrape jobs.

        Continuously polls database for queued scrape jobs and processes them.
        """
        shutdown = get_shutdown_manager()
        while self._running and not shutdown.is_shutting_down:
            try:
                # Get a database session
                db: Session = SessionLocal()
                try:
                    # Atomically claim a job using SELECT FOR UPDATE SKIP LOCKED
                    # This prevents race conditions where multiple workers grab the same job
                    # The row lock is held for the transaction duration, ensuring exclusive access
                    job = (
                        db.query(Job)
                        .filter(Job.type == "scrape", Job.status == "queued")
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .with_for_update(skip_locked=True)
                        .first()
                    )

                    # If no queued jobs, check for stale running jobs that need recovery
                    if not job:
                        stale_threshold = datetime.now(UTC) - timedelta(
                            seconds=self.poll_interval
                        )
                        job = (
                            db.query(Job)
                            .filter(
                                Job.type == "scrape",
                                Job.status == "running",
                                Job.updated_at < stale_threshold,
                            )
                            .order_by(Job.priority.desc(), Job.created_at.asc())
                            .with_for_update(skip_locked=True)
                            .first()
                        )
                        if job:
                            logger.warning(
                                "scrape_recovering_stale_job",
                                job_id=str(job.id),
                                updated_at=str(job.updated_at),
                            )

                    if job:
                        # Process the job with rate limiting
                        # The worker will handle status transitions within its own transaction
                        worker = ScraperWorker(
                            db=db,
                            firecrawl_client=self._firecrawl_client,
                            rate_limiter=self._rate_limiter,
                            retry_config=self._retry_config,
                        )
                        await worker.process_job(job)
                    else:
                        # No jobs available, wait before polling again
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
                # Log error but keep scheduler running
                logger.error("scrape_worker_error", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _run_single_crawl_worker(self, worker_id: int) -> None:
        """Single crawl worker that processes one job at a time.

        This worker continuously polls for crawl jobs and processes them to completion.
        Multiple instances of this worker run in parallel to enable multi-domain parallelism.

        Args:
            worker_id: Unique identifier for this worker (for logging).
        """
        shutdown = get_shutdown_manager()
        logger.info("crawl_worker_started", worker_id=worker_id)

        while self._running and not shutdown.is_shutting_down:
            try:
                db: Session = SessionLocal()
                try:
                    # Strategy: Prefer queued jobs, only poll running jobs if stale
                    # This prevents multiple workers from redundantly polling the same job

                    # First, try to get a queued job (highest priority)
                    job = (
                        db.query(Job)
                        .filter(
                            Job.type == "crawl",
                            Job.status == "queued",
                        )
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .with_for_update(skip_locked=True)
                        .first()
                    )

                    # If no queued jobs, try to get a running job that needs polling
                    # Only poll if it hasn't been updated recently (avoid redundant polls)
                    if not job:
                        stale_threshold = datetime.now(UTC) - timedelta(
                            seconds=self.poll_interval
                        )
                        job = (
                            db.query(Job)
                            .filter(
                                Job.type == "crawl",
                                Job.status == "running",
                                Job.updated_at < stale_threshold,
                            )
                            .order_by(Job.priority.desc(), Job.created_at.asc())
                            .with_for_update(skip_locked=True)
                            .first()
                        )

                    if job:
                        worker = CrawlWorker(
                            db=db,
                            firecrawl_client=self._firecrawl_client,
                        )
                        await worker.process_job(job)
                    else:
                        # No jobs available, wait before polling again
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
                logger.error(
                    "crawl_worker_error",
                    worker_id=worker_id,
                    error=str(e),
                    exc_info=True,
                )
                await asyncio.sleep(self.poll_interval)

        logger.info("crawl_worker_stopped", worker_id=worker_id)

    async def _run_extract_worker(self) -> None:
        """Main loop for processing extraction jobs.

        Continuously polls database for queued extract jobs and processes them.
        """
        shutdown = get_shutdown_manager()
        while self._running and not shutdown.is_shutting_down:
            try:
                # Get a database session
                db: Session = SessionLocal()
                try:
                    # Atomically claim a job using SELECT FOR UPDATE SKIP LOCKED
                    # This prevents race conditions where multiple workers grab the same job
                    # The row lock is held for the transaction duration, ensuring exclusive access
                    job = (
                        db.query(Job)
                        .filter(Job.type == "extract", Job.status == "queued")
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .with_for_update(skip_locked=True)
                        .first()
                    )

                    # If no queued jobs, check for stale running jobs that need recovery
                    if not job:
                        stale_threshold = datetime.now(UTC) - timedelta(
                            seconds=self.poll_interval
                        )
                        job = (
                            db.query(Job)
                            .filter(
                                Job.type == "extract",
                                Job.status == "running",
                                Job.updated_at < stale_threshold,
                            )
                            .order_by(Job.priority.desc(), Job.created_at.asc())
                            .with_for_update(skip_locked=True)
                            .first()
                        )
                        if job:
                            logger.warning(
                                "extract_recovering_stale_job",
                                job_id=str(job.id),
                                updated_at=str(job.updated_at),
                            )

                    if job:
                        # Initialize pipeline service with all dependencies
                        # Pass LLM queue to LLMClient when queue mode is enabled
                        llm_queue = (
                            self._llm_queue if settings.llm_queue_enabled else None
                        )
                        llm_client = LLMClient(settings, llm_queue=llm_queue)
                        orchestrator = ExtractionOrchestrator(llm_client=llm_client)
                        embedding_service = EmbeddingService(settings)
                        qdrant_repo = QdrantRepository(qdrant_client)
                        deduplicator = ExtractionDeduplicator(
                            embedding_service=embedding_service,
                            qdrant_repo=qdrant_repo,
                        )
                        entity_extractor = EntityExtractor(
                            llm_client=llm_client,
                            entity_repo=EntityRepository(db),
                        )
                        pipeline_service = ExtractionPipelineService(
                            orchestrator=orchestrator,
                            deduplicator=deduplicator,
                            entity_extractor=entity_extractor,
                            extraction_repo=ExtractionRepository(db),
                            source_repo=SourceRepository(db),
                            project_repo=ProjectRepository(db),
                            qdrant_repo=qdrant_repo,
                            embedding_service=embedding_service,
                            profile_repo=ProfileRepository(db),
                        )

                        # Process the job
                        # The worker will handle status transitions within its own transaction
                        worker = ExtractionWorker(
                            db=db,
                            pipeline_service=pipeline_service,
                        )
                        await worker.process_job(job)
                    else:
                        # No jobs available, wait before polling again
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
                # Log error but keep scheduler running
                logger.error("extract_worker_error", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def __aenter__(self) -> "JobScheduler":
        """Enter async context manager."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager and cleanup."""
        await self.stop()


# Global scheduler instance
_scheduler: JobScheduler | None = None


async def start_scheduler() -> None:
    """Start the global job scheduler.

    Should be called during application startup.
    """
    global _scheduler
    if _scheduler is None:
        _scheduler = JobScheduler(poll_interval=5)
        await _scheduler.start()


async def stop_scheduler() -> None:
    """Stop the global job scheduler.

    Should be called during application shutdown.
    """
    global _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
