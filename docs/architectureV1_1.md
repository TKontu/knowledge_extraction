# Architecture Documentation

## System Overview

The Knowledge Extraction Orchestrator is a multi-stage pipeline that transforms unstructured web content into structured, searchable knowledge through LLM-powered extraction.

## Pipeline Flow

```
┌─────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌───────────┐    ┌────────┐
│ Crawl/  │───▶│  Smart   │───▶│ Domain   │───▶│ Classify │───▶│ Extract   │───▶│ Store  │
│ Scrape  │    │  Crawl   │    │  Dedup   │    │ (3-tier) │    │ Worker    │    │ (DB)   │
└─────────┘    └──────────┘    └──────────┘    └──────────┘    └───────────┘    └────────┘
     │              │                                │                │              │
     ▼              ▼                                ▼                ▼              ▼
┌─────────┐   ┌──────────┐                    ┌──────────┐    ┌─────────┐    ┌─────────┐
│Firecrawl│   │ Embedding│                    │ Reranker │    │LLM Queue│    │ Qdrant  │
└─────────┘   │ (bge-m3) │                    │(bge-rnk) │    └─────────┘    └─────────┘
     │        └──────────┘                    └──────────┘         │
     ▼                                                             ▼
┌──────────┐                                                 ┌─────────┐
│ Camoufox │                                                 │   LLM   │
│  + Proxy │                                                 │ Worker  │
└──────────┘                                                 └─────────┘
```

### Stage 1: Web Scraping

**Components:**
- `ScraperWorker` - Processes scrape jobs from queue
- `CrawlWorker` - Manages multi-page crawls with depth control
- `FirecrawlClient` - HTTP client for Firecrawl API
- `CamoufoxScraper` - Browser pool for anti-bot protected sites
- `ProxyAdapter` - Smart proxy routing to FlareSolverr
- `DomainRateLimiter` - Redis-based rate limiting

**Flow:**
1. User submits crawl/scrape request via API
2. Job created in database with `status=queued`
3. Scheduler assigns job to worker
4. Worker validates URLs and applies rate limits
5. **Firecrawl routes request:**
   - Simple sites → Direct scrape
   - Bot-protected → Camoufox browser pool
   - Cloudflare challenges → Proxy Adapter → FlareSolverr
6. Content converted to markdown
7. Source record created in database with content
8. Job marked `completed` or `failed`

**Camoufox Features:**
- Browser pool (5 instances, round-robin)
- 0% bot detection rate
- AJAX discovery via interactive element clicking
- Iframe content inlining
- Smart wait: DOM load → networkidle → content stability
- Ad-blocking for faster loads

**Proxy Adapter Features:**
- Domain-based routing (configurable blocklist)
- Automatic FlareSolverr integration
- Cloudflare challenge solving
- Transparent HTTP/HTTPS proxy on port 8192

**Key Features:**
- Per-domain rate limiting (configurable delays)
- Retry with exponential backoff
- Language detection and filtering
- URL pattern include/exclude rules
- Crawl depth limiting
- `llms.txt` awareness (overrides robots.txt if AI allowed)

### Stage 2: Domain Dedup & Classification

**Components:**
- `DomainDedupService` - Two-pass boilerplate removal
- `SmartClassifier` - 3-tier page classification
- `ContentSelector` - Domain-dedup-aware content selection

**Domain Dedup Flow:**
1. `analyze_boilerplate()` called per (project_id, domain)
2. Pass 1: Domain-level — SHA-256 fingerprint all content blocks across pages
3. Blocks appearing in ≥70% of pages marked as boilerplate
4. Pass 2: Section-level — group by URL path prefix (depth=1), repeat analysis
5. Merge fingerprints → store `cleaned_content` per source (original `content` never modified)

**Classification Flow (per source):**
1. **Pattern Skip**: Check URL/content against skip patterns (careers, news, legal)
2. **Embedding Similarity**: Embed page content (truncated to 6000 chars), compare to field group descriptions
   - High similarity (≥0.75) → use matched field groups only
   - Low similarity (<0.40) → use all field groups (conservative fallback)
   - Mid-range → proceed to reranker
3. **Reranker Scoring**: BGE-reranker-v2-m3 scores each (content, field_group) pair
   - Score ≥0.50 → include field group
   - Below threshold → exclude

### Stage 3: Knowledge Extraction

**Components:**
- `ExtractionWorker` - Processes extraction jobs
- `SchemaExtractionPipeline` - Schema-based pipeline returning typed `SchemaPipelineResult`
- `ExtractionPipelineService` - Generic extraction pipeline returning `BatchPipelineResult`
- `SchemaExtractionOrchestrator` - Multi-pass field group extraction + merge (accepts typed config facades)
- `SchemaExtractor` - LLM-based extraction per field group with source quoting
- `SchemaValidator` - Type coercion, enum validation, confidence gating
- `BackpressureManager` - LLM queue backpressure with exponential backoff
- `EmbeddingPipeline` - Unified embed+upsert service
- `EntityExtractor` - Named entity recognition
- `LLMClient` - Enqueues requests to Redis Streams
- `LLMWorker` - Background worker executing LLM calls

**Flow:**
1. Extraction job queued (auto or manual trigger)
2. Worker loads sources, selects content (cleaned if domain dedup enabled, else raw)
3. Content chunked with H2+ multi-level header splitting (max 5000 tokens, 200 token overlap)
4. **Classification** filters irrelevant field groups per source (see Stage 2)
5. **Parallel extraction** across field groups (up to 80 concurrent chunks):
   - Custom fields per project extraction schema
   - Source quoting (verbatim excerpts per field)
   - Conflict detection between chunks
6. Chunk results merged with typed strategies:
   - Boolean: `any()` — True if any chunk says True
   - Numeric: `highest_confidence` — value from best chunk
   - Text: `highest_confidence` — longest non-empty from best chunk
   - List: `merge_dedupe` — merge and deduplicate across chunks
   - Per-field `merge_strategy` override supported
7. Schema validation with type coercion (enum normalization, list parsing)
8. Confidence gating — suppress fields below `validation_min_confidence` (0.3)
9. Entities extracted and normalized
10. Deduplication via embedding similarity (threshold: 0.90)
11. Results persisted to PostgreSQL + Qdrant (via EmbeddingPipeline)

**LLM Worker Features:**
- Adaptive concurrency based on timeout ratio:
  - >10% timeouts → back off (reduce concurrency)
  - <2% timeouts → scale up (increase concurrency)
- Consumer group support for distributed workers
- Request expiration handling
- Dead Letter Queue for manual reprocessing

**Optimization:**
- Parallel chunk processing with semaphore control (80 concurrent)
- Parallel source processing (up to 20 concurrent sources)
- Continuous request flow for KV cache utilization
- Batched embedding generation (50 concurrent)
- Backpressure manager with exponential backoff
- Adaptive concurrency prevents LLM overload

### Stage 4: Storage & Indexing

**Components:**
- `ExtractionRepository` - Extraction CRUD operations
- `EntityRepository` - Entity management
- `QdrantRepository` - Vector storage operations
- `EmbeddingService` - Embedding generation
- `ExtractionDeduplicator` - Similarity-based deduplication

**Storage Layers:**

**PostgreSQL:**
- Projects (schemas, entity types)
- Sources (scraped content)
- Extractions (structured data)
- Entities (normalized entity values)
- Jobs (status tracking)

**Qdrant:**
- Extraction embeddings
- Metadata filters (source_group, extraction_type)
- Similarity search with score threshold

**Flow:**
1. Generate embedding for extraction
2. Check similarity against existing extractions
3. If novel (similarity < threshold), persist
4. Link entities to extractions
5. Update job result statistics

## Data Models

### Core Entities

```python
Project
├── id: UUID
├── name: str (unique)
├── extraction_schema: dict  # Field definitions
├── entity_types: list       # Entity type configs
├── prompt_templates: dict
└── Relationships:
    ├── sources: list[Source]
    ├── extractions: list[Extraction]
    └── entities: list[Entity]

Source
├── id: UUID
├── project_id: UUID
├── uri: str
├── source_group: str        # Company name
├── content: str             # Markdown
├── status: str              # pending|completed|failed
└── Relationships:
    └── extractions: list[Extraction]

Extraction
├── id: UUID
├── project_id: UUID
├── source_id: UUID
├── extraction_type: str     # Field group name
├── data: dict               # Extracted fields
├── confidence: float
├── embedding_id: str        # Qdrant point ID
└── Relationships:
    └── entity_links: list[ExtractionEntity]

Entity
├── id: UUID
├── project_id: UUID
├── entity_type: str         # product|person|location
├── value: str               # Original text
├── normalized_value: str    # Cleaned value
└── attributes: dict

Job
├── id: UUID
├── type: str                # scrape|crawl|extract
├── status: str              # queued|running|completed|failed
├── payload: dict
├── result: dict
└── error: str
```

### Field Groups

Field groups define extraction schemas with typed fields:

**Example: Company Overview Group**
```python
FieldGroup(
    name="company_overview",
    description="Company overview information",
    fields=[
        FieldDefinition(name="company_name", field_type="text", description="Company name"),
        FieldDefinition(name="industry", field_type="text", description="Industry sector"),
        FieldDefinition(name="employee_count", field_type="integer", description="Employee count"),
        FieldDefinition(name="headquarters", field_type="text", description="HQ location"),
    ],
    prompt_hint="Extract company overview information...",
    is_entity_list=False,
    max_items=None,
)
```

**Field Types:**
- `text` - String values
- `integer` / `float` - Numeric values
- `boolean` - True/False flags
- `list` - Arrays of values
- `enum` - Predefined choices (with `allowed_values`)

**Merge Strategies** (per-field `merge_strategy` override supported):

| Strategy | Used For | Behavior |
|----------|----------|----------|
| `any_true` | boolean | True if any chunk says True |
| `highest_confidence` | text, numeric | Value from highest-confidence chunk |
| `merge_dedupe` | list | Merge and deduplicate across chunks |
| `most_common` | enum, text | Most frequently seen value |
| `collect_unique` | list | Collect all unique values |
| `latest` | text | Value from last chunk |

**Valid merge strategies**: `any_true`, `highest_confidence`, `most_common`, `collect_unique`, `latest`, `merge_dedupe`

## Service Architecture

### Middleware Stack

**Order (outer to inner):**
```
Request
   ↓
CORSMiddleware           # CORS handling (outermost)
   ↓
HTTPSRedirectMiddleware  # HTTPS enforcement
   ↓
SecurityHeadersMiddleware # Security headers
   ↓
APIKeyMiddleware         # Authentication
   ↓
RateLimitMiddleware      # API rate limiting
   ↓
RequestLoggingMiddleware # Logs request/response
   ↓
RequestIDMiddleware      # Assigns unique request ID (innermost)
   ↓
FastAPI Router
```

**Note:** Middleware executes outer→inner on request, inner→outer on response.

### Service Container & Job Scheduler

**ServiceContainer** manages 10 app-lifetime services:
```python
ServiceContainer
├── embedding_service      # EmbeddingService (bge-m3)
├── qdrant_repo            # QdrantRepository (1024-dim vectors)
├── extraction_embedding   # EmbeddingPipeline
├── smart_classifier       # SmartClassifier (3-tier)
├── llm_client             # LLMClient
├── llm_queue              # LLMRequestQueue (Redis Streams)
├── llm_worker             # LLMWorker (adaptive concurrency)
├── scrape_worker          # ScrapeWorker
├── crawl_worker           # CrawlWorker
└── extraction_worker      # ExtractionWorker
```

**JobScheduler** (takes ServiceContainer):
```python
Scheduler
├── _cleanup_stale_jobs()    # Startup: mark running/cancelling → failed
├── _run_scrape_worker()     # 1 worker (staggered start)
├── _run_crawl_worker()      # N workers (configurable)
├── _run_extract_worker()    # 1 worker (staggered start)
└── _llm_worker              # LLM request processor
```

**Job Claiming:**
- `SELECT FOR UPDATE SKIP LOCKED` prevents race conditions
- Workers atomically claim jobs
- Stale job recovery on startup
- Priority-based ordering (priority DESC, created_at ASC)
- Staggered worker startup (default 1.0s between each)

### LLM Integration

**Components:**
- `LLMClient` - OpenAI-compatible API wrapper, enqueues to Redis
- `LLMRequestQueue` - Redis Streams-based queue
- `LLMWorker` - Background worker executing LLM calls

**Queue Mode (default enabled):**
1. **Enqueue:** `LLMClient` submits request to Redis Stream
2. **Claim:** `LLMWorker` reads via consumer group (distributed)
3. **Execute:** Worker calls OpenAI-compatible API
4. **Store:** Result saved to Redis with 5-minute TTL
5. **Return:** Client polls for result or times out

**Worker Behavior:**
- Reads batch of requests (up to concurrency limit)
- Processes all in parallel with semaphore
- Tracks success/timeout ratio
- Adjusts concurrency every 10 seconds:
  - >10% timeouts → reduce by 30%
  - <2% timeouts → increase by 20%
- Failed requests retry up to 3 times
- Permanent failures → Dead Letter Queue

**Request Types:**
- `extract_facts` - General fact extraction
- `extract_field_group` - Schema-based field extraction
- `extract_entities` - Named entity recognition

**Benefits:**
- Decouples extraction from LLM execution
- Adaptive concurrency prevents overload
- Fault tolerance via Redis persistence
- DLQ enables manual recovery
- Consumer groups support multiple workers

## Concurrency Patterns

### Scraping Concurrency

- **Per-domain rate limiting** - Prevents overwhelming target servers
- **Multi-domain parallelism** - Different domains scraped concurrently
- **Configurable delays** - `SCRAPE_DELAY_MIN` to `SCRAPE_DELAY_MAX`
- **Daily limits** - `SCRAPE_DAILY_LIMIT_PER_DOMAIN`

### Extraction Concurrency

- **Source-level parallelism** - Up to 20 sources processed concurrently
- **Chunk-level parallelism** - Chunks processed in parallel with semaphore (80 concurrent)
- **Field group parallelism** - All field groups extracted concurrently per source
- **Embedding parallelism** - Up to 50 concurrent embedding/rerank requests
- **KV cache optimization** - Continuous request flow prevents cache eviction
- **Backpressure** - Exponential backoff when LLM queue depth exceeds threshold

### Worker Pools

```python
# Crawl workers (multi-domain parallelism)
MAX_CONCURRENT_CRAWLS = 6  # Separate crawl jobs in parallel

# Crawl rate limiting (per domain)
CRAWL_MAX_CONCURRENCY = 2   # Concurrent requests per domain
CRAWL_DELAY_MS = 2000       # Delay between requests

# Extraction parallelism
EXTRACTION_MAX_CONCURRENT_CHUNKS = 80   # Chunks in flight
EXTRACTION_MAX_CONCURRENT_SOURCES = 20  # Sources in parallel
EXTRACTION_BATCH_SIZE = 20              # Sources per batch
EMBEDDING_MAX_CONCURRENT = 50           # Embedding requests
```

## Storage Patterns

### Source Management

**Lifecycle:**
```
pending → ready → (extraction) → extracted → completed
                    ↓ (skip)      ↓ (on error)
                   skipped         failed
```

**Statuses:**
- `pending` — scraped, awaiting processing
- `ready` — domain dedup cleaned (if enabled)
- `skipped` — classified as irrelevant (careers, news, etc.)
- `extracted` — LLM extraction complete
- `completed` — fully processed (embeddings stored)
- `failed` — error during processing
- `partial` — partially processed (some field groups succeeded)

**Deduplication:**
- Unique constraint on `(project_id, uri)`
- Prevents duplicate scraping
- Updates content if URI re-scraped

### Extraction Deduplication

**Embedding-based similarity:**
```python
1. Generate embedding for new extraction
2. Query Qdrant for similar extractions (same source_group)
3. If max_similarity < THRESHOLD (0.90):
   - Store extraction
   - Index embedding
4. Else:
   - Log as duplicate
   - Skip storage
```

**Benefits:**
- Reduces storage bloat
- Improves search relevance
- Maintains data quality

### Entity Normalization

**Process:**
```python
1. Extract raw entity value from text
2. Normalize (lowercase, trim, remove punctuation)
3. Check if normalized_value exists in source_group
4. If exists: Link to existing entity
5. If new: Create entity, link to extraction
```

**Example:**
```
"Apple Inc." → normalized: "apple inc"
"Apple, Inc" → normalized: "apple inc"  (same entity)
```

## Configuration Management

**Settings Hierarchy:**
1. Environment variables (`.env` file)
2. Default values in `config.py`
3. Pydantic validation and type coercion

**Typed Config Facades:**

Settings are organized into 10 frozen dataclass facades accessible via properties:
- `settings.database` → `DatabaseConfig`
- `settings.llm` → `LLMConfig`
- `settings.llm_queue` → `LLMQueueConfig`
- `settings.extraction` → `ExtractionConfig`
- `settings.classification` → `ClassificationConfig`
- `settings.scraping` → `ScrapingConfig`
- `settings.crawl` → `CrawlConfig`
- `settings.proxy` → `ProxyConfig`
- `settings.scheduler` → `SchedulerConfig`
- `settings.observability` → `ObservabilityConfig`

Services accept these typed facades in constructors (e.g. `SchemaExtractor(llm_config: LLMConfig)`) instead of the monolithic `Settings` object.

**Key Settings:**

| Category | Setting | Default | Purpose |
|----------|---------|---------|---------|
| Security | `API_KEY` | (required) | API authentication |
| Database | `DATABASE_URL` | localhost:5432 | Main PostgreSQL |
| Redis | `REDIS_URL` | localhost:6379 | Cache and queue |
| Qdrant | `QDRANT_URL` | localhost:6333 | Vector DB |
| Firecrawl | `FIRECRAWL_URL` | localhost:3002 | Scraping API |
| LLM | `LLM_MODEL` | Qwen3-30B-A3B-Instruct-4bit | Extraction model |
| LLM | `RAG_EMBEDDING_MODEL` | bge-m3 | Embedding model |
| LLM | `EMBEDDING_DIMENSION` | 1024 | Vector dimension |
| LLM | `LLM_HTTP_TIMEOUT` | 120s | Request timeout |
| LLM | `LLM_MAX_TOKENS` | 8192 | Max response tokens |
| Extraction | `EXTRACTION_CONTENT_LIMIT` | 20000 | Max chars to LLM |
| Extraction | `EXTRACTION_CHUNK_MAX_TOKENS` | 5000 | Tokens per chunk |
| Extraction | `EXTRACTION_BATCH_SIZE` | 20 | Sources per batch |
| Classification | `CLASSIFICATION_ENABLED` | true | Page classification |
| Classification | `SMART_CLASSIFICATION_ENABLED` | true | Embedding-based |
| Classification | `RERANKER_MODEL` | bge-reranker-v2-m3 | Reranker model |
| Classification | `CLASSIFICATION_CONTENT_LIMIT` | 6000 | Classifier char limit |
| Dedup | `DOMAIN_DEDUP_ENABLED` | true | Boilerplate removal |
| Scraping | `SCRAPE_TIMEOUT` | 180s | Page load timeout |
| Crawl | `CRAWL_DELAY_MS` | 2000 | Per-request delay |
| Crawl | `MAX_CONCURRENT_CRAWLS` | 6 | Parallel crawl jobs |
| Scheduler | `SCHEDULER_CLEANUP_STALE_ON_STARTUP` | true | Stale job recovery |

## Error Handling

### Retry Strategies

**Scraping:**
- Max retries: 3
- Base delay: 2s
- Max delay: 60s
- Exponential backoff

**LLM Requests:**
- Max retries: 3
- Backoff: 2s to 30s
- Timeout: 120s (2 minutes)

**Crawl Jobs:**
- Poll interval: 5s
- Stale job recovery (updated_at threshold)
- Graceful failure with partial results

### Job Status Transitions

```
queued → running → completed
         ↓    ↓
        failed  cancelling → cancelled
```

**Recovery:**
- **Startup cleanup**: Running/cancelling jobs from previous instance marked as failed
- Workers use row-level locking (`SELECT FOR UPDATE SKIP LOCKED`) to prevent duplicate processing
- Failed jobs retain error message and partial results
- Stale threshold per job type: scrape 5min, extract 15min, crawl 30min

## Security Architecture

### Authentication
- API key validation via `APIKeyMiddleware`
- Key length minimum: 16 characters
- Configurable via `API_KEY` environment variable

### Rate Limiting
- Per-API-key rate limiting
- Configurable window and burst
- Redis-backed token bucket algorithm

### HTTPS
- Optional HTTPS enforcement
- Redirect middleware (production mode)
- Configurable redirect host

### Headers
- `Content-Security-Policy`
- `X-Frame-Options`
- `X-Content-Type-Options`
- `Strict-Transport-Security` (when HTTPS enabled)

## Scalability Considerations

### Horizontal Scaling

**Stateless API:**
- Multiple FastAPI instances behind load balancer
- Shared PostgreSQL and Redis
- Job queue prevents duplicate processing

**Worker Scaling:**
- Increase `MAX_CONCURRENT_CRAWLS` for more crawl parallelism
- Deploy multiple scheduler instances for higher throughput
- Scale LLM backend (vLLM cluster)

### Database Optimization

**Indexes:**
- `sources(project_id, uri)` - Unique constraint + fast lookup
- `extractions(project_id, source_group)` - Filter queries
- `entities(normalized_value, source_group)` - Entity matching
- `jobs(type, status)` - Worker queries

**Partitioning:**
- Consider table partitioning by `project_id` for large deployments
- Archive old jobs to reduce table scan overhead

### Caching

**Redis Usage:**
- Rate limit counters (TTL-based)
- LLM request queue (streams)
- Optional response caching (future)

**Vector Cache:**
- Qdrant HNSW index for fast ANN search
- In-memory HNSW graph
- Disk-backed payload storage

## Monitoring & Observability

### Structured Logging

**Format:** JSON (production) or Pretty (development)

**Context:**
- `request_id` - Unique per request
- `job_id` - Background job identifier
- `source_id` / `extraction_id` - Entity identifiers
- `error` / `error_type` - Exception details

**Events:**
- `application_startup` - Service version, config
- `scrape_job_created` - Job metadata
- `extraction_job_completed` - Result statistics
- `chunk_extraction_retry` - Retry attempts

### Metrics

**Prometheus Endpoint:** `/api/v1/metrics`

**Metrics:**
- Job counts by type and status
- Extraction throughput
- LLM request latency
- Vector search performance
- Rate limit hits

### Health Checks

**Endpoint:** `/health`

**Checks:**
- Database connectivity
- Redis connectivity
- Qdrant connectivity
- Service version and commit

**Response:**
```json
{
  "status": "ok",
  "service": "scristill-pipeline",
  "version": "v1.3.1",
  "database": {"connected": true},
  "redis": {"connected": true},
  "qdrant": {"connected": true}
}
```

## Deployment

### Docker Compose

**Core Services:**
- `pipeline` - FastAPI application (port 8000)
- `postgres` - Main database (port 5432)
- `redis` - Cache and queue (port 6379)
- `qdrant` - Vector database (port 6333)

**Scraping Stack:**
- `firecrawl-api` - Scraping orchestrator (port 3002)
- `firecrawl-db` - Firecrawl's PostgreSQL (internal)
- `rabbitmq` - Message broker for Firecrawl (ports 5672, 15672)
- `camoufox` - Anti-bot browser service (port 3004)
- `flaresolverr` - Cloudflare solver (port 8191)
- `proxy-adapter` - Smart proxy router (port 8192)

**Utility Services:**
- `migrate` - Database migration runner (one-shot)

### Environment Variables

See `.env.example` for full list of configuration options.

### Database Migrations

```bash
# Generate migration
alembic revision --autogenerate -m "description"

# Apply migrations
alembic upgrade head

# Rollback
alembic downgrade -1
```

### Health Monitoring

- Kubernetes liveness probe: `GET /health`
- Readiness probe: Check `database.connected == true`
- Graceful shutdown via SIGTERM handler

**Shutdown Manager:**
- Global singleton coordinates cleanup
- Registers async cleanup callbacks
- Stops workers before terminating
- 30-second timeout per callback
- Prevents data loss during shutdown

## Project Lifecycle

### Creation

Projects can be created in two ways:

1. **From Template** (recommended):
   ```bash
   POST /api/v1/projects/from-template
   {
     "template": "company_analysis",
     "name": "My Project",
     "description": "..."
   }
   ```

2. **Custom Schema**:
   ```bash
   POST /api/v1/projects
   {
     "name": "My Project",
     "extraction_schema": {...},
     "entity_types": [...],
     ...
   }
   ```

**Available Templates:**
- `company_analysis` - Technical documentation analysis
- `research_survey` - Academic paper extraction
- `contract_review` - Legal document analysis
- `book_catalog` - Book information extraction
- `drivetrain_company_analysis` - Detailed industrial drivetrain company analysis
- `drivetrain_company_simple` - Simplified drivetrain company extraction
- `default` - Generic fact extraction for any content

Templates may include `extraction_context` (source type, entity ID fields), `classification_config` (skip patterns), and `crawl_config` (focus terms).

### Soft Delete

Projects use soft delete to preserve data integrity:

```python
DELETE /api/v1/projects/{id}  # Sets is_active=false
```

**Behavior:**
- Project remains in database with `is_active=false`
- Cascade behavior controlled via foreign keys
- Sources, extractions, entities preserved
- Can be reactivated if needed
- Filters in list queries exclude inactive projects

**Benefits:**
- Data recovery possible
- Audit trail maintained
- No orphaned foreign key references
- Safe for production environments

## Document Processing & Chunking

### Chunking Strategy

Documents are chunked semantically to preserve context while staying within LLM token limits:

**Multi-Level Header Splitting:**
- Primary split on H2+ headers (`## `, `### `, etc.)
- Maximum 5000 tokens per chunk (CJK-aware token counting)
- 200-token overlap between chunks for context continuity
- Header breadcrumb paths preserved for context tracking
- Preamble (content before first header) treated as standalone chunk
- Falls back to paragraph splitting, then word splitting for oversized sections

**Algorithm:**
1. Split document on H2+ headers
2. Combine sections until reaching token limit
3. If single section exceeds limit:
   - Keep header
   - Split content by paragraphs (`\n\n`)
   - If paragraph still too large, split by words
4. Extract header path (H1 > H2 > H3) for breadcrumbs
5. Apply overlap window between adjacent chunks

**Chunk Metadata:**
```python
DocumentChunk(
    content="## API Reference\n\nThe API supports...",
    chunk_index=2,           # 3rd chunk of document
    total_chunks=5,          # Total chunks in document
    header_path=["Documentation", "API Reference"]  # Breadcrumb
)
```

**Content Selection:**
- When `domain_dedup_enabled=True`: prefer `cleaned_content` over raw `content`
- Content truncated to `EXTRACTION_CONTENT_LIMIT` (20000 chars) before chunking
- Content budget: ~21% of Qwen3-30B's 32K token context window

## Report Generation

**Report Types:**
- `TABLE` - Comparison table across source groups
- `SCHEMA_TABLE` - Schema-based structured table
- `SUMMARY` - LLM-generated executive summary
- `DETAILED` - Comprehensive analysis with all extractions

**Output Formats:**
- Markdown (`.md`) - Human-readable reports
- Excel (`.xlsx`) - Structured data with formatting
- PDF (`.pdf`) - Publication-ready documents

**Export Capabilities:**
- CSV/JSON export for entities and extractions
- Filtering by source group, category, confidence
- Bulk data export for external analysis

## Entity Querying

### By-Value Search

Find which source_groups contain a specific entity:

```python
GET /api/v1/projects/{id}/entities/by-value
    ?entity_type=feature
    &value=SSO

Response:
{
  "entity_type": "feature",
  "value": "SSO",
  "source_groups": ["Company A", "Company B", "Company C"],
  "total": 3
}
```

**Use Case:** Quickly identify which companies/documents mention a specific capability, price point, or feature.

**Implementation:**
- Case-insensitive matching via `normalized_value`
- Returns distinct list of source_groups
- Efficient for cross-company feature comparison
- Supports all entity types (features, pricing, limits, certifications, etc.)

## Future Enhancements

- WebSocket support for real-time job updates
- Multi-tenant isolation with project-level API keys
- Advanced caching strategies for repeated extractions
- Custom LLM model selection per field group
- GraphQL API for complex queries
- Batch export to data warehouses
- WebUI for remote control (planned)
