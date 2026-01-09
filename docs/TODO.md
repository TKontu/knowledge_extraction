# TechFacts Scraper - TODO

Master task list. Module-specific details in `docs/TODO_*.md`.

## Phase 1: Infrastructure Setup

### Docker Stack (Remote: 192.168.0.136)

- [x] Create `docker-compose.yml` with all services
- [x] Configure Firecrawl + Playwright
- [x] Configure Redis
- [x] Configure Qdrant
- [x] Configure PostgreSQL
- [x] Create `.env.example` with all variables
- [ ] **Add API key authentication to pipeline service**
- [ ] **Add simple Web UI service for remote control**
- [ ] **Add health check with dependency status**
- [ ] **Add metrics endpoint (Prometheus format)**
- [ ] Test deployment via Portainer
- [ ] Verify network connectivity to vLLM gateway (192.168.0.247:9003)
- [ ] Verify remote access from workstation

### Database Schema

- [ ] Create PostgreSQL migrations
  - [ ] `pages` table
  - [ ] `facts` table  
  - [ ] `jobs` table
  - [ ] `profiles` table
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

- [ ] FastAPI application structure
- [ ] **API key authentication middleware**
- [ ] CORS configuration for Web UI
- [ ] Scrape endpoints (`POST /scrape`, `GET /scrape/{id}`)
- [ ] Extract endpoints (`POST /extract`, `GET /profiles`)
- [ ] Search endpoint (`POST /search`)
- [ ] Report endpoints (`POST /reports`, `GET /reports/{id}`)
- [ ] Jobs endpoint (`GET /jobs`, `GET /jobs/{id}`)
- [ ] Health check endpoint (with dependency checks)
- [ ] Metrics endpoint (`/metrics`)
- [ ] Basic error responses
- [ ] OpenAPI documentation

**Milestone**: Full API functional and secured

---

## Phase 7: Polish & Hardening

- [ ] Job status tracking (proper state machine)
- [ ] Retry failed jobs
- [ ] Logging configuration
- [ ] Basic metrics (scrape count, extraction count)
- [ ] Config validation on startup
- [ ] Graceful shutdown handling

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
| Scraper | `docs/TODO_scraper.md` | Not started |
| Extraction | `docs/TODO_extraction.md` | Not started |
| Storage | `docs/TODO_storage.md` | Not started |
| Reports | `docs/TODO_reports.md` | Not started |

---

## Getting Started

1. Complete Phase 1 (Infrastructure)
2. Work through modules in order (scraper → extraction → storage → reports)
3. Phase 6 (API) can be built incrementally alongside modules
4. Phase 7 after core functionality works
