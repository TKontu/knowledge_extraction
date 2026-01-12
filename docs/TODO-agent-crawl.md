# TODO: Crawl Feature Implementation

**Agent ID**: crawl-feature
**Priority**: High
**Status**: Not Started

## Context

The current scraping system only supports single-page scraping via Firecrawl's `/v1/scrape` endpoint. Users need to manually specify every URL. We need domain crawling that automatically discovers and scrapes pages starting from a landing page.

Firecrawl provides a `/v1/crawl` endpoint with smart link discovery, depth control, and async job management. We should leverage this.

## Objective

Add a `/api/v1/crawl` endpoint that crawls a domain starting from a URL, respects depth/limit constraints, stores all pages as sources, and automatically triggers extraction.

## Firecrawl Crawl API

**Endpoint**: `POST /v1/crawl`

**Request**:
```json
{
  "url": "https://example.com",
  "maxDepth": 2,
  "limit": 100,
  "includePaths": ["/blog/*"],
  "excludePaths": ["/login", "/admin/*"],
  "allowBackwardLinks": false,
  "scrapeOptions": {"formats": ["markdown"]}
}
```

**Response**:
```json
{
  "success": true,
  "id": "crawl-job-id"
}
```

**Status Polling**: `GET /v1/crawl/{id}`
```json
{
  "status": "scraping|completed|failed",
  "total": 50,
  "completed": 25,
  "data": [
    {
      "markdown": "...",
      "metadata": {"title": "...", "url": "..."}
    }
  ]
}
```

## Tasks

### 1. Add Pydantic Models (src/models.py)

Add after `ScrapeResponse`:

```python
class CrawlRequest(BaseModel):
    """Request body for crawl endpoint."""

    url: str = Field(..., description="Starting URL to crawl from")
    project_id: UUID = Field(..., description="Project ID for sources")
    company: str = Field(..., min_length=1, description="Source group name")
    max_depth: int = Field(default=2, ge=1, le=10, description="Crawl depth")
    limit: int = Field(default=100, ge=1, le=1000, description="Max pages")
    include_paths: list[str] | None = Field(default=None, description="URL patterns to include")
    exclude_paths: list[str] | None = Field(default=None, description="URL patterns to exclude")
    allow_backward_links: bool = Field(default=False, description="Allow parent/sibling URLs")
    auto_extract: bool = Field(default=True, description="Auto-trigger extraction")
    profile: str | None = Field(default=None, description="Extraction profile")


class CrawlResponse(BaseModel):
    """Response body for crawl endpoint."""

    job_id: str
    status: str = "queued"
    url: str
    max_depth: int
    limit: int
    project_id: str
    company: str


class CrawlStatusResponse(BaseModel):
    """Response for crawl job status."""

    job_id: str
    status: str  # queued, running, completed, failed
    url: str
    pages_total: int | None = None
    pages_completed: int | None = None
    sources_created: int | None = None
    error: str | None = None
    created_at: str
    completed_at: str | None = None
```

### 2. Extend FirecrawlClient (src/services/scraper/client.py)

Add dataclass after `ScrapeResult`:

```python
@dataclass
class CrawlStatus:
    """Status of a crawl operation."""

    status: str  # "scraping", "completed", "failed"
    total: int
    completed: int
    pages: list[dict]  # List of scraped page data
    error: str | None = None
```

Add methods to `FirecrawlClient`:

```python
async def start_crawl(
    self,
    url: str,
    max_depth: int = 2,
    limit: int = 100,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    allow_backward_links: bool = False,
) -> str:
    """Start async crawl job.

    Args:
        url: Starting URL.
        max_depth: How deep to crawl.
        limit: Maximum pages to crawl.
        include_paths: URL patterns to include.
        exclude_paths: URL patterns to exclude.
        allow_backward_links: Allow sibling/parent URLs.

    Returns:
        Firecrawl job ID.
    """
    response = await self._http_client.post(
        f"{self.base_url}/v1/crawl",
        json={
            "url": url,
            "maxDepth": max_depth,
            "limit": limit,
            "includePaths": include_paths or [],
            "excludePaths": exclude_paths or [],
            "allowBackwardLinks": allow_backward_links,
            "scrapeOptions": {"formats": ["markdown"]},
        },
    )
    data = response.json()
    if not data.get("success"):
        raise ScrapeError(data.get("error", "Failed to start crawl"))
    return data["id"]


async def get_crawl_status(self, crawl_id: str) -> CrawlStatus:
    """Get crawl job status.

    Args:
        crawl_id: Firecrawl job ID.

    Returns:
        CrawlStatus with progress and pages.
    """
    response = await self._http_client.get(
        f"{self.base_url}/v1/crawl/{crawl_id}"
    )
    data = response.json()
    return CrawlStatus(
        status=data.get("status", "unknown"),
        total=data.get("total", 0),
        completed=data.get("completed", 0),
        pages=data.get("data", []),
        error=data.get("error"),
    )
```

### 3. Create Crawl API Endpoint (src/api/v1/crawl.py)

Create new file:

```python
"""Crawl API endpoints."""

from uuid import UUID, uuid4

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from database import get_db
from models import CrawlRequest, CrawlResponse, CrawlStatusResponse
from orm_models import Job

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1", tags=["crawl"])


@router.post("/crawl", status_code=status.HTTP_202_ACCEPTED)
async def create_crawl_job(
    request: CrawlRequest, db: Session = Depends(get_db)
) -> CrawlResponse:
    """Create a new crawl job."""
    job_id = uuid4()

    logger.info(
        "crawl_job_created",
        job_id=str(job_id),
        url=request.url,
        project_id=str(request.project_id),
        max_depth=request.max_depth,
        limit=request.limit,
    )

    job = Job(
        id=job_id,
        type="crawl",
        status="queued",
        payload={
            "url": request.url,
            "project_id": str(request.project_id),
            "company": request.company,
            "max_depth": request.max_depth,
            "limit": request.limit,
            "include_paths": request.include_paths,
            "exclude_paths": request.exclude_paths,
            "allow_backward_links": request.allow_backward_links,
            "auto_extract": request.auto_extract,
            "profile": request.profile,
            "firecrawl_job_id": None,  # Set when crawl starts
        },
    )

    db.add(job)
    db.commit()
    db.refresh(job)

    return CrawlResponse(
        job_id=str(job.id),
        status=job.status,
        url=request.url,
        max_depth=request.max_depth,
        limit=request.limit,
        project_id=str(request.project_id),
        company=request.company,
    )


@router.get("/crawl/{job_id}", status_code=status.HTTP_200_OK)
async def get_crawl_status(
    job_id: str, db: Session = Depends(get_db)
) -> CrawlStatusResponse:
    """Get crawl job status."""
    try:
        job_uuid = UUID(job_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid job_id format",
        )

    job = db.query(Job).filter(Job.id == job_uuid, Job.type == "crawl").first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Crawl job {job_id} not found",
        )

    return CrawlStatusResponse(
        job_id=str(job.id),
        status=job.status,
        url=job.payload.get("url", ""),
        pages_total=job.result.get("pages_total") if job.result else None,
        pages_completed=job.result.get("pages_completed") if job.result else None,
        sources_created=job.result.get("sources_created") if job.result else None,
        error=job.error,
        created_at=job.created_at.isoformat(),
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
    )
```

### 4. Create Crawl Worker (src/services/scraper/crawl_worker.py)

Create new file:

```python
"""Background worker for processing crawl jobs."""

from datetime import UTC, datetime
from urllib.parse import urlparse
from uuid import uuid4

import structlog
from sqlalchemy.orm import Session

from orm_models import Job
from services.scraper.client import FirecrawlClient
from services.storage.repositories.source import SourceRepository

logger = structlog.get_logger(__name__)

POLL_INTERVAL = 10  # seconds between status checks


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
                firecrawl_job_id = await self.client.start_crawl(
                    url=payload["url"],
                    max_depth=payload.get("max_depth", 2),
                    limit=payload.get("limit", 100),
                    include_paths=payload.get("include_paths"),
                    exclude_paths=payload.get("exclude_paths"),
                    allow_backward_links=payload.get("allow_backward_links", False),
                )

                # Store Firecrawl job ID
                job.payload["firecrawl_job_id"] = firecrawl_job_id
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

            # Update progress in result
            job.result = {
                "pages_total": status.total,
                "pages_completed": status.completed,
                "sources_created": 0,
            }
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

        except Exception as e:
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
```

### 5. Update Scheduler (src/services/scraper/scheduler.py)

Add import at top:
```python
from services.scraper.crawl_worker import CrawlWorker
```

Add new task in `__init__`:
```python
self._crawl_task: asyncio.Task | None = None
```

Update `start()` method:
```python
self._crawl_task = asyncio.create_task(self._run_crawl_worker())
```

Update `stop()` method:
```python
if self._crawl_task:
    await self._crawl_task
```

Add new worker loop:
```python
async def _run_crawl_worker(self) -> None:
    """Main loop for processing crawl jobs."""
    shutdown = get_shutdown_manager()
    while self._running and not shutdown.is_shutting_down:
        try:
            db: Session = SessionLocal()
            try:
                # Query for crawl jobs that need processing
                job = (
                    db.query(Job)
                    .filter(
                        Job.type == "crawl",
                        Job.status.in_(["queued", "running"]),
                    )
                    .order_by(Job.priority.desc(), Job.created_at.asc())
                    .first()
                )

                if job:
                    worker = CrawlWorker(
                        db=db,
                        firecrawl_client=self._firecrawl_client,
                    )
                    await worker.process_job(job)
                else:
                    await asyncio.sleep(self.poll_interval)

            finally:
                db.close()

        except Exception as e:
            print(f"Error in crawl worker: {e}")
            await asyncio.sleep(self.poll_interval)
```

### 6. Register Router (src/main.py)

Add import:
```python
from api.v1.crawl import router as crawl_router
```

Add router registration after other routers:
```python
app.include_router(crawl_router)
```

## Test Cases

### Unit Tests (tests/test_crawl_api.py)

```python
"""Tests for crawl API endpoints."""

import pytest
from uuid import uuid4


class TestCreateCrawlJob:
    async def test_creates_crawl_job(self, client, db_session):
        project_id = str(uuid4())
        response = await client.post(
            "/api/v1/crawl",
            json={
                "url": "https://example.com",
                "project_id": project_id,
                "company": "TestCo",
                "max_depth": 2,
                "limit": 50,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "queued"
        assert data["url"] == "https://example.com"
        assert data["max_depth"] == 2
        assert data["limit"] == 50

    async def test_validates_max_depth(self, client):
        response = await client.post(
            "/api/v1/crawl",
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
                "max_depth": 100,  # exceeds limit
            },
        )
        assert response.status_code == 422


class TestGetCrawlStatus:
    async def test_returns_job_status(self, client, db_session):
        # Create job first
        # ... test implementation
        pass

    async def test_returns_404_for_unknown_job(self, client):
        response = await client.get(f"/api/v1/crawl/{uuid4()}")
        assert response.status_code == 404
```

## Constraints

- Do NOT modify existing scrape functionality
- Do NOT add webhook support (future enhancement)
- Keep poll interval at 10 seconds for crawl status
- Respect existing rate limiting configuration
- Use existing Source model structure

## Verification

1. **Start services**:
   ```bash
   docker compose up -d
   ```

2. **Create crawl job**:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/crawl" \
     -H "Content-Type: application/json" \
     -H "X-API-Key: local-dev-key-minimum-32-characters-long" \
     -d '{
       "url": "https://books.toscrape.com/",
       "project_id": "<project-id>",
       "company": "BooksToScrape",
       "max_depth": 2,
       "limit": 10
     }'
   ```

3. **Monitor job status**:
   ```bash
   watch -n5 'curl -s "http://localhost:8000/api/v1/crawl/<job-id>" \
     -H "X-API-Key: local-dev-key-minimum-32-characters-long" | jq'
   ```

4. **Verify sources created**:
   ```bash
   curl "http://localhost:8000/api/v1/projects/<project-id>/sources" \
     -H "X-API-Key: local-dev-key-minimum-32-characters-long"
   ```

5. **Run tests**:
   ```bash
   pytest tests/test_crawl_api.py -v
   pytest tests/test_crawl_worker.py -v
   ```
