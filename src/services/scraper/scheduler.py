"""Background task scheduler for processing scrape jobs."""

import asyncio

import structlog
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

from config import settings
from database import SessionLocal
from orm_models import Job
from qdrant_connection import qdrant_client
from redis_client import redis_client
from services.extraction.extractor import ExtractionOrchestrator
from services.extraction.pipeline import ExtractionPipelineService
from services.extraction.profiles import ProfileRepository
from services.extraction.worker import ExtractionWorker
from services.knowledge.extractor import EntityExtractor
from services.llm.client import LLMClient
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
        self._crawl_task: asyncio.Task | None = None
        self._firecrawl_client: FirecrawlClient | None = None
        self._rate_limiter: DomainRateLimiter | None = None
        self._retry_config: RetryConfig | None = None

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
        # Start scrape, crawl, and extract workers concurrently
        self._scrape_task = asyncio.create_task(self._run_scrape_worker())
        self._crawl_task = asyncio.create_task(self._run_crawl_worker())
        self._extract_task = asyncio.create_task(self._run_extract_worker())

    async def stop(self) -> None:
        """Stop the background scheduler gracefully.

        Waits for current job to finish, then shuts down.
        """
        self._running = False
        if self._scrape_task:
            await self._scrape_task
        if self._crawl_task:
            await self._crawl_task
        if self._extract_task:
            await self._extract_task
        if self._firecrawl_client:
            await self._firecrawl_client.close()

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
                    # Query for queued scrape jobs
                    job = (
                        db.query(Job)
                        .filter(Job.type == "scrape", Job.status == "queued")
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .first()
                    )

                    if job:
                        # Process the job with rate limiting
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

    async def _run_crawl_worker(self) -> None:
        """Main loop for processing crawl jobs with multi-domain parallelism."""
        shutdown = get_shutdown_manager()
        while self._running and not shutdown.is_shutting_down:
            try:
                db: Session = SessionLocal()
                try:
                    # Query for crawl jobs that need processing
                    # Get up to max_concurrent_crawls jobs
                    jobs = (
                        db.query(Job)
                        .filter(
                            Job.type == "crawl",
                            Job.status.in_(["queued", "running"]),
                        )
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .limit(settings.max_concurrent_crawls)
                        .all()
                    )

                    if jobs:
                        # Process jobs in parallel using asyncio.gather
                        tasks = []
                        for job in jobs:
                            # Create separate DB session for each worker
                            job_db = SessionLocal()
                            worker = CrawlWorker(
                                db=job_db,
                                firecrawl_client=self._firecrawl_client,
                            )
                            # Create task and store DB session for cleanup
                            task = self._process_crawl_job_with_cleanup(
                                worker, job, job_db
                            )
                            tasks.append(task)

                        # Wait for all jobs to process (they poll internally)
                        await asyncio.gather(*tasks, return_exceptions=True)
                    else:
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
                logger.error("crawl_worker_error", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _process_crawl_job_with_cleanup(
        self, worker: CrawlWorker, job: Job, db: Session
    ) -> None:
        """Process a single crawl job and cleanup its DB session.

        Args:
            worker: CrawlWorker instance.
            job: Job to process.
            db: Database session to cleanup after processing.
        """
        try:
            await worker.process_job(job)
        finally:
            db.close()

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
                    # Query for queued extract jobs
                    job = (
                        db.query(Job)
                        .filter(Job.type == "extract", Job.status == "queued")
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .first()
                    )

                    if job:
                        # Initialize pipeline service with all dependencies
                        llm_client = LLMClient(settings)
                        orchestrator = ExtractionOrchestrator(
                            llm_client=llm_client
                        )
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
