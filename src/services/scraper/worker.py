"""Background worker for processing scrape jobs."""

from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx
import structlog
from sqlalchemy.orm import Session

from orm_models import Job
from services.projects.repository import ProjectRepository
from services.scraper.client import FirecrawlClient
from services.scraper.rate_limiter import DomainRateLimiter, RateLimitExceeded
from services.scraper.retry import RetryConfig, retry_with_backoff
from services.storage.repositories.source import SourceRepository

logger = structlog.get_logger(__name__)


class ScraperWorker:
    """Background worker for processing scrape jobs.

    Handles queued scrape jobs by:
    1. Updating job status to "running"
    2. Scraping all URLs in the job payload
    3. Storing successful scrapes as Source records
    4. Updating job with results and completion status

    Args:
        db: Database session for persistence.
        firecrawl_client: Client for scraping URLs.
        rate_limiter: Optional rate limiter for controlling request frequency.

    Example:
        async with FirecrawlClient(base_url=settings.firecrawl_url) as client:
            limiter = DomainRateLimiter(redis_client, config)
            worker = ScraperWorker(db=db, firecrawl_client=client, rate_limiter=limiter)
            await worker.process_job(job)
    """

    def __init__(
        self,
        db: Session,
        firecrawl_client: FirecrawlClient,
        rate_limiter: DomainRateLimiter | None = None,
        retry_config: RetryConfig | None = None,
    ) -> None:
        """Initialize ScraperWorker.

        Args:
            db: Database session.
            firecrawl_client: Firecrawl client for scraping.
            rate_limiter: Optional rate limiter (default: None).
            retry_config: Optional retry configuration (default: None).
        """
        self.db = db
        self.client = firecrawl_client
        self.rate_limiter = rate_limiter
        self.retry_config = retry_config or RetryConfig(
            max_retries=3,
            base_delay=2.0,
            max_delay=60.0,
        )

        # Initialize repositories
        self.source_repo = SourceRepository(db)
        self.project_repo = ProjectRepository(db)

    async def process_job(self, job: Job) -> None:
        """Process a scrape job.

        Updates job status, scrapes all URLs, stores results, and marks job complete.

        Args:
            job: Job instance to process.

        Raises:
            None: All exceptions are caught and stored in job.error.
        """
        logger.info("job_processing_started", job_id=str(job.id), job_type=job.type)
        try:
            # Update job status to running
            job.status = "running"
            job.started_at = datetime.now(UTC)
            self.db.commit()

            # Extract payload data
            urls = job.payload.get("urls", [])
            source_group = job.payload.get("source_group") or job.payload.get(
                "company", ""
            )  # Backward compat
            project_id = job.payload.get("project_id")

            logger.info("job_processing_urls", job_id=str(job.id), url_count=len(urls))

            # Get or create default project if project_id not provided
            if not project_id:
                default_project = await self.project_repo.get_default_project()
                project_id = default_project.id

            # Track results
            sources_scraped = 0
            sources_failed = 0
            rate_limited = 0

            # Process each URL
            for url in urls:
                try:
                    # Extract domain for rate limiting
                    domain = self._extract_domain(url)

                    # Scrape with retry
                    result = await self._scrape_url_with_retry(url, domain)

                    # Store successful scrapes
                    if result and result.success and result.markdown:
                        await self.source_repo.create(
                            project_id=project_id,
                            uri=result.url,
                            source_group=source_group,
                            source_type="web",
                            title=result.title,
                            content=result.markdown,
                            meta_data={
                                "domain": result.domain,
                                **result.metadata,
                            },
                            status="pending",  # Ready for extraction
                        )
                        sources_scraped += 1
                        logger.debug(
                            "url_scraped_successfully",
                            job_id=str(job.id),
                            url=url,
                            domain=domain,
                        )
                    else:
                        sources_failed += 1
                        logger.warning("url_scrape_failed", job_id=str(job.id), url=url)

                except RateLimitExceeded:
                    # Rate limit hit for this domain, skip this URL
                    sources_failed += 1
                    rate_limited += 1
                    logger.warning(
                        "url_rate_limited", job_id=str(job.id), url=url, domain=domain
                    )
                    continue

            # Commit all sources
            self.db.commit()

            # Update job with results
            if sources_scraped == 0 and sources_failed > 0:
                # All URLs failed - mark job as failed
                job.status = "failed"
                if rate_limited > 0:
                    job.error = f"All {sources_failed} URLs failed ({rate_limited} rate limited)"
                else:
                    job.error = f"All {sources_failed} URLs failed to scrape"
                logger.error(
                    "job_processing_failed", job_id=str(job.id), error=job.error
                )
            else:
                job.status = "completed"
                logger.info(
                    "job_processing_completed",
                    job_id=str(job.id),
                    sources_scraped=sources_scraped,
                    sources_failed=sources_failed,
                )

            job.completed_at = datetime.now(UTC)
            job.result = {
                "sources_scraped": sources_scraped,
                "sources_failed": sources_failed,
                "rate_limited": rate_limited,
                "total_urls": len(urls),
            }
            self.db.commit()

        except RateLimitExceeded as e:
            # Handle rate limit exceeded at job level
            job.status = "failed"
            job.error = f"Rate limit exceeded: {str(e)}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()
            logger.error(
                "job_rate_limit_exceeded",
                job_id=str(job.id),
                error=str(e),
                exc_info=True,
            )

        except Exception as e:
            # Handle unexpected errors
            self.db.rollback()  # Rollback any partial changes
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self.db.commit()
            logger.error(
                "job_processing_error", job_id=str(job.id), error=str(e), exc_info=True
            )

    async def _scrape_url_with_retry(self, url: str, domain: str):
        """Scrape URL with retry logic.

        Args:
            url: URL to scrape.
            domain: Domain for rate limiting.

        Returns:
            ScrapeResult on success, None on failure after retries.

        Raises:
            RateLimitExceeded: If rate limit is exceeded (not retried).
        """
        async def do_scrape():
            if self.rate_limiter:
                await self.rate_limiter.acquire(domain)
            return await self.client.scrape(url)

        try:
            return await retry_with_backoff(
                func=do_scrape,
                config=self.retry_config,
                retryable_exceptions=(httpx.HTTPError, TimeoutError, ConnectionError),
                operation_name=f"scrape:{domain}",
            )
        except (httpx.HTTPError, TimeoutError, ConnectionError) as e:
            # Retryable exceptions exhausted - log and return None
            logger.error("scrape_failed_after_retries", url=url, error=str(e))
            return None
        except RateLimitExceeded:
            # Re-raise rate limit exceptions to be handled at job level
            raise
        # Let all other exceptions propagate to job-level error handler

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL.

        Args:
            url: Full URL.

        Returns:
            Domain name (e.g., "example.com").
        """
        parsed = urlparse(url)
        return parsed.netloc
