# API Review - Knowledge Extraction Orchestrator

**Date**: 2026-03-02
**Scope**: All REST API endpoints, request/response models, middleware stack, MCP tools

---

## 1. Application Setup

**Framework**: FastAPI (0.115.0)
**Server**: Uvicorn (ASGI)
**Entry point**: `src/main.py`

### Lifespan Management

The app uses an `@asynccontextmanager` lifespan managing:

1. Signal handlers (SIGTERM, SIGINT) for graceful shutdown
2. Template loading from YAML files
3. Qdrant collection initialization with exponential backoff retry (5 attempts)
4. Background job scheduler startup
5. Cleanup callbacks on shutdown

### Middleware Stack (applied bottom-to-top)

| Order | Middleware | Purpose |
|-------|-----------|---------|
| 1 | `RequestIDMiddleware` | Assigns unique request IDs |
| 2 | `RequestLoggingMiddleware` | Structured request/response logging |
| 3 | `RateLimitMiddleware` | API rate limiting |
| 4 | `APIKeyMiddleware` | X-API-Key header authentication |
| 5 | `SecurityHeadersMiddleware` | HSTS, CSP headers |
| 6 | `HTTPSRedirectMiddleware` | HTTPS enforcement (configurable) |
| 7 | `CORSMiddleware` | Cross-origin support |

### Global Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/` | GET | Service info (name, version, commit, docs link) |
| `/health` | GET | Health check (DB/Redis/Qdrant status), returns 503 during shutdown |

---

## 2. API Routers

### 2.1 Projects (`/api/v1/projects`)

Full CRUD for extraction projects with template support.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/api/v1/projects` | POST | 201 | Create project |
| `/api/v1/projects` | GET | 200 | List projects (query: `include_inactive`) |
| `/api/v1/projects/{project_id}` | GET | 200 | Get project details |
| `/api/v1/projects/{project_id}` | PUT | 200 | Update project |
| `/api/v1/projects/{project_id}` | DELETE | 204 | Delete project |
| `/api/v1/projects/from-template` | POST | 201 | Clone from template |
| `/api/v1/projects/templates` | GET | 200 | List templates |
| `/api/v1/projects/templates/{name}` | GET | 200 | Get template details |

**Key behaviors**:
- Auto-applies default template when schema is omitted on creation
- Blocks schema changes if extractions exist (unless `?force=true`)
- Templates define field groups + entity types as YAML files

**Request model (`ProjectCreate`)**:
- `name` (required), `description`, `source_config`, `extraction_schema`, `entity_types`, `prompt_templates`, `is_template`

### 2.2 Crawl (`/api/v1/crawl`)

Recursive website crawling with two modes.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/api/v1/crawl` | POST | 202 | Start crawl job |
| `/api/v1/crawl/{job_id}` | GET | 200 | Check crawl status |

**Request model (`CrawlRequest`)** - 23 fields:
- **Core**: `url`, `project_id`, `company`, `max_depth` (1-10), `limit` (1-1000)
- **URL filtering**: `include_paths`, `exclude_paths` (regex patterns), `allow_backward_links`
- **Language**: `language_detection_enabled`, `allowed_languages` (ISO 639-1), `prefer_english_only`
- **Firecrawl**: `allow_subdomains`, `ignore_query_parameters`
- **Smart crawl**: `smart_crawl_enabled`, `relevance_threshold` (0-1), `focus_terms`
- **Extraction**: `auto_extract` (default true), `profile`

**Response model (`CrawlStatusResponse`)**:
- Progress tracking: `pages_total`, `pages_completed`, `sources_created`
- Smart crawl fields: `smart_crawl_phase`, `urls_discovered`, `urls_relevant`
- Timing: `created_at`, `completed_at`

### 2.3 Scrape (`/api/v1/scrape`)

Explicit URL scraping (no link following).

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/api/v1/scrape` | POST | 202 | Start scrape job |
| `/api/v1/scrape/{job_id}` | GET | 200 | Check scrape status |

**Request**: `urls[]`, `project_id`, `company`, `profile`

### 2.4 Extraction (`/api/v1/projects/{project_id}`)

LLM-based knowledge extraction from sources.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/extract` | POST | 202 | Start extraction job |
| `/extractions` | GET | 200 | List extractions (paginated) |
| `/extract-schema` | POST | 200 | Schema extraction (DEPRECATED) |
| `/extractions/recover` | POST | 200 | Recover orphaned embeddings |

**ExtractRequest**:
- `source_ids` (optional, processes all pending if omitted)
- `force` (default false, re-extracts already processed sources)
- `profile` (extraction depth)

**Extraction list filters**: `source_id`, `extraction_type`, `source_group`, `min_confidence`, `limit`, `offset`

### 2.5 Search (`/api/v1/projects/{project_id}/search`)

Hybrid semantic + structured search.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/search` | POST | 200 | Search extractions |

**SearchRequest**: `query` (1-1000 chars), `limit` (1-100), `source_groups`, `filters` (JSONB)

Uses Qdrant vector search (bge-m3 embeddings) + PostgreSQL filtering.

### 2.6 Entities (`/api/v1/projects/{project_id}`)

Query extracted entities.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/entities` | GET | 200 | List entities (paginated) |
| `/entities/types` | GET | 200 | Entity type summary |
| `/entities/by-value` | GET | 200 | Lookup by type+value |
| `/entities/{entity_id}` | GET | 200 | Get entity details |

### 2.7 Reports (`/api/v1/projects/{project_id}/reports`)

Report generation and retrieval.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/reports` | POST | 201 | Generate report |
| `/reports` | GET | 200 | List reports |
| `/reports/{report_id}` | GET | 200 | Get report content |
| `/reports/{report_id}/pdf` | GET | 200 | Download PDF |
| `/reports/{report_id}/download` | GET | 200 | Download MD/XLSX |

**Report types**: SINGLE (company summary), COMPARISON (multi-company), TABLE (structured data)

**Table grouping**: `source` (one row per URL) or `domain` (LLM smart-merge per domain)

### 2.8 Sources (`/api/v1/projects/{project_id}/sources`)

Query raw documents/pages.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/sources` | GET | 200 | List sources (paginated) |
| `/sources/summary` | GET | 200 | Summary by status/group |
| `/sources/{source_id}` | GET | 200 | Get source details |

### 2.9 Jobs (`/api/v1/jobs`)

Job lifecycle management.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/jobs` | GET | 200 | List jobs (with filters) |
| `/jobs/{job_id}` | GET | 200 | Job details |
| `/jobs/{job_id}/cancel` | POST | 200 | Request cancellation |
| `/jobs/{job_id}/cleanup` | POST | 200 | Remove job artifacts |
| `/jobs/{job_id}` | DELETE | 200 | Delete job record |

**Filters**: `type`, `status`, `created_after`, `created_before`, `limit`, `offset`

### 2.10 Domain Dedup (`/api/v1/projects`)

Boilerplate content analysis and cleaning.

| Endpoint | Method | Status | Description |
|----------|--------|--------|-------------|
| `/{project_id}/analyze-boilerplate` | POST | 200 | Analyze domain boilerplate |
| `/{project_id}/boilerplate-stats` | GET | 200 | Get dedup statistics |

### 2.11 Export (`/api/v1/projects/{project_id}/export`)

Streaming data export.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/entities` | GET | Export entities (CSV/JSON) |
| `/extractions` | GET | Export extractions (CSV/JSON) |
| `/sources` | GET | Export sources (CSV/JSON) |

### 2.12 Dead Letter Queue (`/api/v1/dlq`)

Failed operation inspection and retry.

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/dlq/stats` | GET | DLQ counts |
| `/dlq/scrape` | GET | Failed scrape items |
| `/dlq/extraction` | GET | Failed extraction items |
| `/dlq/scrape/{item_id}/retry` | POST | Retry scrape |
| `/dlq/extraction/{item_id}/retry` | POST | Retry extraction |

### 2.13 Metrics (`/api/metrics`)

Prometheus metrics export (unauthenticated for scraping).

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/metrics` | GET | Prometheus format metrics |

---

## 3. API Design Patterns

### Consistent Patterns
- **Job queue pattern**: Scrape/Crawl/Extract return 202 Accepted with `job_id` for async tracking
- **Pagination**: `limit` (max 100) + `offset` on all list endpoints
- **UUID validation**: All UUID parameters validated in-endpoint (422 on failure)
- **Error responses**: HTTPException with descriptive messages and standard status codes

### Status Codes
| Code | Usage |
|------|-------|
| 200 | Successful reads |
| 201 | Resource created |
| 202 | Job accepted for async processing |
| 204 | Successful deletion |
| 404 | Resource not found |
| 409 | Conflict (concurrent modification) |
| 422 | Validation error |
| 500 | Internal server error |
| 503 | Service unavailable (health check during shutdown) |

---

## 4. MCP (Model Context Protocol) Interface

The MCP server (`src/ke_mcp/`) mirrors the REST API as LLM-friendly tools. It connects via STDIO transport using JSON-RPC.

### MCP Tools (24 total, 6 categories)

| Category | Tools | Maps to API |
|----------|-------|-------------|
| **Projects** | create_project, list_projects, get_project, list_templates, get_template_details | `/api/v1/projects/*` |
| **Acquisition** | crawl_website, scrape_urls, get_job_status, cancel_job, cleanup_job, delete_job | `/api/v1/crawl`, `/api/v1/scrape`, `/api/v1/jobs/*` |
| **Extraction** | extract_knowledge, list_extractions | `/api/v1/projects/{id}/extract*` |
| **Search** | search_knowledge, list_entities, get_entity_summary, list_sources, get_source_summary | `/api/v1/projects/{id}/*` |
| **Reports** | create_report, list_reports, get_report | `/api/v1/projects/{id}/reports` |
| **Dedup** | analyze_boilerplate, get_boilerplate_stats | `/api/v1/projects/{id}/boilerplate*` |

### MCP Configuration
```json
{
  "KE_API_BASE_URL": "http://192.168.0.136:8742",
  "KE_API_KEY": "<api_key>",
  "KE_TIMEOUT_SECONDS": 60,
  "KE_MAX_RETRIES": 3,
  "KE_POLL_INTERVAL": 5,
  "KE_MAX_POLL_ATTEMPTS": 120
}
```

### MCP Workflow Prompts
- `analyze_company_docs()` - 5-step workflow: create project, crawl, extract, search, report
- `compare_competitors()` - Multi-company comparison workflow

---

## 5. Dependency Injection

Three FastAPI dependencies (`src/api/dependencies.py`):

1. **`get_project_or_404(project_id, db)`** - Validates project existence
2. **`get_dlq_service()`** - Returns DLQService with Redis connection
3. **`get_qdrant_repository()`** - Returns QdrantRepository with global client

---

## 6. Endpoint Count Summary

| Category | Endpoints |
|----------|-----------|
| Projects | 8 |
| Crawl | 2 |
| Scrape | 2 |
| Extraction | 4 |
| Search | 1 |
| Entities | 4 |
| Reports | 5 |
| Sources | 3 |
| Jobs | 5 |
| Dedup | 2 |
| Export | 3 |
| DLQ | 5 |
| Metrics | 1 |
| Global | 2 |
| **Total** | **47** |

MCP Tools: **24** (covering the main operational endpoints)
