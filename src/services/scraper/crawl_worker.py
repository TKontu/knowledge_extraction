"""Background worker for processing crawl jobs."""

import asyncio
import re
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
from services.storage.embedding import EmbeddingService
from services.storage.repositories.job import JobRepository
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
    """Worker for processing crawl jobs.

    Supports two crawl modes:
    1. Traditional crawl: Uses Firecrawl's crawl endpoint (recursive)
    2. Smart crawl: Uses Map → Filter → Batch Scrape flow for intelligent URL selection
    """

    def __init__(
        self,
        db: Session,
        firecrawl_client: FirecrawlClient,
        embedding_service: EmbeddingService | None = None,
    ) -> None:
        """Initialize CrawlWorker.

        Args:
            db: Database session.
            firecrawl_client: Firecrawl API client.
            embedding_service: Optional embedding service for smart crawl filtering.
                Required only when smart_crawl_enabled=True.
        """
        self.db = db
        self.client = firecrawl_client
        self.source_repo = SourceRepository(db)
        self.job_repo = JobRepository(db)
        self._embedding_service = embedding_service
        self._url_filter = None  # Lazy-loaded

    async def process_job(self, job: Job) -> None:
        """Process a crawl job.

        Routes to traditional or smart crawl based on smart_crawl_enabled flag.
        """
        logger.info("crawl_job_started", job_id=str(job.id))

        try:
            # Check for cancellation before processing
            if self.job_repo.is_cancellation_requested(job.id):
                logger.info("crawl_job_cancelled_early", job_id=str(job.id))
                self.job_repo.mark_cancelled(job.id)
                job.result = {
                    "cancelled_early": True,
                    "reason": "Cancelled before processing started",
                }
                self.db.commit()
                return

            payload = job.payload

            # Route to smart crawl or traditional crawl
            if payload.get("smart_crawl_enabled"):
                await self._process_smart_crawl(job)
                return

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
                # Check for cancellation before storing pages
                # Note: Firecrawl crawl cannot be cancelled, but we can skip storing results
                if self.job_repo.is_cancellation_requested(job.id):
                    logger.info(
                        "crawl_job_cancelled_before_storage",
                        job_id=str(job.id),
                        firecrawl_job_id=firecrawl_job_id,
                        pages_available=len(status.pages),
                    )
                    self.job_repo.mark_cancelled(job.id)
                    job.result = {
                        "cancelled_before_storage": True,
                        "pages_available": len(status.pages),
                        "pages_stored": 0,
                    }
                    self.db.commit()
                    return

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
                    # Flag for downstream processing - language not confirmed
                    metadata["language_detection_failed"] = True
                    metadata["language_detection_error"] = "timeout"
                    # Continue storing page (timeout shouldn't block crawl)
                except Exception as e:
                    logger.error(
                        "language_detection_error",
                        job_id=str(job.id),
                        url=url,
                        error=str(e),
                        exc_info=True,
                    )
                    # Flag for downstream processing - language not confirmed
                    metadata["language_detection_failed"] = True
                    metadata["language_detection_error"] = str(e)[:200]
                    # Continue storing page (detection error shouldn't break crawl)

            domain = urlparse(url).netloc

            # Use upsert to handle race conditions when concurrent crawlers
            # process the same URL. The unique constraint prevents duplicates.
            source, created = self.source_repo.upsert(
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

    # ==================== Smart Crawl Methods ====================

    async def _process_smart_crawl(self, job: Job) -> None:
        """Process a smart crawl job through Map → Filter → Scrape phases.

        Smart crawl uses Firecrawl's Map endpoint to discover URLs, filters
        them by relevance to the project's field_groups, then batch scrapes
        only the relevant URLs.

        Args:
            job: The crawl job with smart_crawl_enabled=True.
        """
        payload = job.payload
        phase = payload.get("smart_crawl_phase", "map")

        logger.info(
            "smart_crawl_processing",
            job_id=str(job.id),
            phase=phase,
        )

        if phase == "map":
            await self._smart_crawl_map_phase(job)
        elif phase == "filter":
            await self._smart_crawl_filter_phase(job)
        elif phase == "scrape":
            await self._smart_crawl_scrape_phase(job)
        else:
            logger.error(
                "smart_crawl_invalid_phase",
                job_id=str(job.id),
                phase=phase,
            )
            job.status = "failed"
            job.error = f"Invalid smart crawl phase: {phase}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()

    async def _smart_crawl_map_phase(self, job: Job) -> None:
        """Phase 1: Use Firecrawl Map to discover URLs.

        Calls the Map endpoint to get URLs with metadata (title, description).
        Stores discovered URLs in job payload and advances to filter phase.
        """
        payload = job.payload
        url = payload["url"]

        # Merge focus_terms from request and template crawl_config
        focus_terms = list(payload.get("focus_terms") or [])
        crawl_config = await self._load_crawl_config(payload["project_id"])
        if crawl_config and crawl_config.focus_terms:
            focus_terms.extend(crawl_config.focus_terms)

        # Build search term for Firecrawl Map's semantic search
        search_term = " ".join(focus_terms) if focus_terms else None

        logger.info(
            "smart_crawl_map_started",
            job_id=str(job.id),
            url=url,
            search_term=search_term,
            limit=settings.smart_crawl_map_limit,
        )

        # Mark job as running
        job.status = "running"
        job.started_at = datetime.now(UTC)
        job.result = {"phase": "map", "status": "discovering_urls"}
        self.db.commit()

        try:
            # Call Firecrawl Map endpoint
            map_result = await self.client.map(
                url=url,
                search=search_term,
                limit=settings.smart_crawl_map_limit,
                include_subdomains=payload.get("allow_subdomains", False),
                ignore_query_parameters=payload.get("ignore_query_parameters", True),
            )

            if not map_result.success:
                job.status = "failed"
                job.error = f"Map failed: {map_result.error}"
                job.completed_at = datetime.now(UTC)
                self.db.commit()
                return

            # Refresh job to reload attributes after async call (commit expires objects)
            self.db.refresh(job)
            payload = job.payload

            # Store mapped URLs in payload
            payload["mapped_urls"] = map_result.urls
            payload["smart_crawl_phase"] = "filter"
            flag_modified(job, "payload")

            job.result = {
                "phase": "map",
                "status": "completed",
                "urls_discovered": map_result.total,
            }
            self.db.commit()

            logger.info(
                "smart_crawl_map_completed",
                job_id=str(job.id),
                urls_discovered=map_result.total,
            )

            # Continue to filter phase immediately
            await self._smart_crawl_filter_phase(job)

        except Exception as e:
            logger.error(
                "smart_crawl_map_error",
                job_id=str(job.id),
                error=str(e),
                exc_info=True,
            )
            job.status = "failed"
            job.error = f"Map phase error: {e}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()

    async def _smart_crawl_filter_phase(self, job: Job) -> None:
        """Phase 2: Filter URLs by relevance to field_groups.

        Uses embedding-based similarity to filter URLs. Loads field_groups
        from the project's extraction_schema and compares URL metadata
        against them.
        """
        payload = job.payload
        mapped_urls = payload.get("mapped_urls", [])

        if not mapped_urls:
            logger.warning(
                "smart_crawl_filter_no_urls",
                job_id=str(job.id),
            )
            # Complete with 0 sources
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.result = {
                "phase": "filter",
                "urls_discovered": 0,
                "urls_filtered": 0,
                "sources_created": 0,
            }
            self.db.commit()
            return

        logger.info(
            "smart_crawl_filter_started",
            job_id=str(job.id),
            urls_to_filter=len(mapped_urls),
        )

        job.result = {
            "phase": "filter",
            "status": "filtering_urls",
            "urls_discovered": len(mapped_urls),
        }
        self.db.commit()

        try:
            # Load field_groups from project
            field_groups = await self._load_project_field_groups(
                payload["project_id"]
            )

            # Get limit for capping URLs
            limit = payload.get("limit", 100)

            if not field_groups:
                logger.warning(
                    "smart_crawl_filter_no_field_groups",
                    job_id=str(job.id),
                    project_id=payload["project_id"],
                )
                # Without field_groups, can't filter by relevance - pass URLs through but apply limit
                all_urls = [u.get("url") for u in mapped_urls if u.get("url")]
                urls_before_limit = len(all_urls)
                filtered_urls = all_urls[:limit]
                threshold_used = None
            else:
                # Initialize URL filter if needed
                if self._url_filter is None:
                    if self._embedding_service is None:
                        # Create embedding service
                        self._embedding_service = EmbeddingService(settings)
                    from services.scraper.url_filter import UrlRelevanceFilter
                    self._url_filter = UrlRelevanceFilter(
                        self._embedding_service,
                        settings,
                    )

                # Load crawl_config once for all settings
                crawl_config = await self._load_crawl_config(payload["project_id"])

                # Merge include/exclude patterns from request and template
                include_patterns = list(payload.get("include_paths") or [])
                exclude_patterns = list(payload.get("exclude_paths") or [])
                if crawl_config:
                    if crawl_config.include_patterns:
                        include_patterns.extend(crawl_config.include_patterns)
                    if crawl_config.exclude_patterns:
                        exclude_patterns.extend(crawl_config.exclude_patterns)

                # Apply pattern-based pre-filtering first
                pre_filtered = self._apply_url_patterns(
                    mapped_urls,
                    include_patterns=include_patterns if include_patterns else None,
                    exclude_patterns=exclude_patterns if exclude_patterns else None,
                )

                # Get threshold (from request, template, or default)
                threshold = payload.get("relevance_threshold")
                if (
                    threshold is None
                    and crawl_config
                    and crawl_config.relevance_threshold is not None
                ):
                    threshold = crawl_config.relevance_threshold

                # Merge focus terms from request and template
                focus_terms = list(payload.get("focus_terms") or [])
                if crawl_config and crawl_config.focus_terms:
                    focus_terms.extend(crawl_config.focus_terms)

                # Filter by relevance
                filter_result = await self._url_filter.filter_urls(
                    urls=pre_filtered,
                    field_groups=field_groups,
                    focus_terms=focus_terms if focus_terms else None,
                    threshold=threshold,
                )

                filtered_urls = [u.url for u in filter_result.relevant_urls]

                # Apply limit to cap number of URLs for scraping (URLs already sorted by relevance)
                urls_before_limit = len(filtered_urls)
                threshold_used = filter_result.threshold_used
                if len(filtered_urls) > limit:
                    filtered_urls = filtered_urls[:limit]

                logger.info(
                    "smart_crawl_filter_completed",
                    job_id=str(job.id),
                    urls_before=len(mapped_urls),
                    urls_after_patterns=len(pre_filtered),
                    urls_after_relevance=urls_before_limit,
                    urls_after_limit=len(filtered_urls),
                    limit_applied=limit,
                    threshold_used=threshold_used,
                )

            # Refresh job to reload attributes after async calls (commit expires objects)
            self.db.refresh(job)
            payload = job.payload

            # Store filtered URLs
            payload["filtered_urls"] = filtered_urls
            payload["smart_crawl_phase"] = "scrape"
            flag_modified(job, "payload")

            job.result = {
                "phase": "filter",
                "status": "completed",
                "urls_discovered": len(mapped_urls),
                "urls_relevant": urls_before_limit,
                "urls_to_scrape": len(filtered_urls),
                "limit_applied": limit,
            }
            self.db.commit()

            # Continue to scrape phase
            await self._smart_crawl_scrape_phase(job)

        except Exception as e:
            logger.error(
                "smart_crawl_filter_error",
                job_id=str(job.id),
                error=str(e),
                exc_info=True,
            )
            job.status = "failed"
            job.error = f"Filter phase error: {e}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()

    async def _smart_crawl_scrape_phase(self, job: Job) -> None:
        """Phase 3: Batch scrape relevant URLs.

        Uses Firecrawl's batch scrape endpoint to scrape all filtered URLs.
        Polls for completion and stores pages as Sources.
        """
        payload = job.payload
        filtered_urls = payload.get("filtered_urls", [])
        batch_job_id = payload.get("batch_scrape_job_id")

        if not filtered_urls:
            logger.info(
                "smart_crawl_scrape_no_urls",
                job_id=str(job.id),
            )
            # Complete with 0 sources
            job.status = "completed"
            job.completed_at = datetime.now(UTC)
            job.result = {
                "phase": "scrape",
                "urls_discovered": len(payload.get("mapped_urls", [])),
                "urls_relevant": 0,
                "sources_created": 0,
            }
            self.db.commit()

            # Still trigger extraction if auto_extract
            if payload.get("auto_extract", True):
                await self._create_extraction_job(job)
            return

        try:
            # Start batch scrape if not already started
            if not batch_job_id:
                logger.info(
                    "smart_crawl_scrape_starting",
                    job_id=str(job.id),
                    url_count=len(filtered_urls),
                )

                batch_job_id = await self.client.start_batch_scrape(
                    urls=filtered_urls,
                    formats=["markdown"],
                    max_concurrency=settings.smart_crawl_batch_max_concurrency,
                )

                payload["batch_scrape_job_id"] = batch_job_id
                flag_modified(job, "payload")

                job.result = {
                    "phase": "scrape",
                    "status": "scraping",
                    "urls_to_scrape": len(filtered_urls),
                    "batch_job_id": batch_job_id,
                }
                self.db.commit()

                logger.info(
                    "smart_crawl_batch_scrape_started",
                    job_id=str(job.id),
                    batch_job_id=batch_job_id,
                )
                return  # Will be polled on next iteration

            # Check batch scrape status
            status = await self.client.get_batch_scrape_status(batch_job_id)

            job.result = {
                "phase": "scrape",
                "status": status.status,
                "urls_to_scrape": len(filtered_urls),
                "pages_completed": status.completed,
                "pages_total": status.total,
            }
            job.updated_at = datetime.now(UTC)
            self.db.commit()

            if status.status == "scraping":
                logger.debug(
                    "smart_crawl_batch_scrape_progress",
                    job_id=str(job.id),
                    completed=status.completed,
                    total=status.total,
                )
                return  # Continue polling

            if status.status == "failed":
                job.status = "failed"
                job.error = f"Batch scrape failed: {status.error}"
                job.completed_at = datetime.now(UTC)
                self.db.commit()
                return

            if status.status == "completed":
                # Check for cancellation before storing
                if self.job_repo.is_cancellation_requested(job.id):
                    logger.info(
                        "smart_crawl_cancelled_before_storage",
                        job_id=str(job.id),
                        pages_available=len(status.pages),
                    )
                    self.job_repo.mark_cancelled(job.id)
                    job.result = {
                        "cancelled_before_storage": True,
                        "pages_available": len(status.pages),
                    }
                    self.db.commit()
                    return

                # Store pages as sources
                sources_created = await self._store_pages(job, status.pages)

                job.status = "completed"
                job.completed_at = datetime.now(UTC)
                job.result = {
                    "phase": "completed",
                    "urls_discovered": len(payload.get("mapped_urls", [])),
                    "urls_relevant": len(filtered_urls),
                    "pages_scraped": status.completed,
                    "sources_created": sources_created,
                }
                self.db.commit()

                logger.info(
                    "smart_crawl_completed",
                    job_id=str(job.id),
                    urls_discovered=len(payload.get("mapped_urls", [])),
                    urls_relevant=len(filtered_urls),
                    sources_created=sources_created,
                )

                # Auto-extract if enabled
                if payload.get("auto_extract", True):
                    await self._create_extraction_job(job)

        except Exception as e:
            logger.error(
                "smart_crawl_scrape_error",
                job_id=str(job.id),
                error=str(e),
                exc_info=True,
            )
            job.status = "failed"
            job.error = f"Scrape phase error: {e}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()

    def _apply_url_patterns(
        self,
        urls: list[dict],
        include_patterns: list[str] | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> list[dict]:
        """Apply include/exclude URL patterns.

        Args:
            urls: List of URL dicts from Map.
            include_patterns: URL regex patterns to include (whitelist).
            exclude_patterns: URL regex patterns to exclude (blacklist).

        Returns:
            Filtered list of URL dicts.
        """
        if not include_patterns and not exclude_patterns:
            return urls

        # Compile patterns
        include_regexes = []
        exclude_regexes = []

        if include_patterns:
            include_regexes = [re.compile(p) for p in include_patterns]
        if exclude_patterns:
            exclude_regexes = [re.compile(p) for p in exclude_patterns]

        filtered = []
        for url_info in urls:
            url = url_info.get("url", "")

            # Check include patterns (if any, URL must match at least one)
            if include_regexes and not any(r.search(url) for r in include_regexes):
                continue

            # Check exclude patterns (if any, URL must not match any)
            if exclude_regexes and any(r.search(url) for r in exclude_regexes):
                continue

            filtered.append(url_info)

        return filtered

    async def _load_project_field_groups(self, project_id: str) -> list:
        """Load field groups from project's extraction_schema.

        Args:
            project_id: Project UUID string.

        Returns:
            List of FieldGroup objects.
        """
        from uuid import UUID

        from orm_models import Project
        from services.extraction.schema_adapter import SchemaAdapter

        try:
            project_uuid = UUID(project_id)
            project = self.db.query(Project).filter(Project.id == project_uuid).first()

            if not project or not project.extraction_schema:
                return []

            adapter = SchemaAdapter()
            # parse_template returns (field_groups, context, classification_config, crawl_config)
            field_groups, _, _, _ = adapter.parse_template(
                {"extraction_schema": project.extraction_schema}
            )
            return field_groups

        except Exception as e:
            logger.warning(
                "load_field_groups_error",
                project_id=project_id,
                error=str(e),
            )
            return []

    async def _load_crawl_config(self, project_id: str):
        """Load crawl_config from project's extraction_schema.

        crawl_config is embedded inside extraction_schema when creating
        projects from templates (see api/v1/projects.py create_from_template).

        Args:
            project_id: Project UUID string.

        Returns:
            CrawlConfig or None.
        """
        from uuid import UUID

        from orm_models import Project
        from services.extraction.schema_adapter import CrawlConfig

        try:
            project_uuid = UUID(project_id)
            project = self.db.query(Project).filter(Project.id == project_uuid).first()

            if not project or not project.extraction_schema:
                return None

            # crawl_config is stored inside extraction_schema
            crawl_config_data = project.extraction_schema.get("crawl_config")
            return CrawlConfig.from_dict(crawl_config_data)

        except Exception as e:
            logger.warning(
                "load_crawl_config_error",
                project_id=project_id,
                error=str(e),
            )
            return None
