# TechFacts Scraper - TODO

Master task list. Module-specific details in `docs/TODO_*.md`.

## Progress Summary

**Completed (3 PRs merged to main):**
- ✅ PR #1: Foundational FastAPI service with auth, CORS, health check
- ✅ PR #2: Redis connection with health monitoring
- ✅ PR #3: GET job status endpoint

**Current State:**
- 53 tests passing
- Basic API functional with stub endpoints
- In-memory job storage (temporary)
- Database and Redis connections configured but not integrated

**Next Priority:**
1. Database integration for jobs table
2. Qdrant connection and health check
3. Metrics endpoint
4. Structured logging configuration

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
- [ ] **Add Qdrant health check to health endpoint**
- [ ] **Add metrics endpoint (Prometheus format)**
- [ ] **Add simple Web UI service for remote control**
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
- [ ] Create SQLAlchemy ORM models
- [ ] Create database migration system (Alembic)
- [ ] Create Qdrant collection (dim=1024 for BGE-large-en)

---

## Phase 2: Scraper Module

See: `docs/TODO_scraper.md`

- [ ] Firecrawl client wrapper
- [ ] Rate limiting logic (per-domain)
- [ ] URL queue management
- [ ] Page storage (raw markdown)
- [ ] Basic error handling (retries, backoff)
- [ ] FlareSolverr integration (optional fallback)

**Milestone**: Can scrape URLs and store markdown

---

## Phase 3: Extraction Module

See: `docs/TODO_extraction.md`

- [ ] Extraction profile schema
- [ ] Built-in profiles (technical_specs, api_docs, security, pricing, general)
- [ ] Dynamic prompt generation from profile
- [ ] vLLM client (using existing config pattern)
- [ ] Fact parsing and validation
- [ ] Fact storage (PostgreSQL + Qdrant embedding)
- [ ] Basic deduplication (embedding similarity)

**Milestone**: Can extract structured facts from scraped pages

---

## Phase 4: Storage & Search Module

See: `docs/TODO_storage.md`

- [ ] PostgreSQL repository (pages, facts, jobs)
- [ ] Qdrant repository (embeddings)
- [ ] Embedding generation via BGE-large-en
- [ ] Semantic search endpoint
- [ ] Filter support (company, category, date)
- [ ] Pagination

**Milestone**: Can search facts semantically with filters

---

## Phase 5: Report Generation

See: `docs/TODO_reports.md`

- [ ] Report type definitions (single, comparison, topic, summary)
- [ ] Fact aggregation logic
- [ ] Report prompt templates
- [ ] Markdown output generation
- [ ] PDF export (optional, via Pandoc)

**Milestone**: Can generate comparison reports

---

## Phase 6: API & Integration

- [x] FastAPI application structure (PR #1)
- [x] **API key authentication middleware** (PR #1)
- [x] CORS configuration for Web UI (PR #1)
- [x] Basic error responses with FastAPI HTTPException
- [x] OpenAPI documentation (automatic with FastAPI at `/docs`)
- [x] Health check endpoint with DB + Redis status (PR #2)
- [x] Scrape endpoints:
  - [x] `POST /api/v1/scrape` (stub with in-memory storage, PR #1)
  - [x] `GET /api/v1/scrape/{job_id}` (stub with in-memory storage, PR #3)
- [ ] **Integrate scrape endpoints with PostgreSQL jobs table**
- [ ] Extract endpoints (`POST /extract`, `GET /profiles`)
- [ ] Search endpoint (`POST /search`)
- [ ] Report endpoints (`POST /reports`, `GET /reports/{id}`)
- [ ] Jobs endpoint (`GET /jobs` - list all jobs)
- [ ] Metrics endpoint (`/metrics` - Prometheus format)

**Milestone**: Full API functional and secured

**Current Status**: Basic API complete with auth, CORS, health checks. Scrape endpoints functional but using in-memory storage. Need database integration.

---

## Phase 7: Polish & Hardening

- [ ] Configure structured logging with structlog (dependency installed)
- [ ] Job status tracking (proper state machine: queued → running → completed/failed)
- [ ] Retry failed jobs
- [ ] Basic metrics (scrape count, extraction count)
- [x] Config validation on startup (pydantic-settings)
- [ ] Graceful shutdown handling
- [ ] Add request ID tracing
- [ ] Add error monitoring/alerting

---

## Phase 7.5: Web UI (Now Required for Remote Control)

- [ ] Simple HTML/JS dashboard (single page)
- [ ] Job submission forms (scrape, extract, report)
- [ ] Job status list with auto-refresh
- [ ] Search interface
- [ ] Recent activity log
- [ ] API key configuration
- [ ] Containerize with nginx

**Milestone**: Remote control via web browser

---

## Future Enhancements (Post-MVP)

- [ ] Advanced Web UI (React/Vue with real-time updates)
- [ ] Scheduled re-scraping
- [ ] Sitemap discovery
- [ ] Proxy rotation support
- [ ] Celery for parallel extraction
- [ ] Webhook notifications
- [ ] Export to CSV/JSON
- [ ] Multi-user support with auth
- [ ] HTTPS with nginx reverse proxy
- [ ] Grafana dashboards for monitoring

---

## Quick Reference

| Module | File | Status |
|--------|------|--------|
| Pipeline API | `pipeline/` | ✅ In Progress (3 PRs merged) |
| Scraper | `docs/TODO_scraper.md` | Not started |
| Extraction | `docs/TODO_extraction.md` | Not started |
| Storage | `docs/TODO_storage.md` | Not started |
| Reports | `docs/TODO_reports.md` | Not started |

## Test Coverage

**Current: 53 tests passing**
- 14 authentication tests
- 6 CORS tests
- 8 database connection tests
- 8 Redis connection tests
- 10 POST /scrape endpoint tests
- 7 GET /scrape/{job_id} endpoint tests

---

## Getting Started

1. Complete Phase 1 (Infrastructure)
2. Work through modules in order (scraper → extraction → storage → reports)
3. Phase 6 (API) can be built incrementally alongside modules
4. Phase 7 after core functionality works
