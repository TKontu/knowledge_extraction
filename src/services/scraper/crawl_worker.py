"""Background worker for processing crawl jobs."""

from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import structlog
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from config import settings
from orm_models import Job
from services.scraper.client import FirecrawlClient
from services.storage.repositories.source import SourceRepository

logger = structlog.get_logger(__name__)


class CrawlWorker:
    """Worker for processing crawl jobs."""

    def __init__(
        self,
        db: Session,
        firecrawl_client: FirecrawlClient,
    ) -> None:
        self.db = db
        self.client = firecrawl_client
        self.source_repo = SourceRepository(db)

    async def process_job(self, job: Job) -> None:
        """Process a crawl job."""
        logger.info("crawl_job_started", job_id=str(job.id))

        try:
            payload = job.payload
            firecrawl_job_id = payload.get("firecrawl_job_id")

            # Step 1: Start crawl if not already started
            if not firecrawl_job_id:
                # Use configured timeout (convert seconds to milliseconds)
                # Default 60s, but FlareSolverr may need more for anti-bot bypass
                scrape_timeout_ms = settings.scrape_timeout * 1000

                firecrawl_job_id = await self.client.start_crawl(
                    url=payload["url"],
                    max_depth=payload.get("max_depth", 2),
                    limit=payload.get("limit", 100),
                    include_paths=payload.get("include_paths"),
                    exclude_paths=payload.get("exclude_paths"),
                    allow_backward_links=payload.get("allow_backward_links", False),
                    scrape_timeout=scrape_timeout_ms,
                    delay_ms=settings.crawl_delay_ms,
                    max_concurrency=settings.crawl_max_concurrency,
                )

                # Store Firecrawl job ID (must flag_modified for JSON column)
                job.payload["firecrawl_job_id"] = firecrawl_job_id
                flag_modified(job, "payload")
                job.status = "running"
                job.started_at = datetime.now(UTC)
                self.db.commit()

                logger.info(
                    "crawl_started",
                    job_id=str(job.id),
                    firecrawl_job_id=firecrawl_job_id,
                )
                return  # Will be picked up again on next poll

            # Step 2: Check crawl status
            status = await self.client.get_crawl_status(firecrawl_job_id)

            # Update progress in result and touch updated_at to prevent redundant polling
            job.result = {
                "pages_total": status.total,
                "pages_completed": status.completed,
                "sources_created": 0,
            }
            job.updated_at = datetime.now(UTC)
            self.db.commit()

            if status.status == "scraping":
                logger.debug(
                    "crawl_in_progress",
                    job_id=str(job.id),
                    completed=status.completed,
                    total=status.total,
                )
                return  # Continue polling

            if status.status == "failed":
                job.status = "failed"
                job.error = status.error or "Crawl failed"
                job.completed_at = datetime.now(UTC)
                self.db.commit()
                logger.error("crawl_failed", job_id=str(job.id), error=status.error)
                return

            if status.status == "completed":
                # Step 3: Store all pages as sources
                sources_created = await self._store_pages(job, status.pages)

                job.status = "completed"
                job.completed_at = datetime.now(UTC)
                job.result = {
                    "pages_total": status.total,
                    "pages_completed": status.completed,
                    "sources_created": sources_created,
                }
                self.db.commit()

                logger.info(
                    "crawl_completed",
                    job_id=str(job.id),
                    sources_created=sources_created,
                )

                # Step 4: Auto-extract if enabled
                if payload.get("auto_extract", True):
                    await self._create_extraction_job(job)

            elif status.status not in ("scraping", "failed", "completed"):
                # Handle unknown status from Firecrawl
                logger.error(
                    "crawl_unknown_status",
                    job_id=str(job.id),
                    status=status.status,
                )
                job.status = "failed"
                job.error = f"Unexpected crawl status: {status.status}"
                job.completed_at = datetime.now(UTC)
                self.db.commit()

        except Exception as e:
            # Rollback any failed transaction before updating job status
            self.db.rollback()
            job.status = "failed"
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self.db.commit()
            logger.error("crawl_error", job_id=str(job.id), error=str(e))

    async def _store_pages(self, job: Job, pages: list[dict]) -> int:
        """Store crawled pages as Source records."""
        project_id = job.payload["project_id"]
        company = job.payload["company"]
        sources_created = 0

        for page in pages:
            metadata = page.get("metadata", {})
            markdown = page.get("markdown", "")
            url = metadata.get("url") or metadata.get("sourceURL", "")

            if not markdown or not url:
                logger.warning(
                    "crawl_page_skipped",
                    job_id=str(job.id),
                    url=url or "missing",
                    reason="missing_markdown" if not markdown else "missing_url",
                )
                continue

            # Check for duplicate URL
            existing = await self.source_repo.get_by_uri(project_id, url)
            if existing:
                logger.debug("source_already_exists", uri=url)
                continue

            domain = urlparse(url).netloc

            await self.source_repo.create(
                project_id=project_id,
                uri=url,
                source_group=company,
                source_type="web",
                title=metadata.get("title", ""),
                content=markdown,
                meta_data={"domain": domain, **metadata},
                status="pending",
            )
            sources_created += 1

        self.db.commit()
        return sources_created

    async def _create_extraction_job(self, crawl_job: Job) -> None:
        """Create extraction job for crawled sources."""
        extract_job = Job(
            id=uuid4(),
            type="extract",
            status="queued",
            payload={
                "project_id": crawl_job.payload["project_id"],
                "source_ids": None,  # Extract all pending
                "profile": crawl_job.payload.get("profile"),
            },
        )
        self.db.add(extract_job)
        self.db.commit()

        logger.info(
            "extraction_job_created",
            crawl_job_id=str(crawl_job.id),
            extract_job_id=str(extract_job.id),
        )
