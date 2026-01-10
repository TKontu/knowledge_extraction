# TechFacts Scraper - TODO

Master task list. Module-specific details in `docs/TODO_*.md`.

## Progress Summary

**Completed (8 PRs merged to main):**
- ✅ PR #1: Foundational FastAPI service with auth, CORS, health check
- ✅ PR #2: Redis connection with health monitoring
- ✅ PR #3: GET job status endpoint
- ✅ PR #4: SQLAlchemy ORM models for all database tables
- ✅ PR #5: Documentation updates and Claude commands
- ✅ PR #6: Database persistence for jobs and Qdrant health monitoring
- ✅ PR #7: Firecrawl client, background worker, and rate limiting
- ✅ PR #8: LLM integration with document chunking and client

**Current State:**
- 156 tests passing (26 new tests in PR #8)
- Complete scraping pipeline operational (Firecrawl + rate limiting + worker)
- Document chunking with semantic header splitting implemented
- LLM client with retry logic and JSON mode extraction ready
- All infrastructure services monitored (PostgreSQL, Redis, Qdrant, Firecrawl)
- Background job scheduler runs automatically

**Remaining Architectural Gaps:**
1. No database migration strategy (using init.sql)
2. LLM integration complexity not detailed
3. Deduplication strategy undefined
4. **NEW:** No structured entity/relation layer for comparison queries

**Next Priority:**
1. Extraction service (orchestrates chunking + LLM + validation)
2. Extraction API endpoints (POST /extract, GET /profiles)
3. Embeddings and Qdrant storage (fact deduplication)
4. Knowledge layer (entity extraction from facts) - enables structured queries
5. Database migrations (Alembic) - deferred for development speed

---

## Phase 0: Foundation

> **Critical:** Schema evolution requires migrations before production deployment.

### Database Migrations
See: `docs/TODO_migrations.md`

- [ ] Install Alembic
- [ ] Reconcile ORM models with init.sql schema
- [ ] Create alembic.ini and env.py (async-compatible)
- [ ] Generate initial migration from ORM models
- [ ] Create seed migration for builtin profiles
- [ ] Update docker-compose to run migrations on startup
- [ ] Remove init.sql (replaced by migrations)
- [ ] Document migration workflow

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
- [ ] Verify remote access from workstation

### Database Schema

- [x] Create PostgreSQL schema in `init.sql`
  - [x] `pages` table
  - [x] `facts` table
  - [x] `jobs` table
  - [x] `profiles` table (with 5 built-in profiles)
  - [x] `reports` table
  - [x] `rate_limits` table
- [x] **Create SQLAlchemy ORM models** (PR #4 - all 6 tables with relationships)
- [ ] **Create database migration system (Alembic)** → See Phase 0
- [ ] Create Qdrant collection (dim=1024 for BGE-large-en)

---

## Phase 2: Scraper Module ✅ COMPLETE

See: `docs/TODO_scraper.md`

- [x] **Firecrawl client wrapper** (PR #7 - `services/scraper/client.py`)
- [x] **Rate limiting logic (per-domain)** (PR #7 - `services/scraper/rate_limiter.py`)
- [x] **Background worker for job processing** (PR #7 - `services/scraper/worker.py`)
- [x] **Job scheduler with FastAPI integration** (PR #7 - `services/scraper/scheduler.py`)
- [x] **Page storage (raw markdown)** (PR #7 - stores to PostgreSQL `pages` table)
- [x] **Basic error handling** (PR #7 - handles timeouts, 404s, partial failures)
- [ ] Store outbound links from Firecrawl (enables knowledge graph)
- [ ] Retry logic with exponential backoff (nice-to-have)
- [ ] FlareSolverr integration (optional fallback)

**Milestone**: ✅ COMPLETE - Can scrape URLs and store markdown automatically

---

## Phase 3: Extraction Module (In Progress - ~25% Complete)

See: `docs/TODO_extraction.md`

- [x] Built-in profiles defined in database (technical_specs, api_docs, security, pricing, general)
- [x] **Document chunking module** (PR #8 - `services/llm/chunking.py`, 17 tests)
- [x] **LLM client wrapper** (PR #8 - `services/llm/client.py`, 9 tests)
- [x] **Data models** (PR #8 - DocumentChunk, ExtractedFact, ExtractionResult)
- [ ] Extraction profile schema/dataclass
- [ ] Profile loading from database
- [ ] Dynamic prompt generation from profile

### LLM Integration ✅ FOUNDATION COMPLETE
See: `docs/TODO_llm_integration.md`

- [x] **LLM client wrapper (OpenAI-compatible)** (PR #8)
- [x] **Semantic document chunking (split on markdown headers)** (PR #8)
- [x] **Chunk context tracking (header path, position)** (PR #8)
- [x] **JSON mode for structured output** (PR #8)
- [x] **Retry logic for transient failures** (PR #8 - tenacity with exponential backoff)
- [ ] Chunk result merging
- [ ] Fact validation (schema, category matching)
- [ ] Extraction service (orchestration)

### Fact Storage

- [ ] Store facts to PostgreSQL `facts` table
- [ ] Generate embeddings via BGE-large-en
- [ ] Store embeddings to Qdrant with payload

**Milestone**: Can extract structured facts from scraped pages

---

## Phase 4: Knowledge Layer (Entities)

> **NEW:** Enables structured queries like "Which companies support SSO?" without LLM inference.

See: `docs/TODO_knowledge_layer.md`

### Entity Extraction (MVP)
- [ ] Add `entities` and `fact_entities` tables (via Alembic)
- [ ] Create ORM models for Entity, FactEntity
- [ ] Create `EntityExtractor` class
- [ ] Implement entity extraction prompt
- [ ] Value normalization per type (plan, feature, limit, certification, pricing)
- [ ] Integrate into extraction pipeline

### Entity Queries
- [ ] Entity-filtered search endpoint
- [ ] Comparison queries (e.g., compare pricing across companies)
- [ ] Hybrid search (vector + entity filtering)

### Relations (Post-MVP)
- [ ] Add `relations` table
- [ ] Relation extraction (has_feature, has_limit, has_price)
- [ ] Graph queries

**Milestone**: Structured queries on entities, accurate comparison tables

---

## Phase 5: Storage & Search Module

See: `docs/TODO_storage.md`

- [x] **SQLAlchemy ORM models** (PR #4 - all 6 tables with relationships)
- [x] **Qdrant client initialization** (PR #6)
- [ ] PostgreSQL repository classes (pages, facts, profiles, entities)
- [ ] Qdrant repository (embeddings CRUD)
- [ ] Embedding service (BGE-large-en via vLLM)
- [ ] Semantic search endpoint
- [ ] Filter support (company, category, date, **entity type**)
- [ ] Pagination

### Deduplication
See: `docs/TODO_deduplication.md`

- [ ] Implement `FactDeduplicator` class
- [ ] Embedding similarity check before insert
- [ ] Single threshold (0.90) for MVP
- [ ] Same-company deduplication only (MVP)
- [ ] (Future) Cross-company linking

**Milestone**: Can search facts semantically with filters, no duplicates

---

## Phase 6: Report Generation

See: `docs/TODO_reports.md`

- [ ] Report type definitions (single, comparison, topic, summary)
- [ ] Fact aggregation logic
- [ ] **Structured comparison via entities** (not just LLM inference)
- [ ] Report prompt templates
- [ ] Markdown output generation
- [ ] PDF export (optional, via Pandoc)

**Milestone**: Can generate comparison reports with structured entity data

---

## Phase 7: API & Integration

- [x] FastAPI application structure (PR #1)
- [x] **API key authentication middleware** (PR #1)
- [x] CORS configuration for Web UI (PR #1)
- [x] Basic error responses with FastAPI HTTPException
- [x] OpenAPI documentation (automatic with FastAPI at `/docs`)
- [x] Health check endpoint with DB + Redis + Qdrant + Firecrawl status
- [x] Scrape endpoints:
  - [x] `POST /api/v1/scrape` (PR #1, integrated with PostgreSQL in PR #6)
  - [x] `GET /api/v1/scrape/{job_id}` (PR #3, integrated with PostgreSQL in PR #6)
- [x] **Integrate scrape endpoints with PostgreSQL jobs table** (PR #6)
- [ ] Extract endpoints (`POST /extract`, `GET /profiles`)
- [ ] Search endpoint (`POST /search`)
- [ ] **Entity query endpoints** (`GET /entities`, `GET /entities/{type}`)
- [ ] Report endpoints (`POST /reports`, `GET /reports/{id}`)
- [ ] Jobs endpoint (`GET /jobs` - list all jobs)
- [ ] Metrics endpoint (`/metrics` - Prometheus format)

**Milestone**: Full API functional and secured

**Current Status**: Scrape pipeline complete with auth, CORS, health checks, PostgreSQL persistence, background worker, and rate limiting.

---

## Phase 8: Polish & Hardening

- [ ] Configure structured logging with structlog (dependency installed)
- [ ] Add metrics endpoint (Prometheus format)
- [x] Job status tracking (queued → running → completed/failed) - PR #7
- [ ] Retry failed jobs (manual trigger)
- [ ] Basic metrics (scrape count, extraction count)
- [x] Config validation on startup (pydantic-settings)
- [ ] Graceful shutdown handling
- [ ] Add request ID tracing
- [ ] Add error monitoring/alerting

---

## Phase 9: Web UI (Post-MVP)

- [ ] Simple HTML/JS dashboard (single page)
- [ ] Job submission forms (scrape, extract, report)
- [ ] Job status list with auto-refresh
- [ ] Search interface
- [ ] Entity browser
- [ ] Recent activity log
- [ ] API key configuration
- [ ] Containerize with nginx

**Milestone**: Remote control via web browser

---

## Future Enhancements (Post-MVP)

- [ ] Advanced Web UI (React/Vue with real-time updates)
- [ ] Page links storage and crawl expansion
- [ ] Scheduled re-scraping
- [ ] Sitemap discovery
- [ ] Proxy rotation support
- [ ] arq/Celery for parallel extraction (when BackgroundTasks insufficient)
- [ ] Webhook notifications
- [ ] Export to CSV/JSON
- [ ] Multi-user support with auth
- [ ] HTTPS with nginx reverse proxy
- [ ] Grafana dashboards for monitoring

---

## Quick Reference

| Module | File | Priority | Status |
|--------|------|----------|--------|
| Migrations | `docs/TODO_migrations.md` | **Phase 0** | Deferred |
| Scraper | `docs/TODO_scraper.md` | Phase 2 | ✅ Complete (PR #7) |
| Extraction | `docs/TODO_extraction.md` | **Phase 3** | ⚠️ In Progress (25% - PR #8 chunking + client) |
| LLM Integration | `docs/TODO_llm_integration.md` | Phase 3 | ⚠️ Foundation Complete (PR #8) |
| **Knowledge Layer** | `docs/TODO_knowledge_layer.md` | Phase 4 | Not started |
| Deduplication | `docs/TODO_deduplication.md` | Phase 5 | Not started |
| Storage | `docs/TODO_storage.md` | Phase 5 | ⚠️ Partial (ORM + Qdrant client done) |
| Reports | `docs/TODO_reports.md` | Phase 6 | Not started |

---

## Test Coverage

**Current: 156 tests passing** (26 new in PR #8)
- 14 authentication tests
- 6 CORS tests
- 8 database connection tests
- 18 ORM model tests (PR #4)
- 8 Redis connection tests
- 23 scrape endpoint tests (includes persistence tests from PR #6)
- 14 FirecrawlClient tests (PR #7)
- 23 DomainRateLimiter tests (PR #7)
- 16 ScraperWorker tests (PR #7, includes 5 integration tests)
- **17 Document chunking tests (PR #8)**
- **9 LLM client tests (PR #8)**

---

## Getting Started

1. **Complete Phase 3** (Extraction Service) - orchestrate LLM extraction (~25% done)
2. **Complete Phase 3** (API Endpoints) - POST /extract, GET /profiles
3. **Complete Phase 5** (Storage & Search) - embeddings + Qdrant + deduplication
4. **Complete Phase 4** (Knowledge Layer) - entity extraction for structured queries
5. **Complete Phase 0** (Migrations) - Alembic (deferred for development speed)
6. Phase 6 (Reports) after search is working
7. Phase 8 (Polish) after core functionality works
