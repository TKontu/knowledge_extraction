# Knowledge Extraction Pipeline - TODO

Master task list. Module-specific details in `docs/TODO_*.md`.

## Progress Summary

**Completed (8 PRs merged to main):**
- PR #1: Foundational FastAPI service with auth, CORS, health check
- PR #2: Redis connection with health monitoring
- PR #3: GET job status endpoint
- PR #4: SQLAlchemy ORM models for all database tables
- PR #5: Documentation updates and Claude commands
- PR #6: Database persistence for jobs and Qdrant health monitoring
- PR #7: Firecrawl client, background worker, and rate limiting
- PR #8: LLM integration with document chunking and client

**Current State:**
- 493 tests passing (76 new tests from entity/search/dedup agents)
- Complete scraping pipeline operational (Firecrawl + rate limiting + worker)
- Document chunking with semantic header splitting implemented
- LLM client with retry logic and JSON mode extraction ready
- All infrastructure services monitored (PostgreSQL, Redis, Qdrant, Firecrawl)
- Background job scheduler runs automatically
- **Repository layer complete** (Project, Source, Extraction, Entity, Qdrant repositories)
- **Vector search complete** (EmbeddingService, SearchService with hybrid search)
- **Project CRUD API complete** (POST /projects, GET /projects, etc.)
- **Extraction API endpoints complete** (POST /extract, GET /extractions with filtering)
- **Search API endpoint complete** (POST /projects/{id}/search with hybrid search)
- **Entity API endpoints complete** (list, get, filter by type, by-value queries)
- **EntityExtractor complete** (LLM-based extraction with normalization and dedup)
- **ExtractionDeduplicator complete** (embedding similarity with 0.90 threshold)

**Architectural Direction:**
The system is being generalized from "TechFacts Scraper" to a **general-purpose extraction pipeline** supporting any domain via project-based configuration.

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

### Database Migrations
See: `docs/TODO_migrations.md`

> **Critical:** Schema evolution requires migrations before production deployment.

- [ ] Install Alembic
- [ ] Reconcile ORM models with init.sql schema
- [ ] Create alembic.ini and env.py (async-compatible)
- [ ] Generate initial migration from ORM models
- [ ] Create seed migration for builtin profiles/default project
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
- [ ] Additional project templates (research_survey, contract_review)
- [ ] Project-scoped search API endpoint
- [ ] Seed script for default project

**Milestone**: Can create and manage extraction projects with custom schemas

---

## Phase 5: Knowledge Layer (Entities)

See: `docs/TODO_knowledge_layer.md`

> Enables structured queries like "Which companies support SSO?" without LLM inference.

### Entity Extraction (MVP)
- [x] Update `entities` table with project_id (in ORM)
- [x] Entity types from project configuration (stored in project.entity_types JSONB)
- [x] **EntityRepository** (deduplication via get_or_create, 28 tests)
- [x] Value normalization per type (normalized_value field)
- [x] **EntityExtractor class** (LLM-based extraction with `_normalize()`, `_call_llm()`, `_store_entities()`, `extract()`)
- [ ] Integrate into extraction pipeline

### Entity Queries
- [x] **Entity API endpoints** (list, get, filter by type, by-value queries - 15 tests)
- [ ] Comparison queries (e.g., compare pricing across source_groups)
- [x] Hybrid search (vector + entity filtering) - via SearchService

### Relations (Post-MVP)
- [ ] Add `relations` table
- [ ] Relation extraction (has_feature, has_limit, has_price)
- [ ] Graph queries

**Milestone**: Structured queries on entities, accurate comparison tables

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

### Deduplication
See: `docs/TODO_deduplication.md`

**Entity Deduplication (Completed):**
- [x] Entity deduplication via `EntityRepository.get_or_create()`
- [x] Scoped by (project_id, source_group, entity_type, normalized_value)

**Extraction Deduplication (Completed):**
- [x] **ExtractionDeduplicator class** (`services/storage/deduplication.py` - 17 tests)
- [x] Embedding similarity check via `check_duplicate()`
- [x] Single threshold (0.90) for MVP
- [x] Same-source_group deduplication only (MVP)
- [ ] Integrate into extraction pipeline

**Milestone**: Can search extractions semantically with filters, no duplicates

---

## Phase 7: Report Generation

See: `docs/TODO_reports.md`

- [ ] Report type definitions (single, comparison, topic, summary)
- [ ] Fact aggregation logic
- [ ] **Structured comparison via entities** (not just LLM inference)
- [ ] Report prompt templates (project-aware)
- [ ] Markdown output generation
- [ ] PDF export (optional, via Pandoc)

**Milestone**: Can generate comparison reports with structured entity data

---

## Phase 8: API & Integration

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
- [ ] Legacy extract endpoints (`POST /api/v1/extract`, `GET /api/v1/profiles`)
- [x] **Search endpoint** (`POST /api/v1/projects/{project_id}/search`)
- [x] **Entity query endpoints** (`api/v1/entities.py` - list, get, types, by-value)
- [ ] Report endpoints (`POST /reports`, `GET /reports/{id}`)
- [ ] Jobs endpoint (`GET /jobs` - list all jobs)
- [ ] Metrics endpoint (`/metrics` - Prometheus format)

**Backward Compatibility:**
- [ ] Legacy endpoints use default "company_analysis" project
- [ ] New endpoints are project-scoped: `/api/v1/projects/{project_id}/...`

**Milestone**: Full API functional and secured

---

## Phase 9: Polish & Hardening

### Logging & Monitoring
- [ ] Configure structured logging with structlog (currently imported but not initialized)
- [ ] Add logging to health check failures (currently silent try-except blocks)
- [ ] Add metrics endpoint (Prometheus format)
- [x] Job status tracking (queued, running, completed, failed) - PR #7
- [ ] Retry failed jobs (manual trigger)
- [ ] Basic metrics (scrape count, extraction count)
- [x] Config validation on startup (pydantic-settings)
- [ ] Graceful shutdown handling
- [ ] Add request ID tracing

### Security Hardening
- [ ] Remove insecure default API key or make it required via env var (src/config.py:16-19)
- [ ] Add application-level rate limiting (currently only domain-based scraping limits)
- [ ] Add HTTPS enforcement option in configuration
- [ ] Document security best practices in README
- [ ] Add API key rotation mechanism
- [ ] Consider JWT authentication for multi-user scenarios

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
- [ ] Export to CSV/JSON
- [ ] Multi-user support with auth
- [ ] HTTPS with nginx reverse proxy

---

## Quick Reference

| Module | File | Phase | Status |
|--------|------|-------|--------|
| **Generalization** | `docs/TODO_generalization.md` | 0 | **Schema Complete (96 tests)** |
| Migrations | `docs/TODO_migrations.md` | 0 | Not started |
| **Project System** | `docs/TODO_project_system.md` | 4 | **Repository Complete (40 tests)** |
| Scraper | `docs/TODO_scraper.md` | 2 | Complete (needs refactor) |
| Extraction | `docs/TODO_extraction.md` | 3 | ~50% (needs refactor) |
| LLM Integration | `docs/TODO_llm_integration.md` | 3 | Foundation Complete |
| **Knowledge Layer** | `docs/TODO_knowledge_layer.md` | 5 | **EntityExtractor + API Complete** |
| **Deduplication** | `docs/TODO_deduplication.md` | 6 | **Complete (17 tests)** |
| **Storage** | `docs/TODO_storage.md` | 6 | **Search API Complete (112 tests)** |
| Reports | `docs/TODO_reports.md` | 7 | Not started |

---

## Test Coverage

**Current: 493 tests passing** (76 new from agent work)
- 14 authentication tests
- 6 CORS tests
- 8 database connection tests
- 18 ORM model tests (PR #4)
- 17 Generalized ORM model tests (relationships, constraints)
- 8 Redis connection tests
- 23 scrape endpoint tests
- 14 FirecrawlClient tests (PR #7)
- 23 DomainRateLimiter tests (PR #7)
- 16 ScraperWorker tests (PR #7)
- 17 Document chunking tests (PR #8)
- 9 LLM client tests (PR #8)
- 10 Profile repository tests (PR #9)
- 9 Extraction orchestrator tests (PR #9)
- 11 Fact validator tests (PR #9)
- 20 Database schema tests (generalization)
- 19 ProjectRepository tests (generalization)
- 21 SchemaValidator tests (generalization)
- 23 SourceRepository tests (generalization)
- 26 ExtractionRepository tests (generalization)
- 28 EntityRepository tests (generalization)
- 12 QdrantRepository tests
- 7 EmbeddingService tests
- 14 SearchService tests
- 25 Extraction endpoint tests
- **27 EntityExtractor tests (NEW - agent work)**
- **17 ExtractionDeduplicator tests (NEW - agent work)**
- **15 Entity endpoint tests (NEW - agent work)**
- **14 Search endpoint tests (NEW - agent work)**
- **5 Search models tests (NEW - agent work)**

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

1. **Complete Phase 0** (Migrations + Project Layer) - foundation for generalization
2. **Refactor Phase 2** (Scraper) - update for sources/project context
3. **Complete Phase 3** (Extraction) - orchestration + API endpoints
4. **Complete Phase 4** (Project System) - CRUD + templates
5. **Complete Phase 6** (Storage) - embeddings + search + dedup
6. Phase 5 (Knowledge Layer) - entity extraction
7. Phase 7 (Reports) after search is working
8. Phase 9 (Polish) after core functionality works
