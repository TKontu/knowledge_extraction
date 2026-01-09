# TODO: Scraper Module

## Overview

Handles web scraping via Firecrawl, rate limiting, and raw content storage.

## Status

**Completed:**
- ✅ API endpoint skeleton (`POST /api/v1/scrape` and `GET /api/v1/scrape/{job_id}`)
- ✅ Request/response models (`ScrapeRequest`, `ScrapeResponse`, `JobStatusResponse`)
- ✅ In-memory job storage (temporary)
- ✅ Configuration for scraping params in `config.py`
- ✅ Redis connection available for rate limiting

**Pending:**
- Firecrawl client integration
- Rate limiting implementation
- Actual scraping logic
- Database storage for jobs and pages
- Error handling and retries
- FlareSolverr integration

**Next Steps:**
1. Replace in-memory job store with PostgreSQL
2. Implement Firecrawl client
3. Add rate limiting with Redis

## Core Tasks

### Firecrawl Client

- [ ] Create `FirecrawlClient` class
  ```python
  class FirecrawlClient:
      def scrape(self, url: str) -> ScrapeResult
      def scrape_batch(self, urls: list[str]) -> list[ScrapeResult]
  ```
- [ ] Configure Firecrawl API URL from env
- [ ] Handle response parsing (markdown, metadata)
- [ ] Timeout configuration (match your existing pattern)

### Rate Limiting

- [ ] Per-domain rate limiter using Redis
  ```python
  class DomainRateLimiter:
      async def acquire(self, domain: str) -> bool
      async def get_delay(self, domain: str) -> float
  ```
- [ ] Configurable delays: `SCRAPE_DELAY_MIN`, `SCRAPE_DELAY_MAX`
- [ ] Max concurrent per domain: `SCRAPE_MAX_CONCURRENT_PER_DOMAIN`
- [ ] Daily limit per domain (optional): `SCRAPE_DAILY_LIMIT_PER_DOMAIN`
- [ ] Randomized jitter between requests

### URL Queue

- [ ] Redis-backed job queue
- [ ] Priority support (normal, high, low)
- [ ] Job deduplication (don't re-queue same URL if pending)
- [ ] Job state transitions: `queued → running → completed/failed`

### Page Storage

- [ ] Store scraped content to PostgreSQL `pages` table
- [ ] Fields: url, domain, company, title, markdown_content, scraped_at, status
- [ ] Upsert logic (update if URL exists)
- [ ] Track scrape history/versioning (optional)

### Error Handling

- [ ] Retry logic with exponential backoff
  - 429: Wait and retry (respect Retry-After header)
  - 503: Backoff and retry
  - 403: Log and skip (or route to FlareSolverr)
  - Timeout: Retry with longer timeout
- [ ] Max retries configuration
- [ ] Dead letter queue for persistent failures

### FlareSolverr Integration (Optional)

- [ ] FlareSolverr client for Cloudflare bypass
- [ ] Automatic fallback when Firecrawl gets challenged
- [ ] Configuration toggle: `USE_FLARESOLVERR`

---

## Data Models

```python
@dataclass
class ScrapeRequest:
    url: str
    company: str
    priority: int = 0
    profile: str = "general"

@dataclass
class ScrapeResult:
    url: str
    title: str
    markdown: str
    metadata: dict
    scraped_at: datetime
    status: Literal["success", "failed"]
    error: str | None = None

@dataclass
class ScrapedPage:
    id: UUID
    url: str
    domain: str
    company: str
    title: str
    markdown_content: str
    scraped_at: datetime
    status: str
    metadata: dict
```

---

## Configuration

```yaml
scraping:
  firecrawl_url: ${FIRECRAWL_URL:-http://firecrawl-api:3002}
  delay_min: ${SCRAPE_DELAY_MIN:-2}
  delay_max: ${SCRAPE_DELAY_MAX:-5}
  max_concurrent_per_domain: ${SCRAPE_MAX_CONCURRENT_PER_DOMAIN:-2}
  daily_limit_per_domain: ${SCRAPE_DAILY_LIMIT_PER_DOMAIN:-500}
  max_retries: ${SCRAPE_MAX_RETRIES:-3}
  timeout_seconds: ${SCRAPE_TIMEOUT:-60}
  use_flaresolverr: ${USE_FLARESOLVERR:-false}
  flaresolverr_url: ${FLARESOLVERR_URL:-http://flaresolverr:8191}
```

---

## API Endpoints

**Status:** ✅ Endpoints exist as stubs in `pipeline/api/v1/scrape.py`

```python
# POST /api/v1/scrape
# Request:
{
    "urls": ["https://example.com/docs"],
    "company": "Example Inc",
    "profile": "api_docs"  # priority not yet implemented
}
# Response: ✅ Working (stub)
{
    "job_id": "uuid",
    "status": "queued",
    "url_count": 1,
    "company": "Example Inc",
    "profile": "api_docs"
}

# GET /api/v1/scrape/{job_id}
# Response: ✅ Working (stub with in-memory storage)
{
    "job_id": "uuid",
    "status": "queued",  # Always queued for now
    "company": "Example Inc",
    "url_count": 1,
    "profile": "api_docs",
    "created_at": "timestamp",
    "urls": ["..."]
}
```

---

## File Structure

```
pipeline/
├── services/
│   └── scraper/
│       ├── __init__.py
│       ├── client.py          # FirecrawlClient
│       ├── rate_limiter.py    # DomainRateLimiter
│       ├── queue.py           # Job queue management
│       ├── flaresolverr.py    # FlareSolverr client
│       └── service.py         # ScrapeService (orchestration)
├── models/
│   └── scrape.py              # ScrapeRequest, ScrapeResult, ScrapedPage
└── api/
    └── routes/
        └── scrape.py          # API endpoints
```

---

## Testing Checklist

- [ ] Unit: FirecrawlClient with mocked responses
- [ ] Unit: Rate limiter timing logic
- [ ] Integration: Scrape real URL (use httpbin.org or similar)
- [ ] Integration: Rate limiting respects delays
- [ ] Integration: Failed scrape → retry → stored as failed
