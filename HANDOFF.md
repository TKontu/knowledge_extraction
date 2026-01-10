# Handoff: Generalization Architecture + Extraction Service

**Session Date:** 2026-01-10
**Branch:** `feat/extraction-service`
**PR:** https://github.com/TKontu/knowledge_extraction/pull/9
**Tests:** 186 passing (30 new)

---

## Architectural Pivot: Generalization

This session introduced a **major architectural change**: transforming from a single-purpose "TechFacts Scraper" to a **general-purpose extraction pipeline** supporting any domain.

### Key Changes

| Concept | Before | After |
|---------|--------|-------|
| Purpose | Company technical facts only | Any extraction domain |
| Schema | Fixed (fact_text, category, confidence) | Project-defined (JSONB) |
| Grouping | Hardcoded `company` field | Configurable `source_group` |
| Tables | `pages`, `facts`, `profiles` | `projects`, `sources`, `extractions` |
| Entity types | 5 hardcoded types | Project-configurable via JSONB |

### New Documentation

| File | Purpose |
|------|---------|
| `docs/TODO_generalization.md` | Complete design for project-based architecture |
| `docs/TODO_project_system.md` | Project CRUD, templates, schema validation |

### Updated Documentation

- `docs/TODO.md` - Added Phase 0 (Foundation), Phase 4 (Project System)
- `docs/TODO_storage.md` - Generalized schema with JSONB queries
- `docs/TODO_extraction.md` - Schema-driven extraction
- `docs/TODO_knowledge_layer.md` - Project-scoped entities
- `docs/TODO_migrations.md` - Marked as deferred (can recreate from scratch)

---

## Completed This Session ✅

### 1. Architectural Planning
Reviewed candidate TODO proposals and integrated generalization strategy:
- Project abstraction layer for multi-domain support
- JSONB hybrid schema for flexible extraction
- Incremental migration approach preserving existing code
- Backward compatibility via default project

### 2. PR #9: Extraction Service Foundation
Implemented Phase 3 (Extraction Module) core components using TDD:

1. **Profile Repository** (`services/extraction/profiles.py`)
   - Load extraction profiles from PostgreSQL
   - Methods: `get_by_name()`, `list_all()`, `list_builtin()`, `exists()`
   - **10 tests**

2. **Extraction Orchestrator** (`services/extraction/extractor.py`)
   - Orchestrates: chunking → LLM extraction → fact merging
   - Handles multi-chunk documents, deduplication, header context
   - **9 tests**

3. **Fact Validator** (`services/extraction/validator.py`)
   - Validates facts against profile criteria
   - Configurable confidence threshold, category validation
   - **11 tests**

### 3. PR #8: LLM Integration (Previously Merged)
- Document chunking (`services/llm/chunking.py`) - 17 tests
- LLM client (`services/llm/client.py`) - 9 tests

---

## Next Steps 📋

### Phase 0: Foundation (NEW - Priority)

Before continuing with existing extraction work, implement generalization foundation:

1. **Schema Update** (`init.sql`)
   - [ ] Add `projects` table with JSONB configuration
   - [ ] Add `sources` table (generalized `pages`)
   - [ ] Add `extractions` table (generalized `facts`)
   - [ ] Update `entities` table with `project_id`
   - [ ] Update `jobs` table with `project_id`

2. **Project System** (See `docs/TODO_project_system.md`)
   - [ ] Create Project ORM model
   - [ ] Create ProjectRepository
   - [ ] Create SchemaValidator (dynamic Pydantic from JSONB)
   - [ ] Create project templates (company_analysis, research_survey, contract_review)
   - [ ] Create default "company_analysis" project

3. **Refactoring Required**
   - [ ] Update scraper worker: `Page` → `Source` with `project_id`
   - [ ] Update extraction: fixed schema → project schema
   - [ ] Update terminology: `company` → `source_group`

### Phase 3 Continuation: Extraction (After Phase 0)

**Increment 3b: Integration Tests**
- [ ] End-to-end extraction test with mocked LLM
- [ ] Test with different project schemas
- [ ] Test error handling

**Increment 4: API Endpoints**
- [ ] `POST /api/v1/projects/{project_id}/extract`
- [ ] `GET /api/v1/projects/{project_id}/extractions`
- [ ] Legacy endpoints using default project

**Increment 5: Storage & Embeddings**
- [ ] Store extractions with JSONB data
- [ ] Generate embeddings via BGE-large-en
- [ ] JSONB query support

---

## Key Files 📁

### New Documentation
- `docs/TODO_generalization.md` - Generalization architecture
- `docs/TODO_project_system.md` - Project management design

### Production Code
- `pipeline/services/extraction/profiles.py` - Profile loading
- `pipeline/services/extraction/extractor.py` - Orchestration
- `pipeline/services/extraction/validator.py` - Validation
- `pipeline/services/llm/chunking.py` - Document chunking
- `pipeline/services/llm/client.py` - LLM client

### Tests (186 total)
- `test_profile_repository.py` - 10 tests
- `test_extractor.py` - 9 tests
- `test_fact_validator.py` - 11 tests
- `test_chunking.py` - 17 tests
- `test_llm_client.py` - 9 tests

---

## Context & Decisions 💡

### Generalization Strategy
1. **Project abstraction** - Every operation scoped to a project with custom schema
2. **JSONB hybrid** - Dynamic extraction schema + common fields for indexing
3. **Incremental migration** - Preserve existing 186 tests and working code
4. **Backward compatibility** - Legacy endpoints use default "company_analysis" project

### Rejected/Deferred Proposals
- **arq job queue** - Current BackgroundTasks sufficient
- **Full relation extraction** - Entity-only for MVP
- **Alembic migrations** - Can recreate database from scratch

### Terminology Reference
| Old Term | New Term | Notes |
|----------|----------|-------|
| `pages` | `sources` | Supports web, PDF, API, text |
| `facts` | `extractions` | JSONB data field for dynamic schema |
| `company` | `source_group` | Configurable grouping concept |
| `profiles` | `project.extraction_schema` | Part of project configuration |

### Configuration
```python
OPENAI_BASE_URL = "http://192.168.0.247:9003/v1"  # vLLM gateway
OPENAI_EMBEDDING_BASE_URL = "http://192.168.0.136:9003/v1"  # BGE-large-en
LLM_MODEL = "gemma3-12b-awq"
```

---

## Commands for Next Session

```bash
# Check out the extraction service branch
git checkout feat/extraction-service

# Verify tests pass
source pipeline/.venv/bin/activate
pytest -v

# Review generalization design
cat docs/TODO_generalization.md
cat docs/TODO_project_system.md

# Start with schema updates
vim init.sql  # Add projects, sources, extractions tables

# Or continue with current extraction work
pytest pipeline/tests/test_extractor.py -v
```

---

## Session Summary

**Major Achievement:** Defined generalization architecture for multi-domain extraction pipeline.

**Progress:**
- ✅ Architectural planning complete
- ✅ TODO documentation updated for generalization
- ✅ Phase 3 Extraction: ~50% complete (core components done)
- 🔲 Phase 0 Foundation: Not started (new priority)
- 🔲 Phase 4 Project System: Not started

**Recommended Order:**
1. Complete Phase 0 (schema + project system) - enables generalization
2. Refactor Phase 2-3 components for new schema
3. Continue Phase 3 (extraction API + storage)
4. Phase 4 (project CRUD API)

---

## Candidate Files

The `docs/candidate/` folder contains the original proposals that were reviewed and integrated. These can be deleted or archived:
- `docs/candidate/TODO.md`
- `docs/candidate/TODO_generalization.md`
- `docs/candidate/TODO_storage.md`
- `docs/candidate/ARCHITECTURE.md`

---

💡 **Tip:** Run `/clear` to start the next session fresh with this context loaded.
