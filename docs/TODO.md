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
- 340 tests passing (77 new tests in generalization phase)
- Complete scraping pipeline operational (Firecrawl + rate limiting + worker)
- Document chunking with semantic header splitting implemented
- LLM client with retry logic and JSON mode extraction ready
- All infrastructure services monitored (PostgreSQL, Redis, Qdrant, Firecrawl)
- Background job scheduler runs automatically
- **Repository layer complete** (Project, Source, Extraction, Entity repositories)

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

## Phase 3: Extraction Module (In Progress - ~50% Complete)

See: `docs/TODO_extraction.md`

### Completed
- [x] Built-in profiles defined in database
- [x] **Document chunking module** (PR #8 - 17 tests)
- [x] **LLM client implementation** (PR #8 - 9 tests)
- [x] **Data models** (DocumentChunk, ExtractedFact, ExtractionResult)
- [x] **Profile repository** (PR #9 - 10 tests)
- [x] **Extraction orchestrator** (PR #9 - 9 tests)
- [x] **Fact validator** (PR #9 - 11 tests)

### Pending
- [ ] Chunk result merging with deduplication
- [ ] Integration tests for extraction
- [ ] API endpoints (POST /extract, GET /profiles)

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

**Pending:**
- [ ] Project CRUD API endpoints
- [ ] Additional project templates (research_survey, contract_review)
- [ ] Project-scoped search and queries
- [ ] Clone project from template
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
- [ ] Create `EntityExtractor` class (LLM-based extraction)
- [ ] Integrate into extraction pipeline

### Entity Queries
- [ ] Entity-filtered search endpoint
- [ ] Comparison queries (e.g., compare pricing across source_groups)
- [ ] Hybrid search (vector + entity filtering)

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

**Pending:**
- [ ] Embedding service (BGE-large-en via vLLM)
- [ ] Semantic search endpoint (hybrid vector + JSONB)
- [ ] Pagination

### Deduplication
See: `docs/TODO_deduplication.md`

**Entity Deduplication (Completed):**
- [x] Entity deduplication via `EntityRepository.get_or_create()`
- [x] Scoped by (project_id, source_group, entity_type, normalized_value)

**Extraction Deduplication (Pending):**
- [ ] Implement `ExtractionDeduplicator` class
- [ ] Embedding similarity check before insert
- [ ] Single threshold (0.90) for MVP
- [ ] Same-source_group deduplication only (MVP)

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
- [ ] **Project endpoints** (`POST /projects`, `GET /projects/{id}`)
- [ ] Extract endpoints (`POST /extract`, `GET /profiles`)
- [ ] Search endpoint (`POST /search`)
- [ ] Entity query endpoints (`GET /entities`, `GET /entities/{type}`)
- [ ] Report endpoints (`POST /reports`, `GET /reports/{id}`)
- [ ] Jobs endpoint (`GET /jobs` - list all jobs)
- [ ] Metrics endpoint (`/metrics` - Prometheus format)

**Backward Compatibility:**
- [ ] Legacy endpoints use default "company_analysis" project
- [ ] New endpoints are project-scoped: `/api/v1/projects/{project_id}/...`

**Milestone**: Full API functional and secured

---

## Phase 9: Polish & Hardening

- [ ] Configure structured logging with structlog
- [ ] Add metrics endpoint (Prometheus format)
- [x] Job status tracking (queued, running, completed, failed) - PR #7
- [ ] Retry failed jobs (manual trigger)
- [ ] Basic metrics (scrape count, extraction count)
- [x] Config validation on startup (pydantic-settings)
- [ ] Graceful shutdown handling
- [ ] Add request ID tracing

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
| **Knowledge Layer** | `docs/TODO_knowledge_layer.md` | 5 | **Repository Complete (28 tests)** |
| Deduplication | `docs/TODO_deduplication.md` | 6 | Entity dedup done |
| **Storage** | `docs/TODO_storage.md` | 6 | **Qdrant Complete (89 tests)** |
| Reports | `docs/TODO_reports.md` | 7 | Not started |

---

## Test Coverage

**Current: 352 tests passing** (89 new in generalization phase)
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
- 12 QdrantRepository tests (NEW)

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
