# Scristill Pipeline - Knowledge Extraction Orchestrator

A FastAPI-based web scraping and knowledge extraction pipeline that crawls websites, extracts structured data using LLMs, and provides semantic search capabilities through vector embeddings.

## Overview

This system orchestrates a complete data pipeline from web scraping to structured knowledge extraction:

1. **Crawl/Scrape** - Fetches web pages using Firecrawl with rate limiting and retry logic
2. **Smart Crawl** - Filters discovered URLs by relevance to extraction schema before scraping
3. **Domain Dedup** - Removes boilerplate content (navs, footers, cookie banners) per domain
4. **Classify** - 3-tier page classification (skip → embed → rerank) to select relevant field groups
5. **Extract** - Uses LLMs to extract structured data based on configurable schemas
6. **Store** - Persists extractions in PostgreSQL with vector embeddings in Qdrant
7. **Search** - Enables semantic search across extracted knowledge
8. **Report** - Generates markdown and Excel reports from extracted data

## Key Features

### Multi-Domain Project Support
- Define extraction schemas per project
- Support for entity recognition and linking
- Template-based project creation (company analysis, research surveys, etc.)
- Flexible JSONB schemas for domain-specific extraction

### Smart Crawl & Scraping
- Firecrawl integration for robust web scraping
- **Smart Crawl**: Maps site URLs, filters by embedding similarity to extraction schema, scrapes only relevant pages
- Domain-level rate limiting with Redis
- Automatic retry with exponential backoff
- Language detection and filtering (12 languages excluded by default)
- Crawl depth control with URL pattern filtering

### Domain Boilerplate Deduplication
- Two-pass dedup: domain-level then section-level (by URL path prefix)
- SHA-256 block fingerprinting with whitespace normalization
- Configurable threshold (default 70% of pages)
- Stores `cleaned_content` separately (never modifies `source.content`)
- Per `(project_id, domain)` scope

### Page Classification
- 3-tier classification: pattern skip → embedding similarity → reranker scoring
- Embedding-based (not regex) using bge-m3 + BGE-reranker-v2-m3
- Configurable thresholds per tier
- Template-specific skip patterns (careers, news, legal pages)

### Schema-Based Extraction
- Multi-pass extraction across field groups
- Chunk-based processing with H2+ multi-level header splitting
- CJK-aware token counting
- Parallel extraction with KV cache optimization (up to 80 concurrent chunks)
- Source quoting (verbatim excerpts per field)
- Conflict detection between chunks
- Schema validation with type coercion
- Confidence gating (suppress low-confidence fields)
- Entity extraction and normalization

### Storage & Search
- PostgreSQL for relational data and job tracking
- Qdrant vector database for semantic search
- Deduplication based on embedding similarity
- Source-to-extraction lineage tracking

### Background Job Processing
- **ServiceContainer** manages 10 app-lifetime services with create/cache/teardown
- **JobScheduler** with worker pools and staggered startup
- Separate workers for scraping, crawling, and extraction
- Stale job cleanup on startup
- Graceful shutdown via ShutdownManager
- Job status tracking and error recovery
- Dead Letter Queue (DLQ) for failed LLM requests

## Architecture Components

### Core Services

| Service | Purpose |
|---------|---------|
| **ServiceContainer** | Creates, caches, and tears down 10 app-lifetime services |
| **Scraper** | Manages Firecrawl client, rate limiting, and retry logic |
| **Camoufox** | Browser-based scraper with anti-bot evasion and AJAX discovery |
| **Proxy Adapter** | Routes requests through FlareSolverr for challenge solving |
| **SmartClassifier** | 3-tier page classification (skip → embed → rerank) |
| **DomainDedupService** | Two-pass boilerplate removal per domain |
| **Extraction** | Orchestrates LLM-based extraction across field groups |
| **EmbeddingPipeline** | Unified embed+upsert service for both pipelines |
| **Storage** | Handles persistence to PostgreSQL and Qdrant |
| **LLM Client** | Enqueues requests to Redis Streams for async processing |
| **LLM Worker** | Background worker executing LLM calls with adaptive concurrency |
| **Knowledge** | Entity extraction and relationship management |
| **Filtering** | Language detection and content filtering |
| **Reports** | Generates markdown, Excel, and PDF reports |
| **Metrics** | Prometheus metrics collection and export |

### Infrastructure

- **PostgreSQL** - Primary database (projects, sources, extractions, entities, jobs)
- **Firecrawl PostgreSQL** - Separate database for Firecrawl's NUQ job queue
- **Redis** - Rate limiting, job queuing, LLM request queue
- **RabbitMQ** - Message broker for Firecrawl job distribution
- **Qdrant** - Vector database for semantic search
- **Firecrawl API** - Web scraping orchestration service
- **Camoufox** - Anti-bot browser service (0% detection rate)
- **FlareSolverr** - Cloudflare challenge solver
- **Proxy Adapter** - Smart proxy routing requests through FlareSolverr

## Project Structure

```
src/
├── api/v1/             # FastAPI endpoints (scrape, crawl, extraction, projects, search, dedup)
├── services/
│   ├── scraper/
│   │   ├── service_container.py  # App-lifetime service creation/caching/teardown
│   │   ├── scheduler.py          # Job scheduler with staggered startup
│   │   ├── scrape_worker.py      # Scrape job processing
│   │   ├── crawl_worker.py       # Multi-page crawl with depth control
│   │   └── ...                   # Rate limiting, Firecrawl client, smart crawl
│   ├── extraction/
│   │   ├── pipeline.py           # Main pipeline orchestration (SchemaExtractionPipeline)
│   │   ├── schema_orchestrator.py # Multi-pass field group extraction + merge
│   │   ├── schema_extractor.py   # LLM-based extraction per field group
│   │   ├── schema_validator.py   # Type coercion, enum, confidence gating
│   │   ├── smart_classifier.py   # 3-tier page classification
│   │   ├── domain_dedup.py       # Two-pass boilerplate removal
│   │   ├── embedding_pipeline.py # Unified embed+upsert service
│   │   ├── backpressure.py       # LLM queue backpressure manager
│   │   ├── content_selector.py   # Domain-dedup-aware content selection
│   │   ├── field_groups.py       # FieldDefinition (with merge_strategy), FieldGroup
│   │   ├── schema_adapter.py     # Template → FieldGroup conversion
│   │   ├── worker.py             # Extraction job worker
│   │   └── ...                   # Content cleaner, page classifier
│   ├── storage/        # Repositories (source, extraction, entity), Qdrant, embedding
│   ├── llm/            # LLM client, chunking (CJK-aware), request queue
│   ├── knowledge/      # Entity extraction
│   ├── dlq/            # Dead Letter Queue service
│   └── filtering/      # Language detection, pattern filtering
├── middleware/         # Auth, rate limiting, logging, security headers
├── models.py           # Pydantic models for API
├── orm_models.py       # SQLAlchemy ORM models
├── database.py         # Database connection
├── config.py           # Settings, 10 typed subsystem facades (frozen dataclasses)
└── main.py             # FastAPI application entry point
```

## Quick Start

### Prerequisites

- Python 3.12+
- Docker & Docker Compose (recommended) OR:
  - PostgreSQL (x2 instances: main + Firecrawl)
  - Redis
  - RabbitMQ
  - Qdrant
  - Firecrawl API
  - Camoufox browser service
  - FlareSolverr
  - Proxy adapter

### Installation

```bash
# Clone repository
git clone <repository-url>
cd knowledge_extraction-orchestrator

# Create virtual environment
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Run database migrations
alembic upgrade head
```

### Configuration

Create a `.env` file with required settings:

```bash
# Security
API_KEY=your-secure-api-key-here

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/dbname

# Redis
REDIS_URL=redis://localhost:6379

# Qdrant
QDRANT_URL=http://localhost:6333

# Firecrawl
FIRECRAWL_URL=http://localhost:3002

# Anti-Bot Protection
FLARESOLVERR_URL=http://localhost:8191
USE_FLARESOLVERR=true

# Camoufox Browser Service
CAMOUFOX_BROWSER_COUNT=5
CAMOUFOX_POOL_SIZE=10

# LLM Configuration
OPENAI_BASE_URL=http://localhost:9003/v1
LLM_MODEL=Qwen3-30B-A3B-Instruct-4bit
RAG_EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSION=1024
```

### Running the Application

```bash
# Development server
cd src && uvicorn main:app --reload

# Production server
cd src && uvicorn main:app --host 0.0.0.0 --port 8000
```

### Using Docker Compose

```bash
# Start all services
docker-compose up -d

# Check logs
docker-compose logs -f pipeline

# Stop services
docker-compose down
```

## API Endpoints

### Projects
- `POST /api/v1/projects` - Create new project
- `GET /api/v1/projects` - List all projects
- `GET /api/v1/projects/templates` - List available templates
- `GET /api/v1/projects/{id}` - Get project details
- `PUT /api/v1/projects/{id}` - Update project
- `DELETE /api/v1/projects/{id}` - Soft delete project
- `POST /api/v1/projects/from-template` - Create from template

### Scraping
- `POST /api/v1/scrape` - Queue scrape job for specific URLs
- `GET /api/v1/scrape/{job_id}` - Get scrape job status
- `POST /api/v1/crawl` - Start website crawl
- `GET /api/v1/crawl/{job_id}` - Get crawl job status

### Extraction
- `POST /api/v1/projects/{id}/extract` - Queue extraction job
- `GET /api/v1/projects/{id}/extractions` - List extractions with filters
- `GET /api/v1/projects/{id}/extractions/{extraction_id}` - Get extraction details

### Domain Dedup
- `POST /api/v1/projects/{id}/analyze-boilerplate` - Run boilerplate analysis
- `GET /api/v1/projects/{id}/boilerplate-stats` - Get per-domain dedup statistics

### Search & Entities
- `POST /api/v1/search` - Semantic search across extractions
- `GET /api/v1/projects/{id}/entities` - List entities with filtering
- `GET /api/v1/projects/{id}/entities/types` - Get entity type counts
- `GET /api/v1/projects/{id}/entities/{entity_id}` - Get single entity
- `GET /api/v1/projects/{id}/entities/by-value` - Find source_groups with entity value

### Reports & Export
- `POST /api/v1/reports` - Generate report (markdown, Excel, PDF)
- `GET /api/v1/reports/{id}` - Download report
- `GET /api/v1/projects/{id}/export/entities` - Export entities (CSV/JSON)
- `GET /api/v1/projects/{id}/export/extractions` - Export extractions (CSV/JSON)

### Jobs & Metrics
- `GET /api/v1/jobs` - List all jobs
- `GET /api/v1/jobs/{id}` - Get job details
- `GET /api/v1/metrics` - Prometheus metrics

## Usage Example

```python
import httpx

# List available templates
templates = httpx.get(
    "http://localhost:8000/api/v1/projects/templates",
    headers={"X-API-Key": "your-api-key"}
).json()

# Create a project from template
project_response = httpx.post(
    "http://localhost:8000/api/v1/projects/from-template",
    json={
        "template": "company_analysis",
        "name": "Tech Companies 2026",
        "description": "Analysis of technology companies"
    },
    headers={"X-API-Key": "your-api-key"}
)
project_id = project_response.json()["id"]

# Crawl a website
crawl_response = httpx.post(
    "http://localhost:8000/api/v1/crawl",
    json={
        "url": "https://example.com",
        "project_id": project_id,
        "company": "Example Corp",
        "max_depth": 2,
        "limit": 50,
        "auto_extract": True
    },
    headers={"X-API-Key": "your-api-key"}
)

# Or trigger extraction manually
extract_response = httpx.post(
    f"http://localhost:8000/api/v1/projects/{project_id}/extract",
    json={
        "source_ids": ["source-uuid"]
    },
    headers={"X-API-Key": "your-api-key"}
)

# Check job status
job_id = crawl_response.json()["job_id"]
status = httpx.get(
    f"http://localhost:8000/api/v1/crawl/{job_id}",
    headers={"X-API-Key": "your-api-key"}
)

# Search extracted data
search_results = httpx.post(
    f"http://localhost:8000/api/v1/projects/{project_id}/search",
    json={
        "query": "What products does the company offer?",
        "limit": 10,
        "source_groups": ["Example Corp"]
    },
    headers={"X-API-Key": "your-api-key"}
)

# Find which companies have a specific feature
entity_search = httpx.get(
    f"http://localhost:8000/api/v1/projects/{project_id}/entities/by-value",
    params={
        "entity_type": "feature",
        "value": "SSO"
    },
    headers={"X-API-Key": "your-api-key"}
)
# Returns: {"source_groups": ["Company A", "Company B"], "total": 2}
```

## Project Templates

The system includes pre-built templates for common use cases:

| Template | Use Case | Entity Types |
|----------|----------|--------------|
| `company_analysis` | Technical documentation analysis | plan, feature, limit, certification, pricing |
| `research_survey` | Academic paper extraction | author, institution, method, metric, dataset, citation |
| `contract_review` | Legal document analysis | party, date, amount, duration, jurisdiction |
| `book_catalog` | Book information extraction | book, author, price, category, publisher |
| `drivetrain_company_analysis` | Detailed industrial drivetrain company analysis | product, certification, application, standard |
| `drivetrain_company_simple` | Simplified drivetrain company extraction | product, service, certification |
| `default` | Generic fact extraction for any content | entity, fact, attribute |

All templates include extraction schemas, entity type definitions, optional extraction context (source type, entity ID fields), and optional classification/crawl configuration.

## Configuration Options

### Scraping Configuration
- `SCRAPE_DELAY_MIN/MAX` - Delay between requests (seconds)
- `SCRAPE_MAX_CONCURRENT_PER_DOMAIN` - Concurrent scrapes per domain
- `SCRAPE_DAILY_LIMIT_PER_DOMAIN` - Daily rate limit
- `SCRAPE_TIMEOUT` - Request timeout (seconds)
- `SCRAPE_RETRY_BASE_DELAY` - Base retry delay (seconds)
- `SCRAPE_RETRY_MAX_DELAY` - Max retry delay (seconds)
- `CRAWL_DELAY_MS` - Delay between crawl requests (milliseconds)
- `CRAWL_MAX_CONCURRENCY` - Concurrent requests per domain during crawl
- `MAX_CONCURRENT_CRAWLS` - Parallel crawl jobs across domains

### LLM Configuration
- `LLM_MODEL` - Model name for extraction (default: `Qwen3-30B-A3B-Instruct-4bit`)
- `RAG_EMBEDDING_MODEL` - Embedding model name (default: `bge-m3`)
- `EMBEDDING_DIMENSION` - Embedding vector dimension (default: 1024)
- `LLM_HTTP_TIMEOUT` - Request timeout in seconds (default: 120)
- `LLM_MAX_TOKENS` - Maximum response tokens (default: 8192)
- `LLM_MAX_RETRIES` - Maximum retry attempts (default: 3)
- `LLM_RETRY_BACKOFF_MIN/MAX` - Retry backoff range in seconds (default: 2/30)
- `LLM_QUEUE_ENABLED` - Enable Redis queue mode (default: false)
- `EXTRACTION_MAX_CONCURRENT_CHUNKS` - Parallel chunk processing (default: 80)

### Extraction Configuration
- `EXTRACTION_CONTENT_LIMIT` - Max characters sent to LLM (default: 20000)
- `EXTRACTION_CHUNK_MAX_TOKENS` - Max tokens per chunk (default: 5000)
- `EXTRACTION_CHUNK_OVERLAP_TOKENS` - Overlap between chunks (default: 200)
- `EXTRACTION_SOURCE_QUOTING_ENABLED` - Source quotes per field (default: true)
- `EXTRACTION_CONFLICT_DETECTION_ENABLED` - Merge conflict recording (default: true)
- `EXTRACTION_VALIDATION_ENABLED` - Schema validation (default: true)
- `EXTRACTION_BATCH_SIZE` - Sources per batch in schema pipeline (default: 20)

### Classification Configuration
- `CLASSIFICATION_ENABLED` - Enable page classification (default: true)
- `CLASSIFICATION_SKIP_ENABLED` - Skip irrelevant pages (default: true)
- `SMART_CLASSIFICATION_ENABLED` - Embedding-based classification (default: true)
- `RERANKER_MODEL` - Reranker model (default: `bge-reranker-v2-m3`)
- `CLASSIFICATION_CONTENT_LIMIT` - Max chars for classifier (default: 6000)

### Domain Dedup Configuration
- `DOMAIN_DEDUP_ENABLED` - Use cleaned content for extraction (default: true)
- `DOMAIN_DEDUP_THRESHOLD_PCT` - Boilerplate frequency threshold (default: 0.7)
- `DOMAIN_DEDUP_MIN_PAGES` - Minimum pages before analysis (default: 5)
- `DOMAIN_DEDUP_MIN_BLOCK_CHARS` - Minimum block size (default: 50)

### Language Filtering
- `LANGUAGE_FILTERING_ENABLED` - Enable language detection (default: true)
- `LANGUAGE_DETECTION_CONFIDENCE_THRESHOLD` - Confidence threshold (default: 0.7)
- `EXCLUDED_LANGUAGE_CODES` - Languages to exclude (default: de,fi,fr,es,it,nl,pt,pl,ru,sv,no,da)

## Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src --cov-report=html

# Run specific test file
pytest tests/test_extraction.py -v

# Run matching pattern
pytest -k "test_chunking"
```

## Monitoring

- **Health Check** - `GET /health` - Service status and component health
- **Metrics** - `GET /api/v1/metrics` - Prometheus metrics endpoint
- **Logs** - Structured JSON logging via structlog

## Security

- API key authentication via `X-API-Key` header
- HTTPS enforcement (configurable)
- Security headers middleware
- Rate limiting per API key
- CORS configuration

## License

See LICENSE file for details.
