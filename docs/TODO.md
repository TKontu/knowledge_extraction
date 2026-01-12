# Knowledge Extraction Pipeline - TODO

Master task list. Module-specific details in `docs/TODO_*.md`.

## Progress Summary

**Completed (28+ PRs merged to main):**
- PR #1-8: Core infrastructure (FastAPI, Redis, Qdrant, ORM, Firecrawl, LLM)
- PR #9-20: Repositories, services, and extraction pipeline
- PR #21-28: Entity extraction, deduplication, reports, jobs API, metrics

**Current State:**
- **611 tests passing** (28 new tests from shutdown/export agents)
- Complete extraction pipeline operational (scrape → chunk → extract → dedupe → entities → vectors)
- **ExtractionPipelineService complete** - Full orchestration of extraction flow
- **ExtractionWorker complete** - Background job processing
- **ReportService complete** - Single and comparison reports with entity tables
- **Jobs API complete** - GET /api/v1/jobs with filtering
- **Prometheus metrics complete** - GET /metrics endpoint
- **Structured logging complete** - structlog config, request ID tracing
- **Graceful shutdown complete** - SIGTERM/SIGINT handlers, worker cleanup
- **Rate limiting complete** - Per-API-key sliding window rate limiting
- **Export API complete** - CSV/JSON export for entities and extractions
- **Project templates complete** - research_survey and contract_review templates added
- All infrastructure services monitored (PostgreSQL, Redis, Qdrant, Firecrawl)
- Background job scheduler runs automatically
- **Repository layer complete** (Project, Source, Extraction, Entity, Qdrant repositories)
- **Vector search complete** (EmbeddingService, SearchService with hybrid search)
- **Project CRUD API complete** (POST /projects, GET /projects, etc.)
- **Extraction API endpoints complete** (POST /extract, GET /extractions with filtering)
- **Search API endpoint complete** (POST /projects/{id}/search with hybrid search)
- **Entity API endpoints complete** (list, get, filter by type, by-value queries)
- **EntityExtractor integrated** (LLM-based extraction in pipeline)
- **ExtractionDeduplicator integrated** (embedding similarity check in pipeline)

**Architectural Direction:**
The system is being generalized from "Scristill" to a **general-purpose extraction pipeline** supporting any domain via project-based configuration.

---

## Phase 0: Foundation (Priority)

### System Generalization
See: `docs/TODO_generalization.md`

**Goal:** Transform from single-purpose to general-purpose extraction pipeline.

- [x] Design project model (extraction schema, entity types, prompts)
- [x] Create projects table with JSONB configuration (in init.sql + ORM)
- [x] Create sources table to replace pages
- [x] Create extractions table to replace facts
- [x] Create entities table with project scoping
- [ ] Implement incremental migration strategy (Alembic)
- [ ] Create default "company_analysis" project for existing data
- [ ] Update API for project-scoped operations

**Key Terminology Changes:**
| Current | Generalized | Purpose |
|---------|-------------|---------|
| `pages` | `sources` | Generic content source (web, PDF, API) |
| `facts` | `extractions` | Schema-driven extracted data |
| `company` | `source_group` | Configurable grouping (company, paper, contract) |
| `profiles` | `project.extraction_schema` | Dynamic extraction configuration |

### Database Migrations - COMPLETE
See: `docs/TODO_migrations.md`

- [x] Install Alembic (in requirements.txt)
- [x] Create alembic.ini and env.py (async-compatible with psycopg3)
- [x] Generate initial migration from ORM models (`20260110_001_initial_schema.py`)
- [x] Create seed migration for builtin profiles (`20260110_002_seed_builtin_profiles.py`)
- [ ] Update docker-compose to run migrations on startup
- [ ] Remove init.sql (replaced by migrations)

---

## Phase 1: Infrastructure Setup

### Docker Stack (Remote: 192.168.0.136)

- [x] Create `docker-compose.yml` with all services
- [x] Configure Firecrawl + Playwright
- [x] Configure Redis
- [x] Configure Qdrant
- [x] Configure PostgreSQL
- [x] Create `.env.example` with all variables
- [x] **Add API key authentication to pipeline service** (PR #1)
- [x] **Add health check with dependency status** (DB + Redis, PR #2)
- [x] **Add Qdrant health check to health endpoint** (PR #6)
- [ ] Test deployment via Portainer
- [ ] Verify network connectivity to vLLM gateway (192.168.0.247:9003)

### Database Schema

- [x] Create PostgreSQL schema in `init.sql`
- [x] **Create SQLAlchemy ORM models** (PR #4)
- [x] **Add projects table** (in init.sql + orm_models.py)
- [x] **Add sources table** (replaces pages - in init.sql + orm_models.py)
- [x] **Add extractions table** (replaces facts - in init.sql + orm_models.py)
- [x] **Add entities table** (in init.sql + orm_models.py)
- [x] **Add extraction_entities junction table** (in init.sql + orm_models.py)
- [ ] Create Qdrant collection (dim=1024 for BGE-large-en)

---

## Phase 2: Scraper Module - COMPLETE

See: `docs/TODO_scraper.md`

- [x] **Firecrawl client wrapper** (PR #7)
- [x] **Rate limiting logic (per-domain)** (PR #7)
- [x] **Background worker for job processing** (PR #7)
- [x] **Job scheduler with FastAPI integration** (PR #7)
- [x] **Page storage (raw markdown)** (PR #7)
- [x] **Basic error handling** (PR #7)
- [ ] Store outbound links from Firecrawl (enables link graph)
- [ ] Retry logic with exponential backoff (nice-to-have)

**Refactoring Required:**
- [x] Update scraper to create `sources` instead of `pages` ✅ **DONE**
- [x] Add project_id context to scrape jobs ✅ **DONE**
- [x] Replace `company` with `source_group` ✅ **DONE**

**Milestone**: Can scrape URLs and store markdown automatically

---

## Phase 3: Extraction Module (In Progress - ~65% Complete)

See: `docs/TODO_extraction.md`

### Completed
- [x] Built-in profiles defined in database
- [x] **Document chunking module** (PR #8 - 17 tests)
- [x] **LLM client implementation** (PR #8 - 9 tests)
- [x] **Data models** (DocumentChunk, ExtractedFact, ExtractionResult)
- [x] **Profile repository** (PR #9 - 10 tests)
- [x] **Extraction orchestrator** (PR #9 - 9 tests)
- [x] **Fact validator** (PR #9 - 11 tests)
- [x] **Extraction API endpoints** (`api/v1/extraction.py` - 25 tests)
  - POST /api/v1/projects/{project_id}/extract (async job creation)
  - GET /api/v1/projects/{project_id}/extractions (list with filtering)

### Pending
- [ ] Chunk result merging with deduplication
- [ ] Integration tests for extraction
- [ ] Legacy API endpoints (POST /api/v1/extract, GET /api/v1/profiles)

### Refactoring Required (Generalization)
- [ ] Update extraction to use project schema (JSONB fields)
- [ ] Dynamic prompt generation from project.extraction_schema
- [ ] Replace fixed categories with project-defined categories
- [ ] Store results in `extractions` table

**Milestone**: Can extract structured data from scraped sources using project schema

---

## Phase 4: Project System

See: `docs/TODO_project_system.md`

**Goal:** Implement project management for multi-domain extraction.

**Completed:**
- [x] **ProjectRepository** (9 methods, 19 tests) - CRUD, templates, default project
- [x] **SchemaValidator** (dynamic Pydantic from JSONB, 21 tests)
- [x] **Project ORM model** with relationships
- [x] **COMPANY_ANALYSIS_TEMPLATE** - default project template
- [x] **Project CRUD API endpoints** (POST, GET, PUT, DELETE /api/v1/projects)
- [x] **Clone project from template** (POST /api/v1/projects/from-template)
- [x] **List templates** (GET /api/v1/projects/templates)

**Pending:**
- [x] Additional project templates (research_survey, contract_review) - PR #29
- [ ] Project-scoped search API endpoint
- [ ] Seed script for default project

**Milestone**: Can create and manage extraction projects with custom schemas

---

## Phase 5: Knowledge Layer (Entities) - COMPLETE

See: `docs/TODO_knowledge_layer.md`

> Enables structured queries like "Which companies support SSO?" without LLM inference.

### Entity Extraction (MVP) - COMPLETE
- [x] Update `entities` table with project_id (in ORM)
- [x] Entity types from project configuration (stored in project.entity_types JSONB)
- [x] **EntityRepository** (deduplication via get_or_create, 28 tests)
- [x] Value normalization per type (normalized_value field)
- [x] **EntityExtractor class** (LLM-based extraction with `_normalize()`, `_call_llm()`, `_store_entities()`, `extract()`)
- [x] **Integrated into extraction pipeline** (ExtractionPipelineService)

### Entity Queries - COMPLETE
- [x] **Entity API endpoints** (list, get, filter by type, by-value queries - 15 tests)
- [x] **Comparison tables via ReportService** (entity-based structured comparisons)
- [x] Hybrid search (vector + entity filtering) - via SearchService

### Relations (Post-MVP)
- [ ] Add `relations` table
- [ ] Relation extraction (has_feature, has_limit, has_price)
- [ ] Graph queries

**Milestone**: ✅ Structured queries on entities, accurate comparison tables

---

## Phase 6: Storage & Search Module

See: `docs/TODO_storage.md`

**Completed:**
- [x] **SQLAlchemy ORM models** (PR #4)
- [x] **Qdrant client initialization** (PR #6)
- [x] **SourceRepository** (6 methods, 23 tests) - CRUD, filtering, content updates
- [x] **ExtractionRepository** (8 methods, 26 tests) - CRUD, batch ops, JSONB queries
- [x] **EntityRepository** (8 methods, 28 tests) - Deduplication, entity-extraction links
- [x] **ProjectRepository** (9 methods, 19 tests) - From Phase 4
- [x] **JSONB field filtering** (query_jsonb, filter_by_data with PostgreSQL/SQLite support)
- [x] Filter support (source_group, entity_type, confidence ranges, etc.)
- [x] **QdrantRepository** (5 methods, 12 tests) - Collection init, upsert, batch, search, delete
- [x] **EmbeddingService** (2 methods, 7 tests) - Single + batch embedding via BGE-large-en
- [x] **SearchService** (hybrid search, 14 tests) - Vector + JSONB filtering with over-fetching

**Pending:**
- [ ] Pagination support in API

### Search API
- [x] **Search API endpoint** (`POST /api/v1/projects/{project_id}/search` - 14 tests)
- [x] Hybrid vector + JSONB filtering
- [x] Source group filtering

### Deduplication - COMPLETE
See: `docs/TODO_deduplication.md`

**Entity Deduplication (Completed):**
- [x] Entity deduplication via `EntityRepository.get_or_create()`
- [x] Scoped by (project_id, source_group, entity_type, normalized_value)

**Extraction Deduplication (Completed):**
- [x] **ExtractionDeduplicator class** (`services/storage/deduplication.py` - 17 tests)
- [x] Embedding similarity check via `check_duplicate()`
- [x] Single threshold (0.90) for MVP
- [x] Same-source_group deduplication only (MVP)
- [x] **Integrated into extraction pipeline** (ExtractionPipelineService)

**Milestone**: ✅ Can search extractions semantically with filters, no duplicates

---

## Phase 7: Report Generation - COMPLETE

See: `docs/TODO_reports.md`

- [x] **ReportService** (`services/reports/service.py`) - Full implementation
- [x] **Report types** (single, comparison) with ReportType enum
- [x] **Report API endpoints** (`api/v1/reports.py`)
  - POST /api/v1/projects/{project_id}/reports (create report)
  - GET /api/v1/projects/{project_id}/reports (list reports)
  - GET /api/v1/projects/{project_id}/reports/{report_id} (get report)
- [x] **Entity-based comparison tables** (structured, not LLM-inferred)
- [x] Extraction aggregation by source_group
- [x] Markdown output generation

**Pending (Post-MVP):**
- [ ] PDF export (via Pandoc)
- [ ] Topic and summary report types
- [ ] Custom prompt templates

**Milestone**: ✅ Can generate comparison reports with structured entity data

---

## Phase 8: API & Integration - COMPLETE

- [x] FastAPI application structure (PR #1)
- [x] **API key authentication middleware** (PR #1)
- [x] CORS configuration for Web UI (PR #1)
- [x] Health check endpoint with DB + Redis + Qdrant + Firecrawl status
- [x] Scrape endpoints: POST /api/v1/scrape, GET /api/v1/scrape/{job_id}
- [x] **Extraction endpoints** (`api/v1/extraction.py`)
  - POST /api/v1/projects/{project_id}/extract
  - GET /api/v1/projects/{project_id}/extractions
- [x] **Project endpoints** (`api/v1/projects.py`)
  - POST /api/v1/projects
  - GET /api/v1/projects
  - GET /api/v1/projects/{id}
  - PUT /api/v1/projects/{id}
  - DELETE /api/v1/projects/{id}
  - POST /api/v1/projects/from-template
  - GET /api/v1/projects/templates
- [x] **Search endpoint** (`POST /api/v1/projects/{project_id}/search`)
- [x] **Entity query endpoints** (`api/v1/entities.py` - list, get, types, by-value)
- [x] **Report endpoints** (`api/v1/reports.py`)
  - POST /api/v1/projects/{project_id}/reports
  - GET /api/v1/projects/{project_id}/reports
  - GET /api/v1/projects/{project_id}/reports/{report_id}
- [x] **Jobs endpoint** (`api/v1/jobs.py` - GET /api/v1/jobs with filtering)
- [x] **Metrics endpoint** (`api/v1/metrics.py` - GET /metrics Prometheus format)

**Pending (Low Priority):**
- [ ] Legacy extract endpoints (`POST /api/v1/extract`, `GET /api/v1/profiles`)
- [ ] Legacy endpoints use default "company_analysis" project

**Milestone**: ✅ Full API functional and secured

---

## Phase 9: Polish & Hardening

### Logging & Monitoring - COMPLETE
- [x] **Structured logging with structlog** (`logging_config.py`)
- [x] **Request ID tracing** (`middleware/request_id.py`)
- [x] **Request/response logging** (`middleware/request_logging.py`)
- [x] **Prometheus metrics endpoint** (`api/v1/metrics.py` - GET /metrics)
- [x] **System metrics collection** (`services/metrics/collector.py`)
- [x] Job status tracking (queued, running, completed, failed) - PR #7
- [x] Config validation on startup (pydantic-settings)
- [x] **Graceful shutdown handling** (`shutdown.py`, signal handlers) - PR #31
- [ ] Retry failed jobs (manual trigger)

### Security Hardening
- [ ] Remove insecure default API key or make it required via env var (src/config.py:16-19)
- [x] **Application-level rate limiting** (`middleware/rate_limit.py`) - PR #30
- [ ] Add HTTPS enforcement option in configuration
- [ ] Document security best practices in README
- [ ] Add API key rotation mechanism
- [ ] Consider JWT authentication for multi-user scenarios

### Completed Agent Tasks (PRs #29-32)
- [x] `agent-templates`: research_survey and contract_review templates - PR #29
- [x] `agent-shutdown`: Graceful shutdown handling - PR #31
- [x] `agent-ratelimit`: Rate limiting middleware - PR #30
- [x] `agent-export`: CSV/JSON export endpoints - PR #32

### Code Quality
- [x] Fix coverage configuration (changed "app" to "src") - 2026-01-11
- [x] Remove duplicate dependencies in requirements.txt - 2026-01-11
- [x] Modernize Pydantic patterns (ConfigDict vs Config class) - 2026-01-11
- [ ] Add complete type hints to all functions (some missing in tests)
- [ ] Improve exception handling specificity (avoid bare except blocks)
- [ ] Add context to exception chains (use `raise ... from e`)
- [ ] Consistent import patterns across modules

### Testing Improvements
- [x] Fix pytest asyncio fixture scope - 2026-01-11
- [ ] Fix test database isolation (test_db_engine is session-scoped, causes crosstalk)
- [ ] Add transaction rollback to test fixtures
- [ ] Increase test coverage to 90%+ (current coverage unknown due to previous config issue)
- [ ] Add integration tests for full extraction pipeline

### Configuration & Deployment
- [x] Fix webui service in docker-compose.yml (commented out until implemented) - 2026-01-11
- [x] Update .env.example with security warnings and better defaults - 2026-01-11
- [ ] Add resource limits to docker-compose services (memory, CPU)
- [ ] Add health check logging for debugging
- [ ] Create deployment documentation
- [ ] Add backup/restore procedures for PostgreSQL

### Project Configuration
- [ ] Align project naming: pyproject.toml uses "app" but code is in "src/"
- [ ] Update Ruff known-first-party to ["src"] instead of ["app"]
- [ ] Decide on package name and apply consistently

---

## Phase 10: Web UI (Post-MVP)

- [ ] Simple HTML/JS dashboard (single page)
- [ ] **Project management UI**
- [ ] Job submission forms (scrape, extract, report)
- [ ] Job status list with auto-refresh
- [ ] Search interface
- [ ] Entity browser
- [ ] API key configuration
- [ ] Containerize with nginx

**Milestone**: Remote control via web browser

---

## Future Enhancements (Post-MVP)

- [ ] Advanced Web UI (React/Vue with real-time updates)
- [ ] PDF source support (beyond web scraping)
- [ ] Scheduled re-scraping
- [ ] Sitemap discovery
- [ ] Proxy rotation support
- [ ] arq/Celery for parallel extraction (when BackgroundTasks insufficient)
- [ ] Webhook notifications
- [x] Export to CSV/JSON - PR #32
- [ ] Multi-user support with auth
- [ ] HTTPS with nginx reverse proxy

---

## Quick Reference

| Module | File | Phase | Status |
|--------|------|-------|--------|
| **Generalization** | `docs/TODO_generalization.md` | 0 | **Schema Complete (96 tests)** |
| **Migrations** | `docs/TODO_migrations.md` | 0 | **Complete (2 migrations)** |
| **Project System** | `docs/TODO_project_system.md` | 4 | **Complete - API + Repository (40 tests)** |
| Scraper | `docs/TODO_scraper.md` | 2 | **Complete** |
| Extraction | `docs/TODO_extraction.md` | 3 | **Pipeline Complete** |
| LLM Integration | `docs/TODO_llm_integration.md` | 3 | **Complete** |
| **Knowledge Layer** | `docs/TODO_knowledge_layer.md` | 5 | **Complete - Integrated in Pipeline** |
| **Deduplication** | `docs/TODO_deduplication.md` | 6 | **Complete - Integrated in Pipeline** |
| **Storage** | `docs/TODO_storage.md` | 6 | **Complete (112 tests)** |
| **Reports** | `docs/TODO_reports.md` | 7 | **Complete - ReportService + API** |

---

## Test Coverage

**Current: 611 tests passing** (28 new from shutdown/export agents)

### Core Infrastructure
- 14 authentication tests
- 6 CORS tests
- 8 database connection tests
- 18 ORM model tests
- 17 Generalized ORM model tests
- 8 Redis connection tests

### Scraper Module
- 23 scrape endpoint tests
- 14 FirecrawlClient tests
- 23 DomainRateLimiter tests
- 16 ScraperWorker tests

### Extraction Module
- 17 Document chunking tests
- 9 LLM client tests
- 10 Profile repository tests
- 9 Extraction orchestrator tests
- 11 Fact validator tests
- 25 Extraction endpoint tests

### Storage & Search
- 19 ProjectRepository tests
- 21 SchemaValidator tests
- 23 SourceRepository tests
- 26 ExtractionRepository tests
- 28 EntityRepository tests
- 12 QdrantRepository tests
- 7 EmbeddingService tests
- 14 SearchService tests
- 14 Search endpoint tests
- 5 Search models tests

### Knowledge Layer
- 27 EntityExtractor tests
- 17 ExtractionDeduplicator tests
- 15 Entity endpoint tests

### Pipeline & Reports
- ExtractionPipelineService tests
- ExtractionWorker tests
- ReportService tests
- Reports endpoint tests
- Jobs endpoint tests
- Metrics endpoint tests

### Shutdown & Export (NEW - PRs #31-32)
- Graceful shutdown tests (test_graceful_shutdown.py)
- Export API tests (test_export_api.py)

---

## Refactoring Summary

The following components need updates for generalization:

| Component | Current | Target | Priority | Status |
|-----------|---------|--------|----------|---------|
| `orm_models.py` | pages, facts | sources, extractions, projects | High | ✅ **DONE** |
| `repositories/` | N/A | Project, Source, Extraction, Entity | High | ✅ **DONE** |
| `services/projects/schema.py` | N/A | SchemaValidator | High | ✅ **DONE** |
| `scraper/worker.py` | Creates Page | Creates Source | High | ✅ **DONE** |
| `extraction/extractor.py` | Fixed schema | Project schema | High | TODO |
| `extraction/profiles.py` | Hardcoded profiles | Project config | Medium | TODO |
| `api/v1/scrape.py` | Uses company | Uses source_group + project | Medium | TODO |
| `models.py` | Fixed models | Dynamic validation | Medium | TODO |

---

## Getting Started

The core extraction pipeline is **complete**. All 4 agent tasks merged (PRs #29-32). Next priorities:

1. **Run Integration Tests** - Verify pipeline with real LLM
2. **Phase 0** (Migrations) - Alembic setup for production
3. **Phase 10** (Web UI) - Dashboard for pipeline management

### Development
```bash
# Run tests
pytest tests/ -v

# Start server
cd src && uvicorn main:app --reload
```
