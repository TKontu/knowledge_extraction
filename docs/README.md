# Knowledge Extraction Pipeline

Self-hosted system for scraping documentation, extracting structured data via LLM, and generating comparison reports. Supports multiple extraction domains through **project-based configuration**.

## Features

- **Project-Based Extraction**: Define custom schemas, entity types, and prompts per project
- **Web Scraping**: Firecrawl-based scraping with JS rendering and anti-bot handling
- **LLM Extraction**: Chunking, extraction, validation, and deduplication pipeline
- **Entity Recognition**: Extract and normalize entities (plans, features, limits, pricing)
- **Vector Search**: Hybrid semantic + structured search via Qdrant
- **Report Generation**: Single source and comparison reports with entity tables
- **Observability**: Prometheus metrics, structured logging, request tracing
- **583 Tests**: Comprehensive test coverage

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Existing vLLM gateway at `192.168.0.247:9003`
- BGE-large-en available at `192.168.0.136:9003`

### 1. Clone and Configure

```bash
git clone <repo>
cd knowledge_extraction

# Copy and edit environment
cp .env.example .env
# Edit .env with your settings
```

### 2. Deploy

```bash
docker compose up -d
```

### 3. Verify Services

```bash
# Health check (includes DB, Redis, Qdrant, Firecrawl status)
curl http://localhost:8000/health

# Prometheus metrics
curl http://localhost:8000/metrics
```

## API Overview

All data operations are **project-scoped**. Create a project first, then scrape, extract, and query within that project.

### Projects

```bash
# List available templates
curl http://localhost:8000/api/v1/projects/templates

# Create project from template
curl -X POST http://localhost:8000/api/v1/projects/from-template \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "template": "company_analysis",
    "name": "my_research_project",
    "description": "Analyzing competitor documentation"
  }'

# List projects
curl http://localhost:8000/api/v1/projects \
  -H "X-API-Key: your-api-key"
```

### Scraping

```bash
# Scrape URLs into a project
curl -X POST http://localhost:8000/api/v1/scrape \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "urls": ["https://docs.example.com/api"],
    "project_id": "uuid-here",
    "source_group": "example_inc"
  }'

# Check job status
curl http://localhost:8000/api/v1/scrape/{job_id} \
  -H "X-API-Key: your-api-key"
```

### Extraction

```bash
# Trigger extraction for a project
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/extract \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "source_ids": ["uuid1", "uuid2"],
    "profile": "detailed"
  }'

# List extractions with filtering
curl "http://localhost:8000/api/v1/projects/{project_id}/extractions?source_group=example_inc&min_confidence=0.8" \
  -H "X-API-Key: your-api-key"
```

### Entities

```bash
# List entities by type
curl "http://localhost:8000/api/v1/projects/{project_id}/entities?entity_type=feature" \
  -H "X-API-Key: your-api-key"

# Get entity type counts
curl http://localhost:8000/api/v1/projects/{project_id}/entities/types \
  -H "X-API-Key: your-api-key"

# Find which source_groups have an entity value
curl "http://localhost:8000/api/v1/projects/{project_id}/entities/by-value?value=SSO" \
  -H "X-API-Key: your-api-key"
```

### Search

```bash
# Hybrid semantic + structured search
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/search \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "query": "API rate limits",
    "source_groups": ["company_a", "company_b"],
    "limit": 20
  }'
```

### Reports

```bash
# Single source_group report
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/reports \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "type": "single",
    "source_groups": ["example_inc"],
    "title": "Example Inc Analysis"
  }'

# Comparison report with entity tables
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/reports \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "type": "comparison",
    "source_groups": ["company_a", "company_b"],
    "entity_types": ["feature", "limit", "pricing"],
    "title": "Feature Comparison"
  }'

# List reports
curl http://localhost:8000/api/v1/projects/{project_id}/reports \
  -H "X-API-Key: your-api-key"
```

### Jobs & Metrics

```bash
# List all jobs with filtering
curl "http://localhost:8000/api/v1/jobs?status=completed&job_type=extract" \
  -H "X-API-Key: your-api-key"

# Prometheus metrics
curl http://localhost:8000/metrics
```

## Project Templates

| Template | Use Case | Entity Types |
|----------|----------|--------------|
| `company_analysis` | Technical documentation analysis | plan, feature, limit, certification, pricing |
| `research_survey` | Academic paper extraction (coming soon) | author, method, dataset, metric, citation |
| `contract_review` | Legal document analysis (coming soon) | party, date, amount, duration, jurisdiction |

## Configuration

Key environment variables:

```bash
# LLM (via your existing gateway)
OPENAI_BASE_URL=http://192.168.0.247:9003/v1
OPENAI_EMBEDDING_BASE_URL=http://192.168.0.136:9003/v1
LLM_MODEL=gemma3-12b-awq
RAG_EMBEDDING_MODEL=bge-large-en

# Scraping behavior
SCRAPE_DELAY_MIN=2
SCRAPE_DELAY_MAX=5
SCRAPE_MAX_CONCURRENT_PER_DOMAIN=2

# Services
REDIS_URL=redis://redis:6379
QDRANT_URL=http://qdrant:6333
DATABASE_URL=postgresql://user:pass@postgres:5432/extraction

# Security
API_KEY=your-secure-api-key
```

See `.env.example` for full list.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed system design.

```
┌─────────────────────────────────────────────────────────────┐
│                    Pipeline Service (FastAPI)                │
│                                                             │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐  ┌─────────────────┐ │
│  │ Scraper │→ │ Chunker │→ │Extractor│→ │ Deduplicator    │ │
│  │ Worker  │  │         │  │ (LLM)   │  │ (Embeddings)    │ │
│  └─────────┘  └─────────┘  └─────────┘  └────────┬────────┘ │
│                                                  │          │
│                                         ┌────────▼────────┐ │
│                                         │ Entity Extractor│ │
│                                         │ (LLM + Normalization)│
│                                         └────────┬────────┘ │
│                                                  │          │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────▼─────┐    │
│  │PostgreSQL│  │  Redis   │  │  Qdrant  │  │ Report   │    │
│  │(metadata)│  │ (queue)  │  │(vectors) │  │ Service  │    │
│  └──────────┘  └──────────┘  └──────────┘  └──────────┘    │
└─────────────────────────────────────────────────────────────┘
```

## Project Structure

```
knowledge_extraction/
├── src/
│   ├── main.py                 # FastAPI app with lifespan
│   ├── config.py               # Pydantic settings
│   ├── orm_models.py           # SQLAlchemy models
│   ├── api/v1/
│   │   ├── projects.py         # Project CRUD
│   │   ├── extraction.py       # Extraction endpoints
│   │   ├── entities.py         # Entity queries
│   │   ├── search.py           # Hybrid search
│   │   ├── reports.py          # Report generation
│   │   ├── jobs.py             # Job listing
│   │   └── metrics.py          # Prometheus metrics
│   ├── services/
│   │   ├── extraction/
│   │   │   ├── pipeline.py     # ExtractionPipelineService
│   │   │   └── worker.py       # Background job processing
│   │   ├── knowledge/
│   │   │   └── extractor.py    # EntityExtractor
│   │   ├── reports/
│   │   │   └── service.py      # ReportService
│   │   ├── storage/
│   │   │   ├── repositories/   # Project, Source, Extraction, Entity repos
│   │   │   ├── embedding.py    # EmbeddingService
│   │   │   ├── deduplication.py# ExtractionDeduplicator
│   │   │   └── search.py       # SearchService
│   │   ├── scraper/
│   │   │   ├── firecrawl.py    # FirecrawlClient
│   │   │   ├── rate_limiter.py # DomainRateLimiter
│   │   │   └── worker.py       # ScraperWorker
│   │   └── llm/
│   │       ├── client.py       # LLMClient
│   │       └── chunking.py     # Document chunking
│   └── middleware/
│       ├── auth.py             # API key authentication
│       ├── request_id.py       # Request ID tracing
│       └── request_logging.py  # Structured logging
├── tests/                      # 583 tests
├── docs/
│   ├── ARCHITECTURE.md
│   ├── TODO.md                 # Master task list
│   └── TODO_*.md               # Module-specific docs
├── alembic/                    # Database migrations
├── docker-compose.yml
├── Dockerfile
└── requirements.txt
```

## Development

```bash
# Setup
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
pip install -r requirements.txt
pip install -r requirements-dev.txt

# Run tests
pytest tests/ -v

# Run with coverage
pytest --cov=src --cov-report=html

# Lint and format
ruff check . --fix
ruff format .

# Start dev server
cd src && uvicorn main:app --reload
```

## Troubleshooting

### Firecrawl not scraping JS content

Check Playwright service:
```bash
docker compose logs playwright
```

### LLM timeouts

Increase timeout in config or check vLLM gateway status.

### Extraction duplicates

The system uses embedding similarity (0.90 threshold) for deduplication. Adjust `DEDUP_THRESHOLD` if needed.

### Cloudflare blocks

Enable FlareSolverr in docker-compose:
```bash
USE_FLARESOLVERR=true
```

## License

MIT
