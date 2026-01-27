"""Background worker for processing crawl jobs."""

import asyncio
import time
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


def _get_stale_warning(elapsed_seconds: float) -> str | None:
    """Get stale warning level based on elapsed time."""
    if elapsed_seconds >= 300:
        return "CRITICAL"
    if elapsed_seconds >= 120:
        return "HIGH"
    if elapsed_seconds >= 60:
        return "MEDIUM"
    if elapsed_seconds >= 30:
        return "LOW"
    return None


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
            poll_start = time.monotonic()
            status = await self.client.get_crawl_status(firecrawl_job_id)
            poll_duration_ms = int((time.monotonic() - poll_start) * 1000)

            # Calculate elapsed time since job started
            elapsed_seconds = 0.0
            if job.started_at:
                elapsed_seconds = (datetime.now(UTC) - job.started_at).total_seconds()
            stale_warning = _get_stale_warning(elapsed_seconds)

            # Update progress in result and touch updated_at to prevent redundant polling
            job.result = {
                "pages_total": status.total,
                "pages_completed": status.completed,
                "sources_created": 0,
            }
            job.updated_at = datetime.now(UTC)
            self.db.commit()

            if status.status == "scraping":
                log_data = {
                    "job_id": str(job.id),
                    "firecrawl_job_id": firecrawl_job_id,
                    "completed": status.completed,
                    "total": status.total,
                    "elapsed_seconds": round(elapsed_seconds, 1),
                    "poll_duration_ms": poll_duration_ms,
                }
                if stale_warning:
                    log_data["stale_warning"] = stale_warning
                    logger.warning("crawl_status_polled", **log_data)
                else:
                    logger.debug("crawl_status_polled", **log_data)
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

                # Issue #12 investigation: Log warning if crawl returned 0 pages
                if sources_created == 0:
                    logger.warning(
                        "crawl_completed_zero_sources",
                        job_id=str(job.id),
                        firecrawl_job_id=firecrawl_job_id,
                        pages_total=status.total,
                        pages_completed=status.completed,
                        pages_in_response=len(status.pages),
                        url=payload.get("url"),
                        company=payload.get("company"),
                    )
                else:
                    logger.info(
                        "crawl_completed",
                        job_id=str(job.id),
                        sources_created=sources_created,
                        pages_total=status.total,
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
            job.error = f"{type(e).__name__}: {str(e)}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()
            logger.error(
                "crawl_error",
                job_id=str(job.id),
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )

    async def _store_pages(self, job: Job, pages: list[dict]) -> int:
        """Store crawled pages as Source records."""
        project_id = job.payload["project_id"]
        company = job.payload["company"]
        sources_created = 0

        # Get language filtering settings from job payload (Layer 2: Content-Based Post-Filtering)
        language_detection_enabled = job.payload.get("language_detection_enabled", True)
        allowed_languages = job.payload.get("allowed_languages") or ["en"]

        # Initialize language service if needed
        lang_service = None
        if language_detection_enabled and settings.language_filtering_enabled:
            from services.filtering.language import get_language_service

            lang_service = get_language_service(
                confidence_threshold=settings.language_detection_confidence_threshold
            )

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

            # Filter HTTP errors (400+) - don't store error pages
            status_code = metadata.get("statusCode")
            if status_code and status_code >= 400:
                logger.warning(
                    "page_http_error_skipped",
                    job_id=str(job.id),
                    url=url,
                    status_code=status_code,
                    reason="HTTP error pages not stored as sources",
                )
                continue

            # Language detection (if enabled)
            if lang_service:
                try:
                    # Detect language with timeout
                    result = await asyncio.wait_for(
                        lang_service.detect(markdown, url=url),
                        timeout=settings.language_detection_timeout_seconds,
                    )

                    if result.language not in allowed_languages:
                        logger.info(
                            "page_language_filtered",
                            job_id=str(job.id),
                            url=url,
                            detected_language=result.language,
                            confidence=result.confidence,
                            allowed_languages=allowed_languages,
                            detection_method=result.detected_from,
                        )
                        continue

                    # Store language detection result in metadata
                    metadata["detected_language"] = result.language
                    metadata["language_confidence"] = result.confidence

                except TimeoutError:
                    logger.warning(
                        "language_detection_timeout",
                        job_id=str(job.id),
                        url=url,
                        timeout=settings.language_detection_timeout_seconds,
                    )
                    # Continue storing page (timeout shouldn't block crawl)
                except Exception as e:
                    logger.error(
                        "language_detection_error",
                        job_id=str(job.id),
                        url=url,
                        error=str(e),
                        exc_info=True,
                    )
                    # Continue storing page (detection error shouldn't break crawl)

            domain = urlparse(url).netloc

            # Use upsert to handle race conditions when concurrent crawlers
            # process the same URL. The unique constraint prevents duplicates.
            source, created = await self.source_repo.upsert(
                project_id=project_id,
                uri=url,
                source_group=company,
                source_type="web",
                title=metadata.get("title", ""),
                content=markdown,
                meta_data={"domain": domain, "http_status": status_code, **metadata},
                status="pending",
                created_by_job_id=job.id,
            )
            if created:
                sources_created += 1
            else:
                logger.debug("source_already_exists", uri=url)

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
