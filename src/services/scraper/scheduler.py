"""Background task scheduler for processing scrape jobs."""

import asyncio
from typing import Optional

from sqlalchemy.orm import Session

from config import settings
from database import SessionLocal
from orm_models import Job
from redis_client import redis_client
from services.scraper.client import FirecrawlClient
from services.scraper.rate_limiter import DomainRateLimiter, RateLimitConfig
from services.scraper.worker import ScraperWorker


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
        self._task: Optional[asyncio.Task] = None
        self._firecrawl_client: Optional[FirecrawlClient] = None
        self._rate_limiter: Optional[DomainRateLimiter] = None

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
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        """Stop the background scheduler gracefully.

        Waits for current job to finish, then shuts down.
        """
        self._running = False
        if self._task:
            await self._task
        if self._firecrawl_client:
            await self._firecrawl_client.close()

    async def _run(self) -> None:
        """Main loop for processing jobs.

        Continuously polls database for queued jobs and processes them.
        """
        while self._running:
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
                        )
                        await worker.process_job(job)
                    else:
                        # No jobs available, wait before polling again
                        await asyncio.sleep(self.poll_interval)

                finally:
                    db.close()

            except Exception as e:
                # Log error but keep scheduler running
                print(f"Error in scheduler: {e}")
                await asyncio.sleep(self.poll_interval)

    async def __aenter__(self) -> "JobScheduler":
        """Enter async context manager."""
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager and cleanup."""
        await self.stop()


# Global scheduler instance
_scheduler: Optional[JobScheduler] = None


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
