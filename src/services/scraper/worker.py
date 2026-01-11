"""Background worker for processing scrape jobs."""

from datetime import datetime, UTC
from typing import Optional
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.orm import Session

from orm_models import Job
from services.scraper.client import FirecrawlClient, ScrapeResult
from services.scraper.rate_limiter import DomainRateLimiter, RateLimitExceeded
from services.storage.repositories.source import SourceRepository
from services.projects.repository import ProjectRepository


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
        rate_limiter: Optional[DomainRateLimiter] = None,
    ) -> None:
        """Initialize ScraperWorker.

        Args:
            db: Database session.
            firecrawl_client: Firecrawl client for scraping.
            rate_limiter: Optional rate limiter (default: None).
        """
        self.db = db
        self.client = firecrawl_client
        self.rate_limiter = rate_limiter

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
        try:
            # Update job status to running
            job.status = "running"
            job.started_at = datetime.now(UTC)
            self.db.commit()

            # Extract payload data
            urls = job.payload.get("urls", [])
            source_group = job.payload.get("source_group") or job.payload.get("company", "")  # Backward compat
            profile = job.payload.get("profile")
            project_id = job.payload.get("project_id")

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

                    # Acquire rate limit permission if rate limiter is available
                    if self.rate_limiter:
                        await self.rate_limiter.acquire(domain)

                    # Scrape the URL
                    result = await self.client.scrape(url)

                    # Store successful scrapes
                    if result.success and result.markdown:
                        source = await self.source_repo.create(
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
                            status="completed",
                        )
                        sources_scraped += 1
                    else:
                        sources_failed += 1

                except RateLimitExceeded:
                    # Rate limit hit for this domain, skip this URL
                    sources_failed += 1
                    rate_limited += 1
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
            else:
                job.status = "completed"

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

        except Exception as e:
            # Handle unexpected errors
            job.status = "failed"
            job.error = str(e).lower()
            job.completed_at = datetime.now(UTC)
            self.db.commit()

    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL.

        Args:
            url: Full URL.

        Returns:
            Domain name (e.g., "example.com").
        """
        parsed = urlparse(url)
        return parsed.netloc
