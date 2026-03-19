# Knowledge Extraction Orchestrator

A template-agnostic knowledge extraction platform that crawls websites, extracts structured data via LLM, and makes it searchable through vector embeddings.

---

## What It Does

Takes unstructured web content and converts it into structured, searchable knowledge:

```
Websites → Crawl/Scrape → Extract (LLM) → Embed → Search → Reports
```

Works with any domain: company analysis, product specs, academic papers, job listings, recipes, contracts, etc. Define a schema template and the system handles the rest.

---

## Core Capabilities

- **Web Crawling**: Recursive crawling with smart URL filtering (traditional + semantic)
- **Scraping**: Explicit URL list scraping with rate limiting and retry
- **LLM Extraction**: Schema-based structured extraction using field groups
- **Smart Classification**: Embedding-based page relevance filtering
- **Domain Deduplication**: Two-pass boilerplate removal (navbars, footers, etc.)
- **Semantic Search**: Vector-based search across extractions (bge-m3 + Qdrant)
- **Entity Extraction**: Named entity recognition with deduplication
- **Report Generation**: Single-company summary, multi-company comparison, data tables
- **MCP Interface**: 24 tools for LLM-driven workflows via Model Context Protocol

---

## Tech Stack

| Component | Technology | Purpose |
|-----------|-----------|---------|
| **API** | FastAPI 0.115 + Uvicorn | REST API (47 endpoints) |
| **Database** | PostgreSQL + SQLAlchemy 2.0 (psycopg 3) | Primary data store |
| **Vectors** | Qdrant | Embedding search (1024-dim bge-m3) |
| **Cache/Queue** | Redis | Rate limiting, LLM queue, caching |
| **LLM** | Qwen3-30B (OpenAI-compatible API) | Extraction + synthesis |
| **Embeddings** | bge-m3 (8192 tokens, 1024 dims) | Semantic search + classification |
| **Reranker** | bge-reranker-v2-m3 | Classification confirmation |
| **Scraping** | Firecrawl (self-hosted) | Web crawling + scraping |
| **MCP** | FastMCP (STDIO) | LLM tool interface |

---

## Quick Start

```bash
# Environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .

# Configuration (.env)
DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db
REDIS_URL=redis://host:6379/0
QDRANT_URL=http://host:6333
LLM_BASE_URL=http://host:9003/v1
EMBEDDING_BASE_URL=http://host:9003/v1
API_KEY=your-api-key-here

# Run
cd src && uvicorn main:app --host 0.0.0.0 --port 8000
```

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────┐
│                    FastAPI Application                     │
│                                                           │
│  API Layer (47 endpoints)                                │
│  ├── Projects, Templates                                 │
│  ├── Crawl, Scrape                                       │
│  ├── Extraction, Entities                                │
│  ├── Search, Reports                                     │
│  ├── Jobs, DLQ, Export                                   │
│  └── Metrics, Dedup                                      │
│                                                           │
│  Middleware (auth, rate-limit, logging, security)         │
│                                                           │
│  Job Scheduler (background)                              │
│  ├── Scrape worker (1x)                                  │
│  ├── Crawl workers (3x parallel)                         │
│  ├── Extract worker (1x)                                 │
│  └── LLM queue worker (optional)                         │
│                                                           │
│  Service Layer                                            │
│  ├── Extraction (schema + generic)                       │
│  ├── LLM (client, queue, worker)                         │
│  ├── Scraper (crawl, scrape, URL filter)                 │
│  ├── Storage (repositories + Qdrant)                     │
│  ├── Reports (synthesis, table, formats)                 │
│  └── Metrics, DLQ, Alerting                              │
│                                                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐               │
│  │PostgreSQL │  │  Qdrant  │  │  Redis   │               │
│  └──────────┘  └──────────┘  └──────────┘               │
└──────────────────────────────────────────────────────────┘

MCP Server (separate process, STDIO)
  └── 24 tools mirroring API endpoints
```

---

## Main Pipelines

### 1. Crawl/Scrape Pipeline

Two crawl modes:
- **Traditional**: Firecrawl recursive crawl → poll → store
- **Smart**: Map (discover URLs) → Filter (embedding similarity) → Scrape (batch)

Features: rate limiting, retry with backoff, language filtering, llms.txt support, auto-extraction trigger.

### 2. Extraction Pipeline

Two paths:
- **Schema-based**: Classification → Chunk → Parallel field group extraction → Merge → Validate → Embed
- **Generic**: Single-pass fact extraction → Embed

Features: 80-concurrent chunk extraction, temperature variation on retry, checkpoint/resume, conflict detection, source quoting.

### 3. Report Pipeline

Three report types:
- **Single**: LLM narrative synthesis for one company
- **Comparison**: Side-by-side multi-company analysis
- **Table**: Structured data (group by source or domain)

Output formats: Markdown, XLSX, PDF.

---

## Project Structure

```
src/
├── main.py                      # FastAPI app, lifespan, middleware
├── config.py                    # Pydantic Settings (100+ params)
├── models.py                    # Request/response schemas
├── orm_models.py                # SQLAlchemy ORM models
├── constants.py, exceptions.py, utils.py
│
├── api/v1/                      # REST endpoints (13 routers)
│   ├── crawl.py, scrape.py      # Web acquisition
│   ├── extraction.py            # Knowledge extraction
│   ├── projects.py              # Project CRUD + templates
│   ├── search.py, entities.py   # Query interface
│   ├── reports.py               # Report generation
│   ├── jobs.py, sources.py      # Job/source management
│   ├── dedup.py                 # Boilerplate analysis
│   ├── export.py, dlq.py        # Export + dead letter queue
│   └── metrics.py               # Prometheus metrics
│
├── services/
│   ├── extraction/              # Core extraction logic
│   │   ├── pipeline.py          # Pipeline orchestration
│   │   ├── schema_orchestrator.py   # Multi-pass extraction
│   │   ├── schema_extractor.py  # LLM field group extraction
│   │   ├── schema_validator.py  # Type coercion + validation
│   │   ├── smart_classifier.py  # Embedding-based classification
│   │   ├── content_cleaner.py   # Markdown cleaning
│   │   └── domain_dedup.py      # Boilerplate fingerprinting
│   │
│   ├── llm/                     # LLM integration
│   │   ├── client.py            # Dual-mode LLM client
│   │   ├── queue.py             # Redis Streams queue
│   │   ├── worker.py            # Adaptive concurrency worker
│   │   └── chunking.py          # Document chunking
│   │
│   ├── scraper/                 # Web acquisition
│   │   ├── scheduler.py         # Job scheduler
│   │   ├── crawl_worker.py      # Crawl logic (traditional + smart)
│   │   ├── worker.py            # Scrape worker
│   │   └── url_filter.py        # Embedding-based URL filter
│   │
│   ├── storage/                 # Data persistence
│   │   ├── repositories/        # Repository pattern (Source, Extraction, Entity, Job)
│   │   └── qdrant/              # Vector store
│   │
│   └── reports/                 # Report generation
│       └── synthesis.py         # LLM-based report synthesis
│
├── ke_mcp/                      # MCP server
│   ├── server.py, client.py     # MCP setup + API client
│   └── tools/                   # 24 MCP tools (6 categories)
│
└── middleware/                  # Auth, rate limit, logging, security
```

---

## Testing

```bash
pytest                              # Run all tests
pytest tests/test_foo.py -v         # Single file
pytest -k "test_name"               # By name
pytest --cov=src --cov-report=html  # Coverage
```

Test patterns: async tests with `pytest-asyncio`, transactional rollback fixtures, fixture-based DI.

---

## MCP Integration

The MCP server enables LLM-driven workflows:

```bash
# Run MCP server
python -m src.ke_mcp
```

Configuration in `.mcp.json`:
```json
{
  "mcpServers": {
    "knowledge-extraction": {
      "command": ".venv/bin/python",
      "args": ["-m", "src.ke_mcp"],
      "env": {
        "KE_API_BASE_URL": "http://host:8742",
        "KE_API_KEY": "your-key"
      }
    }
  }
}
```

---

## Key Configuration

See `src/config.py` for all options. Key settings:

```bash
# LLM
LLM_MODEL=Qwen3-30B-A3B-Instruct-4bit
LLM_HTTP_TIMEOUT=120
LLM_MAX_RETRIES=3

# Extraction
EXTRACTION_CONTENT_LIMIT=20000
EXTRACTION_CHUNK_MAX_TOKENS=5000
EXTRACTION_MAX_CONCURRENT_CHUNKS=80
EXTRACTION_MAX_CONCURRENT_SOURCES=20

# Crawl
MAX_CONCURRENT_CRAWLS=3
CRAWL_DELAY_MS=500
SCRAPE_DAILY_LIMIT_PER_DOMAIN=500

# Classification
CLASSIFICATION_ENABLED=true
SMART_CLASSIFICATION_ENABLED=false

# Domain Dedup
DOMAIN_DEDUP_ENABLED=true
DOMAIN_DEDUP_THRESHOLD_PCT=0.7
```

---

## Documentation

Detailed pipeline and architecture reviews are available in `docs/review_20260302/`:

| Document | Contents |
|----------|----------|
| `api_review.md` | All 47 endpoints, models, middleware |
| `pipeline_crawl_scrape.md` | Crawl/scrape pipeline details |
| `pipeline_extraction.md` | Extraction pipeline deep dive |
| `pipeline_llm.md` | LLM service layer review |
| `pipeline_reports.md` | Report generation pipeline |
| `storage_layer.md` | ORM models, repositories, Qdrant |
| `architecture_analysis.md` | Critical architecture assessment |
