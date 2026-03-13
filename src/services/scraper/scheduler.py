"""Background task scheduler for processing scrape jobs."""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy.orm import Session

logger = structlog.get_logger(__name__)

from config import settings
from constants import JobStatus, JobType
from database import SessionLocal
from orm_models import Job
from services.extraction.consolidation_worker import ConsolidationWorker
from services.extraction.worker import ExtractionWorker
from services.scraper.crawl_worker import CrawlWorker
from services.scraper.service_container import ServiceContainer
from services.scraper.worker import ScraperWorker
from shutdown import get_shutdown_manager


# Per-job-type stale thresholds
# These prevent long-running jobs from being incorrectly marked as stale
def get_stale_thresholds() -> dict[str, timedelta]:
    """Get stale thresholds from settings.

    Returns:
        Dictionary mapping job types to their stale thresholds.
    """
    return {
        JobType.SCRAPE: timedelta(seconds=settings.scheduler.stale_threshold_scrape),
        JobType.EXTRACT: timedelta(seconds=settings.scheduler.stale_threshold_extract),
        JobType.CRAWL: timedelta(seconds=settings.scheduler.stale_threshold_crawl),
        JobType.CONSOLIDATE: timedelta(seconds=1800),  # 30 minutes for LLM consolidation
        "default": timedelta(seconds=600),  # 10 minutes default
    }


class JobScheduler:
    """Background scheduler for processing queued jobs.

    Periodically checks for queued scrape jobs and processes them
    using the ScraperWorker. Receives a ServiceContainer for access
    to app-lifetime services.

    Args:
        services: Container holding initialized services.
        poll_interval: Seconds between database polls for new jobs.

    Example:
        container = ServiceContainer()
        await container.start()
        scheduler = JobScheduler(services=container, poll_interval=5)
        await scheduler.start()
        await scheduler.stop()
        await container.stop()
    """

    def __init__(self, services: ServiceContainer, poll_interval: int = 5) -> None:
        self._services = services
        self.poll_interval = poll_interval
        self._running = False
        self._scrape_task: asyncio.Task | None = None
        self._extract_task: asyncio.Task | None = None
        self._consolidate_task: asyncio.Task | None = None
        self._crawl_tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start the background scheduler.

        Performs startup cleanup of stale jobs, then starts worker loops
        with a configurable stagger delay between each.
        """
        self._running = True

        # Startup resilience: cleanup stale jobs from previous instance
        if settings.scheduler.cleanup_stale_on_startup:
            await self._cleanup_stale_jobs()

        # Startup resilience: stagger worker creation
        stagger = settings.scheduler.startup_stagger_seconds

        self._scrape_task = asyncio.create_task(self._run_scrape_worker())
        if stagger > 0:
            await asyncio.sleep(stagger)

        # Spawn multiple crawl workers for multi-domain parallelism
        num_crawl_workers = settings.crawl.max_concurrent_crawls
        for i in range(num_crawl_workers):
            self._crawl_tasks.append(
                asyncio.create_task(self._run_single_crawl_worker(worker_id=i))
            )
            if stagger > 0:
                await asyncio.sleep(stagger)

        if stagger > 0:
            await asyncio.sleep(stagger)
        self._extract_task = asyncio.create_task(self._run_extract_worker())

        if stagger > 0:
            await asyncio.sleep(stagger)
        self._consolidate_task = asyncio.create_task(self._run_consolidate_worker())

    async def stop(self) -> None:
        """Stop the background scheduler gracefully.

        Waits for worker loops to finish. Does NOT stop services —
        the ServiceContainer handles that.
        """
        self._running = False
        if self._scrape_task:
            await self._scrape_task
        if self._crawl_tasks:
            await asyncio.gather(*self._crawl_tasks, return_exceptions=True)
        if self._extract_task:
            await self._extract_task
        if self._consolidate_task:
            await self._consolidate_task

    async def _cleanup_stale_jobs(self) -> dict[str, int]:
        """Mark running/cancelling jobs as failed on startup.

        At startup, no jobs can be legitimately running since the process
        just started. Any jobs in running/cancelling state are leftovers
        from a crashed previous instance.
        """
        db = SessionLocal()
        try:
            now = datetime.now(UTC)
            counts: dict[str, int] = {}
            for status in (JobStatus.RUNNING, JobStatus.CANCELLING):
                stale = (
                    db.query(Job)
                    .filter(Job.status == status)
                    .with_for_update(skip_locked=True)
                    .all()
                )
                for job in stale:
                    job.status = JobStatus.FAILED
                    job.error = (
                        f"Server restart: was {status} when previous instance stopped"
                    )
                    job.completed_at = now
                    logger.warning(
                        "startup_cleanup_stale_job",
                        job_id=str(job.id),
                        job_type=job.type,
                        previous_status=status,
                    )
                counts[status] = len(stale)
            db.commit()
            total = sum(counts.values())
            logger.info("scheduler_startup_cleanup", total=total, **counts)
            return counts
        except Exception as e:
            db.rollback()
            logger.error("startup_cleanup_failed", error=str(e))
            return {}
        finally:
            db.close()

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
                    job = (
                        db.query(Job)
                        .filter(
                            Job.type == JobType.SCRAPE, Job.status == JobStatus.QUEUED
                        )
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .with_for_update(skip_locked=True)
                        .first()
                    )

                    # If no queued jobs, check for stale running jobs that need recovery
                    if not job:
                        thresholds = get_stale_thresholds()
                        stale_threshold = datetime.now(UTC) - thresholds["scrape"]
                        job = (
                            db.query(Job)
                            .filter(
                                Job.type == JobType.SCRAPE,
                                Job.status == JobStatus.RUNNING,
                                Job.updated_at < stale_threshold,
                            )
                            .order_by(Job.priority.desc(), Job.created_at.asc())
                            .with_for_update(skip_locked=True)
                            .first()
                        )
                        if job:
                            runtime = datetime.now(UTC) - job.updated_at
                            logger.warning(
                                "scrape_recovering_stale_job",
                                job_id=str(job.id),
                                job_type=JobType.SCRAPE,
                                runtime_seconds=runtime.total_seconds(),
                                updated_at=str(job.updated_at),
                                threshold_seconds=thresholds["scrape"].total_seconds(),
                            )

                    if job:
                        worker = ScraperWorker(
                            db=db,
                            firecrawl_client=self._services.firecrawl_client,
                            rate_limiter=self._services.rate_limiter,
                            retry_config=self._services.retry_config,
                        )
                        await worker.process_job(job)
                    else:
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
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
                    # First, try to get a queued job (highest priority)
                    job = (
                        db.query(Job)
                        .filter(
                            Job.type == JobType.CRAWL,
                            Job.status == JobStatus.QUEUED,
                        )
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .with_for_update(skip_locked=True)
                        .first()
                    )

                    # If no queued jobs, try to get a running job that needs polling
                    if not job:
                        poll_threshold = datetime.now(UTC) - timedelta(
                            seconds=settings.crawl.poll_interval
                        )
                        job = (
                            db.query(Job)
                            .filter(
                                Job.type == JobType.CRAWL,
                                Job.status == JobStatus.RUNNING,
                                Job.updated_at < poll_threshold,
                            )
                            .order_by(Job.updated_at.asc())
                            .with_for_update(skip_locked=True)
                            .first()
                        )

                        # Log warning if job is past stale threshold
                        if job:
                            thresholds = get_stale_thresholds()
                            stale_threshold = datetime.now(UTC) - thresholds["crawl"]
                            if job.updated_at < stale_threshold:
                                runtime = datetime.now(UTC) - job.updated_at
                                logger.warning(
                                    "crawl_recovering_stale_job",
                                    job_id=str(job.id),
                                    job_type=JobType.CRAWL,
                                    runtime_seconds=runtime.total_seconds(),
                                    updated_at=str(job.updated_at),
                                    threshold_seconds=thresholds[
                                        "crawl"
                                    ].total_seconds(),
                                )

                    if job:
                        worker = CrawlWorker(
                            db=db,
                            firecrawl_client=self._services.firecrawl_client,
                        )
                        await worker.process_job(job)
                    else:
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
                db: Session = SessionLocal()
                try:
                    job = (
                        db.query(Job)
                        .filter(
                            Job.type == JobType.EXTRACT, Job.status == JobStatus.QUEUED
                        )
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .with_for_update(skip_locked=True)
                        .first()
                    )

                    # If no queued jobs, check for stale running jobs
                    if not job:
                        thresholds = get_stale_thresholds()
                        stale_threshold = datetime.now(UTC) - thresholds["extract"]
                        job = (
                            db.query(Job)
                            .filter(
                                Job.type == JobType.EXTRACT,
                                Job.status == JobStatus.RUNNING,
                                Job.updated_at < stale_threshold,
                            )
                            .order_by(Job.priority.desc(), Job.created_at.asc())
                            .with_for_update(skip_locked=True)
                            .first()
                        )
                        if job:
                            runtime = datetime.now(UTC) - job.updated_at
                            logger.warning(
                                "extract_recovering_stale_job",
                                job_id=str(job.id),
                                job_type=JobType.EXTRACT,
                                runtime_seconds=runtime.total_seconds(),
                                updated_at=str(job.updated_at),
                                threshold_seconds=thresholds["extract"].total_seconds(),
                            )

                    if job:
                        llm_queue = (
                            self._services.llm_queue
                            if settings.llm_queue.enabled
                            else None
                        )

                        worker = ExtractionWorker(
                            db=db,
                            llm=settings.llm,
                            extraction=settings.extraction,
                            classification=settings.classification,
                            embedding_service=self._services.embedding_service,
                            extraction_embedding=self._services.extraction_embedding,
                            request_timeout=settings.llm_queue.request_timeout,
                            llm_queue=llm_queue,
                        )
                        await worker.process_job(job)
                    else:
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
                logger.error("extract_worker_error", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)

    async def _run_consolidate_worker(self) -> None:
        """Main loop for processing consolidation jobs.

        Continuously polls database for queued consolidation jobs and processes them.
        """
        shutdown = get_shutdown_manager()
        while self._running and not shutdown.is_shutting_down:
            try:
                db: Session = SessionLocal()
                try:
                    job = (
                        db.query(Job)
                        .filter(
                            Job.type == JobType.CONSOLIDATE,
                            Job.status == JobStatus.QUEUED,
                        )
                        .order_by(Job.priority.desc(), Job.created_at.asc())
                        .with_for_update(skip_locked=True)
                        .first()
                    )

                    if job:
                        worker = ConsolidationWorker(
                            db=db,
                            llm_config=settings.llm,
                        )
                        await worker.process_job(job)
                    else:
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
                logger.error("consolidate_worker_error", error=str(e), exc_info=True)
                await asyncio.sleep(self.poll_interval)


# Global instances for start_scheduler()/stop_scheduler()
_container: ServiceContainer | None = None
_scheduler: JobScheduler | None = None


async def start_scheduler() -> None:
    """Start the global job scheduler.

    Should be called during application startup.
    """
    global _container, _scheduler
    if _container is None:
        _container = ServiceContainer()
        await _container.start()
    if _scheduler is None:
        _scheduler = JobScheduler(services=_container, poll_interval=5)
        await _scheduler.start()


async def stop_scheduler() -> None:
    """Stop the global job scheduler.

    Should be called during application shutdown.
    """
    global _container, _scheduler
    if _scheduler is not None:
        await _scheduler.stop()
        _scheduler = None
    if _container is not None:
        await _container.stop()
        _container = None
