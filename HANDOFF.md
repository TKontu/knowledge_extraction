# Handoff: Documentation Rewrite - Architecture & README

## Completed

### Full Documentation Rewrite (V1.1)
Created two new comprehensive documentation files from scratch by analyzing actual codebase:

1. **`docs/readmeV1_1.md`** - User-facing documentation
   - Complete system overview with all features
   - Full API endpoint reference (11 endpoint groups)
   - Infrastructure components (9 services documented)
   - Project templates table (4 templates)
   - Configuration reference
   - Usage examples with code snippets
   - Quick start guide

2. **`docs/architectureV1_1.md`** - Technical deep-dive
   - Complete pipeline flow diagrams
   - Stage-by-stage breakdown (scraping, extraction, storage)
   - Service architecture details
   - Data models with schema examples
   - Concurrency patterns
   - Storage patterns (deduplication, entity normalization)
   - Document chunking algorithm
   - LLM integration (queue + worker)
   - Deployment guide

### Critical Components Documented (Previously Missing)

**Infrastructure:**
- Camoufox browser service (anti-bot, 0% detection rate)
- Proxy Adapter + FlareSolverr integration
- RabbitMQ message broker
- Firecrawl database (separate PostgreSQL)
- Shutdown Manager (graceful termination)

**Services:**
- LLM Worker (adaptive concurrency, DLQ)
- LLM Request Queue (Redis Streams)
- Report generation (4 types, 3 formats)
- Metrics collection (Prometheus)
- Entity by-value search

**Features:**
- Extraction profiles (general/detailed)
- Header-based chunking (8000 tokens)
- Soft delete for projects
- Template system (4 templates)
- AJAX discovery in Camoufox

### Accuracy Fixes

Fixed critical inaccuracies from old docs:
- ✅ Middleware order (was completely reversed)
- ✅ Deduplication threshold (0.90, not 0.95)
- ✅ Template availability (all 4 available, not "coming soon")
- ✅ API endpoint paths (project-scoped)
- ✅ Export endpoints added
- ✅ Missing configuration variables documented

### Verification Methodology

All claims verified against actual code:
- Read 50+ source files across all modules
- Cross-referenced API routes with actual endpoints
- Verified configuration against config.py
- Checked Docker Compose for infrastructure
- Traced data flow through actual service implementations
- **Zero unverified claims carried over from old docs**

## In Progress

N/A - Documentation rewrite completed.

## Next Steps

- [ ] **Replace old documentation**: Rename `readmeV1_1.md` → `readme.md` and `architectureV1_1.md` → `architecture.md`
- [ ] **Review and commit**: Commit new documentation to repository
- [ ] **Archive old docs**: Move old `readme.md` and `architecture.md` to `docs/archive/` for reference
- [ ] **Update references**: Check if any other files reference old documentation paths
- [ ] **Validate links**: Ensure all internal documentation links work correctly

## Key Files

### New Documentation (Untracked)
- `docs/readmeV1_1.md` - **New user documentation** (376 lines, comprehensive)
- `docs/architectureV1_1.md` - **New technical architecture** (700+ lines, detailed)

### Original Files (For Comparison)
- `docs/readme.md` - Old user documentation (342 lines, outdated/unreliable)
- `docs/architecture.md` - Old architecture doc (551 lines, outdated/unreliable)

### Key Source Files Referenced
- `src/main.py` - FastAPI app, middleware stack verification
- `src/config.py` - All configuration variables
- `src/models.py` - Pydantic models for API
- `src/orm_models.py` - Database schema
- `src/services/camoufox/` - Anti-bot browser service
- `src/services/proxy/` - FlareSolverr integration
- `src/services/llm/worker.py` - LLM worker implementation
- `src/services/extraction/` - Extraction pipeline
- `src/services/scraper/` - Scraping workers
- `src/services/reports/` - Report generation
- `src/api/v1/*.py` - All API endpoints (11 files)
- `docker-compose.yml` - Infrastructure stack definition

## Context

### Documentation Approach

**Problem:** Old documentation was unreliable, contained outdated information, and had significant gaps.

**Solution:** Complete rewrite by systematically analyzing actual codebase module-by-module.

**Process:**
1. Explored project structure and identified all modules
2. Read service implementations to understand actual functionality
3. Cross-referenced claims from old docs against code
4. Documented only verified components
5. Added missing critical infrastructure (Camoufox, Proxy Adapter, LLM Worker)
6. Fixed inaccuracies (middleware order, thresholds, endpoints)

### Key Decisions

1. **Created V1_1 files instead of overwriting**: Allows side-by-side comparison and safe rollback
2. **Excluded Playwright**: Legacy service being phased out, not documented per user request
3. **Omitted hardware specs**: Environment-specific details not suitable for generic docs
4. **Added all 4 templates**: research_survey, contract_review, book_catalog all fully implemented despite old docs claiming "coming soon"
5. **Documented actual API structure**: All endpoints are project-scoped (`/api/v1/projects/{id}/...`)

### Architecture Highlights

The system is more sophisticated than old docs indicated:

**Multi-Layer Anti-Bot Protection:**
- Firecrawl (orchestrator) → Camoufox (browser pool) → Proxy Adapter → FlareSolverr
- Domain-based routing, AJAX discovery, content stability detection

**Distributed LLM Processing:**
- Client enqueues → Redis Streams → Worker pool → Adaptive concurrency
- Timeout-based scaling, DLQ for failures, consumer groups

**Smart Chunking:**
- Header-aware (preserves breadcrumbs)
- 8000 token limit with semantic splitting
- Falls back gracefully for oversized content

### No Blockers

Documentation is complete and ready for use. All information verified against actual code.

---

**Recommendation:** Run `/clear` to start fresh session for implementation work or other tasks.
