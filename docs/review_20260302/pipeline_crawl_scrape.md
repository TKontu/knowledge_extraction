# Pipeline Review: Crawl & Scrape

**Date**: 2026-03-02
**Scope**: Web content acquisition - crawling, scraping, URL filtering, scheduling, rate limiting

---

## 1. Overview

The crawl/scrape pipeline acquires web content and stores it as `Source` records for downstream extraction. It supports two acquisition modes:

- **Crawl**: Recursive website traversal with optional smart filtering
- **Scrape**: Explicit URL list processing (no link following)

Both modes feed into the same source storage, and can optionally auto-trigger extraction on completion.

```
API Request → Job Queue → Scheduler → Worker → Firecrawl API → Source Storage → [Auto-Extract]
```

---

## 2. Job Scheduling (`src/services/scraper/scheduler.py`)

### Architecture

The `JobScheduler` runs as a background task during FastAPI lifespan. It operates multiple concurrent worker loops:

| Worker | Concurrency | Purpose |
|--------|-------------|---------|
| Scrape loop | 1 | Processes scrape jobs sequentially |
| Crawl loops | `max_concurrent_crawls` (default 3) | Parallel crawl workers |
| Extract loop | 1 | Processes extraction jobs sequentially |
| LLM queue worker | 1 | For Redis-queue LLM calls (if enabled) |

### Job Claim Strategy

Uses `SELECT FOR UPDATE SKIP LOCKED` for distributed job locking:

1. Query for QUEUED jobs of target type
2. Row-lock prevents concurrent claim
3. If none found, check for STALE RUNNING jobs (configurable per type)
4. Mark claimed job as RUNNING

**Stale recovery thresholds**:
- Scrape: 300 seconds (5 min)
- Extract: 1200 seconds (20 min)
- Crawl: 600 seconds (10 min)

### Service Lifecycle

**Cached (stateless, shared across jobs)**:
- `EmbeddingService` - for URL filtering embeddings
- `QdrantRepository` - vector store
- `ExtractionDeduplicator` - dedup across batches
- `DomainRateLimiter` - Redis-backed per-domain throttling

**Per-job (created fresh)**:
- `LLMClient`, `ExtractionOrchestrator`, `EntityExtractor`
- `ScraperWorker`, `CrawlWorker`

---

## 3. Traditional Crawl Mode

### 3-Phase Process

**Phase 1: Start Crawl**
```
POST /v1/crawl → Firecrawl API
  - Calculates absolute max_depth (starting_depth + relative)
  - Checks llms.txt for AI agent permissions
  - Builds scrapeOptions with User-Agent header
  - Stores firecrawl_job_id in job.payload
  - Returns immediately (polling begins next cycle)
```

**Phase 2: Poll & Progress**
```
GET /v1/crawl/{firecrawl_job_id} → status check
  - Tracks: pages_completed / pages_total
  - Stale warning levels:
    - 30s: LOW, 60s: MEDIUM, 120s: HIGH, 300s: CRITICAL
  - Continues polling while status="scraping"
```

**Phase 3: Store & Complete**
```
When status="completed":
  - Fetch all pages (handles Firecrawl pagination, follows "next" cursor)
  - Check for cancellation before storing
  - Store each page as Source record
  - Auto-create extraction job if auto_extract=True
  - Mark job COMPLETED
```

### Firecrawl Integration

```
Endpoints used:
  POST /v1/crawl        - Start recursive crawl
  GET  /v1/crawl/{id}   - Poll crawl status
  POST /v1/map          - Discover URLs (smart crawl)
  POST /v1/batch/scrape - Batch scrape URLs (smart crawl)
  GET  /v1/batch/scrape/{id} - Poll batch status

Client: src/services/scraper/client.py (FirecrawlClient)
  - Async httpx with configurable timeout
  - Pagination handling (follows "next" cursor)
  - Error mapping with structured logging
```

### llms.txt Integration

Before starting a crawl, checks `{domain}/llms.txt` for AI agent permissions:
- Parses User-Agent allow directives
- Recognizes: GPTBot, ClaudeBot, PerplexityBot, etc.
- If AI agents allowed, can override robots.txt restrictions

---

## 4. Smart Crawl Mode

An alternative to traditional crawling that uses semantic filtering to focus on relevant pages.

### 3-Phase Pipeline

**Phase 1: Map** (`_smart_crawl_map_phase`)
```
POST /v1/map → discover all URLs with metadata
  - Uses Firecrawl Map API with semantic search hints
  - Merges focus_terms from request + template
  - Returns: list of {url, title, description}
  - Fallback: if < 3 URLs found, switches to traditional crawl
  - Stores mapped_urls in payload
```

**Phase 2: Filter** (`_smart_crawl_filter_phase`)
```
For each URL:
  1. Apply pattern-based pre-filtering (include/exclude regex)
  2. Build context from field_groups + focus_terms
  3. Embed context text (via bge-m3)
  4. Batch embed URL metadata (title + description + path hints)
  5. Calculate cosine similarity to context embedding
  6. Filter by relevance_threshold
  7. Sort by score (descending), apply limit cap
```

**Phase 3: Scrape** (`_smart_crawl_scrape_phase`)
```
POST /v1/batch/scrape → scrape filtered URLs
  - Batch scrape via Firecrawl batch API
  - Poll for completion (same pagination as traditional)
  - Store pages as sources
  - Auto-extract if enabled
```

### State Machine

```
payload["smart_crawl_phase"]: "map" → "filter" → "scrape"
payload["mapped_urls"]       → Map results
payload["filtered_urls"]     → Filter results
payload["batch_scrape_job_id"] → Batch job ID
```

State is persisted in job payload, allowing resume after scheduler restart.

---

## 5. URL Relevance Filtering (`src/services/scraper/url_filter.py`)

### UrlRelevanceFilter

Embedding-based URL filtering for smart crawl:

**Context Building**:
```
"Focus: {focus_terms}
Extraction targets:
- {group_name}: {group_description}
  Fields: {field1}, {field2}, ..."
```

**URL Metadata**:
```
"Path: products specifications
Title: Widget Pro Specifications
Description: Complete specs for our Widget..."
```

**Process**:
1. Embed context text (single embedding)
2. Batch embed all URL metadata texts
3. Cosine similarity per URL
4. Filter by threshold (default 0.5)
5. Sort by relevance (descending)

---

## 6. Scrape Worker (`src/services/scraper/worker.py`)

Processes explicit URL lists with retry and rate limiting.

### Flow

```
For each URL in job.urls:
  1. Check for cancellation
  2. Extract domain from URL
  3. rate_limiter.acquire(domain) → wait if needed, check daily limit
  4. retry_with_backoff(client.scrape(url))
  5. On success: create Source record (PENDING status)
  6. On RateLimitExceeded: skip (count as failed)
  7. Batch commit all sources
```

### Rate Limiting (`rate_limiter.py`)

Redis-backed, two mechanisms:

| Mechanism | Implementation | Default |
|-----------|---------------|---------|
| **Delay** | Per-domain timestamp + random sleep | 2-5 seconds |
| **Daily limit** | Per-domain UTC-day counter | 500 req/domain/day |

```
Redis keys:
  ratelimit:{domain}:last_request         → timestamp
  ratelimit:{domain}:daily_count:{date}   → counter
```

### Retry Logic (`retry.py`)

Exponential backoff with jitter:

```
delay(attempt) = min(base_delay * 2^attempt, max_delay) * (0.75 + random*0.5)

Attempt 0: 2s  → 1.5-2.5s
Attempt 1: 4s  → 3-5s
Attempt 2: 8s  → 6-10s
```

Retryable: `httpx.HTTPError`, `TimeoutError`, `ConnectionError`

---

## 7. Source Storage

### Page Processing (`_store_pages`)

For each page from Firecrawl:

1. **Validation**: Skip if missing markdown/URL or HTTP error (status >= 400)
2. **Language detection** (if enabled): Async language detection with timeout, filter by `allowed_languages`
3. **Domain extraction**: `urlparse(url).netloc` stored in `meta_data["domain"]`
4. **Upsert**: Race-condition safe via PostgreSQL `ON CONFLICT DO UPDATE` on `(project_id, uri)`

**Source record fields**:
- `content`: Processed markdown for extraction
- `raw_content`: Original unmodified content
- `status`: PENDING (ready for extraction)
- `meta_data`: `{domain, http_status, title, language, ...}`

### Auto-Extraction Trigger

When `auto_extract=True` (default), creates a follow-up extraction job:

```python
Job(
    type="extract",
    status="queued",
    payload={
        "project_id": crawl_job.project_id,
        "source_ids": None,  # ALL pending sources
        "profile": crawl_job.profile
    }
)
```

---

## 8. Cancellation & Graceful Shutdown

### Cancellation Points

The scheduler checks `job_repo.is_cancellation_requested(job_id)` at:
- Before processing starts
- Before storing pages
- Before each URL in scrape jobs

On cancellation: mark CANCELLED, store partial results.

### Graceful Shutdown

1. FastAPI lifespan event calls `stop_scheduler()`
2. Scheduler sets `_running = False`
3. Waits for all workers to finish current job
4. Closes Firecrawl client, async Redis, LLM worker

---

## 9. Configuration

```python
# Firecrawl
firecrawl_url = "http://localhost:3002"
scrape_timeout = 60  # seconds

# Rate Limiting
scrape_delay_min = 2       # seconds between requests
scrape_delay_max = 5
scrape_daily_limit_per_domain = 500
scrape_retry_max_attempts = 3
scrape_retry_base_delay = 2.0
scrape_retry_max_delay = 60.0

# Crawl
crawl_delay_ms = 500       # delay between Firecrawl requests
crawl_max_concurrency = 5  # concurrent requests in Firecrawl
max_concurrent_crawls = 3  # parallel crawl workers in scheduler

# Polling
crawl_poll_interval = 10   # seconds between status checks
job_stale_threshold_crawl = 600    # 10 min
job_stale_threshold_scrape = 300   # 5 min

# Language
language_filtering_enabled = True
language_detection_confidence_threshold = 0.8
language_detection_timeout_seconds = 5

# Smart Crawl
smart_crawl_enabled = False  # off by default
smart_crawl_map_limit = 5000
smart_crawl_batch_max_concurrency = 10
smart_crawl_default_relevance_threshold = 0.5
```

---

## 10. Data Flow Diagram

```
                          ┌──────────────┐
                          │  API Request  │
                          │  (POST /crawl │
                          │  or /scrape)  │
                          └──────┬───────┘
                                 │
                          ┌──────▼───────┐
                          │  Create Job   │
                          │  (QUEUED)     │
                          └──────┬───────┘
                                 │
                    ┌────────────▼────────────┐
                    │    Job Scheduler Loop    │
                    │  SELECT FOR UPDATE       │
                    │  SKIP LOCKED             │
                    └────────────┬────────────┘
                                 │
                 ┌───────────────┼───────────────┐
                 │               │               │
         ┌───────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐
         │ Traditional  │ │  Smart     │ │  Scrape    │
         │ Crawl        │ │  Crawl     │ │  Worker    │
         │              │ │            │ │            │
         │ 1. Start     │ │ 1. Map     │ │ For each   │
         │ 2. Poll      │ │ 2. Filter  │ │ URL:       │
         │ 3. Store     │ │ 3. Scrape  │ │  - Rate    │
         └───────┬──────┘ └─────┬──────┘ │    limit   │
                 │               │        │  - Retry   │
                 │               │        │  - Store   │
                 └───────┬───────┘        └─────┬──────┘
                         │                      │
                  ┌──────▼──────────────────────▼──────┐
                  │         Source Storage               │
                  │  - Language detection                │
                  │  - Domain extraction                 │
                  │  - Upsert (project_id, uri)          │
                  │  - Status: PENDING                   │
                  └──────────────┬──────────────────────┘
                                 │
                          ┌──────▼───────┐
                          │ Auto-Extract │
                          │ (if enabled) │
                          └──────────────┘
```

---

## 11. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `SELECT FOR UPDATE SKIP LOCKED` | Prevents race conditions in multi-worker environment without distributed locks |
| Firecrawl delegation | Offloads actual HTTP fetching, JS rendering, robots.txt to specialized service |
| Smart crawl as opt-in | Traditional crawl is simpler and more predictable; smart crawl adds complexity |
| Rate limiting in Redis | Shared state across workers, survives restarts |
| Source upsert on `(project_id, uri)` | Prevents duplicate URLs per project from concurrent crawls |
| llms.txt check | Respects AI-specific access policies beyond robots.txt |
| State machine in payload | Allows smart crawl to resume across scheduler restarts |
